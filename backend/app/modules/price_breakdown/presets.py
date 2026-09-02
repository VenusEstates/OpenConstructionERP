# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Presentation presets for a price breakdown.

The breakdown model is country-neutral. A preset is only a labelling and
grouping choice for output: which categories to show, in which order, under
which heading. The international preset is the default; the other presets lay
the same data out the way a given standard or market words it (German
procurement sheets, UK NRM/CESMM detailed rates, US bid cost breakdown, a
generic cost-plus sheet). Adding a country convention is one entry in
``PRESETS`` - no model change, the underlying ResourceKind values never fork.

Every preset also carries stable i18n keys (``price_breakdown.kind.<value>``
per category and ``price_breakdown.preset.<name>`` for its own label) so a
frontend can translate the headings later. The keys live here as data only; no
shared locale file is edited by this module.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.modules.price_breakdown.model import (
    LINE_I18N_KEYS,
    PriceBreakdown,
    ResourceKind,
    kind_i18n_key,
    money_quantum,
)

_2P = Decimal("0.01")
_4P = Decimal("0.0001")


@dataclass(frozen=True)
class Preset:
    name: str
    label: str
    region: str
    # Heading shown per resource kind, in display order.
    kind_labels: tuple[tuple[ResourceKind, str], ...]

    @property
    def label_i18n_key(self) -> str:
        """Stable i18n key for this preset's own name."""
        return f"price_breakdown.preset.{self.name}"

    def to_dict(self) -> dict:
        """Machine-readable view a UI can render and translate.

        Each kind row exposes the canonical ``kind`` value, the English default
        ``label`` and the stable ``i18n_key``. The summary-line keys are added
        so the whole sheet (categories plus markup lines) is translatable.
        """
        return {
            "name": self.name,
            "label": self.label,
            "label_i18n_key": self.label_i18n_key,
            "region": self.region,
            "kinds": [
                {
                    "kind": kind.value,
                    "label": label,
                    "i18n_key": kind_i18n_key(kind),
                }
                for kind, label in self.kind_labels
            ],
            "line_i18n_keys": dict(LINE_I18N_KEYS),
        }


_INTERNATIONAL = Preset(
    name="international",
    label="Unit price analysis",
    region="international",
    kind_labels=(
        (ResourceKind.LABOUR, "Labour"),
        (ResourceKind.MATERIAL, "Material"),
        (ResourceKind.MACHINERY, "Machinery"),
        (ResourceKind.EQUIPMENT, "Equipment"),
        (ResourceKind.SUBCONTRACT, "Subcontract"),
        (ResourceKind.OTHER, "Other"),
    ),
)

# EFB (Einheitliche Formblaetter, German public procurement handbook). 221 is
# the own-labour sheet, 222 the subcontract sheet, 223 the material list; here
# they are one labelled view of the same categories.
_EFB = Preset(
    name="efb",
    label="EFB price sheets (221/222/223)",
    region="DE",
    kind_labels=(
        (ResourceKind.LABOUR, "Lohnkosten (221)"),
        (ResourceKind.MATERIAL, "Stoffkosten (223)"),
        (ResourceKind.MACHINERY, "Geraetekosten"),
        (ResourceKind.EQUIPMENT, "Vorhaltekosten"),
        (ResourceKind.SUBCONTRACT, "Nachunternehmerleistungen (222)"),
        (ResourceKind.OTHER, "Sonstige Kosten"),
    ),
)

# UK detailed rate build-up, wording shared by NRM (New Rules of Measurement)
# unit-rate analysis and CESMM (Civil Engineering Standard Method of
# Measurement). Plant is the UK term for machinery; a subcontract line is a
# domestic/nominated sub package.
_NRM = Preset(
    name="nrm",
    label="Detailed rate (NRM / CESMM)",
    region="UK",
    kind_labels=(
        (ResourceKind.LABOUR, "Labour"),
        (ResourceKind.MATERIAL, "Materials"),
        (ResourceKind.MACHINERY, "Plant"),
        (ResourceKind.EQUIPMENT, "Temporary works / hire"),
        (ResourceKind.SUBCONTRACT, "Sublet"),
        (ResourceKind.OTHER, "Other"),
    ),
)

# US bid cost breakdown wording (division of the unit price into the cost codes
# an estimator carries on a hard-bid). Equipment (US) is owned/operated plant,
# which the platform stores as machinery; small tools and rented gear map onto
# the equipment kind.
_US_BID = Preset(
    name="us_bid",
    label="Bid cost breakdown",
    region="US",
    kind_labels=(
        (ResourceKind.LABOUR, "Labor"),
        (ResourceKind.MATERIAL, "Material"),
        (ResourceKind.MACHINERY, "Equipment"),
        (ResourceKind.EQUIPMENT, "Small tools and consumables"),
        (ResourceKind.SUBCONTRACT, "Subcontractor"),
        (ResourceKind.OTHER, "Other direct cost"),
    ),
)

# Hungarian anyag/dij sheet. A Hungarian bill quotes every priced line twice,
# once as material (anyag) and once as fee (dij), and totals the two separately
# per sub-chapter, per chapter and on the cover, because the client compares
# tenderers on both columns rather than on the sum alone. Six resource kinds
# therefore land in two columns, not six: material is the anyag column and
# everything else - labour, plant, hired equipment, sublet work and the rest -
# is the dij column. The labels say which column each kind falls in, so the
# grouping is readable from the sheet instead of being knowledge the reader has
# to bring.
#
# This is the one preset that does not open with labour. The others follow the
# order an estimator builds a rate in; this one follows the order the Hungarian
# workbook prints, which is anyag first. Reordering it to match the rest would
# make the sheet disagree with the column order the client reads it against.
#
# Wording follows packs/hungary-hu rule pack ``hu_anyag_dij_bontas``, which
# derives it from Hungarian workbooks in production use rather than from a
# translation of these English headings.
_HU_ANYAG_DIJ = Preset(
    name="hu_anyag_dij",
    label="Anyag és díj bontás (material and fee split)",
    region="HU",
    kind_labels=(
        (ResourceKind.MATERIAL, "Anyag"),
        (ResourceKind.LABOUR, "Díj - munkadíj"),
        (ResourceKind.MACHINERY, "Díj - gépköltség"),
        (ResourceKind.EQUIPMENT, "Díj - eszközköltség"),
        (ResourceKind.SUBCONTRACT, "Díj - alvállalkozói teljesítés"),
        (ResourceKind.OTHER, "Díj - egyéb költség"),
    ),
)

# Generic cost-plus sheet: neutral, market-agnostic wording for a build-up
# where a fee is added to measured cost. Useful as a plain fallback heading set.
_COST_PLUS = Preset(
    name="cost_plus",
    label="Cost-plus breakdown",
    region="international",
    kind_labels=(
        (ResourceKind.LABOUR, "Labour cost"),
        (ResourceKind.MATERIAL, "Material cost"),
        (ResourceKind.MACHINERY, "Machinery cost"),
        (ResourceKind.EQUIPMENT, "Equipment cost"),
        (ResourceKind.SUBCONTRACT, "Subcontract cost"),
        (ResourceKind.OTHER, "Other cost"),
    ),
)

PRESETS: dict[str, Preset] = {p.name: p for p in (_INTERNATIONAL, _EFB, _NRM, _US_BID, _HU_ANYAG_DIJ, _COST_PLUS)}


def get_preset(name: str | None) -> Preset:
    return PRESETS.get((name or "").strip().lower(), _INTERNATIONAL)


#: Country codes whose bill convention this product has always tagged with a
#: name that is not the ISO code. "UK" is the everyday abbreviation for a
#: country whose code is "GB", and the markup table spells it the same way.
#:
#: Austria and Switzerland are deliberately absent. They share a language with
#: the German preset and not a form: EFB 221/222/223 are the German federal
#: procurement sheets, and an Austrian or Swiss bill is not laid out that way.
#: Handing them the German preset because the words look familiar is exactly
#: the substitution the neutral preset exists to avoid.
_PRESET_REGION_ALIASES: dict[str, str] = {"GB": "UK"}

#: ``region`` -> preset name, built from the presets themselves so a preset
#: added with a region is reachable without a second list to keep in step.
#:
#: "international" is skipped rather than indexed. Two presets declare it, the
#: neutral one and cost-plus, so it identifies no single answer; and it is the
#: fallback below, which is what an unrecognised market should get anyway.
_PRESET_BY_REGION: dict[str, str] = {
    preset.region.upper(): name
    for name, preset in PRESETS.items()
    if preset.region and preset.region.lower() != "international"
}


def preset_for_country(country_code: str | None) -> str:
    """Name of the preset a project in this country should be read with.

    A price analysis is a document an estimator hands to somebody who expects
    it in their own market's shape. A Hungarian bill quotes every line twice,
    as anyag and dij, and reading it as a single unit rate is not a translation
    of a Hungarian bill but a different document. The preset that does this has
    shipped since the Hungarian pack landed, and nothing chose it: the endpoint
    defaulted to the international preset by name, so a Hungarian estimator got
    the international shape unless they knew the preset's own slug.

    ``Preset.region`` has carried the answer the whole time and had no reader.

    Args:
        country_code: ISO 3166-1 alpha-2, case-insensitive, or ``None``.

    Returns:
        A key of :data:`PRESETS`. ``"international"`` for a market with no
        preset of its own, and for no market at all, which are the same answer
        here: the neutral preset is the one that assumes nothing.
    """
    code = (country_code or "").strip().upper()
    if not code:
        return _INTERNATIONAL.name
    region = _PRESET_REGION_ALIASES.get(code, code)
    return _PRESET_BY_REGION.get(region, _INTERNATIONAL.name)


def _q(value: Decimal, quant: Decimal = _2P) -> str:
    """Format one money amount. Callers pass the currency's own quantum.

    The default is two decimals only so an omitted argument keeps the old
    behaviour; every renderer in this module passes ``money_quantum(bd.currency)``
    because a peso rate printed with cents is false precision on a tendered
    number. Leaving one renderer on the default would fix the API and leave the
    CSV wrong, which is harder to notice than fixing neither.
    """
    return str(Decimal(value).quantize(quant, rounding=ROUND_HALF_UP))


def _qty(value: Decimal) -> str:
    return str(Decimal(value).quantize(_4P, rounding=ROUND_HALF_UP))


def efb_221_view(bd: PriceBreakdown) -> dict:
    """Group the components the way an EFB 221-style sheet does: totals per
    resource category plus the markup lines, keyed by category."""
    kt = bd.kind_totals
    preset = _EFB
    money = money_quantum(bd.currency)
    rows = [{"kind": kind.value, "label": label, "amount": _q(kt[kind], money)} for kind, label in preset.kind_labels]
    return {
        "position_ref": bd.position_ref,
        "unit": bd.unit,
        "currency": bd.currency,
        "rows": rows,
        "direct_unit_cost": _q(bd.direct_unit_cost, money),
        "overhead_amount": _q(bd.overhead_amount, money),
        "risk_amount": _q(bd.risk_amount, money),
        "profit_amount": _q(bd.profit_amount, money),
        "unit_rate": _q(bd.unit_rate, money),
    }


def render_markdown(bd: PriceBreakdown, *, preset: str = "international") -> str:
    """A compact, readable price-analysis table (works for any language later
    once the labels move to i18n; the numbers are the point)."""
    p = get_preset(preset)
    cur = bd.currency
    money = money_quantum(cur)
    lines: list[str] = []
    lines.append(f"# {p.label}: {bd.position_ref} {bd.description}".rstrip())
    lines.append("")
    lines.append(f"Unit: {bd.unit}   Quantity: {bd.position_quantity}   Currency: {cur}")
    lines.append("")
    lines.append("| Resource | Description | Qty | Unit cost | Amount |")
    lines.append("| --- | --- | ---: | ---: | ---: |")
    label_by_kind = dict(p.kind_labels)
    for c in bd.components:
        lines.append(
            f"| {label_by_kind.get(c.kind, c.kind.value)} | {c.description} | "
            f"{c.quantity} | {_q(c.unit_cost, money)} | {_q(c.amount, money)} |"
        )
    lines.append("")
    lines.append(f"Direct cost per unit: {_q(bd.direct_unit_cost, money)} {cur}")
    if bd.overhead_pct:
        lines.append(f"Overhead ({bd.overhead_pct}%): {_q(bd.overhead_amount, money)} {cur}")
    if bd.risk_pct:
        lines.append(f"Risk ({bd.risk_pct}%): {_q(bd.risk_amount, money)} {cur}")
    if bd.profit_pct:
        lines.append(f"Profit ({bd.profit_pct}%): {_q(bd.profit_amount, money)} {cur}")
    lines.append(f"Unit rate: {_q(bd.unit_rate, money)} {cur}")
    lines.append(f"Position total: {_q(bd.position_total, money)} {cur}")
    return "\n".join(lines)


def render_csv(bd: PriceBreakdown, *, preset: str = "international") -> str:
    """Render the price analysis as spreadsheet-friendly CSV.

    Layout: a short title block (position, unit, quantity, currency), then one
    row per cost component with kind / description / unit / quantity / unit cost
    / amount, then the summary lines (direct, overhead, risk, profit, unit rate,
    position total). Money is 2dp and quantities 4dp, Decimal-exact. Fields are
    comma-separated with minimal quoting, so commas or quotes inside a
    description are escaped safely by the csv writer.
    """
    p = get_preset(preset)
    cur = bd.currency
    money = money_quantum(cur)
    label_by_kind = dict(p.kind_labels)
    buf = io.StringIO()
    # Fixed line terminator so the output is deterministic across platforms.
    writer = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)

    writer.writerow(["Price analysis", p.label])
    writer.writerow(["Position", bd.position_ref, bd.description])
    writer.writerow(["Unit", bd.unit, "Quantity", _qty(bd.position_quantity), "Currency", cur])
    writer.writerow([])
    writer.writerow(["Kind", "Description", "Unit", "Quantity", "Unit cost", "Amount"])
    for c in bd.components:
        writer.writerow(
            [
                label_by_kind.get(c.kind, c.kind.value),
                c.description,
                c.unit,
                _qty(c.quantity),
                _q(c.unit_cost, money),
                _q(c.amount, money),
            ]
        )
    writer.writerow([])
    writer.writerow(["Direct cost per unit", "", "", "", "", _q(bd.direct_unit_cost, money)])
    writer.writerow([f"Overhead ({bd.overhead_pct}%)", "", "", "", "", _q(bd.overhead_amount, money)])
    writer.writerow([f"Risk ({bd.risk_pct}%)", "", "", "", "", _q(bd.risk_amount, money)])
    writer.writerow([f"Profit ({bd.profit_pct}%)", "", "", "", "", _q(bd.profit_amount, money)])
    writer.writerow(["Unit rate", "", "", "", "", _q(bd.unit_rate, money)])
    writer.writerow(["Position total", "", "", "", "", _q(bd.position_total, money)])
    return buf.getvalue()
