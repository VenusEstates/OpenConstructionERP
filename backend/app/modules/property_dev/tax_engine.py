# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Property-dev jurisdiction-aware tax / VAT / stamp-duty engine.

Pure-function library - every helper takes :class:`~decimal.Decimal`
in / returns Decimal out, never touches the DB, never raises HTTP
exceptions, and never reads the wall clock. The thin async wrapper in
``service.py`` resolves a contract to inputs and hands them to these
functions; the engine itself has zero side-effects so it can be
unit-tested in isolation and reused from the BOQ engine, the
finance module, or batch revenue-recognition jobs.

Design intent
-------------
* **Data-driven** - every rate lives in ``data/tax_rates.yaml`` (not in
  Python source). Adding a new jurisdiction = a YAML edit, not a code
  change.
* **Decimal everywhere** - no float arithmetic. Money is rounded
  ``ROUND_HALF_UP`` to 2 dp on output; intermediate maths uses 6 dp
  of precision so successive percentage applications don't drift.
* **Effective-date aware** - a rate class holds either one band or a dated
  history, and a quote takes the band in force on the contract's own date.
  A contract signed before the earliest band the class holds is refused
  rather than priced, because the alternative is inventing a rate. The
  refusal is scoped to a rate class, not to a jurisdiction, and each class
  begins on its own day: GB dates its standard rate from 1991, its reduced
  rate from 1994 and its zero rate from 1973. Every VAT and GST class in the
  shipped table is dated now except two, and those two are undated on purpose
  with the reason written beside them in the yaml: an Indian rate that was
  computed rather than published, and an Indian supply that is outside GST
  rather than zero-rated. Stamp duty now takes a date the same way:
  :func:`compute_stamp_duty` and the ``band_history`` key it reads accept
  one, mirroring the rate-history mechanism above rather than inventing a
  second one. What stamp duty still lacks is not the signature but the data
  - no jurisdiction in the shipped table has written a ``band_history`` yet,
  so every stamp-duty figure answers alike whatever date is asked, which is
  the gap left open until each jurisdiction's dates are sourced and written
  one at a time. Note the two axes this module calls bands: a stamp-duty
  band is a slice of *price* (:func:`_progressive_band_amount`), a rate band
  is a slice of *time*. The helpers for the second say ``period`` so the two
  never read alike in code, even where a stamp-duty ``band_history`` period
  holds a nested ``bands`` list rather than a scalar ``rate``. A quote
  reports the date its rate (or its band table) is dated from, or reports
  that the table dates it from nothing, so an undated class or table is
  visible to a caller instead of being indistinguishable from a promise.
* **No currency conversion** - the caller supplies the price in the
  contract's currency. Mixing currencies is a finance-module job, not
  a tax-engine job.
* **Unknown jurisdiction → explicit error** - never falls back to a
  silent default that would generate billing-incorrect invoices.

The full tax model for a property purchase is the sum of:

    grand_total = net
                + vat (or zero-rated / exempt)
                + stamp_duty / land-transfer tax
                + transfer_fee  (UAE DLD style)
                + registration_fee
                + late_interest (on overdue instalments)

Each line is itemised in :func:`compute_total_taxes_for_contract` and
exposed through the ``breakdown`` field so the frontend can render a
human-readable invoice row-by-row.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from threading import Lock
from typing import Any

import yaml

from app.core.provenance import Provenance, declared, fell_back, unavailable

# ── Module-level table cache ────────────────────────────────────────────

_TABLE_CACHE: dict[str, Any] | None = None
_TABLE_LOCK = Lock()
_TABLE_PATH = Path(__file__).parent / "data" / "tax_rates.yaml"

# Decimal quantum constants - six-dp intermediate, two-dp final.
_Q_MONEY = Decimal("0.01")
_Q_INTERMEDIATE = Decimal("0.000001")
_ZERO = Decimal("0")

#: The table key a jurisdiction with no ``vat``/``gst`` block must carry.
VAT_ABSENCE_KEY = "vat_absence"

#: The jurisdiction levies no VAT or GST. Zero is the right figure.
VAT_ABSENT_BY_LAW = "by_law"
#: We do not model this jurisdiction's VAT or GST. There is no figure.
VAT_ABSENT_NOT_MODELLED = "not_modelled"

#: Both permitted values, and deliberately only two.
#:
#: The pair names where the gap lives, in the law or in our table, which is a
#: question with two answers and no middle. It is not a confidence or a
#: completeness scale: a third value such as ``"partial"`` would say how well
#: something is modelled rather than whether it is, and a caller cannot derive
#: a source from it. :func:`_validate_vat_absence` refuses anything outside
#: this set at load time, because a comment saying "only these two" does not
#: stop anyone adding a third.
VAT_ABSENCE_VALUES = frozenset({VAT_ABSENT_BY_LAW, VAT_ABSENT_NOT_MODELLED})

#: The provenance axis these quotes describe.
#:
#: ``"vat"`` rather than ``"jurisdiction"``, which the calendar and the address
#: rules use, because a quote resolves several things from one jurisdiction and
#: this describes exactly one of them. An axis named for the jurisdiction would
#: read as a verdict on the whole quote, and the stamp duty beside it is
#: resolved separately and is not covered by this.
VAT_AXIS = "vat"

#: What stood in when a jurisdiction levies no VAT at all.
#:
#: Named for what answered, per :mod:`app.core.provenance`: the zero came from
#: the jurisdiction having no such tax, not from a rate row that happens to
#: read zero.
#:
#: The check to run on this token, and on any token added to any axis later:
#: **would it still be true if the jurisdiction were BR?** If yes, it describes
#: the slot rather than the thing that filled it, and it is too weak. This one
#: fails that question in the right direction. Brazil's VAT is absent from this
#: table while being present in Brazilian law, so nothing about Brazil is
#: described by saying the law levies none.
#:
#: That question is sharper than "name the thing that answered", which is a
#: description a reader has to interpret rather than a check a reader can run.
#: A token drawn from the older summariser sentence about the table modelling
#: no VAT here would have passed the description and failed the check: it
#: covers both blockless rows and so tells a reader nothing. That sentence was
#: written while the two rows were indistinguishable, which is the condition
#: the marker exists to end.
VAT_STANDIN_NO_VAT_IN_LAW = "NO_VAT_IN_LAW"


# ── Exceptions ──────────────────────────────────────────────────────────


class TaxEngineError(Exception):
    """Base for all tax-engine-level errors."""


class UnsupportedJurisdictionError(TaxEngineError):
    """Raised when a jurisdiction code is not in the rate table.

    The message includes the canonical list of supported codes so the
    caller (HTTP layer) can surface it to the user verbatim.
    """

    def __init__(self, jurisdiction: str, supported: Iterable[str]) -> None:
        codes = sorted(supported)
        super().__init__(f"Unsupported jurisdiction '{jurisdiction}'. Supported: {', '.join(codes)}")
        self.jurisdiction = jurisdiction
        self.supported = list(codes)


class MissingRegionSubcodeError(TaxEngineError):
    """Raised when a jurisdiction needs a region subcode (DE state, IN state,
    AU state, US state) and the caller didn't supply one."""

    def __init__(self, jurisdiction: str, supported: Iterable[str]) -> None:
        codes = sorted(supported)
        super().__init__(
            f"Jurisdiction '{jurisdiction}' requires a region_subcode. Supported subcodes: {', '.join(codes)}"
        )
        self.jurisdiction = jurisdiction
        self.supported = list(codes)


class UnknownRateClassError(TaxEngineError):
    """Raised when a VAT rate class is requested that the jurisdiction
    does not define (e.g. ``rate_class='reduced'`` in RU).

    Narrow on purpose: the jurisdiction has a VAT or GST block and the caller
    named a class that is not in it, which is a question this engine cannot
    answer and the caller can correct. A jurisdiction with no block at all is
    :class:`NoVatBlockError` instead. Both used to raise this one, so a caller
    could not tell "you asked for something that does not exist" from "no VAT
    is modelled here", and the summariser flattened both onto the same zero.
    """


class NoVatBlockError(TaxEngineError):
    """Raised when the rate table holds no VAT or GST block for a jurisdiction.

    Deliberately named for the table rather than for the world. It says a block
    is absent, which is checkable by reading ``data/tax_rates.yaml``, and it
    does not say the jurisdiction levies no VAT, which is a claim about tax law
    that nothing here is in a position to make. The distinction is not
    pedantic: the absence is correct for the US, which has no federal VAT, and
    wrong for BR, which levies ICMS, ISS, PIS and COFINS on property
    transactions and is simply not modelled yet. One sentence would be true of
    the first and false of the second, so this says only what is true of both.

    Not a subclass of :class:`UnknownRateClassError`. Making it one would leave
    every existing ``except UnknownRateClassError`` catching it, which would
    make the split cosmetic.
    """

    def __init__(self, jurisdiction: str) -> None:
        super().__init__(f"The rate table holds no VAT or GST block for jurisdiction '{jurisdiction}'")
        self.jurisdiction = jurisdiction


class RateNotInForceError(TaxEngineError):
    """Raised when the table carries no rate in force on the date asked about.

    This is the absence of an answer rather than an answer of zero, and the
    distinction is the reason the class exists. Until it did, a genuinely
    zero-rated supply and a date the table cannot speak for both came back as
    ``Decimal("0.00")``, identical down to :meth:`~decimal.Decimal.as_tuple`,
    so nothing downstream could tell a tax-free sale from an unpriced one. A
    GB contract signed before 2011-01-04 was quoted zero VAT when the rate in
    force that day was 17.5 per cent.

    Returning an older rate is available wherever the table holds one. A rate
    class may carry a dated history, and a date inside it resolves to the band
    that governed that day rather than raising, so a GB contract signed in 2009
    is now quoted at the 15 per cent then in force. What still raises is a date
    before the *earliest* band the class holds, which is the same absence as
    before moved back to where the table genuinely stops: any number produced
    there would be invented rather than approximate. The message names both
    dates so the caller can either ask about a date the table can speak for or
    extend the history in ``data/tax_rates.yaml``.

    ``effective_from`` on this exception means the earliest date the class can
    speak for, which is what the caller has to act on. It is deliberately not
    "the start of the band we were looking at": there is no such band when
    nothing is in force.
    """

    def __init__(
        self,
        jurisdiction: str,
        rate_class: str,
        effective_on: date,
        effective_from: date,
    ) -> None:
        super().__init__(
            f"Jurisdiction '{jurisdiction}' has no '{rate_class}' rate in force on "
            f"{effective_on.isoformat()}; the earliest rate in the table begins "
            f"{effective_from.isoformat()}"
        )
        self.jurisdiction = jurisdiction
        self.rate_class = rate_class
        self.effective_on = effective_on
        self.effective_from = effective_from


# ── Table loader (thread-safe, lazy, cached) ────────────────────────────


def _validate_vat_absence(raw: dict[str, Any]) -> None:
    """Refuse a table whose VAT gaps do not say where they live.

    Runs once per load rather than once per quote, and raises rather than
    defaulting. A default is how the next jurisdiction would acquire a
    provenance nobody chose: it would resolve to whatever the accessor happens
    to return, and nothing would notice. Both blockless rows are marked today,
    so this cannot fire on the shipped table; it exists for the row somebody
    adds next year.

    Three refusals, and the middle one is why this is code rather than a
    comment on the YAML:

    * a jurisdiction with no VAT or GST block that declares nothing;
    * a declared value outside :data:`VAT_ABSENCE_VALUES`;
    * a jurisdiction that has a block and declares the key anyway, which is the
      same shape of contradiction :class:`app.core.provenance.Provenance`
      refuses, told about a table instead of an answer.

    Raises:
        TaxEngineError: on any of the three, naming the jurisdiction. Deferred
            to load rather than to import so a malformed table fails the first
            caller loudly instead of breaking module import for every module
            that happens to depend on this one.
    """
    for code, jur in (raw.get("jurisdictions") or {}).items():
        if not isinstance(jur, dict):
            continue
        declared = jur.get(VAT_ABSENCE_KEY)
        if _has_vat_block(jur):
            if declared is not None:
                raise TaxEngineError(
                    f"jurisdiction '{code}' has a VAT/GST block and also declares "
                    f"{VAT_ABSENCE_KEY}={declared!r}; that key describes an absent block"
                )
            continue
        if declared is None:
            raise TaxEngineError(
                f"jurisdiction '{code}' has no VAT or GST block and does not say why. Add "
                f"{VAT_ABSENCE_KEY}: {VAT_ABSENT_BY_LAW} if the jurisdiction levies none, or "
                f"{VAT_ABSENCE_KEY}: {VAT_ABSENT_NOT_MODELLED} if we simply do not model it"
            )
        if declared not in VAT_ABSENCE_VALUES:
            raise TaxEngineError(
                f"jurisdiction '{code}' declares {VAT_ABSENCE_KEY}={declared!r}, which is not one of "
                f"{sorted(VAT_ABSENCE_VALUES)}. The key says where the gap lives, not how complete it is"
            )


def _check_history_is_ordered(entry: list[Any], where: str) -> None:
    """Refuse a period list that is not read like a dated history.

    The three refusals - empty, an unreadable date, out-of-order - enforced
    once and shared by :func:`_validate_rate_histories` (the ``vat``/``gst``
    rate histories) and :func:`_validate_band_histories` (stamp duty's
    ``band_history`` key), so the two axes this table keeps deliberately
    apart still run the same check rather than two copies of it that could
    drift.

    * an empty history, which names a slot and then says nothing about it;
    * a period with no readable ``effective_from``, which leaves "before the
      earliest period" undefined and so leaves the refusal in
      :func:`_period_in_force` with no date to name;
    * a history that is not in strictly ascending date order.

    The third is the one worth explaining, because :func:`_period_in_force`
    selects by maximum date and so cannot be fooled by order. Nothing computes
    a wrong number from a mis-ordered history; a person reads one. The
    oldest-first rule is for the reader who scans a list for the current rate
    and takes the last line, and without a check the convention would be a
    comment that nobody enforces. Equal dates are refused too: two periods
    starting the same day make "which one is in force" ambiguous, and the
    tie-break would be silent.

    Args:
        entry: the raw list from the table.
        where: dotted path to the list, for the error message.

    Raises:
        TaxEngineError: naming ``where`` and what is wrong with it.
    """
    if not entry:
        raise TaxEngineError(f"rate history '{where}' is empty; a history with no period cannot be quoted")
    previous: date | None = None
    for period in entry:
        effective = _period_date(period) if isinstance(period, Mapping) else None
        if effective is None:
            raise TaxEngineError(
                f"rate history '{where}' has a period with no readable effective_from; every period "
                f"in a history needs one, so that the earliest date the class can speak for is known"
            )
        if previous is not None and effective <= previous:
            raise TaxEngineError(
                f"rate history '{where}' is not in ascending date order: {effective.isoformat()} "
                f"follows {previous.isoformat()}. Write a history oldest first, one period per change"
            )
        previous = effective


def _validate_rate_histories(raw: dict[str, Any]) -> None:
    """Refuse a rate history that is not a dated list written oldest first.

    Scoped to the classes under ``vat`` and ``gst``, and that scope is
    load-bearing rather than tidy. The rest of this table is full of lists of
    mappings on an entirely different axis - ``stamp_duty.bands``,
    ``first_home_relief.bands``, ``bsd.bands``, ``itbi.bands`` - whose elements
    carry a price ceiling and no date at all. A validator keyed on "a list of
    mappings" would refuse the shipped table on its first load. See
    :func:`_validate_band_histories` for that axis's own history key.

    Args:
        raw: the parsed table, before it is cached.

    Raises:
        TaxEngineError: naming the jurisdiction, the block and the rate class.
    """
    for code, jur in (raw.get("jurisdictions") or {}).items():
        if not isinstance(jur, dict):
            continue
        for block_key in ("vat", "gst"):
            block = jur.get(block_key)
            if not isinstance(block, Mapping):
                continue
            for rate_class, entry in block.items():
                if not isinstance(entry, list):
                    continue
                _check_history_is_ordered(entry, f"{code}.{block_key}.{rate_class}")


def _validate_band_histories(raw: dict[str, Any]) -> None:
    """Refuse a ``band_history`` that is not a dated list written oldest first.

    The stamp-duty sibling of :func:`_validate_rate_histories`, kept as its
    own pass rather than folded into that one's block-key loop. That loop is
    scoped away from ``stamp_duty`` on purpose (see its docstring): a
    ``stamp_duty`` block is full of plain ``bands`` lists - price data with no
    date in it - and widening the scope to that block name would reintroduce
    exactly the "is this a list of mappings" sniffing the scoping exists to
    avoid. Scanning for the literal key ``band_history`` instead means the two
    axes still never share a detector; they share only the three checks in
    :func:`_check_history_is_ordered` once one has been found.

    Every slot that can carry one is walked explicitly, because unlike
    ``vat``/``gst`` (a flat dict of rate classes) stamp duty nests its bands at
    a different depth per jurisdiction shape: the top-level block, its
    ``first_home_relief``, each ``by_state`` entry, and the ``bsd``/``itbi``
    blocks that live outside ``stamp_duty`` entirely.

    Args:
        raw: the parsed table, before it is cached.

    Raises:
        TaxEngineError: naming the jurisdiction and the slot.
    """
    for code, jur in (raw.get("jurisdictions") or {}).items():
        if not isinstance(jur, dict):
            continue
        sd = jur.get("stamp_duty")
        if isinstance(sd, Mapping):
            history = sd.get("band_history")
            if isinstance(history, list):
                _check_history_is_ordered(history, f"{code}.stamp_duty")
            relief = sd.get("first_home_relief")
            if isinstance(relief, Mapping):
                relief_history = relief.get("band_history")
                if isinstance(relief_history, list):
                    _check_history_is_ordered(relief_history, f"{code}.stamp_duty.first_home_relief")
            by_state = sd.get("by_state")
            if isinstance(by_state, Mapping):
                for sub, entry in by_state.items():
                    if isinstance(entry, Mapping):
                        sub_history = entry.get("band_history")
                        if isinstance(sub_history, list):
                            _check_history_is_ordered(sub_history, f"{code}.stamp_duty.{sub}")
        for key in ("bsd", "itbi"):
            block = jur.get(key)
            if isinstance(block, Mapping):
                history = block.get("band_history")
                if isinstance(history, list):
                    _check_history_is_ordered(history, f"{code}.{key}")


def _load_table(*, force_reload: bool = False) -> dict[str, Any]:
    """Return the parsed YAML table (cached after first call)."""
    global _TABLE_CACHE
    if _TABLE_CACHE is not None and not force_reload:
        return _TABLE_CACHE
    with _TABLE_LOCK:
        if _TABLE_CACHE is not None and not force_reload:
            return _TABLE_CACHE
        if not _TABLE_PATH.exists():
            raise TaxEngineError(f"tax_rates.yaml not found at expected path {_TABLE_PATH}")
        with _TABLE_PATH.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            raise TaxEngineError("tax_rates.yaml root must be a mapping")
        # Before the cache, not after: a table that fails either of these must
        # not be left behind for the next caller to read as if it had passed.
        _validate_vat_absence(raw)
        _validate_rate_histories(raw)
        _validate_band_histories(raw)
        _TABLE_CACHE = raw
        return raw


def reload_tax_table() -> None:
    """Force-reload the tax table (test helper / hot-reload hook)."""
    _load_table(force_reload=True)


def _table_for(jurisdiction: str) -> dict[str, Any]:
    code = (jurisdiction or "").strip().upper()
    if not code:
        raise UnsupportedJurisdictionError(jurisdiction, supported_jurisdictions())
    table = _load_table()
    jurisdictions = table.get("jurisdictions") or {}
    if code not in jurisdictions:
        raise UnsupportedJurisdictionError(code, jurisdictions.keys())
    return jurisdictions[code]


# ── Decimal helpers ─────────────────────────────────────────────────────


def _D(value: Any) -> Decimal:  # noqa: N802 - short capital is intentional shorthand for Decimal coerce.
    """Coerce anything sane to :class:`Decimal` - strings, ints, floats, None."""
    if value is None or value == "":
        return _ZERO
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        # Avoid Python's bool-is-int trap.
        return Decimal("1") if value else _ZERO
    return Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    """Quantise a Decimal to 2 dp using banker-safe HALF_UP."""
    return value.quantize(_Q_MONEY, rounding=ROUND_HALF_UP)


def _intermediate(value: Decimal) -> Decimal:
    """Quantise to 6 dp for intermediate maths (avoids drift)."""
    return value.quantize(_Q_INTERMEDIATE, rounding=ROUND_HALF_UP)


def _parse_iso(date_str: str | None) -> date | None:
    if date_str is None or not isinstance(date_str, str):
        return None
    try:
        return date.fromisoformat(date_str[:10])
    except (TypeError, ValueError):
        return None


def _period_date(period: Mapping[str, Any]) -> date | None:
    """The day a rate period takes effect, however YAML handed it over.

    ``effective_from: "1991-04-01"`` arrives as a string and
    ``effective_from: 1991-04-01`` arrives already parsed by PyYAML into a
    :class:`~datetime.date`. Both are ordinary ways of writing the same day, so
    both are read here rather than one of them being refused at load over its
    punctuation. A timestamp is narrowed to its date so the comparisons in
    :func:`_period_in_force` never mix the two types.

    Args:
        period: one rate period from the table.

    Returns:
        The effective date, or ``None`` when the period carries none or
        carries something unreadable as one.
    """
    raw = period.get("effective_from")
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    return _parse_iso(raw)


# ── Public introspection ────────────────────────────────────────────────


def supported_jurisdictions() -> list[str]:
    """Return the sorted list of ISO-3166 alpha-2 codes with a rule loaded."""
    table = _load_table()
    return sorted((table.get("jurisdictions") or {}).keys())


def jurisdiction_metadata(jurisdiction: str) -> dict[str, Any]:
    """Return the raw rate block for a jurisdiction (for UI rendering)."""
    return dict(_table_for(jurisdiction))


# ── VAT / GST ───────────────────────────────────────────────────────────


def _has_vat_block(jur_table: dict[str, Any]) -> bool:
    """Whether the table models any VAT or GST at all for this jurisdiction.

    The question :func:`_resolve_vat_entry` cannot answer: it returns None both
    for a jurisdiction with no block and for a class missing from one that has
    it, and those are different events with different right answers.
    """
    return bool(jur_table.get("vat") or jur_table.get("gst"))


def vat_absence(jurisdiction: str) -> str:
    """Where *jurisdiction*'s missing VAT block lives: in the law, or in us.

    Returns :data:`VAT_ABSENT_BY_LAW` or :data:`VAT_ABSENT_NOT_MODELLED`. The
    two are not interchangeable and the difference is the whole reason this
    exists: for a jurisdiction that levies no VAT, zero is the right figure and
    a caller may add it to a total; for one we have not modelled, there is no
    figure at all and a total carrying zero understates it. Both used to come
    back as the same zero from :func:`compute_vat`.

    Callers should branch on the returned token against the two constants
    rather than on its text, for the same reason
    :class:`app.core.provenance.Provenance` says to branch on ``source`` and
    never on ``used``.

    Raises:
        UnsupportedJurisdictionError: no such jurisdiction in the table.
        TaxEngineError: the jurisdiction *has* a VAT or GST block, so the
            question does not apply to it. Raising rather than returning a
            third token keeps the return type at the two values a caller can
            actually act on, and a caller asking this about a jurisdiction
            with a block has made a mistake worth hearing about.
    """
    jur = _table_for(jurisdiction)
    if _has_vat_block(jur):
        raise TaxEngineError(
            f"jurisdiction '{jurisdiction}' has a VAT/GST block, so its VAT is not absent; "
            f"ask {_has_vat_block.__name__} before asking why a block is missing"
        )
    # The loader has already refused a blockless jurisdiction that declares
    # nothing or declares something outside the permitted pair, so anything
    # reaching here is one of the two.
    return str(jur[VAT_ABSENCE_KEY])


def _provenance_for_absent_vat(jurisdiction: str) -> Provenance:
    """Why a quote for *jurisdiction* carries no VAT figure of its own.

    Only ever called from a handler for :class:`NoVatBlockError`, which has a
    single raise site guarded by ``not _has_vat_block(...)``. That guard is what
    makes :func:`vat_absence` safe here: it refuses a jurisdiction that has a
    block, and by construction this one does not.

    The two halves are different kinds of thing rather than two shades of one.

    A jurisdiction that levies no VAT has been answered. Not by a row of its
    own, so it is not ``DECLARED``, but the generic rule applied and the answer
    it produced is correct: zero, addable to a total, nothing missing. That is
    what :class:`~app.core.provenance.Source.FALLBACK` means, and the docstring
    saying a fallback is an answer that may well be right is the sentence this
    leans on.

    A jurisdiction we have not modelled has not been answered at all. Brazil
    levies indirect taxes this table does not carry on the VAT path, so the
    zero in the field is a placeholder rather than a figure, and a caller adding
    it to a total understates that total. Nothing stood in, so there is no token
    to name, and the type refuses to let one be given.
    """
    absence = vat_absence(jurisdiction)
    if absence == VAT_ABSENT_BY_LAW:
        return fell_back(VAT_AXIS, jurisdiction, VAT_STANDIN_NO_VAT_IN_LAW)
    if absence == VAT_ABSENT_NOT_MODELLED:
        return unavailable(VAT_AXIS, jurisdiction)
    # Not reachable through the loader, which refuses any third value at load
    # time. It is spelled out anyway because the alternative is an ``else`` that
    # hands a future third marker the unavailable branch by default, and the
    # cost of that is a wrong provenance on a real quote rather than an error.
    # This function and VAT_ABSENCE_VALUES have to move together; raising is
    # what makes forgetting audible rather than leaving a comment asking.
    raise TaxEngineError(f"no provenance rule for vat_absence {absence!r} on '{jurisdiction}'")


def _vat_entry_or_raise(jur_table: dict[str, Any], jurisdiction: str, rate_class: str) -> Any:
    """The rate-class entry for ``rate_class``, or the error that says why there isn't one."""
    entry = _resolve_vat_entry(jur_table, rate_class)
    if entry is not None:
        return entry
    if not _has_vat_block(jur_table):
        raise NoVatBlockError(jurisdiction)
    raise UnknownRateClassError(f"Jurisdiction '{jurisdiction}' has no VAT/GST rate class '{rate_class}'")


def _resolve_vat_entry(jur_table: dict[str, Any], rate_class: str) -> Any | None:
    """Return the raw VAT/GST entry for ``rate_class``.

    Looks in ``vat.*`` first then ``gst.*`` so IN/SG/AU work the same
    way as DACH. Returns ``None`` if the class is not defined.

    The entry comes back exactly as the table wrote it, un-normalised, because
    :func:`_rate_periods` owns the shapes it may be written in. Normalising in
    two places is how the two come to disagree about a third shape.
    """
    for key in ("vat", "gst"):
        block = jur_table.get(key) or {}
        if rate_class in block:
            return block[rate_class]
    return None


def _rate_periods(entry: Any) -> list[dict[str, Any]]:
    """Return a rate class's periods, one mapping each, in the order written.

    Three shapes reach here, and this is the only place that knows there are
    three:

    * a single mapping, ``{ rate: 0.05 }`` - one period, in force for every
      date unless it carries ``effective_from``, and a one-period history
      when it does. AE ``standard``, GB ``zero`` and AU ``standard`` are the
      dated form; IN ``commercial`` is the undated one;
    * a list of mappings written oldest first, each with its own ``rate`` and
      ``effective_from`` - a history. GB, DE, CH, RU and SA carry one;
    * a bare scalar, rare shorthand for ``{ rate: <scalar> }``.

    A class with no ``rate`` key at all - ``AE.vat.exempt`` is one, carrying
    only ``applies_to`` - comes back with none, so the zero the callers default
    to is unchanged by any of this.

    Args:
        entry: whatever the table holds under the rate class.

    Returns:
        The periods, unsorted. Ordering is the table's business and is checked
        at load by :func:`_validate_rate_histories`.
    """
    if isinstance(entry, list):
        return [dict(period) if isinstance(period, Mapping) else {"rate": period} for period in entry]
    if isinstance(entry, Mapping):
        return [dict(entry)]
    # Allow scalar shorthand (rare).
    return [{"rate": entry}]


def _period_in_force(
    periods: list[dict[str, Any]],
    jurisdiction: str,
    rate_class: str,
    effective_on: date | None,
) -> dict[str, Any]:
    """Pick the rate period that governs ``effective_on``.

    The rule, and it is the whole of it: the period with the greatest
    ``effective_from`` that is not after the date asked about. A period with no
    ``effective_from`` is in force for every date, which is what keeps the
    single-mapping shape answering exactly as it did before histories existed.

    A date earlier than every dated period raises
    :class:`RateNotInForceError` naming the earliest date the class can speak
    for. "We do not have a rate that old" stays an honest refusal; stretching
    the oldest period we do hold backwards over it would be an invention with
    a plausible number attached.

    ``effective_on=None`` means current rates and so takes the newest period.
    Note what that implies for a rate change legislated in advance: a
    future-dated period becomes the answer for every caller passing no date,
    because this module never reads the wall clock and so cannot tell a
    future period from a current one. Add one only once callers pass dates.

    Selection is by maximum rather than by position, so a mis-ordered history
    cannot produce a wrong number here. That is deliberate, and it is why the
    oldest-first convention is enforced at load instead of relied on at
    resolution.

    Args:
        periods: the class's periods, from :func:`_rate_periods`.
        jurisdiction: ISO-3166 alpha-2 code, for the error message.
        rate_class: the class asked about, for the error message.
        effective_on: the date to resolve, or ``None`` for current rates.

    Returns:
        The governing period.

    Raises:
        RateNotInForceError: ``effective_on`` precedes every dated period.
    """
    dated = [(period, _period_date(period)) for period in periods]
    if effective_on is None:
        return max(dated, key=lambda pair: pair[1] or date.min)[0]
    in_force = [(period, start) for period, start in dated if start is None or start <= effective_on]
    if not in_force:
        earliest = min(start for _, start in dated if start is not None)
        raise RateNotInForceError(jurisdiction, rate_class, effective_on, earliest)
    return max(in_force, key=lambda pair: pair[1] or date.min)[0]


def _vat_rate_and_start(
    jur_table: dict[str, Any],
    jurisdiction: str,
    rate_class: str,
    effective_on: date | None,
) -> tuple[Decimal, date | None]:
    """The VAT/GST rate to apply, and the date the table dates it from.

    One resolution answers both, because the second is a description of the
    first. A caller told that its rate has been in force since 2011-01-04 is
    being told something about the very period that produced the figure beside
    it, and resolving twice is two chances to describe a different one.

    Args:
        jur_table: the jurisdiction's block.
        jurisdiction: ISO-3166 alpha-2 code.
        rate_class: ``standard`` | ``reduced`` | ``zero_rated`` etc.
        effective_on: the contract's date, or ``None`` for current rates.

    Returns:
        The rate as a fraction - zero when the class defines no ``rate`` key -
        and the ``effective_from`` of the period it came from. That date is
        ``None`` whenever the period carries none, which is now the rare
        case rather than the common one: every VAT and GST class in the table
        is dated except IN ``commercial`` and IN ``ready_to_move``, both of
        which are undated as a decision rather than as an omission.

    Raises:
        NoVatBlockError: the table holds no VAT or GST block here.
        UnknownRateClassError: the block holds no such class.
        RateNotInForceError: the date precedes every period of that class.
    """
    entry = _vat_entry_or_raise(jur_table, jurisdiction, rate_class)
    period = _period_in_force(_rate_periods(entry), jurisdiction, rate_class, effective_on)
    return _D(period.get("rate", 0)), _period_date(period)


def _vat_amount(net: Any, rate: Decimal) -> Decimal:
    """The VAT on ``net`` at ``rate``, rounded HALF_UP to 2 dp.

    Shared by :func:`compute_vat` and by the quote, which resolves its own rate
    so that it can also say where that rate came from. One arithmetic rule in
    one place is what keeps the quote from becoming a second way of arriving at
    a figure the public function already owns.
    """
    return _money(_D(net) * rate)


def _vat_rate_in_force(
    jur_table: dict[str, Any],
    jurisdiction: str,
    rate_class: str,
    effective_on: date | None,
) -> Decimal:
    """The rate alone, for the callers that price without explaining themselves.

    One function for what used to be the same six lines in :func:`compute_vat`
    and in :func:`net_from_gross`. Two copies of a date rule are two places to
    extend it, and the history support this now carries would have had to land
    in both of them to keep an inclusive price and an exclusive one agreeing
    about the same contract.

    See :func:`_vat_rate_and_start` for the resolution itself; this drops the
    date rather than resolving anything of its own.

    Args:
        jur_table: the jurisdiction's block.
        jurisdiction: ISO-3166 alpha-2 code.
        rate_class: ``standard`` | ``reduced`` | ``zero_rated`` etc.
        effective_on: the contract's date, or ``None`` for current rates.

    Returns:
        The rate as a fraction; zero when the class defines no ``rate`` key.

    Raises:
        NoVatBlockError: the table holds no VAT or GST block here.
        UnknownRateClassError: the block holds no such class.
        RateNotInForceError: the date precedes every period of that class.
    """
    return _vat_rate_and_start(jur_table, jurisdiction, rate_class, effective_on)[0]


def compute_vat(
    net: Any,
    jurisdiction: str,
    *,
    rate_class: str = "standard",
    effective_on: date | None = None,
) -> Decimal:
    """Return the VAT amount (not the gross) for ``net``.

    Args:
        net: pre-VAT amount in the contract currency.
        jurisdiction: ISO-3166 alpha-2 code.
        rate_class: ``standard`` | ``reduced`` | ``zero`` | ``zero_rated``
            | ``exempt`` | ``first_home`` etc. Class must exist in the
            jurisdiction's VAT block.
        effective_on: optional signing date. The class's band in force that
            day is used, which for a class carrying a history may be an older
            rate than today's. A date before the earliest band that class
            holds has no rate at all and raises. When None, current rates
            apply.

    Returns:
        VAT amount rounded HALF_UP to 2 dp. Zero-rated / exempt
        classes return Decimal("0.00"), and that zero is a statement about
        the supply rather than a stand-in for a rate this function could
        not find; a date with no rate in force raises instead.

    Raises:
        UnsupportedJurisdictionError: jurisdiction not in table.
        UnknownRateClassError: ``rate_class`` not defined for this jurisdiction.
        RateNotInForceError: ``effective_on`` precedes the earliest band the
            table carries for this class, so no rate applies to that date.
    """
    jur = _table_for(jurisdiction)
    rate = _vat_rate_in_force(jur, jurisdiction, rate_class, effective_on)
    return _vat_amount(net, rate)


def gross_from_net(
    net: Any,
    jurisdiction: str,
    *,
    rate_class: str = "standard",
    effective_on: date | None = None,
) -> Decimal:
    """Return ``net + compute_vat(net)`` rounded to 2 dp."""
    net_d = _D(net)
    vat = compute_vat(net_d, jurisdiction, rate_class=rate_class, effective_on=effective_on)
    return _money(net_d + vat)


def net_from_gross(
    gross: Any,
    jurisdiction: str,
    *,
    rate_class: str = "standard",
    effective_on: date | None = None,
) -> Decimal:
    """Return ``net`` such that ``net * (1 + rate) == gross`` (rounded).

    Useful when the buyer is quoted an inclusive price and the finance
    module needs to split it into a recognised-revenue + tax-payable
    pair on the ledger.

    Raises:
        RateNotInForceError: ``effective_on`` precedes the earliest band the
            table carries for this class. Returning ``gross`` unchanged would
            assert that no VAT was baked into the inclusive price, which is a
            claim about the supply this function has no grounds to make.
    """
    jur = _table_for(jurisdiction)
    rate = _vat_rate_in_force(jur, jurisdiction, rate_class, effective_on)
    if rate == _ZERO:
        return _money(_D(gross))
    divisor = Decimal("1") + rate
    net = _D(gross) / divisor
    return _money(net)


# ── Stamp duty / transfer tax (progressive + flat) ──────────────────────


def _progressive_band_amount(price: Decimal, bands: Iterable[Mapping[str, Any]]) -> Decimal:
    """Apply marginal-band progressive rates and return total tax.

    Each band: ``{up_to: <inclusive ceiling>, rate: <0..1 fraction>}``.
    Final band uses ``up_to: null`` for the open-ended top tier.
    """
    total = _ZERO
    previous_ceiling = _ZERO
    for band in bands:
        rate = _D(band.get("rate", 0))
        ceiling_raw = band.get("up_to")
        if ceiling_raw is None:
            # Top open band - apply to remainder.
            if price > previous_ceiling:
                taxable = price - previous_ceiling
                total += taxable * rate
            break
        ceiling = _D(ceiling_raw)
        if price <= previous_ceiling:
            break
        slice_top = min(price, ceiling)
        taxable = slice_top - previous_ceiling
        if taxable > _ZERO:
            total += taxable * rate
        previous_ceiling = ceiling
        if price <= ceiling:
            break
    return _money(total)


def _bands_and_start(
    container: Mapping[str, Any],
    jurisdiction: str,
    rate_class: str,
    effective_on: date | None,
) -> tuple[list[dict[str, Any]], date | None]:
    """The progressive band table to apply, and the date it has been in force since.

    Mirrors :func:`_vat_rate_and_start` one axis over: that function resolves
    a *rate* against a dated history of periods; this resolves a *band
    table* - itself a slice of price - against the same kind of dated
    history of periods. ``container["band_history"]``, when present, is that
    history: the oldest-first list of ``{effective_from, bands}`` periods
    :func:`_rate_periods` and :func:`_period_in_force` already know how to
    read, because neither ever looks at anything in a period but
    ``effective_from``. Its absence means ``container["bands"]`` has never
    been dated, and that is reported as ``None`` rather than silently
    handing today's bands out under a caller's real date - the same thing
    ``None`` already means for ``vat_rate_effective_from``.

    ``band_history`` deliberately does not live in the same key as
    ``bands``: that key already means a plain list of *price* bands, and
    :func:`_validate_band_histories` scans for the literal key
    ``band_history`` rather than "a list of mappings" for the same reason
    :func:`_validate_rate_histories` stays off this block entirely - a
    reader must not have to guess which axis a given list is on.

    Args:
        container: the mapping holding ``bands`` and optionally
            ``band_history`` - e.g. ``jur["stamp_duty"]``, its
            ``first_home_relief`` block, one ``by_state`` entry, or the
            ``bsd`` / ``itbi`` blocks.
        jurisdiction: ISO-3166 alpha-2 code, for the error message.
        rate_class: a label for the error message, e.g. ``"stamp_duty"`` or
            ``"stamp_duty.NSW"``.
        effective_on: the contract's date, or ``None`` for current rates.

    Returns:
        The band list to apply to the price, and the ``effective_from`` of
        the period it came from - ``None`` when this slot carries no
        ``band_history`` at all.

    Raises:
        RateNotInForceError: ``effective_on`` precedes every dated period.
    """
    history = container.get("band_history")
    if not history:
        return list(container.get("bands") or []), None
    period = _period_in_force(_rate_periods(history), jurisdiction, rate_class, effective_on)
    return list(period.get("bands") or []), _period_date(period)


def _stamp_duty_amount_and_start(
    price_d: Decimal,
    jur: Mapping[str, Any],
    jurisdiction: str,
    *,
    region_subcode: str | None,
    is_first_home: bool,
    is_additional_property: bool,
    effective_on: date | None,
) -> tuple[Decimal, date | None]:
    """Stamp duty / land-transfer tax, and the date its band table is dated from.

    Holds every path :func:`compute_stamp_duty` documents; that function is
    now the thin public wrapper returning this call's first element, the
    same relationship :func:`_vat_rate_in_force` has to
    :func:`_vat_rate_and_start`. :func:`compute_total_taxes_for_contract`
    calls this directly so it can report both the amount and
    ``stamp_duty_effective_from`` from one resolution, rather than resolving
    the band table twice and risking the two disagreeing about which one the
    money came from.

    ``effective_on`` only has anywhere to matter today for the tables that
    carry a ``band_history``: GB's main bands and first-home relief, SG's
    BSD, BR's ITBI and AU's by-state bands. Every other path (DE, IN, RU, SA,
    CH, US) has no dated history of its own yet and answers ``None``
    regardless of what date is asked for - the same undated state VAT's
    ``gst`` blocks are in, and not a defect this function introduces.
    """
    sd = jur.get("stamp_duty") or {}
    by_state = sd.get("by_state") if isinstance(sd, Mapping) else None

    # Path A - GB-style progressive bands at the top level. Gated on either
    # ``bands`` or ``band_history`` being present, not on ``bands`` alone -
    # a jurisdiction whose bands have been fully dated may carry only the
    # latter, and that must still route here rather than falling through to
    # "no applicable rule".
    has_bands = isinstance(sd, Mapping) and bool(sd.get("bands") or sd.get("band_history"))
    if has_bands and not by_state:
        if is_first_home and sd.get("first_home_relief"):
            relief = sd["first_home_relief"]
            # max_price is read here, not from inside a period: it is the
            # relief's price cap, not one of its bands, and _bands_and_start
            # only ever resolves the "bands" key of whatever it is handed. If
            # first_home_relief ever gains a band_history, this cap stays
            # single-valued across every period in it - GB's real cap moved
            # on the same days its bands did, so dating the bands without
            # also dating the cap would price purchases in the gap on the
            # wrong side of a boundary that no longer applied. Open question
            # for whoever dates this table: does a period carry its own
            # max_price, or does the cap get a separate dated slot?
            max_price = relief.get("max_price")
            if max_price is not None and price_d > _D(max_price):
                # Above the relief cap → fall back to standard bands.
                active_bands, start = _bands_and_start(sd, jurisdiction, "stamp_duty", effective_on)
            else:
                active_bands, start = _bands_and_start(
                    relief, jurisdiction, "stamp_duty.first_home_relief", effective_on
                )
        else:
            active_bands, start = _bands_and_start(sd, jurisdiction, "stamp_duty", effective_on)
        duty = _progressive_band_amount(price_d, active_bands)
        if is_additional_property:
            surcharge_pct = _D(sd.get("additional_property_surcharge", 0))
            duty = _money(duty + (price_d * surcharge_pct))
        return duty, start

    # Path B - by-state flat / banded (DE, IN, AU, US, CH).
    if by_state:
        if not region_subcode:
            raise MissingRegionSubcodeError(jurisdiction, by_state.keys())
        sub = region_subcode.upper()
        if sub not in by_state:
            raise MissingRegionSubcodeError(jurisdiction, by_state.keys())
        entry = by_state[sub]
        start: date | None = None
        if isinstance(entry, Mapping) and ("bands" in entry or "band_history" in entry):
            active_bands, start = _bands_and_start(entry, jurisdiction, f"stamp_duty.{sub}", effective_on)
            duty = _progressive_band_amount(price_d, active_bands)
        else:
            rate = _D(entry)
            duty = _money(price_d * rate)
        if is_additional_property:
            surcharge_pct = _D(sd.get("additional_property_surcharge", 0))
            duty = _money(duty + (price_d * surcharge_pct))
        return duty, start

    # Path C - alternate top-level keys (DE Grunderwerbsteuer is its own block).
    grunder = jur.get("grunderwerbsteuer")
    if isinstance(grunder, Mapping):
        states = grunder.get("by_state")
        if states:
            if not region_subcode:
                raise MissingRegionSubcodeError(jurisdiction, states.keys())
            sub = region_subcode.upper()
            if sub not in states:
                raise MissingRegionSubcodeError(jurisdiction, states.keys())
            rate = _D(states[sub])
            return _money(price_d * rate), None
        if "flat" in grunder:
            return _money(price_d * _D(grunder["flat"])), None

    # Path D - SG bands under ``bsd``.
    bsd = jur.get("bsd")
    if isinstance(bsd, Mapping) and (bsd.get("bands") or bsd.get("band_history")):
        active_bands, start = _bands_and_start(bsd, jurisdiction, "bsd", effective_on)
        return _progressive_band_amount(price_d, active_bands), start

    # Path E - IN ``stamp_duty.by_state`` already handled above; ITBI (BR).
    itbi = jur.get("itbi")
    if isinstance(itbi, Mapping) and (itbi.get("bands") or itbi.get("band_history")):
        active_bands, start = _bands_and_start(itbi, jurisdiction, "itbi", effective_on)
        return _progressive_band_amount(price_d, active_bands), start

    # Path F - RU has no stamp duty (uses state_duty flat fee instead).
    if "state_duty" in jur:
        return _money(_D(jur["state_duty"])), None

    # Path G - SA RETT.
    if "rett" in jur:
        return _money(price_d * _D(jur["rett"])), None

    # Path H - CH transfer_tax by Kanton.
    transfer = jur.get("transfer_tax")
    if isinstance(transfer, Mapping) and transfer.get("by_state"):
        states = transfer["by_state"]
        if not region_subcode:
            raise MissingRegionSubcodeError(jurisdiction, states.keys())
        sub = region_subcode.upper()
        if sub not in states:
            raise MissingRegionSubcodeError(jurisdiction, states.keys())
        return _money(price_d * _D(states[sub])), None

    # Path I - US ``state_transfer_tax`` already handled above.
    stt = jur.get("state_transfer_tax")
    if isinstance(stt, Mapping) and stt.get("by_state"):
        states = stt["by_state"]
        if not region_subcode:
            raise MissingRegionSubcodeError(jurisdiction, states.keys())
        sub = region_subcode.upper()
        if sub not in states:
            raise MissingRegionSubcodeError(jurisdiction, states.keys())
        return _money(price_d * _D(states[sub])), None

    # No applicable rule - return zero (a few jurisdictions have no
    # stamp duty by design; this is not an error).
    return _money(_ZERO), None


def compute_stamp_duty(
    price: Any,
    jurisdiction: str,
    *,
    region_subcode: str | None = None,
    is_first_home: bool = False,
    is_additional_property: bool = False,
    effective_on: date | None = None,
) -> Decimal:
    """Compute stamp duty / land-transfer tax for a property purchase.

    Args:
        price: full purchase price in the contract currency.
        jurisdiction: ISO-3166 alpha-2 code.
        region_subcode: REQUIRED for DE (Grunderwerbsteuer by state),
            IN (state stamp duty), AU (state stamp duty), US (state
            transfer tax), CH (Kanton transfer). IGNORED for GB / SG.
        is_first_home: UK first-time-buyer relief flag.
        is_additional_property: UK 3 % surcharge flag (also applies to
            ABSD-style flows elsewhere).
        effective_on: optional signing date. Only the slots that carry a
            ``band_history`` (GB's bands and first-home relief, SG's BSD,
            BR's ITBI, AU's by-state bands) can answer a date at all today;
            every other path answers alike whatever is asked, because it has
            never been dated. When None, current rates apply.

    Returns:
        Stamp-duty amount rounded HALF_UP to 2 dp.

    Raises:
        UnsupportedJurisdictionError: jurisdiction not in table.
        MissingRegionSubcodeError: jurisdiction needs subcode + none given.
        RateNotInForceError: ``effective_on`` precedes the earliest band a
            dated slot carries.
    """
    jur = _table_for(jurisdiction)
    price_d = _D(price)
    duty, _start = _stamp_duty_amount_and_start(
        price_d,
        jur,
        jurisdiction,
        region_subcode=region_subcode,
        is_first_home=is_first_home,
        is_additional_property=is_additional_property,
        effective_on=effective_on,
    )
    return duty


def compute_absd(
    price: Any,
    jurisdiction: str,
    *,
    buyer_profile: str,
) -> Decimal:
    """Return Singapore-style Additional Buyer's Stamp Duty (ABSD).

    Args:
        price: full purchase price in SGD.
        jurisdiction: must be ``"SG"`` (or any jurisdiction defining
            an ``absd`` block).
        buyer_profile: one of ``sc_first``, ``sc_second``, ``spr_first``,
            ``spr_second``, ``foreigner``, ``entity``.

    Returns:
        ABSD amount rounded HALF_UP to 2 dp. Zero when profile is
        ``sc_first`` (Singapore Citizen first home).

    Raises:
        UnsupportedJurisdictionError, UnknownRateClassError.
    """
    jur = _table_for(jurisdiction)
    absd = jur.get("absd")
    if not isinstance(absd, Mapping):
        raise UnknownRateClassError(f"Jurisdiction '{jurisdiction}' has no ABSD table")
    if buyer_profile not in absd:
        raise UnknownRateClassError(
            f"Unknown ABSD buyer profile '{buyer_profile}' for '{jurisdiction}'. Supported: {sorted(absd.keys())}"
        )
    rate = _D(absd[buyer_profile])
    return _money(_D(price) * rate)


# ── Transfer fee (UAE DLD style) ────────────────────────────────────────


def compute_transfer_fee(
    price: Any,
    jurisdiction: str,
    *,
    emirate: str | None = None,
) -> Decimal:
    """Return DLD-style flat transfer fee.

    Args:
        price: full purchase price in the contract currency.
        jurisdiction: ISO-3166 alpha-2 code (typically AE).
        emirate: ``dubai`` | ``abu_dhabi`` | ``sharjah`` | ``ajman``.
            REQUIRED when the jurisdiction's transfer_fee block is a
            mapping; ignored for jurisdictions without subkeys.

    Returns:
        Transfer fee amount, 2-dp HALF_UP rounded.
    """
    jur = _table_for(jurisdiction)
    block = jur.get("transfer_fee")
    if block is None:
        return _money(_ZERO)
    if isinstance(block, (int, float, str, Decimal)):
        return _money(_D(price) * _D(block))
    if not isinstance(block, Mapping):
        return _money(_ZERO)
    if emirate is None:
        # Caller didn't specify, so this falls back to whichever subcode sorts
        # first. That is the whole rule: it is not the most-used emirate, and
        # nothing here knows which one that would be. The sort only buys a
        # repeatable answer, not a defensible one.
        #
        # Note the asymmetry with the check below, which is the risk worth
        # seeing before changing anything here. A subcode we do not recognise
        # raises; a subcode nobody supplied is filled in silently and prices
        # the contract at some other emirate's rate. Transfer rates differ
        # across the emirates, so on a property deal that gap is money rather
        # than rounding, and the caller gets no signal that a default was used.
        # Making this raise as well would be symmetric, but it changes the
        # behaviour of a shipped API and is a decision rather than a fix.
        keys = sorted(block.keys())
        if not keys:
            return _money(_ZERO)
        emirate = keys[0]
    key = emirate.lower().replace("-", "_")
    if key not in block:
        raise MissingRegionSubcodeError(jurisdiction, block.keys())
    rate = _D(block[key])
    return _money(_D(price) * rate)


# ── Registration / notary fees ──────────────────────────────────────────


def compute_registration_fee(price: Any, jurisdiction: str) -> Decimal:
    """Return the flat-% registration fee (IN, BR, AT).

    Returns Decimal("0.00") if the jurisdiction has no registration
    fee defined.
    """
    jur = _table_for(jurisdiction)
    rate = jur.get("registration_fee") or jur.get("land_registry_fee") or jur.get("notary_fee_pct") or 0
    return _money(_D(price) * _D(rate))


# ── Late-payment interest ───────────────────────────────────────────────


def compute_late_interest(
    principal: Any,
    jurisdiction: str,
    *,
    days_overdue: int | None = None,
    due_date: date | None = None,
    paid_date: date | None = None,
) -> Decimal:
    """Return accrued late-payment interest on an overdue instalment.

    Calling pattern (mutually exclusive):
        * pass ``days_overdue`` directly, or
        * pass ``due_date`` + ``paid_date`` (or leave ``paid_date``
          None to use ``date.today()``).

    Compounding modes (per jurisdiction):
        * ``simple``  - ``principal * annual_rate * days / 365``.
        * ``daily``   - full daily compounding (rare).

    Negative ``days_overdue`` → Decimal("0.00") (paid early - no charge).
    """
    if days_overdue is None:
        if due_date is None:
            raise TaxEngineError("compute_late_interest needs either days_overdue or due_date")
        end = paid_date or date.today()
        days_overdue = (end - due_date).days
    if days_overdue <= 0:
        return _money(_ZERO)
    jur = _table_for(jurisdiction)
    block = jur.get("late_interest") or {}
    rate = _D(block.get("annual", 0))
    if rate == _ZERO:
        return _money(_ZERO)
    compounding = (block.get("compounding") or "simple").lower()
    days_d = Decimal(str(days_overdue))
    principal_d = _D(principal)
    if compounding == "daily":
        # (1 + daily_rate)^days - 1, daily_rate = annual / 365.
        daily_rate = rate / Decimal("365")
        factor = (Decimal("1") + daily_rate) ** int(days_overdue)
        interest = principal_d * (factor - Decimal("1"))
    else:
        # Simple interest.
        interest = principal_d * rate * days_d / Decimal("365")
    return _money(interest)


# ── High-level contract summariser ──────────────────────────────────────


def compute_total_taxes_for_contract(
    contract: Mapping[str, Any],
    jurisdiction: str,
    *,
    region_subcode: str | None = None,
    is_first_home: bool = False,
    is_additional_property: bool = False,
    vat_rate_class: str = "standard",
    effective_on: date | None = None,
    absd_buyer_profile: str | None = None,
    emirate: str | None = None,
    overdue_instalments: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """High-level helper - sum every applicable tax for a contract.

    ``contract`` is a plain mapping (so this helper works with
    Pydantic models, ORM dicts and raw JSON) and must carry:
        * ``net`` - the net price (pre-VAT) in the contract currency, or
        * ``total_value`` - the gross price (some flows already
          have VAT baked in); when only ``total_value`` is present we
          derive ``net`` via :func:`net_from_gross`.
        * ``currency`` - ISO-4217 code (informational only here).

    Returns a dict with these keys::

        {
          "jurisdiction": "GB",
          "vat_provenance": Provenance(axis="vat", ...),
          "vat_rate_effective_from": date(2011, 1, 4) | None,
          "region_subcode": None,
          "currency": "GBP",
          "net": Decimal(...),
          "vat": Decimal(...),
          "stamp_duty": Decimal(...),
          "stamp_duty_effective_from": date(2025, 4, 1) | None,
          "transfer_fee": Decimal(...),
          "registration_fee": Decimal(...),
          "absd": Decimal(...),         # only when relevant
          "late_interest": Decimal(...),
          "subtotal_taxes": Decimal(...),
          "grand_total": Decimal(...),
          "breakdown": [
            {"line": "Net price", "amount": ...},
            {"line": "VAT (standard 20%)", "amount": ...},
            ...
          ]
        }

    ``vat_provenance`` is how the ``vat`` figure was arrived at, and it exists
    because the figure alone cannot say. A zero-rated supply, a jurisdiction
    that levies no VAT and a jurisdiction this table does not model all put the
    same bytes in ``vat``, and only the first two are safe to add to a total.
    Branch on its ``source``, never on the text of ``used``; see
    :mod:`app.core.provenance`.

    ``vat_rate_effective_from`` is the date the table dates the applied rate
    from, and ``None`` says the table dates it from nothing at all. It is a
    statement about the rate rather than about the quote: it is filled in the
    same way whether or not ``effective_on`` was given, because a rate that has
    run since 2011-01-04 has run since 2011-01-04 no matter what was asked.

    The ``None`` is what this field exists for. Which classes carry dated
    histories is written in ``data/tax_rates.yaml`` and every other one answers
    any date at all with its one undated rate - IN ``commercial`` prices a 1900
    contract at 12 % without the table ever having said that 12 % applied in
    1900. That is a number nobody promised, and before this field a caller
    could not tell it from one that was.

    Two situations share the ``None`` and are told apart by ``vat_provenance``
    rather than here: a class the table never dated (``source`` DECLARED, as
    above) and a jurisdiction with no VAT rate at all (US, BR - FALLBACK or
    UNAVAILABLE). A caller that needs the difference must read both fields,
    which is a join, and it is called out here rather than papered over.

    Note that ``grand_total`` adds ``vat`` whatever the provenance says, so for
    an unmodelled jurisdiction it is a total with a placeholder in it. That is
    unchanged from before this field existed, and the field is what makes it
    visible rather than what causes it.

    ``stamp_duty_effective_from`` is the same statement, one axis over, for
    the ``stamp_duty`` figure: the date the band table it was priced from has
    been in force since, or ``None`` when that table has never been dated at
    all. Today that is every jurisdiction's - GB, SG, BR and AU's five states
    included - because none carries a ``band_history`` yet; adding one to a
    table's ``band_history`` key is the only thing that ever turns this from
    ``None`` into a date, the same way ``vat_rate_effective_from`` only turns
    into one for a VAT/GST class that carries a rate history. There is no
    ``stamp_duty_provenance`` alongside it: unlike VAT, every stamp-duty path
    that answers at all answers on its own terms or as a design zero (a few
    jurisdictions genuinely levy none), so the declared/fallback/unavailable
    question ``vat_provenance`` exists to answer does not arise here yet.

    Raises:
        UnsupportedJurisdictionError, MissingRegionSubcodeError,
        UnknownRateClassError.
        RateNotInForceError: the contract's date precedes the earliest band
            the table holds for its rate class, or - the stamp-duty sibling of
            the same refusal - the earliest band a dated ``band_history``
            holds. Nothing here catches it: the one ``try`` below covers
            :class:`NoVatBlockError` alone, so this propagates from the
            :func:`net_from_gross` call in step 1, the rate resolution in
            step 2, and the stamp-duty resolution in step 3, to whatever maps
            it for the caller.
    """
    jur = _table_for(jurisdiction)
    # Normalised once, and used both for the provenance below and for the
    # ``jurisdiction`` field of the returned quote, so the two cannot come to
    # disagree about what was asked for.
    quoted_jurisdiction = (jurisdiction or "").strip().upper()

    # ── 1. Net price ─────────────────────────────────────────────
    if "net" in contract and contract["net"] not in (None, "", 0, "0"):
        net = _D(contract["net"])
    elif "total_value" in contract and contract["total_value"] not in (None, "", 0, "0"):
        # Treat total_value as gross only when no net field is present.
        try:
            net = net_from_gross(
                contract["total_value"],
                jurisdiction,
                rate_class=vat_rate_class,
                effective_on=effective_on,
            )
        except NoVatBlockError:
            # Nothing was baked into the inclusive price, because the table
            # models no VAT here, so the gross is the net. The sibling of the
            # catch in step 2, and the pair is the point: the same contract must
            # get the same answer whichever price field carries it.
            net = _money(_D(contract["total_value"]))
    else:
        net = _ZERO

    # ── 2. VAT / GST ────────────────────────────────────────────
    try:
        # Resolved here rather than through compute_vat because the quote says
        # which period priced it, and a second resolution to fetch that date
        # could name a different period from the one the money came from. The
        # arithmetic is still compute_vat's, via the helper they share.
        vat_rate, vat_rate_effective_from = _vat_rate_and_start(
            jur,
            jurisdiction,
            vat_rate_class,
            effective_on,
        )
        vat = _vat_amount(net, vat_rate)
        vat_provenance = declared(VAT_AXIS, quoted_jurisdiction)
    except NoVatBlockError:
        # The table models no VAT here, so a quote is still the right answer and
        # no VAT is part of it. Caught at both call sites rather than this one
        # alone: catching it here only was what made the answer depend on
        # whether the contract carried ``net`` or ``total_value``.
        #
        # UnknownRateClassError is deliberately NOT caught. A caller naming a
        # class this jurisdiction does not define has asked a question the
        # engine cannot answer, and silently returning zero told them it could.
        vat = _money(_ZERO)
        # The zero above is the same bytes for both blockless jurisdictions and
        # for a genuinely zero-rated supply. This is the field that tells them
        # apart, and it is set HERE and nowhere else on purpose. The sibling
        # catch in step 1 fires as well on a ``total_value`` contract, so
        # setting it in both places would be two chances to disagree about one
        # contract; this call is unconditional, so one place covers every path.
        vat_provenance = _provenance_for_absent_vat(quoted_jurisdiction)
        # No rate applied, so there is no period and no date to report. The
        # field beside it carries which of the two absences this is.
        vat_rate_effective_from = None

    # ── 3. Stamp duty / transfer tax ────────────────────────────
    # Conventionally applied to the consideration (net headline
    # price), not to net+VAT. UK SDLT, DE Grunderwerbsteuer, AU
    # stamp duty, SG BSD - all assess on the purchase price.
    #
    # Resolved through _stamp_duty_amount_and_start rather than
    # compute_stamp_duty, for the same reason step 2 resolves through
    # _vat_rate_and_start rather than compute_vat: this quote also needs to
    # say which band table the money came from, and a second resolution to
    # fetch that date could name a different table from the one that
    # actually priced it.
    stamp_duty, stamp_duty_effective_from = _stamp_duty_amount_and_start(
        net,
        jur,
        jurisdiction,
        region_subcode=region_subcode,
        is_first_home=is_first_home,
        is_additional_property=is_additional_property,
        effective_on=effective_on,
    )

    # ── 4. Transfer fee (UAE DLD style) ─────────────────────────
    transfer_fee = (
        compute_transfer_fee(net, jurisdiction, emirate=emirate) if jur.get("transfer_fee") else _money(_ZERO)
    )

    # ── 5. Registration / notary fee ────────────────────────────
    registration_fee = compute_registration_fee(net, jurisdiction)

    # ── 6. ABSD (SG style) ──────────────────────────────────────
    absd = _money(_ZERO)
    if absd_buyer_profile and jur.get("absd"):
        absd = compute_absd(net, jurisdiction, buyer_profile=absd_buyer_profile)

    # ── 7. Late interest on overdue instalments ─────────────────
    late_interest = _money(_ZERO)
    overdue_lines: list[dict[str, Any]] = []
    if overdue_instalments:
        for item in overdue_instalments:
            principal = _D(item.get("amount", 0))
            days = item.get("days_overdue")
            due_dt: date | None = None
            paid_dt: date | None = None
            if days is None:
                due_dt = _parse_iso(item.get("due_date"))
                paid_dt = _parse_iso(item.get("paid_date"))
            this = compute_late_interest(
                principal,
                jurisdiction,
                days_overdue=days,
                due_date=due_dt,
                paid_date=paid_dt,
            )
            late_interest += this
            overdue_lines.append(
                {
                    "line": f"Late interest on instalment {item.get('sequence', '?')}",
                    "amount": this,
                }
            )

    # ── 8. Roll up ──────────────────────────────────────────────
    subtotal = stamp_duty + transfer_fee + registration_fee + absd
    grand_total = net + vat + subtotal + late_interest

    breakdown: list[dict[str, Any]] = [
        {"line": "Net price", "amount": _money(net)},
    ]
    if vat > _ZERO:
        breakdown.append({"line": f"VAT/GST ({vat_rate_class})", "amount": vat})
    if stamp_duty > _ZERO:
        breakdown.append({"line": "Stamp duty / transfer tax", "amount": stamp_duty})
    if transfer_fee > _ZERO:
        breakdown.append({"line": "Transfer fee", "amount": transfer_fee})
    if registration_fee > _ZERO:
        breakdown.append({"line": "Registration / notary fee", "amount": registration_fee})
    if absd > _ZERO:
        breakdown.append({"line": f"ABSD ({absd_buyer_profile})", "amount": absd})
    breakdown.extend(overdue_lines)

    return {
        "jurisdiction": quoted_jurisdiction,
        "vat_provenance": vat_provenance,
        "vat_rate_effective_from": vat_rate_effective_from,
        "region_subcode": (region_subcode or "").upper() or None,
        "currency": (contract.get("currency") or "").upper(),
        "net": _money(net),
        "vat": vat,
        "stamp_duty": stamp_duty,
        "stamp_duty_effective_from": stamp_duty_effective_from,
        "transfer_fee": transfer_fee,
        "registration_fee": registration_fee,
        "absd": absd,
        "late_interest": late_interest,
        "subtotal_taxes": _money(subtotal + late_interest),
        "grand_total": _money(grand_total),
        "breakdown": breakdown,
    }


__all__ = [
    "MissingRegionSubcodeError",
    "NoVatBlockError",
    "RateNotInForceError",
    "TaxEngineError",
    "UnknownRateClassError",
    "UnsupportedJurisdictionError",
    "VAT_ABSENCE_KEY",
    "VAT_ABSENCE_VALUES",
    "VAT_ABSENT_BY_LAW",
    "VAT_ABSENT_NOT_MODELLED",
    "VAT_AXIS",
    "VAT_STANDIN_NO_VAT_IN_LAW",
    "compute_absd",
    "compute_late_interest",
    "compute_registration_fee",
    "compute_stamp_duty",
    "compute_total_taxes_for_contract",
    "compute_transfer_fee",
    "compute_vat",
    "gross_from_net",
    "jurisdiction_metadata",
    "net_from_gross",
    "reload_tax_table",
    "supported_jurisdictions",
    "vat_absence",
]
