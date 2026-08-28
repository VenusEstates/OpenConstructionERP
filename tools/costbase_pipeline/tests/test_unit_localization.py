from __future__ import annotations

import math

import pandas as pd
from unit_localization import (
    US_CUSTOMARY_CONVERSIONS,
    apply_unit_localization,
    convert_metric_to_us_customary,
    validate_resource_cost_invariance,
)


def test_resource_unit_conversion_preserves_resource_cost() -> None:
    source = pd.DataFrame(
        [
            {
                "resource_unit": "m",
                "resource_quantity": 2.0,
                "resource_price_per_unit_current": 10.0,
                "resource_cost": 20.0,
            }
        ]
    )
    converted, report = convert_metric_to_us_customary(source)
    factor = US_CUSTOMARY_CONVERSIONS["m"].factor

    assert report["resource_unit_converted_rows"] == 1
    assert converted.loc[0, "resource_unit"] == "LF"
    assert converted.loc[0, "resource_unit_metric"] == "m"
    assert math.isclose(converted.loc[0, "resource_quantity"], 2.0 * factor)
    assert math.isclose(converted.loc[0, "resource_price_per_unit_current"], 10.0 / factor)
    assert converted.loc[0, "resource_cost"] == 20.0
    assert validate_resource_cost_invariance(source, converted)["ok"]


def test_position_basis_conversion_rebases_totals_to_one_target_unit() -> None:
    source = pd.DataFrame(
        [
            {
                "rate_unit": "m3",
                "rate_unit_copy": "m3",
                "resource_unit": "kg",
                "resource_quantity": 100.0,
                "resource_price_per_unit_current": 2.0,
                "resource_cost": 200.0,
                "total_cost_per_position": 200.0,
                "total_resource_cost_per_position": 200.0,
            }
        ]
    )
    converted, report = convert_metric_to_us_customary(source)
    kg_factor = US_CUSTOMARY_CONVERSIONS["kg"].factor
    cy_basis = 1 / US_CUSTOMARY_CONVERSIONS["m3"].factor

    assert report["rate_basis_converted_rows"] == 1
    assert converted.loc[0, "rate_unit"] == "CY"
    assert converted.loc[0, "rate_unit_metric"] == "m3"
    assert converted.loc[0, "rate_unit_copy"] == "CY"
    assert converted.loc[0, "rate_unit_copy_metric"] == "m3"
    assert converted.loc[0, "resource_unit"] == "LB"
    assert math.isclose(converted.loc[0, "resource_quantity"], 100.0 * kg_factor * cy_basis)
    assert math.isclose(converted.loc[0, "resource_price_per_unit_current"], 2.0 / kg_factor)
    # Rebasing to "per 1 CY" shrinks the resource money by the basis, exactly like the
    # quantity, so the row still multiplies out and sums back to the position total.
    assert math.isclose(converted.loc[0, "resource_cost"], 200.0 * cy_basis)
    assert math.isclose(converted.loc[0, "total_cost_per_position"], 200.0 * cy_basis)
    assert math.isclose(converted.loc[0, "total_resource_cost_per_position"], 200.0 * cy_basis)
    quantity = converted.loc[0, "resource_quantity"]
    price = converted.loc[0, "resource_price_per_unit_current"]
    assert math.isclose(quantity * price, converted.loc[0, "resource_cost"])
    assert math.isclose(
        converted.loc[0, "resource_cost"],
        converted.loc[0, "total_resource_cost_per_position"],
    )


def test_unchanged_units_are_not_modified() -> None:
    source = pd.DataFrame(
        [
            {
                "rate_unit": "each",
                "resource_unit": "h",
                "resource_quantity": 3.0,
                "resource_price_per_unit_current": 50.0,
                "resource_cost": 150.0,
                "total_cost_per_position": 150.0,
            }
        ]
    )
    converted, report = convert_metric_to_us_customary(source)

    assert report["resource_unit_converted_rows"] == 0
    assert report["rate_basis_converted_rows"] == 0
    assert converted.loc[0, "rate_unit"] == "each"
    assert converted.loc[0, "resource_unit"] == "h"
    assert converted.loc[0, "resource_quantity"] == 3.0
    assert converted.loc[0, "resource_price_per_unit_current"] == 50.0
    assert converted.loc[0, "resource_cost"] == 150.0
    assert converted.loc[0, "total_cost_per_position"] == 150.0


def test_us_customary_gate_only_converts_usa() -> None:
    source = pd.DataFrame([{"rate_unit": "m2", "resource_unit": "m", "resource_quantity": 1.0}])

    usa, usa_report = apply_unit_localization(source, target_region="USA_USD", target_lang="en")
    gb, gb_report = apply_unit_localization(source, target_region="GB_LONDON", target_lang="en")

    assert usa_report["unit_system"] == "us_customary"
    assert usa.loc[0, "rate_unit"] == "SF"
    assert usa.loc[0, "resource_unit"] == "LF"
    assert gb_report["unit_conversion_basis"] == "metric_no_conversion"
    assert gb.loc[0, "rate_unit"] == "m2"
    assert gb.loc[0, "resource_unit"] == "m"


def test_unknown_compound_units_are_logged_not_converted() -> None:
    source = pd.DataFrame(
        [
            {
                "rate_unit": "M3XKM",
                "resource_unit": "m/mm",
                "resource_quantity": 7.0,
                "resource_price_per_unit_current": 3.0,
            }
        ]
    )
    converted, report = convert_metric_to_us_customary(source)

    assert converted.loc[0, "rate_unit"] == "M3XKM"
    assert converted.loc[0, "resource_unit"] == "m/mm"
    assert converted.loc[0, "resource_quantity"] == 7.0
    assert converted.loc[0, "resource_price_per_unit_current"] == 3.0
    assert "M3XKM" in report["unknown_unit_values"]["rate_unit"]
    assert "m/mm" in report["unknown_unit_values"]["resource_unit"]
