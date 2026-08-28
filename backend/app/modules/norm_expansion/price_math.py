# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Pure production-norm pricing math.

Where :mod:`app.modules.norm_expansion.expand_math` turns a work item into an
*unpriced* resource demand (hours and material quantities), this module puts a
price on the norm's per-unit coefficients so an estimator gets a full unit-rate
build-up: labour-hours costed by an all-in labour rate, machine-hours costed by
an equipment rate, and each material costed by a resolved unit cost. The sum of
the line totals is the built-up unit rate behind one unit of the work item.

An assembly is a recipe for ONE unit of a work item, so the build-up is done on
the norm's PER-UNIT coefficients (labour_hours_per_unit, machine_hours_per_unit,
material qty_per_unit), never on an expanded total. That keeps the resulting
assembly reusable at any quantity.

A material coefficient is the NET (installed) quantity. Real estimating buys more
than it installs to cover offcuts, laps and breakage, so each material line is
grossed up net -> gross by a waste factor (``gross = net * factor``, factor
``>= 1``) before it is priced: the priced amount uses the GROSS quantity. The
net->gross arithmetic is delegated to the waste-factors engine
(:func:`app.modules.waste_factors.waste_math.apply`) so it lives in one place;
the caller resolves the per-material factor from the waste-factor library and
hands it in on each :class:`MaterialPrice`. A factor of 1 (labour, machine, and
any material with no library entry) leaves gross == net and the waste at 0 pct.

Everything here is deliberately free of SQLAlchemy, FastAPI and I/O so the math
can be unit-tested without a database. The service layer resolves the rates and
material prices (from labour-rate templates and cost items) and hands the pure
value objects to :func:`price_build_up`.

All arithmetic uses :class:`decimal.Decimal`; every figure is quantised to four
decimal places with ``ROUND_HALF_UP`` so the same inputs always yield the same
strings (no binary-float drift), matching the module's :mod:`expand_math`
convention. A ``float`` is refused everywhere the way expand_math refuses it -
money, rates, factors and quantities never enter the pipeline as binary floats.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.modules.norm_expansion.expand_math import NormCoefficients
from app.modules.price_breakdown.model import coerce_kind, kind_i18n_key
from app.modules.waste_factors.waste_math import apply as apply_waste_factor

# Money and quantities are carried to four decimal places - the same precision
# expand_math uses for resource demand. Four places keep a per-unit unit-cost
# (which can be a fraction of a cent when a small material factor meets a low
# unit price) exact through the build-up instead of rounding it away at two
# places and drifting once it is multiplied by a large work quantity.
_QUANT = Decimal("0.0001")

# Canonical resource-type tokens. They are valid values for both
# ``assemblies.Component.resource_type`` and the price-breakdown vocabulary, so
# a priced line reads and writes the same token end to end.
LABOR = "labor"
EQUIPMENT = "equipment"
MATERIAL = "material"

# Canonical provenance tokens for ``PricedLine.price_source``. They are stable
# API values (a UI maps them to i18n keys), so they carry no prose and are
# defined here - in the pure module - so the DB layer and the wire contract can
# never drift apart on spelling.
COST_ITEM_EXACT = "cost_item_exact"
COST_ITEM_FUZZY = "cost_item_fuzzy"
LABOR_RATE_TEMPLATE = "labor_rate_template"
EXPLICIT_RATE = "explicit_rate"
UNPRICED = "unpriced"


def _to_decimal(value: Decimal | int | str) -> Decimal:
    """Coerce a rate / quantity to a finite :class:`Decimal`.

    Accepts a ``Decimal``, an ``int`` or a decimal *string* (the storage form
    used across the cost spine). A ``float`` is refused on purpose - money,
    rates and factors are never allowed to enter the pipeline as binary floats.
    A ``NaN`` / ``Infinity`` is rejected so a poisoned value can never propagate
    into a priced build-up.

    Args:
        value: The raw rate, unit cost or coefficient.

    Returns:
        The value as a finite ``Decimal``.

    Raises:
        TypeError: If ``value`` is a ``float`` or an unsupported type.
        ValueError: If ``value`` cannot be parsed or is not finite.
    """
    if isinstance(value, bool):  # bool is an int subclass; never a rate
        raise TypeError("value must not be a bool")
    if isinstance(value, float):
        raise TypeError("value must be Decimal/int/str, not float")
    if isinstance(value, Decimal):
        dec = value
    else:
        try:
            dec = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"invalid decimal value: {value!r}") from exc
    if not dec.is_finite():
        raise ValueError("value must be finite (no NaN / Infinity)")
    return dec


def _q4(value: Decimal) -> Decimal:
    """Quantise a Decimal to four decimal places, half-up."""
    return value.quantize(_QUANT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class MaterialPrice:
    """A resolved unit cost for one of a norm's material coefficients.

    Aligned by position with ``NormCoefficients.materials`` when handed to
    :func:`price_build_up`. A ``None`` ``unit_cost`` means no cost item matched
    the material: the line is still emitted (so the estimator sees the demand)
    but at a zero unit cost and flagged unpriced.

    Attributes:
        unit_cost: The material's unit cost, or ``None`` when unpriced. Decimal
            or decimal-string; never a float.
        cost_item_id: The matched cost item's id, when a match was found.
        matched_description: The matched cost item's description, for audit.
        waste_factor: The net -> gross waste multiplier resolved from the
            waste-factor library (``>= 1``; ``1`` means no waste). Decimal,
            int or decimal-string; never a float. Defaults to ``1`` so a caller
            that does not resolve waste prices the net quantity unchanged.
        waste_matched: ``True`` when a waste-factor library entry was found for
            this material, ``False`` when the pass-through factor was used.
            Carried through (like ``cost_item_id``) for the caller to record;
            the pure math does not compute it.
        match_method: Provenance token for how the cost item was found
            (``cost_item_exact`` / ``cost_item_fuzzy`` / ``unpriced``).
        match_confidence: ``1`` for an exact normalized identity match, the
            lexical matcher's 0..1 score for a fuzzy one, ``None`` when nothing
            matched.
        matched_code: The matched cost item's code, for audit.
        matched_source: The matched cost item's ``source`` (which catalogue or
            import the price came from).
        price_as_of: ISO date the matched rate was last set or verified, or
            ``""`` when the row carries no price date.
        needs_review: ``True`` when the price came from a heuristic (fuzzy)
            match, so a human confirms it rather than it being applied silently.
    """

    unit_cost: Decimal | int | str | None
    cost_item_id: str | None = None
    matched_description: str = ""
    waste_factor: Decimal | int | str = Decimal("1")
    waste_matched: bool = False
    match_method: str = ""
    match_confidence: Decimal | None = None
    matched_code: str = ""
    matched_source: str = ""
    price_as_of: str = ""
    needs_review: bool = False


@dataclass(frozen=True)
class PricedLine:
    """One priced line of a unit-rate build-up.

    Attributes:
        resource_type: The canonical resource token (``labor`` / ``equipment``
            / ``material``) - a valid ``Component.resource_type`` value.
        kind: The canonical price-breakdown kind for this line.
        kind_i18n_key: The stable i18n key for ``kind``.
        description: Human-readable line description.
        unit: Unit the line quantity is measured in (``h`` for hours; the
            material unit otherwise).
        quantity: The per-unit coefficient (labour/machine hours per unit, or
            material NET quantity per unit), quantised to four decimal places.
        unit_cost: The resolved unit cost, quantised to four decimal places
            (zero when the line is unpriced).
        total: ``gross_qty * unit_cost``, quantised to four decimal places -
            the line is priced on the GROSS (purchased) quantity, so a material
            with a waste allowance costs more than its net quantity implies.
        priced: ``False`` when no rate / cost was available for this line.
        net_qty: The net (installed) quantity - the same figure as ``quantity``,
            surfaced explicitly for the net -> gross story.
        waste_pct: The waste allowance as a percentage, ``(factor - 1) * 100``
            (``0`` for labour, machine and unmatched materials).
        gross_qty: The gross (purchased) quantity, ``net_qty`` grossed up by the
            waste factor. Equal to ``net_qty`` when there is no waste.
        waste_matched: ``True`` when a waste-factor library entry was applied to
            this line.
        cost_item_id: The linked cost item id for a priced material line.
        note: A short reason string when the line is unpriced.
        price_source: What put a price on this line - ``cost_item_exact``,
            ``cost_item_fuzzy``, ``labor_rate_template``, ``explicit_rate`` or
            ``unpriced``. A caller can audit every line's origin from this alone.
        match_confidence: 0..1 confidence in that source, or ``None`` when the
            line is unpriced.
        matched_description: What the line actually matched (the cost item's
            description, or the labour-rate template's name).
        matched_code: The matched cost item's code, when there is one.
        matched_source: The matched cost item's ``source`` catalogue.
        price_as_of: ISO date the matched rate was last verified, or ``""``.
        needs_review: ``True`` when the price came from a heuristic match and a
            human has to confirm it.
    """

    resource_type: str
    kind: str
    kind_i18n_key: str
    description: str
    unit: str
    quantity: Decimal
    unit_cost: Decimal
    total: Decimal
    priced: bool
    cost_item_id: str | None = None
    note: str = ""
    net_qty: Decimal = Decimal("0")
    waste_pct: Decimal = Decimal("0")
    gross_qty: Decimal = Decimal("0")
    waste_matched: bool = False
    price_source: str = ""
    match_confidence: Decimal | None = None
    matched_description: str = ""
    matched_code: str = ""
    matched_source: str = ""
    price_as_of: str = ""
    needs_review: bool = False


@dataclass(frozen=True)
class PricedBuildUp:
    """The full priced build-up behind one unit of a work item.

    Attributes:
        lines: The priced lines, ordered labour, machine, then materials in the
            norm's material order.
        labor_cost: Labour contribution to the unit rate (quantised).
        machine_cost: Machine / equipment contribution to the unit rate.
        material_cost: Total material contribution to the unit rate.
        unit_rate: The built-up unit rate - the sum of all line totals.
        currency: The currency the build-up is expressed in (echoed from the
            caller; the pure math does not convert across currencies).
        unpriced: Descriptions of the lines that could not be priced, so a UI
            can flag them for the estimator to resolve.
        needs_review: Descriptions of the lines a heuristic priced, which a
            human still has to confirm.
    """

    lines: tuple[PricedLine, ...]
    labor_cost: Decimal
    machine_cost: Decimal
    material_cost: Decimal
    unit_rate: Decimal
    currency: str = ""
    unpriced: tuple[str, ...] = field(default_factory=tuple)
    needs_review: tuple[str, ...] = field(default_factory=tuple)

    @property
    def complete(self) -> bool:
        """``False`` when at least one line carries no price.

        A ``unit_rate`` built with an unpriced line is a partial sum, and a
        caller reading only the number cannot tell. This is the flag that says
        the total is missing a cost line rather than being finished.
        """
        return not self.unpriced

    def as_dict(self) -> dict[str, object]:
        """Render the build-up as plain Decimal-as-string JSON primitives.

        Every numeric value becomes a fixed-point decimal *string* (e.g.
        ``"47.8365"``) so it round-trips through JSON without float precision
        loss, matching the platform's Decimal-as-string wire contract.
        """
        return {
            "currency": self.currency,
            "labor_cost": format(self.labor_cost, "f"),
            "machine_cost": format(self.machine_cost, "f"),
            "material_cost": format(self.material_cost, "f"),
            "unit_rate": format(self.unit_rate, "f"),
            "unpriced": list(self.unpriced),
            "needs_review": list(self.needs_review),
            "complete": self.complete,
            "lines": [
                {
                    "resource_type": line.resource_type,
                    "kind": line.kind,
                    "kind_i18n_key": line.kind_i18n_key,
                    "description": line.description,
                    "unit": line.unit,
                    "quantity": format(line.quantity, "f"),
                    "unit_cost": format(line.unit_cost, "f"),
                    "total": format(line.total, "f"),
                    "priced": line.priced,
                    "net_qty": format(line.net_qty, "f"),
                    "waste_pct": format(line.waste_pct, "f"),
                    "gross_qty": format(line.gross_qty, "f"),
                    "waste_matched": line.waste_matched,
                    "cost_item_id": line.cost_item_id,
                    "note": line.note,
                    "price_source": line.price_source,
                    "match_confidence": (None if line.match_confidence is None else format(line.match_confidence, "f")),
                    "matched_description": line.matched_description,
                    "matched_code": line.matched_code,
                    "matched_source": line.matched_source,
                    "price_as_of": line.price_as_of,
                    "needs_review": line.needs_review,
                }
                for line in self.lines
            ],
        }


def _price_line(
    *,
    resource_type: str,
    description: str,
    unit: str,
    quantity: Decimal,
    rate: Decimal | int | str | None,
    cost_item_id: str | None = None,
    unpriced_note: str,
    waste_factor: Decimal | int | str = Decimal("1"),
    waste_matched: bool = False,
    price_source: str = "",
    match_confidence: Decimal | None = None,
    matched_description: str = "",
    matched_code: str = "",
    matched_source: str = "",
    price_as_of: str = "",
    needs_review: bool = False,
) -> PricedLine:
    """Build one priced line, folding in the waste gross-up and unpriced fallback.

    The line's ``quantity`` is the NET (installed) coefficient. It is grossed up
    to the purchased quantity via the waste-factors engine (``gross = net *
    factor``) and the line is priced on that gross quantity, so a material with a
    waste allowance costs more than its net quantity implies. A factor of 1
    (labour, machine, unmatched material) leaves gross == net and waste at 0 pct.

    A ``None`` rate yields a zero-cost line flagged ``priced=False`` with
    ``note=unpriced_note`` so the demand is still visible and the caller can
    surface the missing price rather than silently dropping the line. Such a
    line always reports ``price_source="unpriced"`` and no confidence: an
    unpriced line must never read as a line that was priced at zero.

    Raises:
        TypeError: If ``waste_factor`` is a float.
        ValueError: If ``waste_factor`` is not finite.
    """
    kind = coerce_kind(resource_type)
    qty = _q4(quantity)
    # Net -> gross via the shared waste-factors math (reused, not reimplemented).
    factor = _to_decimal(waste_factor)
    gross = apply_waste_factor(qty, factor)
    waste_pct = _q4((factor - Decimal("1")) * Decimal("100"))
    if rate is None:
        unit_cost = _q4(Decimal("0"))
        return PricedLine(
            resource_type=resource_type,
            kind=kind.value,
            kind_i18n_key=kind_i18n_key(kind),
            description=description,
            unit=unit,
            quantity=qty,
            unit_cost=unit_cost,
            total=unit_cost,
            priced=False,
            cost_item_id=cost_item_id,
            note=unpriced_note,
            net_qty=qty,
            waste_pct=waste_pct,
            gross_qty=gross,
            waste_matched=waste_matched,
            price_source=UNPRICED,
            match_confidence=None,
            matched_description=matched_description,
            matched_code=matched_code,
            matched_source=matched_source,
            price_as_of=price_as_of,
            needs_review=False,
        )
    unit_cost = _q4(_to_decimal(rate))
    return PricedLine(
        resource_type=resource_type,
        kind=kind.value,
        kind_i18n_key=kind_i18n_key(kind),
        description=description,
        unit=unit,
        quantity=qty,
        unit_cost=unit_cost,
        total=_q4(gross * unit_cost),
        priced=True,
        cost_item_id=cost_item_id,
        note="",
        net_qty=qty,
        waste_pct=waste_pct,
        gross_qty=gross,
        waste_matched=waste_matched,
        price_source=price_source,
        match_confidence=match_confidence,
        matched_description=matched_description,
        matched_code=matched_code,
        matched_source=matched_source,
        price_as_of=price_as_of,
        needs_review=needs_review,
    )


def price_build_up(
    norm: NormCoefficients,
    *,
    labor_rate: Decimal | int | str | None,
    machine_rate: Decimal | int | str | None,
    material_prices: Sequence[MaterialPrice],
    labor_description: str = "Labour",
    labor_unit: str = "h",
    machine_description: str = "Machine / equipment",
    machine_unit: str = "h",
    currency: str = "",
    labor_rate_source: str = "",
    labor_rate_label: str = "",
    labor_unpriced_note: str = "no labour rate resolved",
    machine_rate_source: str = "",
    machine_rate_label: str = "",
    machine_unpriced_note: str = "no equipment rate resolved",
) -> PricedBuildUp:
    """Price a norm's per-unit coefficients into a unit-rate build-up.

    Costs the labour-hours and machine-hours of one unit of the work item by
    their resolved hourly rates, and each material by its resolved unit cost.
    Each material's NET coefficient is first grossed up to its purchased
    quantity by the ``waste_factor`` carried on its :class:`MaterialPrice`
    (``gross = net * factor``) and priced on the gross, so the build-up already
    includes each material's waste allowance. The build-up is per unit: the sum
    of the line totals is the unit rate an assembly built from this norm would
    carry.

    A labour or machine line is emitted only when its per-unit coefficient is
    greater than zero (a work item with no machine time gets no machine line).
    Every material coefficient is emitted, in the norm's order, so the material
    takeoff is always complete. A missing rate / cost produces a zero-cost line
    flagged ``priced=False`` rather than a dropped line.

    Args:
        norm: The per-unit productivity coefficients of the work item.
        labor_rate: The all-in labour rate per hour, or ``None`` when no labour
            rate is available (labour then prices to zero and is flagged).
        machine_rate: The equipment rate per machine-hour, or ``None`` when no
            equipment rate is available (machine then prices to zero and is
            flagged).
        material_prices: One :class:`MaterialPrice` per material coefficient, in
            the same order as ``norm.materials``.
        labor_description: Description for the labour line.
        labor_unit: Unit for the labour line (hours).
        machine_description: Description for the machine line.
        machine_unit: Unit for the machine line (hours).
        currency: The currency the rates are expressed in (echoed onto the
            result; no cross-currency conversion is done here).
        labor_rate_source: Provenance token for the labour rate (which of the
            caller's rate sources supplied it).
        labor_rate_label: What the labour rate was read from, for audit.
        labor_unpriced_note: The reason recorded on the labour line when no
            labour rate was available.
        machine_rate_source: Provenance token for the equipment rate.
        machine_rate_label: What the equipment rate was read from, for audit.
        machine_unpriced_note: The reason recorded on the machine line when no
            equipment rate was available.

    Returns:
        A :class:`PricedBuildUp` with the ordered priced lines, the per-kind
        subtotals and the built-up unit rate, every figure quantised to four
        decimal places.

    Raises:
        TypeError: If any rate / cost is a float.
        ValueError: If ``material_prices`` does not align one-to-one with
            ``norm.materials``, or any value is non-finite.
    """
    if len(material_prices) != len(norm.materials):
        raise ValueError(
            f"material_prices ({len(material_prices)}) must align with norm.materials ({len(norm.materials)})"
        )

    lines: list[PricedLine] = []
    unpriced: list[str] = []
    needs_review: list[str] = []

    labor_qty = _to_decimal(norm.labor_hours_per_unit)
    if labor_qty > 0:
        line = _price_line(
            resource_type=LABOR,
            description=labor_description,
            unit=labor_unit,
            quantity=labor_qty,
            rate=labor_rate,
            unpriced_note=labor_unpriced_note,
            price_source=labor_rate_source,
            match_confidence=None if labor_rate is None else Decimal("1"),
            matched_description=labor_rate_label,
        )
        lines.append(line)
        if not line.priced:
            unpriced.append(line.description)

    machine_qty = _to_decimal(norm.machine_hours_per_unit)
    if machine_qty > 0:
        line = _price_line(
            resource_type=EQUIPMENT,
            description=machine_description,
            unit=machine_unit,
            quantity=machine_qty,
            rate=machine_rate,
            unpriced_note=machine_unpriced_note,
            price_source=machine_rate_source,
            match_confidence=None if machine_rate is None else Decimal("1"),
            matched_description=machine_rate_label,
        )
        lines.append(line)
        if not line.priced:
            unpriced.append(line.description)

    for coeff, price in zip(norm.materials, material_prices, strict=True):
        line = _price_line(
            resource_type=MATERIAL,
            description=coeff.name,
            unit=coeff.unit,
            quantity=_to_decimal(coeff.qty_per_unit),
            rate=price.unit_cost,
            cost_item_id=price.cost_item_id,
            unpriced_note="no matching cost item",
            waste_factor=price.waste_factor,
            waste_matched=price.waste_matched,
            price_source=price.match_method,
            match_confidence=price.match_confidence,
            matched_description=price.matched_description,
            matched_code=price.matched_code,
            matched_source=price.matched_source,
            price_as_of=price.price_as_of,
            needs_review=price.needs_review,
        )
        lines.append(line)
        if not line.priced:
            unpriced.append(line.description)
        elif line.needs_review:
            needs_review.append(line.description)

    labor_cost = _q4(sum((ln.total for ln in lines if ln.resource_type == LABOR), Decimal("0")))
    machine_cost = _q4(sum((ln.total for ln in lines if ln.resource_type == EQUIPMENT), Decimal("0")))
    material_cost = _q4(sum((ln.total for ln in lines if ln.resource_type == MATERIAL), Decimal("0")))
    unit_rate = _q4(labor_cost + machine_cost + material_cost)

    return PricedBuildUp(
        lines=tuple(lines),
        labor_cost=labor_cost,
        machine_cost=machine_cost,
        material_cost=material_cost,
        unit_rate=unit_rate,
        currency=currency,
        unpriced=tuple(unpriced),
        needs_review=tuple(needs_review),
    )
