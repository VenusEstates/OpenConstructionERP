# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Cases module validation rules.

A case scenario is teaching material, and the ways it goes wrong are not syntax
errors: it opens screens without saying why, it claims five minutes for twenty
steps, it walks the same page three times. None of that stops a case being
saved, and none of it can be caught by a schema.

So the rules run on save and the findings come back with the saved case, and
they are a **gate on one action only**: publishing. A private draft may be as
rough as its author likes. Sharing a case with the team is a claim that it is
worth someone's time, and an ERROR finding contradicts that claim.

Rules, all registered under the ``cases`` rule set:

* ``cases.has_steps``         - ERROR.   A case with no steps teaches nothing.
* ``cases.step_titled``       - ERROR.   Every step needs a title and a target.
* ``cases.step_purpose``      - WARNING. A step with no "why" is a click
                                          instruction, not a walkthrough.
* ``cases.distinct_screens``  - WARNING. Consecutive steps on the same screen
                                          read as one step to the follower.
* ``cases.duration_plausible``- WARNING. The stated minutes should survive
                                          contact with the step count.
* ``cases.described``         - WARNING. A case needs a summary to be findable.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.validation.engine import (
    RuleCategory,
    RuleResult,
    Severity,
    ValidationContext,
    ValidationRule,
    rule_registry,
    validation_engine,
)

logger = logging.getLogger(__name__)

CASES_RULE_SET = "cases"

# The finding raised when the engine itself could not run. Named so that a
# caller can tell "we checked and this is wrong" from "we could not check",
# which is the distinction an empty list used to swallow.
VALIDATION_UNAVAILABLE = "cases.validation_unavailable"

# Below this many minutes per step the estimate stops being believable. A step
# is "open this screen, read it, do the thing" - half a minute is the floor a
# reader could actually keep up with.
_MIN_MINUTES_PER_STEP = 0.5


def _case(context: ValidationContext) -> dict[str, Any]:
    data = context.data
    return data if isinstance(data, dict) else {}


def _steps(context: ValidationContext) -> list[dict[str, Any]]:
    steps = _case(context).get("steps")
    if not isinstance(steps, list):
        return []
    return [step for step in steps if isinstance(step, dict)]


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _result(
    rule: ValidationRule,
    passed: bool,
    message: str,
    *,
    element_ref: str | None = None,
    suggestion: str | None = None,
    details: dict[str, Any] | None = None,
) -> RuleResult:
    """Build a RuleResult carrying the rule's own id / name / severity / category."""
    return RuleResult(
        rule_id=rule.rule_id,
        rule_name=rule.name,
        severity=rule.severity,
        category=rule.category,
        passed=passed,
        message=message,
        element_ref=element_ref,
        suggestion=suggestion,
        details=details or {},
    )


class CaseHasSteps(ValidationRule):
    rule_id = "cases.has_steps"
    name = "Case Has Steps"
    standard = "cases"
    severity = Severity.ERROR
    category = RuleCategory.COMPLETENESS
    description = "A case with no steps teaches nothing and cannot be followed."

    async def validate(self, context: ValidationContext) -> list[RuleResult]:
        steps = _steps(context)
        if steps:
            return [_result(self, True, "OK", details={"step_count": len(steps)})]
        return [
            _result(
                self,
                False,
                "This case has no steps yet, so there is nothing for a reader to follow.",
                suggestion="Add at least one step naming the screen to open and what to do there.",
                details={"step_count": 0},
            )
        ]


class CaseStepTitled(ValidationRule):
    rule_id = "cases.step_titled"
    name = "Case Steps Named And Targeted"
    standard = "cases"
    severity = Severity.ERROR
    category = RuleCategory.STRUCTURE
    description = "Every step needs a title and a screen to open, or it cannot be rendered as a step."

    async def validate(self, context: ValidationContext) -> list[RuleResult]:
        broken = [
            step.get("id") or f"#{index + 1}"
            for index, step in enumerate(_steps(context))
            if not _text(step.get("title")) or not _text(step.get("to"))
        ]
        if not broken:
            return [_result(self, True, "OK")]
        return [
            _result(
                self,
                False,
                f"{len(broken)} step(s) are missing a title or a screen to open.",
                suggestion="Give every step a short title and pick the screen it opens.",
                details={"steps": [str(ref) for ref in broken]},
            )
        ]


class CaseStepPurpose(ValidationRule):
    rule_id = "cases.step_purpose"
    name = "Case Steps Explain Why"
    standard = "cases"
    severity = Severity.WARNING
    category = RuleCategory.COMPLETENESS
    description = "A step with no reason given is a click instruction rather than a walkthrough."

    async def validate(self, context: ValidationContext) -> list[RuleResult]:
        steps = _steps(context)
        if not steps:
            return []
        silent = [step.get("id") or f"#{index + 1}" for index, step in enumerate(steps) if not _text(step.get("why"))]
        if not silent:
            return [_result(self, True, "OK")]
        return [
            _result(
                self,
                False,
                (
                    f"{len(silent)} of {len(steps)} step(s) do not say why they matter, so the case reads as "
                    "a list of clicks rather than an explanation."
                ),
                suggestion="Add one line per step on what it is for; that is the part a new starter needs.",
                details={"steps": [str(ref) for ref in silent], "step_count": len(steps)},
            )
        ]


class CaseDistinctScreens(ValidationRule):
    rule_id = "cases.distinct_screens"
    name = "Case Steps Move Between Screens"
    standard = "cases"
    severity = Severity.WARNING
    category = RuleCategory.CONSISTENCY
    description = "Consecutive steps pointing at the same screen read as one step to the follower."

    async def validate(self, context: ValidationContext) -> list[RuleResult]:
        steps = _steps(context)
        if len(steps) < 2:
            return [_result(self, True, "OK")]
        repeats: list[str] = []
        for index in range(1, len(steps)):
            previous = _text(steps[index - 1].get("to"))
            current = _text(steps[index].get("to"))
            if previous and previous == current:
                repeats.append(str(steps[index].get("id") or f"#{index + 1}"))
        if not repeats:
            return [_result(self, True, "OK")]
        return [
            _result(
                self,
                False,
                (
                    f"{len(repeats)} step(s) open the same screen as the step before them, so the reader "
                    "sees no change and cannot tell the steps apart."
                ),
                suggestion="Merge them into one step, or point the second at the screen the work actually moves to.",
                details={"steps": repeats},
            )
        ]


class CaseDurationPlausible(ValidationRule):
    rule_id = "cases.duration_plausible"
    name = "Case Duration Matches Its Steps"
    standard = "cases"
    severity = Severity.WARNING
    category = RuleCategory.QUALITY
    description = "The stated duration should survive contact with the number of steps."

    async def validate(self, context: ValidationContext) -> list[RuleResult]:
        steps = _steps(context)
        if not steps:
            return []
        try:
            minutes = int(_case(context).get("est_minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0
        floor = _MIN_MINUTES_PER_STEP * len(steps)
        if minutes >= floor:
            return [_result(self, True, "OK", details={"est_minutes": minutes, "step_count": len(steps)})]
        return [
            _result(
                self,
                False,
                (
                    f"This case claims {minutes} minute(s) for {len(steps)} step(s). "
                    "A reader who cannot keep up stops trusting the rest of the estimate."
                ),
                suggestion=f"Raise the estimate to at least {int(floor + 0.5)} minutes or drop some steps.",
                details={"est_minutes": minutes, "step_count": len(steps), "suggested_minimum": floor},
            )
        ]


class CaseDescribed(ValidationRule):
    rule_id = "cases.described"
    name = "Case Has A Summary"
    standard = "cases"
    severity = Severity.WARNING
    category = RuleCategory.COMPLETENESS
    description = "A case needs a one-line summary or nobody browsing the list will open it."

    async def validate(self, context: ValidationContext) -> list[RuleResult]:
        if _text(_case(context).get("description")):
            return [_result(self, True, "OK")]
        return [
            _result(
                self,
                False,
                "This case has no summary, so the list shows a title and nothing else.",
                suggestion="Add one sentence on what the reader will have done by the end.",
            )
        ]


_CASES_RULES: tuple[ValidationRule, ...] = (
    CaseHasSteps(),
    CaseStepTitled(),
    CaseStepPurpose(),
    CaseDistinctScreens(),
    CaseDurationPlausible(),
    CaseDescribed(),
)


def register_cases_rules() -> None:
    """Register the module's validation rules with the core rule registry.

    Idempotent - the registry overwrites a rule by id, so a re-import or hot
    reload re-registers cleanly. Called from the module ``on_startup`` hook.
    """
    for rule in _CASES_RULES:
        rule_registry.register(rule, [CASES_RULE_SET])
    logger.debug("Registered %d cases validation rules", len(_CASES_RULES))


async def evaluate_case(case: dict[str, Any], *, case_id: str = "", locale: str = "") -> list[RuleResult]:
    """Run the case rules and return every finding, passing ones dropped.

    Guarded: a validation failure must not stop somebody saving their work, so
    the engine dying degrades to a finding and a log line rather than a 500.

    That finding is the point. This used to return an empty list, which is the
    same value a case gets when it has been checked and nothing is wrong, so
    one value carried two meanings and no caller could tell them apart. A case
    nobody had been able to validate therefore passed the publish gate looking
    exactly like a clean one. Validation is not optional here, and being unable
    to run it is a reason to withhold publication rather than to grant it.

    The row is DIAGNOSTIC because it records an infrastructure failure and not
    something wrong with the case, and ERROR because it must stop the case
    being shared. It deliberately does not set ``is_engine_error``: that flag
    marks rows which are reported alongside a verdict without changing it, and
    this row is the verdict.
    """
    try:
        report = await validation_engine.validate(
            data=case,
            rule_sets=[CASES_RULE_SET],
            target_type="case",
            target_id=case_id,
            metadata={"locale": locale},
        )
    except Exception:  # noqa: BLE001 - validation augments the save; never break it
        logger.warning("cases validation failed for case %s", case_id, exc_info=True)
        return [
            RuleResult(
                rule_id=VALIDATION_UNAVAILABLE,
                rule_name="Case validation could not be run",
                severity=Severity.ERROR,
                category=RuleCategory.DIAGNOSTIC,
                passed=False,
                message=(
                    "This case could not be checked, so it cannot be shared yet. "
                    "Your work is saved. Try again, and tell an administrator if it keeps happening."
                ),
                details={"case_id": case_id},
                suggestion="Save the case as a private draft and share it once validation is working again.",
            )
        ]
    return [result for result in report.results if not result.passed and not result.is_engine_error]


def blocking_findings(results: list[RuleResult]) -> list[RuleResult]:
    """The subset that must be fixed before a case can be shared with the team."""
    return [result for result in results if result.severity == Severity.ERROR]


__all__ = [
    "CASES_RULE_SET",
    "VALIDATION_UNAVAILABLE",
    "blocking_findings",
    "evaluate_case",
    "register_cases_rules",
]
