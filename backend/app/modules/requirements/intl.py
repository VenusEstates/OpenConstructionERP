# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Localized labels and guarded coverage maths for requirements.

The deliverable coverage roll-up in :mod:`app.modules.requirements.evaluator`
is language-neutral, but the status and priority words shown next to it were
English only, and the coverage percentage was worked out inline. These pure
helpers give that a localized, guarded surface with no database, ORM, or
``app.*`` import, so they unit-test on any runner.

Priorities follow the MoSCoW convention (must / should / could / will not have),
an international requirements-management standard, not any one product.

Labels are looked up by base language via :func:`_norm_lang`. A lookup that
matched a regional code such as ``de-AT`` only by exact string would have
silently answered in English - the same result a language nobody has
translated at all gives, and nothing downstream can tell the two apart.
"""

from __future__ import annotations

#: Deliverable status words (see evaluator ``_deliverable_status``).
DELIVERABLE_STATUS_LABELS: dict[str, dict[str, str]] = {
    "accepted": {"en": "accepted", "de": "abgenommen", "ru": "принято"},
    "submitted": {"en": "submitted", "de": "eingereicht", "ru": "подано"},
    "missing": {"en": "missing", "de": "fehlt", "ru": "отсутствует"},
}

#: MoSCoW requirement priority words.
PRIORITY_LABELS: dict[str, dict[str, str]] = {
    "must": {"en": "must have", "de": "Muss-Anforderung", "ru": "обязательно"},
    "should": {"en": "should have", "de": "Soll-Anforderung", "ru": "желательно"},
    "could": {"en": "could have", "de": "Kann-Anforderung", "ru": "возможно"},
    "wont": {"en": "will not have", "de": "wird nicht umgesetzt", "ru": "не будет"},
    #: ``may`` is the spelling this module's API accepted before this catalog
    #: existed, and rows still carry it. It means ``could``, and without an
    #: entry here it rendered as the raw word "may" in every language.
    "may": {"en": "could have", "de": "Kann-Anforderung", "ru": "возможно"},
}

#: MoSCoW order, most important first, for stable sorting. ``may`` is absent on
#: purpose: it is a spelling of ``could``, not a fifth priority, and
#: :func:`priority_rank` gives it the same rank through :data:`PRIORITY_ALIASES`.
PRIORITY_ORDER: tuple[str, ...] = ("must", "should", "could", "wont")

#: Legacy spellings mapped onto the MoSCoW word they mean.
PRIORITY_ALIASES: dict[str, str] = {"may": "could"}

#: Coverage bands over the percentage.
BAND_NONE = "none"
BAND_PARTIAL = "partial"
BAND_COMPLETE = "complete"


def _norm_lang(lang: str) -> str:
    """Reduce a locale hint like ``de-AT`` or ``de_AT`` to its base language.

    Both catalogs below are keyed by base language only (``en``, ``de``,
    ``ru``). Without this, a regional code missed the dict by exact string
    match and silently fell to English - the same result as a language
    nobody has translated at all, and indistinguishable from it.
    """
    return str(lang).strip().lower().replace("_", "-").split("-", 1)[0]


def _label(catalog: dict[str, dict[str, str]], key: str, lang: str) -> str:
    per_lang = catalog.get(key)
    if per_lang is None:
        return key
    return per_lang.get(_norm_lang(lang)) or per_lang["en"]


def deliverable_status_label(status: str, lang: str = "en") -> str:
    """Localized word for a deliverable status, English as fallback."""
    return _label(DELIVERABLE_STATUS_LABELS, status, lang)


def priority_label(priority: str, lang: str = "en") -> str:
    """Localized MoSCoW priority word, English as fallback."""
    return _label(PRIORITY_LABELS, priority, lang)


def priority_rank(priority: str) -> int:
    """Sort rank for a MoSCoW priority (0 = must). Unknown sorts last.

    A legacy spelling ranks where the word it means ranks, so a list holding
    both ``could`` and ``may`` does not split one priority into two groups.
    """
    canonical = PRIORITY_ALIASES.get(priority, priority)
    try:
        return PRIORITY_ORDER.index(canonical)
    except ValueError:
        return len(PRIORITY_ORDER)


def coverage_rate(accepted: int, total: int) -> float:
    """Accepted deliverables as a percent of total, in [0, 100].

    Mirrors the evaluator rule (``accepted / total * 100``). An empty set is a
    well-defined 0.0, accepted is clamped into ``[0, total]`` so a stray count
    never exceeds 100, and negative inputs raise ``ValueError``.
    """
    if accepted < 0 or total < 0:
        raise ValueError("accepted and total counts cannot be negative")
    if total == 0:
        return 0.0
    capped = min(accepted, total)
    return round(capped / total * 100.0, 2)


def coverage_band(pct: float) -> str:
    """Classify a coverage percent into none / partial / complete."""
    if pct <= 0:
        return BAND_NONE
    if pct >= 100:
        return BAND_COMPLETE
    return BAND_PARTIAL


def explain_coverage(accepted: int, submitted: int, missing: int) -> str:
    """One-line plain-language statement of deliverable coverage."""
    for name, value in (("accepted", accepted), ("submitted", submitted), ("missing", missing)):
        if value < 0:
            raise ValueError(f"{name} count cannot be negative")
    total = accepted + submitted + missing
    if total == 0:
        return "No deliverables defined for this requirement."
    pct = coverage_rate(accepted, total)
    return f"{accepted} of {total} deliverables accepted ({pct}%); {submitted} in review, {missing} outstanding."


__all__ = [
    "DELIVERABLE_STATUS_LABELS",
    "PRIORITY_LABELS",
    "PRIORITY_ORDER",
    "BAND_NONE",
    "BAND_PARTIAL",
    "BAND_COMPLETE",
    "deliverable_status_label",
    "priority_label",
    "priority_rank",
    "coverage_rate",
    "coverage_band",
    "explain_coverage",
]
