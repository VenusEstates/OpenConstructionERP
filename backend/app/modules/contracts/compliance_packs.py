# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Compliance rule packs for contract gates.

A *rule pack* is a jurisdiction-scoped bundle of validation rule-SET ids
(the same set names the core :class:`ValidationEngine` already knows -
``boq_quality``, ``din276``, ``gaeb``, ``nrm``, ``masterformat`` …). Each
pack also declares which workflow gates enforce it (currently only
``contract_signature``).

These packs are deterministic seed data, not user-authored DSL - they map a
project's region to a concrete, runnable set of validation rules so the
compliance gate that runs on a contract ``draft → active`` transition has
something real to execute. A project picks which packs it enforces via the
``Project.compliance_rule_packs`` JSON column; the gate resolves the union
of every pack's ``rule_sets`` and feeds them to the validation engine.

Design choices:
    * ``rule_sets`` reference rule sets that genuinely exist in the engine's
      registry. Unknown set names are simply skipped by the engine
      (``get_rules_for_sets`` ignores them), so a pack can declare an
      aspirational set without crashing - but the shipped packs only list
      sets we actually register, so the gate always evaluates real rules.
    * The ``universal`` pack is the safe default for any project with no
      region match - it enforces the cross-market ``boq_quality`` rule set.
    * Region → pack auto-mapping is a *suggestion*; projects can override.
"""

from __future__ import annotations

from typing import Any

# ── Workflow gate identifiers ──────────────────────────────────────────────

WORKFLOW_CONTRACT_SIGNATURE = "contract_signature"


# ── Pack registry ──────────────────────────────────────────────────────────
#
# ``rule_sets`` are the names the ValidationEngine resolves via
# ``rule_registry.get_rules_for_sets``. Keep every entry pointing at a set
# that is registered in app.core.validation.rules so the gate always runs
# real checks.

RULE_PACKS: dict[str, dict[str, Any]] = {
    "universal": {
        "id": "universal",
        "name": "Universal Compliance",
        "description": "Cross-market quality and completeness checks applied "
        "to the contract's schedule of values before signature.",
        "jurisdiction": None,
        "enforced_workflows": [WORKFLOW_CONTRACT_SIGNATURE],
        "rule_sets": ["boq_quality"],
    },
    "de_compliance": {
        "id": "de_compliance",
        "name": "Germany / DACH Compliance",
        "description": "DIN 276 cost-group structure and GAEB tender-format "
        "checks plus the universal quality baseline.",
        "jurisdiction": "DE",
        "enforced_workflows": [WORKFLOW_CONTRACT_SIGNATURE],
        "rule_sets": ["boq_quality", "din276", "gaeb"],
    },
    "uk_compliance": {
        "id": "uk_compliance",
        "name": "United Kingdom Compliance",
        "description": "NRM measurement-rule compliance plus the universal quality baseline.",
        "jurisdiction": "GB",
        "enforced_workflows": [WORKFLOW_CONTRACT_SIGNATURE],
        "rule_sets": ["boq_quality", "nrm"],
    },
    "us_compliance": {
        "id": "us_compliance",
        "name": "United States Compliance",
        "description": "MasterFormat classification checks plus the universal quality baseline.",
        "jurisdiction": "US",
        "enforced_workflows": [WORKFLOW_CONTRACT_SIGNATURE],
        "rule_sets": ["boq_quality", "masterformat"],
    },
    "mx_compliance": {
        "id": "mx_compliance",
        "name": "Mexico Compliance",
        "description": "APU unit-price completeness, IVA and CFDI invoicing, and "
        "subcontract retencion checks for LOPSRM public works plus the "
        "universal quality baseline.",
        "jurisdiction": "MX",
        "enforced_workflows": [WORKFLOW_CONTRACT_SIGNATURE],
        "rule_sets": ["boq_quality", "mexico"],
    },
}

#: Default pack every project falls back to when nothing else matches.
DEFAULT_PACK_ID = "universal"

# --------------------------------------------------------------------------- #
# Pack resolution: three keyspaces that cannot overlap.
#
# A project carries an ISO 3166-1 alpha-2 ``country_code`` (a controlled value)
# and a free-text ``region`` (whatever somebody typed). Resolution reads the
# ISO column first and treats the region label as a fallback only.
#
# This used to be one table of substrings matched against the region alone, and
# two of its entries were two letters long. ``de`` matched the Spanish and
# French preposition, so "Ciudad de Mexico" and "Ile-de-France" both enforced
# the German pack, and ``us`` matched inside "Russia", "Australia", "Belarus"
# and "Cyprus", all of which enforced the American one. The tables below cannot
# repeat that: nothing here is ever matched as a bare substring. ISO codes and
# legacy codes match a whole string, labels match a whole word or phrase, and
# no two-letter token appears in the label table at all - so the order the
# tables are consulted in does not decide any answer.
# --------------------------------------------------------------------------- #

#: ISO 3166-1 alpha-2 country code → pack id. Matched exactly, never as a
#: substring. Seeded from each pack's own ``jurisdiction`` so there is one
#: source of truth for which pack covers which country, then extended with the
#: countries a pack covers beyond the one it is named for.
#:
#: On the ambiguity of ``DE``, which is now halved rather than gone.
#: ``Project.country_code`` was NOT NULL with ``server_default='DE'`` until
#: revision ``v3319``, so it had three states that looked like two: a project
#: created through the API with no country chosen held 'DE' from the default,
#: a project created through the demo path held '' because an empty string
#: does not trigger a server default, and a project whose owner really did
#: choose Germany held 'DE' as well. Explicit Germany was indistinguishable
#: from never-chosen in the row. The note here used to end by saying that
#: fixing it needed a nullable column and was a migration rather than a
#: resolver change; that migration has since been written.
#:
#: What changed is only the future. A project created from ``v3319`` onwards
#: with no country holds NULL, which every reader in this file already treats
#: as unknown, so never-chosen and Germany are finally distinct. What did not
#: change is the past: rows written before that revision still hold 'DE', and
#: no signal in the data separates the deliberate ones from the defaulted
#: ones, so the migration deliberately rewrites nothing. We therefore still
#: resolve 'DE' at face value - demoting it would break every real German
#: project to protect the ones that never chose - and still treat '' as
#: unknown. Note that ``currency`` in the same model deliberately defaults to
#: '' with the comment "No EUR bias"; ``country_code`` got the same treatment
#: only for rows written from here on.
PACK_BY_COUNTRY: dict[str, str] = {
    **{str(pack["jurisdiction"]): pack_id for pack_id, pack in RULE_PACKS.items() if pack.get("jurisdiction")},
    # Austria and Switzerland run the DACH pack; it is not a German-only pack.
    "AT": "de_compliance",
    "CH": "de_compliance",
}

#: Short codes that are *not* ISO alpha-2, matched against a whole region
#: string only. "UK" is the everyday abbreviation for a country whose ISO code
#: is "GB", and this product's own region tags have always used it.
PACK_BY_LEGACY_CODE: dict[str, str] = {
    "uk": "uk_compliance",
}

#: Human region labels → pack id, matched as a whole word or a whole phrase
#: against the region text. Every key here is longer than two characters; the
#: test suite gates that, because a two-letter key is what caused the defect
#: this table replaced.
PACK_BY_LABEL: dict[str, str] = {
    "dach": "de_compliance",
    "germany": "de_compliance",
    "deutschland": "de_compliance",
    "austria": "de_compliance",
    "switzerland": "de_compliance",
    "united kingdom": "uk_compliance",
    "great britain": "uk_compliance",
    "britain": "uk_compliance",
    "england": "uk_compliance",
    "scotland": "uk_compliance",
    "wales": "uk_compliance",
    "united states": "us_compliance",
    "united states of america": "us_compliance",
    "america": "us_compliance",
    "usa": "us_compliance",
    "mexico": "mx_compliance",
    "méxico": "mx_compliance",
}


def _normalise_country_code(country_code: str | None) -> str | None:
    """Normalise an ISO country code, or ``None`` when none was given.

    The empty string is *not* a country: it is how the demo path spells "no
    country was chosen", because an empty string does not trigger the column's
    server default. Anything that is not two letters is rejected rather than
    guessed at.
    """
    if not country_code:
        return None
    code = country_code.strip().upper()
    if len(code) != 2 or not code.isalpha():
        return None
    return code


def _normalise_region(region: str) -> str:
    """Lower-case ``region`` and reduce it to space-separated word tokens.

    Punctuation becomes a separator, so "Ile-de-France" and "Ciudad de Mexico"
    both split into words and neither can be matched by a fragment of one.
    """
    lowered = region.strip().lower()
    return " ".join("".join(c if c.isalnum() else " " for c in lowered).split())


def get_rule_pack(pack_id: str) -> dict[str, Any] | None:
    """Return the rule-pack definition for ``pack_id`` (or ``None``)."""
    return RULE_PACKS.get(pack_id)


def list_rule_packs() -> list[dict[str, Any]]:
    """Return every known rule pack as a list (stable order)."""
    return list(RULE_PACKS.values())


def valid_pack_ids(pack_ids: list[str]) -> list[str]:
    """Filter ``pack_ids`` down to the ones that actually exist.

    Order-preserving and de-duplicating. Used to validate a project's
    requested pack selection before persisting it so a typo never silently
    disables the gate.
    """
    seen: set[str] = set()
    out: list[str] = []
    for pid in pack_ids:
        if pid in RULE_PACKS and pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def suggest_pack_for_country(country_code: str | None) -> str | None:
    """Pack for an ISO 3166-1 alpha-2 ``country_code``, or ``None``.

    ``None`` means "this column gave no answer" and covers both a country with
    no pack registered (Canada, China) and no usable country at all (the empty
    string the demo path writes). Callers that need to tell those apart should
    use :func:`resolve_pack`, which does.
    """
    code = _normalise_country_code(country_code)
    if code is None:
        return None
    return PACK_BY_COUNTRY.get(code)


def suggest_pack_for_region(region: str | None) -> str:
    """Suggest a single default pack id for a coarse ``region`` tag.

    A fallback for when no ISO country is on the record - prefer
    :func:`resolve_pack`, which consults the country column first.

    Matched in three passes, none of which is a bare substring test: the whole
    string against the ISO codes, then the whole string against the non-ISO
    legacy codes, then whole words and phrases against the labels. Falls back
    to :data:`DEFAULT_PACK_ID`. Pure and deterministic.
    """
    if not region:
        return DEFAULT_PACK_ID
    normalised = _normalise_region(region)
    if not normalised:
        return DEFAULT_PACK_ID

    # A region tag that is exactly a country code means that country. This is
    # an equality test on the whole string, so it carries none of the substring
    # risk that made "de" match "Ciudad de Mexico".
    exact = suggest_pack_for_country(normalised)
    if exact is not None:
        return exact
    legacy = PACK_BY_LEGACY_CODE.get(normalised)
    if legacy is not None:
        return legacy

    # Whole word or whole phrase. Padding both sides means a label can only
    # match on token boundaries, so "mexico" matches "Ciudad de Mexico" while
    # "usa" cannot match inside "Russia".
    padded = f" {normalised} "
    for label, pack_id in PACK_BY_LABEL.items():
        if f" {_normalise_region(label)} " in padded:
            return pack_id
    return DEFAULT_PACK_ID


def resolve_pack(country_code: str | None, region: str | None) -> str:
    """Resolve one default pack from the ISO country, then the region label.

    The ISO column decides whenever it holds a usable code, including when that
    country has no pack registered: a project that declares itself Canadian
    gets the universal pack, not whatever its free-text region happens to
    spell. The region label is consulted only when no country is known at all.

    Used to seed a new project's default selection - never to override an
    explicit choice the caller made.
    """
    code = _normalise_country_code(country_code)
    if code is not None:
        return PACK_BY_COUNTRY.get(code, DEFAULT_PACK_ID)
    return suggest_pack_for_region(region)


def resolve_rule_sets(
    pack_ids: list[str],
    *,
    workflow: str = WORKFLOW_CONTRACT_SIGNATURE,
) -> list[str]:
    """Resolve the union of validation rule-set names for ``pack_ids``.

    Only packs that enforce ``workflow`` contribute their rule sets. Unknown
    pack ids are skipped. The result is order-preserving and de-duplicated so
    the validation engine receives a clean, stable list.
    """
    seen: set[str] = set()
    out: list[str] = []
    for pid in pack_ids:
        pack = RULE_PACKS.get(pid)
        if pack is None:
            continue
        if workflow not in pack.get("enforced_workflows", []):
            continue
        for rs in pack.get("rule_sets", []):
            if rs not in seen:
                seen.add(rs)
                out.append(rs)
    return out
