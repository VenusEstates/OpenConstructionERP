# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Validation rules for a variation request's dedicated bill of quantities.

The variations module shipped without any registered rules, so these two are
the first. They are deliberately narrow: they answer the two questions a
priced variation can be wrong about in a way no other module would notice.

* ``variations.boq_lines_are_traced`` - every priced line in a variation bill
  says where it came from. A variation is an argument about a contract, and a
  line nobody can trace back to a schedule-of-values line or to the estimate
  is a line that will be argued about.
* ``variations.boq_total_matches_estimate`` - the request's headline
  ``estimated_cost_impact`` and the bill's grand total do not disagree.
  Disagreement is not an error: the headline is a forecast made before the
  bill existed, and the whole point of pricing is to replace it. It is a
  warning so the reader knows which of the two numbers on the screen is now
  the stale one.

Both are WARNING severity on purpose. Neither states that the data is
invalid; both state that a human is about to read two numbers and needs to
know which one to believe. Errors would block the workflow the rules exist to
inform.

The rules run on the read path of ``GET /variation-requests/{id}/boq/`` and
their results are part of that response, so validation is not an optional
extra screen somebody has to know to open.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.validation.engine import (
    RuleCategory,
    RuleResult,
    Severity,
    ValidationContext,
    ValidationRule,
    rule_registry,
)
from app.core.validation.messages import translate

logger = logging.getLogger(__name__)

#: Rule set name callers pass to ``ValidationEngine.validate``.
VARIATIONS_RULE_SET = "variations"

#: Below this the two figures are treated as the same number. Money is
#: rounded to cents in both places it comes from, so a sub-cent gap is
#: rounding, not disagreement.
_MONEY_EPSILON = Decimal("0.01")


def _ok(locale: str) -> str:
    """Shared "OK" string, the same key every built-in rule uses."""
    return translate("common.ok", locale=locale)


def _locale(context: ValidationContext) -> str:
    """The caller's locale, defaulting to English."""
    meta = getattr(context, "metadata", None) or {}
    return str(meta.get("locale") or "en")


def _decimal(value: Any) -> Decimal:
    """Coerce a money-ish value to Decimal, degrading to zero on junk."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _lines(context: ValidationContext) -> list[dict[str, Any]]:
    """The priced lines of the variation bill, as plain dicts."""
    data = context.data
    if isinstance(data, dict):
        raw = data.get("lines") or []
        return [line for line in raw if isinstance(line, dict)]
    return []


class VariationBOQLinesAreTraced(ValidationRule):
    """Every priced line in a variation bill names where it came from."""

    rule_id = "variations.boq_lines_are_traced"
    name = "Variation Bill Lines Are Traced"
    standard = "variations"
    severity = Severity.WARNING
    category = RuleCategory.COMPLETENESS
    description = "Flags priced lines in a variation's bill that trace back to nothing."

    async def validate(self, context: ValidationContext) -> list[RuleResult]:
        locale = _locale(context)
        lines = _lines(context)
        results: list[RuleResult] = []
        for line in lines:
            # Structure, not scope: a line with no unit carries no money and
            # a section header is a heading. This is the same test the bill
            # view applies when it counts priced lines, so one line cannot be
            # left out of the count by one and flagged by the other.
            if str(line.get("unit") or "") in ("", "section"):
                continue
            traced = bool(line.get("source_position_id") or line.get("contract_line_id"))
            label = str(line.get("ordinal") or line.get("id") or "")
            results.append(
                RuleResult(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    severity=self.severity,
                    category=self.category,
                    passed=traced,
                    message=(
                        _ok(locale)
                        if traced
                        else translate(
                            "variations.boq_lines_are_traced.fail",
                            locale=locale,
                            line=label,
                        )
                    ),
                    element_ref=str(line.get("id") or label),
                    suggestion=(
                        None if traced else translate("variations.boq_lines_are_traced.suggestion", locale=locale)
                    ),
                )
            )
        return results


class VariationBOQTotalMatchesEstimate(ValidationRule):
    """The request's headline estimate agrees with its priced bill."""

    rule_id = "variations.boq_total_matches_estimate"
    name = "Variation Bill Total Matches Estimate"
    standard = "variations"
    severity = Severity.WARNING
    category = RuleCategory.CONSISTENCY
    description = "Flags a variation request whose headline estimate disagrees with its priced bill."

    async def validate(self, context: ValidationContext) -> list[RuleResult]:
        data = context.data
        if not isinstance(data, dict):
            return []
        locale = _locale(context)
        # A bill that blends currencies has no single grand total to compare
        # against, so comparing one would manufacture a disagreement out of
        # an FX blend. The mixed-currency flag is its own warning elsewhere.
        if bool(data.get("is_mixed_currency")):
            return []
        headline = _decimal(data.get("estimated_cost_impact"))
        priced = _decimal(data.get("grand_total"))
        passed = abs(headline - priced) < _MONEY_EPSILON
        return [
            RuleResult(
                rule_id=self.rule_id,
                rule_name=self.name,
                severity=self.severity,
                category=self.category,
                passed=passed,
                message=(
                    _ok(locale)
                    if passed
                    else translate(
                        "variations.boq_total_matches_estimate.fail",
                        locale=locale,
                        estimate=format(headline, "f"),
                        priced=format(priced, "f"),
                    )
                ),
                element_ref=str(data.get("variation_request_id") or ""),
                suggestion=(
                    None if passed else translate("variations.boq_total_matches_estimate.suggestion", locale=locale)
                ),
            )
        ]


def register_variations_rules() -> None:
    """Idempotently register the variation-bill rules.

    Registration is keyed on ``rule_id`` inside the registry, so calling this
    twice replaces each rule with an equal one rather than doubling the set.
    """
    rule_registry.register(VariationBOQLinesAreTraced(), rule_sets=[VARIATIONS_RULE_SET])
    rule_registry.register(VariationBOQTotalMatchesEstimate(), rule_sets=[VARIATIONS_RULE_SET])
