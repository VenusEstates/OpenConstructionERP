# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Resolve a production-norm material to the cost item that prices it.

A norm's material coefficient carries a NAME, not an id, so building a priced
assembly has to find the cost item behind that name. Two tiers, tried strictly
in this order:

1. **Exact normalized name** against the cost-item spine. The material name and
   each candidate's description are reduced to a comparison key by
   :func:`normalize_material_name` (accents folded, case folded, punctuation and
   whitespace dropped, a decimal separator between digits kept as a mark), and
   only an exact key equality counts. This is a
   deterministic identity match, not a guess: confidence is 1.0 and the priced
   line needs no review.
2. **Fuzzy lexical match** over the catalogue, via the shared
   :func:`app.modules.costs.matcher.match_cwicr_items`. This is a heuristic
   proposal: it prices the line but is reported with its score and flagged for
   human review.

The ordering is the fix, not a stricter fuzzy threshold. A threshold change only
moves which wrong answers appear; putting an exact identity match ahead of a
lexical one removes the class of "a different product priced this line while the
right one sat in the same table".

Why the exact tier does not reuse the lexical matcher's candidate window: that
window is an ``OR``-ILIKE over the query tokens capped at 400 rows with no
ordering, so on a large catalogue the correct row can simply be absent from it.
The exact tier runs its own, far more selective queries and falls back to a
bounded window only when the selective passes find nothing.

No module state, no I/O beyond the session it is handed, so the normalizer and
the tie-break are unit-testable on their own.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.costs.models import CostItem

# The provenance tokens live in the pure pricing module so the DB layer and the
# wire contract cannot drift apart on spelling.
from app.modules.norm_expansion.price_math import COST_ITEM_EXACT, COST_ITEM_FUZZY

# Rows pulled by a selective (equality / AND-ILIKE) pass. A material name that
# matches this many rows exactly is a data problem, not a matching problem; the
# tie-break still returns one deterministically.
_EXACT_CAP = 200

# Rows pulled by the last-resort OR-ILIKE window, used only when both selective
# passes come back empty. Wider than the lexical matcher's 400 because this pass
# is a filter (exact key equality) rather than a ranking.
_WINDOW_CAP = 1000

_MIN_TOKEN_LEN = 3

# The character a decimal separator between digits is folded to before the
# punctuation strip below runs. Any character the strip keeps would do; what
# matters is that something survives where the separator stood, so that a value
# and its separator-less neighbour cannot become the same key.
_DECIMAL_MARK = "·"

_DECIMAL_SEPARATOR = re.compile(r"(?<=\d)[.,](?=\d)")

# Everything that is neither a letter, a digit nor the decimal mark. ``\w`` is
# Unicode-aware in a str pattern, so a Cyrillic, Greek or CJK name keeps its
# letters instead of being reduced to whatever ASCII digits it happens to carry;
# it also matches ``_``, which is punctuation for this purpose, so the
# underscore is put back on the junk side. The mark is interpolated rather than
# spelt again: written twice, changing it in one place would silently start
# stripping the very character the other place inserts.
_NON_ALNUM = re.compile(rf"(?:[^\w{re.escape(_DECIMAL_MARK)}]|_)+")

# Tokens for the SQL prefilter. Unicode-aware for the same reason: a name whose
# letters are not Latin has to produce tokens, or two of the three candidate
# passes below return early and only the byte-exact literal one is left.
_TOKEN = re.compile(r"[^\W_]+")


def normalize_material_name(text: str | None) -> str:
    """Reduce a material name to its comparison key.

    Two names that denote the same product but were typed by different people
    (or exported by different systems) differ in case, accents, punctuation and
    spacing far more often than in words. The key folds exactly those away:
    Unicode NFKD decomposition drops combining marks (``Lámina`` -> ``lamina``),
    the result is case-folded, and every character that is not a letter or a
    digit is removed - so ``"12 mm"`` and ``"12mm"`` collapse to the same key
    while ``"12 mm"`` and ``"15 mm"`` stay apart.

    Two details of that fold are load-bearing, because the tier calling this an
    identity match has no second opinion to fall back on.

    A letter is any Unicode letter, not an ASCII one. Keeping only ASCII would
    reduce ``"Бетон М300"`` to ``"300"`` and ``"Кирпич 300"`` to the same
    ``"300"``, and the caller would then price concrete at the brick's rate and
    report it as an identity match needing no review. Every script the
    production norms ship in is affected the same way, so the fold keeps the
    letters and lets the names disagree.

    A decimal separator between digits is folded to a fixed mark rather than
    deleted. Deleting it is what makes ``"1,22"`` and ``"1.22"`` agree, which is
    the whole point of the key, but it also makes ``"1,2 mm"`` and ``"12 mm"``
    agree, and a 1.2 mm steel stud priced as a 12 mm board is the same silent
    wrong answer. Folding to a mark keeps the first pair equal and the second
    pair apart. The cost is that a thousands separator no longer disappears, so
    ``"1,000 kg"`` and ``"1000 kg"`` are now different keys; that direction is
    the safe one, because a missed exact match falls through to the fuzzy tier
    and arrives flagged for review rather than as a settled price.

    Args:
        text: The raw material name or cost-item description.

    Returns:
        The comparison key, or ``""`` when the input carries no letters or
        digits (an empty key never matches anything).
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    # The mark belongs to this function, so a literal one in the source text is
    # punctuation like any other and goes before it can be read as a separator
    # that was never there.
    folded = stripped.casefold().replace(_DECIMAL_MARK, "")
    return _NON_ALNUM.sub("", _DECIMAL_SEPARATOR.sub(_DECIMAL_MARK, folded))


def _prefilter_tokens(text: str) -> list[str]:
    """Case-folded letter / digit tokens of length >= 3, de-duplicated, in order.

    Used to build the SQL prefilter. Short tokens (``mm``, ``x``, ``12``) are
    dropped because they match almost everything and would not narrow the scan.

    A token is a run of Unicode letters or digits, so a name written in any
    script produces tokens. It has to: the two token passes below return an
    empty list when handed no tokens, so a name that tokenizes to nothing is
    reachable only by the byte-exact literal pass, and any difference in spacing
    between the norm and the catalogue then leaves the material unpriced.

    The tokens are read off the raw name rather than the folded key, so an
    accent survives here where it does not survive :func:`normalize_material_name`
    (``Lámina`` is one token, ``lámina``). ``ILIKE`` is not accent-insensitive,
    so that token finds an accented catalogue row and misses an unaccented one;
    the last-resort window exists for exactly that miss, and the key comparison
    still decides every match.
    """
    seen: set[str] = set()
    out: list[str] = []
    for tok in _TOKEN.findall(text.casefold()):
        if len(tok) < _MIN_TOKEN_LEN or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


@dataclass(frozen=True)
class MaterialMatch:
    """One resolved cost item behind a norm material, with its provenance.

    Attributes:
        item: The matched :class:`CostItem`.
        method: The provenance token - ``cost_item_exact`` or
            ``cost_item_fuzzy``.
        confidence: ``1`` for an exact normalized identity match, the matcher's
            0..1 score for a fuzzy one.
        needs_review: ``True`` for a fuzzy match. A heuristic that puts money on
            a line is a proposal, so the caller is told to have a human confirm
            it rather than having it applied silently.
    """

    item: CostItem
    method: str
    confidence: Decimal
    needs_review: bool


def _candidate_keys(item: CostItem) -> set[str]:
    """Every comparison key a cost item can be recognised by.

    The primary ``description`` plus any localized description, so a catalogue
    that stores the Spanish product name under ``descriptions['es']`` is still
    recognised by a Spanish norm material name.
    """
    keys = {normalize_material_name(item.description)}
    localized = item.descriptions
    if isinstance(localized, dict):
        for value in localized.values():
            if isinstance(value, str) and value:
                keys.add(normalize_material_name(value))
    keys.discard("")
    return keys


def _freshness_rank(item: CostItem) -> tuple[int, int]:
    """Sort key putting the most recently priced item first, undated ones last.

    ``price_as_of`` is the day the rate was last set or verified. A catalogue
    that carries the same product twice - once at a price fixed months ago and
    once repriced since - must resolve to the repriced row, otherwise an exact
    name match still hands back a stale number.
    """
    as_of = item.price_as_of
    if isinstance(as_of, date):
        return (0, -as_of.toordinal())
    return (1, 0)


def _tie_break(item: CostItem, *, unit: str | None, region: str | None) -> tuple:
    """Deterministic ordering over several exact matches.

    Unit agreement first (a rate quoted per m2 prices an m2 coefficient), then
    the requested region, then price freshness, then ``code`` so the outcome is
    stable when everything else ties.
    """
    unit_norm = (unit or "").strip().casefold()
    item_unit = (item.unit or "").strip().casefold()
    unit_rank = 0 if unit_norm and item_unit == unit_norm else 1
    region_norm = (region or "").strip()
    item_region = (item.region or "").strip()
    region_rank = 0 if region_norm and item_region == region_norm else 1
    return (unit_rank, region_rank, *_freshness_rank(item), item.code or "")


async def _rows_by_literal_description(session: AsyncSession, name: str) -> list[CostItem]:
    """Rows whose trimmed, lower-cased description equals the name verbatim."""
    stmt = (
        select(CostItem)
        .where(CostItem.is_active.is_(True))
        .where(func.lower(func.trim(CostItem.description)) == name.strip().casefold())
        .limit(_EXACT_CAP)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _rows_by_all_tokens(session: AsyncSession, tokens: list[str]) -> list[CostItem]:
    """Rows whose description contains EVERY distinctive token of the name.

    ``AND`` rather than the lexical matcher's ``OR``: a row that carries all of
    ``lamina``, ``gypsum`` and ``blanca`` is a genuine candidate for that
    material, while a row carrying only ``gypsum`` is noise. The narrower
    predicate is what lets this pass run without a ranking window.
    """
    if not tokens:
        return []
    stmt = (
        select(CostItem)
        .where(CostItem.is_active.is_(True))
        .where(and_(*[CostItem.description.ilike(f"%{tok}%") for tok in tokens]))
        .limit(_EXACT_CAP)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _rows_by_any_token(session: AsyncSession, tokens: list[str]) -> list[CostItem]:
    """Last-resort bounded window over rows carrying ANY distinctive token.

    Reached only when the literal and all-token passes are empty, which happens
    when the stored description differs from the material name by an accented
    character: SQL ``ILIKE`` is not accent-insensitive, so ``'%lamina%'`` does
    not find ``'Lámina'`` even though the two share a comparison key. The keys
    are still compared in Python, so this pass only widens what is looked at, it
    never loosens what counts as a match.
    """
    if not tokens:
        return []
    stmt = (
        select(CostItem)
        .where(CostItem.is_active.is_(True))
        .where(or_(*[CostItem.description.ilike(f"%{tok}%") for tok in tokens]))
        .order_by(CostItem.code)
        .limit(_WINDOW_CAP)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def find_exact_cost_item(
    session: AsyncSession,
    name: str,
    *,
    unit: str | None = None,
    region: str | None = None,
) -> CostItem | None:
    """Find the cost item whose normalized name equals this material's.

    Runs up to three progressively wider candidate passes and keeps only rows
    whose comparison key equals the material's, so widening the scan never
    widens what counts as a match. When several rows match exactly, the
    tie-break picks one deterministically: unit agreement, then region, then the
    freshest ``price_as_of``, then ``code``.

    Args:
        session: Active async DB session.
        name: The norm material's name.
        unit: The material's unit, preferred in the tie-break.
        region: Optional region, preferred in the tie-break.

    Returns:
        The matched :class:`CostItem`, or ``None`` when no exact match exists.
    """
    key = normalize_material_name(name)
    if not key:
        return None

    tokens = _prefilter_tokens(name)
    for loader in (
        lambda: _rows_by_literal_description(session, name),
        lambda: _rows_by_all_tokens(session, tokens),
        lambda: _rows_by_any_token(session, tokens),
    ):
        rows = await loader()
        hits = [row for row in rows if key in _candidate_keys(row)]
        if hits:
            hits.sort(key=lambda row: _tie_break(row, unit=unit, region=region))
            return hits[0]
    return None


async def resolve_material_cost_item(
    session: AsyncSession,
    name: str,
    *,
    unit: str | None = None,
    region: str | None = None,
    min_fuzzy_score: float,
) -> MaterialMatch | None:
    """Resolve a material name to a cost item, exact tier first.

    Args:
        session: Active async DB session.
        name: The norm material's name.
        unit: The material's unit; feeds the exact tie-break and the lexical
            matcher's unit bonus.
        region: Optional region preferred by the exact tie-break.
        min_fuzzy_score: Score floor below which a fuzzy candidate is refused
            and the material is left unpriced.

    Returns:
        The match with its provenance, or ``None`` when nothing resolved.
    """
    import uuid as _uuid

    from app.modules.costs.matcher import match_cwicr_items

    exact = await find_exact_cost_item(session, name, unit=unit, region=region)
    if exact is not None:
        return MaterialMatch(
            item=exact,
            method=COST_ITEM_EXACT,
            confidence=Decimal("1"),
            needs_review=False,
        )

    matches = await match_cwicr_items(session, name, unit=unit or None, top_k=1, source=None)
    if not matches or matches[0].score < min_fuzzy_score:
        return None
    best = matches[0]
    try:
        item_id = _uuid.UUID(best.cost_item_id)
    except (TypeError, ValueError):
        return None
    item = await session.get(CostItem, item_id)
    if item is None:
        return None
    return MaterialMatch(
        item=item,
        method=COST_ITEM_FUZZY,
        confidence=Decimal(str(best.score)),
        needs_review=True,
    )


__all__ = [
    "MaterialMatch",
    "find_exact_cost_item",
    "normalize_material_name",
    "resolve_material_cost_item",
]
