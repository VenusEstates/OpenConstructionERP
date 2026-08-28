# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Composite alert-rule expression evaluator.

Grammar:

    Node = LeafKPI | LeafField | LogicalOp

    LeafKPI    = {"op": "kpi", "code": str, "compare": Compare, "value": Decimal}
    LeafField  = {"op": "field", "source": str, "path": str, "compare": Compare,
                  "value": Any}
    LogicalOp  = {"op": "and" | "or" | "not", "operands": [Node, ...]}

    Compare    = "lt" | "lte" | "gt" | "gte" | "eq" | "neq"

Example - fire when CPI<0.95 AND the project is in execution phase:

    {
      "op": "and",
      "operands": [
        {"op": "kpi", "code": "cpi", "compare": "lt", "value": "0.95"},
        {"op": "field", "source": "project", "path": "phase",
         "compare": "eq", "value": "execution"}
      ]
    }

The evaluator is sandboxed - it can only:
    * call registered KPI formulas via :mod:`.kpis.compute`
    * read attributes off a small allow-list of source models
      (``project`` only at v1)

It does not exec/eval arbitrary code. Unknown operators raise; that
means a bogus expression fails closed (alert doesn't fire).

An expression is checked by :func:`validate_alert_expression` when the
rule is written, not when it runs. A rule that is only checked as it runs
fails once a cycle for as long as it exists, and the failure is reported
in a place nobody reads, so the rule looks like a quiet system rather
than like a rule nobody can evaluate.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.bi_dashboards import kpis as _kpis

logger = logging.getLogger(__name__)


VALID_COMPARES = ("lt", "lte", "gt", "gte", "eq", "neq")
VALID_LOGICAL = ("and", "or", "not")
#: Sources a ``field`` leaf may read. Kept in step with the branches in
#: :func:`_read_field` - this tuple is what the write path checks against,
#: that function is what the read path walks.
VALID_FIELD_SOURCES = ("project",)


class AlertExpressionError(ValueError):
    """Raised when an expression node is malformed."""


def _is_number(v: Any) -> bool:
    """True for a value the numeric branch of :func:`_compare` should own.

    ``bool`` is excluded even though it is an ``int``. Reading a boolean
    as a number folds ``True`` and ``False`` onto the same Decimal, so
    every boolean comparison came out equal, ``True == False`` included.
    Left alone, two booleans compare as booleans and get the right answer.
    """
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def _coerce_decimal(v: Any) -> Decimal | None:
    """Read ``v`` as a number a comparison can use, or ``None``.

    ``None`` means "not a usable number", and it is deliberately not
    ``Decimal("0")``. Zero is a threshold somebody may genuinely mean, so
    a value that could not be read must not arrive at the comparison
    wearing one: that is how a NULL project field came to compare as zero
    and fire a rule on missing data.

    Non-finite values are refused for the same reason they are refused at
    creation. ``Decimal`` parses ``NaN``, ``sNaN`` and ``Infinity`` in any
    case, and each one then answers a comparison without measuring
    anything: an ordering comparison against ``NaN`` raises, ``neq``
    against it is true forever, and an infinity is simply larger or
    smaller than everything.
    """
    if isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        return v if v.is_finite() else None
    try:
        parsed = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _compare(lhs: Any, op: str, rhs: Any) -> bool:
    """Comparison primitive - works on numeric + string types.

    When one side is a number and the other cannot be read as a finite
    number, this raises instead of answering. Answering ``False`` would
    look like the safe choice and is not: under a ``not`` it inverts, so
    a leaf nobody can evaluate would fire the rule on every cycle. The
    raise reaches ``BIDashboardsService.evaluate_alert``, which catches it
    per rule and logs it, and ``evaluate_alerts`` catches per rule on top
    of that, so one unevaluatable rule fails closed and the loop carries
    on.
    """
    # One numeric side pulls the other onto Decimal for a fair comparison.
    # Two booleans, or two strings, are left to compare as themselves.
    if _is_number(lhs) or _is_number(rhs):
        lhs_n = _coerce_decimal(lhs)
        rhs_n = _coerce_decimal(rhs)
        if lhs_n is None or rhs_n is None:
            side = "left" if lhs_n is None else "right"
            unreadable = lhs if lhs_n is None else rhs
            raise AlertExpressionError(
                f"Cannot evaluate '{op}': the {side} side, {unreadable!r}, is not a finite number",
            )
        lhs, rhs = lhs_n, rhs_n
    if op == "lt":
        return lhs < rhs
    if op == "lte":
        return lhs <= rhs
    if op == "gt":
        return lhs > rhs
    if op == "gte":
        return lhs >= rhs
    if op == "eq":
        return lhs == rhs
    if op == "neq":
        return lhs != rhs
    raise AlertExpressionError(f"Unknown compare op: {op}")


async def _read_field(
    session: AsyncSession,
    project_id: uuid.UUID | None,
    source: str,
    path: str,
) -> Any:
    """Read a single attribute from an allow-listed source row.

    Currently only ``project`` is allowed - extending this is intentional
    cross-module coupling and should be done via a new branch here.
    """
    if source == "project":
        if project_id is None:
            return None
        try:
            from app.modules.projects.models import Project  # type: ignore

            proj = await session.get(Project, project_id)
            if proj is None:
                return None
            return getattr(proj, path, None)
        except ImportError:
            return None
        except Exception:
            logger.debug("alert_dsl: project field read failed", exc_info=True)
            return None
    raise AlertExpressionError(f"Unknown field source: {source}")


def _validate_compare(compare: Any, path: str) -> None:
    if compare not in VALID_COMPARES:
        raise AlertExpressionError(
            f"Invalid compare '{compare}' at {path}; expected one of {', '.join(VALID_COMPARES)}",
        )


def _validate_threshold(value: Any, path: str) -> None:
    """Check the right-hand side of a ``kpi`` leaf.

    A KPI is measured as a number, so the thing it is measured against
    has to be one. A boolean is refused by name because it does not fail
    to parse loudly: ``Decimal(str(True))`` raises, and the fallback that
    used to catch that turned ``true`` into ``Decimal("0")``, so a rule
    written against ``true`` compared against zero and read as deliberate.
    """
    if value is None:
        raise AlertExpressionError(f"Missing threshold at {path}")
    if isinstance(value, bool):
        raise AlertExpressionError(
            f"Threshold at {path} is a boolean, and a KPI is measured as a number",
        )
    if _coerce_decimal(value) is None:
        raise AlertExpressionError(
            f"Threshold at {path} is not a finite number: {value!r}",
        )


def _validate_literal(value: Any, path: str) -> None:
    """Check the right-hand side of a ``field`` leaf, which may be text.

    A field can hold a phase name as easily as an amount, so this refuses
    only what is shaped like a number and is not one. The trade is that a
    text field can no longer be compared against the literal string
    ``"NaN"``, which is a name for nothing that a field is likely to hold
    and a spelling of the defect this refusal exists to stop. Booleans
    stay legitimate here: a boolean column is a real thing to compare
    against, and :func:`_is_number` keeps that comparison off the numeric
    path where it used to be answered wrongly.
    """
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, (int, float, Decimal)):
        if _coerce_decimal(value) is None:
            raise AlertExpressionError(f"Value at {path} is not a finite number: {value!r}")
        return
    if isinstance(value, str):
        try:
            parsed = Decimal(value)
        except (InvalidOperation, ValueError):
            return  # Plain text, compared as text.
        if not parsed.is_finite():
            raise AlertExpressionError(f"Value at {path} is not a finite number: {value!r}")


def _validate_node(node: Any, path: str) -> None:
    if not isinstance(node, dict):
        raise AlertExpressionError(f"Expected dict at {path}, got {type(node).__name__}")
    op = node.get("op")
    if op in VALID_LOGICAL:
        operands = node.get("operands")
        if not isinstance(operands, list) or not operands:
            # An empty ``and`` evaluates to True and fires the rule every
            # cycle; an empty ``not`` does the same. Neither is anything a
            # person meant to write.
            raise AlertExpressionError(f"Operator '{op}' at {path} needs at least one operand")
        for index, child in enumerate(operands):
            _validate_node(child, f"{path}.{op}[{index}]")
        return
    if op == "kpi":
        code = node.get("code")
        if not isinstance(code, str) or not code.strip():
            # Without this the code falls back to "", ``kpis.compute``
            # answers an unknown code with a zero computation, and the
            # leaf compares that zero against the threshold.
            raise AlertExpressionError(f"KPI leaf at {path} needs a code")
        _validate_compare(node.get("compare") or "lt", path)
        _validate_threshold(node.get("value"), f"{path}.value")
        return
    if op == "field":
        source = node.get("source")
        if source not in VALID_FIELD_SOURCES:
            raise AlertExpressionError(
                f"Unknown field source '{source}' at {path}; expected one of {', '.join(VALID_FIELD_SOURCES)}",
            )
        field_path = node.get("path")
        if not isinstance(field_path, str) or not field_path.strip():
            raise AlertExpressionError(f"Field leaf at {path} needs a path")
        _validate_compare(node.get("compare") or "eq", path)
        _validate_literal(node.get("value"), f"{path}.value")
        return
    raise AlertExpressionError(f"Unknown op '{op}' at {path}")


def validate_alert_expression(expression: Any) -> None:
    """Check an alert expression at the moment somebody writes it.

    An empty expression is not an expression: the rule falls back to the
    single ``condition`` plus ``threshold_value`` path, so nothing here
    applies to it.

    Args:
        expression: The submitted ``expression_json`` tree.

    Raises:
        AlertExpressionError: A node is malformed, names an operator or a
            source outside the grammar, or compares against a value that
            is not a number the evaluator can use. The message names the
            path into the tree that was refused.
    """
    if not expression:
        return
    _validate_node(expression, "$")


async def evaluate_alert_expression(
    expression: dict[str, Any],
    session: AsyncSession,
    *,
    project_id: uuid.UUID | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Evaluate an alert expression tree.

    Returns ``(fired, trace)`` where ``trace`` records every leaf
    evaluation for audit/debug - embedded in the ``bi.alert.triggered``
    event payload so subscribers can show *why* the alert fired.
    """
    trace: dict[str, Any] = {}

    async def _eval(node: dict[str, Any], path: str) -> bool:
        if not isinstance(node, dict):
            raise AlertExpressionError(
                f"Expected dict at {path}, got {type(node).__name__}",
            )
        op = node.get("op")
        if op == "and":
            results = [await _eval(child, f"{path}.and[{i}]") for i, child in enumerate(node.get("operands") or [])]
            return all(results) if results else True
        if op == "or":
            results = [await _eval(child, f"{path}.or[{i}]") for i, child in enumerate(node.get("operands") or [])]
            return any(results) if results else False
        if op == "not":
            operands = node.get("operands") or []
            if not operands:
                return True
            return not await _eval(operands[0], f"{path}.not")
        if op == "kpi":
            code = str(node.get("code") or "")
            compare = str(node.get("compare") or "lt")
            rhs = node.get("value")
            if compare not in VALID_COMPARES:
                raise AlertExpressionError(
                    f"Invalid compare '{compare}' at {path}",
                )
            result = await _kpis.compute(
                code,
                session,
                project_id=project_id,
            )
            lhs = result.value
            # ``rhs`` goes in as written. ``_compare`` coerces both sides
            # itself, and coercing here as well would hide which side of
            # the comparison a refusal came from.
            outcome = _compare(lhs, compare, rhs)
            trace[path] = {
                "kpi": code,
                "compare": compare,
                "lhs": str(lhs),
                "rhs": str(rhs),
                "outcome": outcome,
            }
            return outcome
        if op == "field":
            source = str(node.get("source") or "")
            field_path = str(node.get("path") or "")
            compare = str(node.get("compare") or "eq")
            rhs = node.get("value")
            if compare not in VALID_COMPARES:
                raise AlertExpressionError(
                    f"Invalid compare '{compare}' at {path}",
                )
            lhs = await _read_field(session, project_id, source, field_path)
            outcome = _compare(lhs, compare, rhs)
            trace[path] = {
                "field": f"{source}.{field_path}",
                "compare": compare,
                "lhs": str(lhs),
                "rhs": str(rhs),
                "outcome": outcome,
            }
            return outcome
        raise AlertExpressionError(f"Unknown op '{op}' at {path}")

    if not expression:
        return False, trace
    fired = await _eval(expression, "$")
    return fired, trace


__all__ = [
    "AlertExpressionError",
    "VALID_COMPARES",
    "VALID_FIELD_SOURCES",
    "VALID_LOGICAL",
    "evaluate_alert_expression",
    "validate_alert_expression",
]
