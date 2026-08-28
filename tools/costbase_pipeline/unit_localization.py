from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class UnitConversion:
    source_unit: str
    target_unit: str
    factor: float  # number of target units in one source unit


US_CUSTOMARY_CONVERSIONS: dict[str, UnitConversion] = {
    "m": UnitConversion("m", "LF", 3.280839895),
    "m2": UnitConversion("m2", "SF", 10.76391042),
    "m3": UnitConversion("m3", "CY", 1.307950619),
    "kg": UnitConversion("kg", "LB", 2.204622622),
    "t": UnitConversion("t", "TON", 1.102311311),
    "mm": UnitConversion("mm", "IN", 0.039370079),
    "cm": UnitConversion("cm", "IN", 0.393700787),
    "km": UnitConversion("km", "MI", 0.621371192),
    "l": UnitConversion("L", "GAL", 0.264172052),
    "ha": UnitConversion("ha", "AC", 2.471053815),
}

UNCHANGED_UNITS = {
    "",
    "%",
    "each",
    "ea",
    "nr",
    "no",
    "pcs",
    "pc",
    "set",
    "lump",
    "ls",
    "h",
    "hr",
    "hour",
    "man-day",
    "shift",
    "kwh",
}

RESOURCE_UNIT_COLUMN = "resource_unit"
RESOURCE_QUANTITY_COLUMN = "resource_quantity"
RESOURCE_PRICE_COLUMN = "resource_price_per_unit_current"
RESOURCE_COST_COLUMN = "resource_cost"
RATE_UNIT_COLUMNS = ["rate_unit", "rate_unit_copy"]
PARAMETER_UNIT_COLUMN = "parameter_service_unit"
PARAMETER_QUANTITY_COLUMN = "parameter_service_quantity"
PARAMETER_RESOURCE_QUANTITY_COLUMN = "parameter_resource_quantity"
MASS_UNIT_COLUMN = "mass_unit"
MASS_VALUE_COLUMN = "mass_value"
ABSTRACT_RESOURCE_UNIT_COLUMN = "price_abstract_resource_unit"

POSITION_MONEY_COLUMNS = [
    "total_cost_per_position",
    "total_resource_cost_per_position",
    "total_material_cost_per_position",
    "materials_resource_cost",
    "total_value_machinery_equipment",
    "cost_of_working_hours",
    "cost_operator_sum",
    "price_operator_wages",
    "price_cost_without_wages",
    "service_cost_sum",
    "total_value_abstract_resources",
]

ROW_MONEY_COLUMNS_PRESERVED_BY_RESOURCE_UNIT = [
    RESOURCE_COST_COLUMN,
    "electricity_cost_total_sum",
]


def normalize_unit(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    unit = " ".join(str(value).strip().split())
    lowered = unit.lower()
    aliases = {
        "M": "m",
        "MT": "m",
        "M1": "m",
        "m1": "m",
        "m'": "m",
        "m²": "m2",
        "M2": "m2",
        "M²": "m2",
        "sqm": "m2",
        "sq m": "m2",
        "m^2": "m2",
        "m³": "m3",
        "M3": "m3",
        "M³": "m3",
        "cum": "m3",
        "cu m": "m3",
        "m^3": "m3",
        "meter": "m",
        "metre": "m",
        "meters": "m",
        "metres": "m",
        "kilogram": "kg",
        "kilograms": "kg",
        "tonne": "t",
        "tonnes": "t",
        "ton": "t",
        "liter": "l",
        "litre": "l",
        "liters": "l",
        "litres": "l",
        "lít": "l",
    }
    return aliases.get(unit, aliases.get(lowered, lowered))


def conversion_for(value: object) -> UnitConversion | None:
    unit = normalize_unit(value)
    if unit in US_CUSTOMARY_CONVERSIONS:
        return US_CUSTOMARY_CONVERSIONS[unit]
    if unit in UNCHANGED_UNITS:
        return None
    return None


def should_use_us_customary(
    *,
    target_region: str | None = None,
    target_lang: str | None = None,
    unit_system: str | None = None,
) -> bool:
    if unit_system:
        return unit_system == "us_customary"
    return (target_region or "").upper() == "USA_USD" and (target_lang or "").lower() in {"en", "en-us", "en_us"}


def apply_unit_localization(
    frame: pd.DataFrame,
    *,
    target_region: str | None = None,
    target_lang: str | None = None,
    unit_system: str | None = None,
) -> tuple[pd.DataFrame, dict]:
    if should_use_us_customary(target_region=target_region, target_lang=target_lang, unit_system=unit_system):
        return convert_metric_to_us_customary(frame)
    unchanged = frame.copy(deep=True)
    unchanged["unit_system"] = unit_system or "metric"
    unchanged["unit_conversion_basis"] = "metric_no_conversion"
    return unchanged, {
        "unit_system": unchanged["unit_system"].iloc[0] if len(unchanged) else unit_system or "metric",
        "unit_conversion_basis": "metric_no_conversion",
        "converted": False,
    }


def _ensure_metric_audit_column(df: pd.DataFrame, column: str) -> None:
    audit = f"{column}_metric"
    if column in df.columns and audit not in df.columns:
        df[audit] = df[column]


def _multiply_numeric(df: pd.DataFrame, column: str, factors: pd.Series) -> None:
    if column not in df.columns:
        return
    values = pd.to_numeric(df[column], errors="coerce")
    # _factors_for maps to floats mixed with pd.NA, so factors arrives as an object
    # Series; coerce it to numeric so the product is float64 and assign the whole
    # column at once (pandas 3 rejects a masked in-place assign of an object result
    # into a float64 column).
    factor_values = pd.to_numeric(factors, errors="coerce")
    mask = values.notna() & factor_values.notna()
    if not bool(mask.any()):
        return
    df[column] = values.where(~mask, values * factor_values).astype("float64")


def _divide_numeric(df: pd.DataFrame, column: str, factors: pd.Series) -> None:
    if column not in df.columns:
        return
    values = pd.to_numeric(df[column], errors="coerce")
    factor_values = pd.to_numeric(factors, errors="coerce")
    mask = values.notna() & factor_values.notna() & factor_values.ne(0)
    if not bool(mask.any()):
        return
    df[column] = values.where(~mask, values / factor_values).astype("float64")


def _target_units_for(series: pd.Series) -> pd.Series:
    return series.map(lambda value: conversion_for(value).target_unit if conversion_for(value) else value)


def _factors_for(series: pd.Series) -> pd.Series:
    return series.map(lambda value: conversion_for(value).factor if conversion_for(value) else pd.NA)


def convert_metric_to_us_customary(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    localized = frame.copy(deep=True)
    report: dict[str, object] = {
        "unit_system": "us_customary",
        "resource_unit_converted_rows": 0,
        "rate_basis_converted_rows": 0,
        "mass_converted_rows": 0,
        "parameter_converted_rows": 0,
        "unchanged_money_columns": ROW_MONEY_COLUMNS_PRESERVED_BY_RESOURCE_UNIT,
        "unknown_unit_values": {},
    }

    def record_unknown(column: str) -> None:
        if column not in localized.columns:
            return
        values = sorted(
            {
                str(value)
                for value in localized[column].dropna().unique().tolist()
                if normalize_unit(value) not in US_CUSTOMARY_CONVERSIONS
                and normalize_unit(value) not in UNCHANGED_UNITS
            }
        )
        if values:
            report["unknown_unit_values"][column] = values[:50]

    if RESOURCE_UNIT_COLUMN in localized.columns:
        record_unknown(RESOURCE_UNIT_COLUMN)
        _ensure_metric_audit_column(localized, RESOURCE_UNIT_COLUMN)
        factors = _factors_for(localized[RESOURCE_UNIT_COLUMN])
        converted = factors.notna()
        report["resource_unit_converted_rows"] = int(converted.sum())
        _multiply_numeric(localized, RESOURCE_QUANTITY_COLUMN, factors)
        _divide_numeric(localized, RESOURCE_PRICE_COLUMN, factors)
        localized.loc[converted, RESOURCE_UNIT_COLUMN] = _target_units_for(
            localized.loc[converted, f"{RESOURCE_UNIT_COLUMN}_metric"]
        )

    if MASS_UNIT_COLUMN in localized.columns:
        record_unknown(MASS_UNIT_COLUMN)
        _ensure_metric_audit_column(localized, MASS_UNIT_COLUMN)
        factors = _factors_for(localized[MASS_UNIT_COLUMN])
        converted = factors.notna()
        report["mass_converted_rows"] = int(converted.sum())
        _multiply_numeric(localized, MASS_VALUE_COLUMN, factors)
        localized.loc[converted, MASS_UNIT_COLUMN] = _target_units_for(
            localized.loc[converted, f"{MASS_UNIT_COLUMN}_metric"]
        )

    if PARAMETER_UNIT_COLUMN in localized.columns:
        record_unknown(PARAMETER_UNIT_COLUMN)
        _ensure_metric_audit_column(localized, PARAMETER_UNIT_COLUMN)
        factors = _factors_for(localized[PARAMETER_UNIT_COLUMN])
        converted = factors.notna()
        report["parameter_converted_rows"] = int(converted.sum())
        _multiply_numeric(localized, PARAMETER_QUANTITY_COLUMN, factors)
        _multiply_numeric(localized, PARAMETER_RESOURCE_QUANTITY_COLUMN, factors)
        localized.loc[converted, PARAMETER_UNIT_COLUMN] = _target_units_for(
            localized.loc[converted, f"{PARAMETER_UNIT_COLUMN}_metric"]
        )

    if ABSTRACT_RESOURCE_UNIT_COLUMN in localized.columns:
        record_unknown(ABSTRACT_RESOURCE_UNIT_COLUMN)
        _ensure_metric_audit_column(localized, ABSTRACT_RESOURCE_UNIT_COLUMN)
        converted = _factors_for(localized[ABSTRACT_RESOURCE_UNIT_COLUMN]).notna()
        localized.loc[converted, ABSTRACT_RESOURCE_UNIT_COLUMN] = _target_units_for(
            localized.loc[converted, f"{ABSTRACT_RESOURCE_UNIT_COLUMN}_metric"]
        )

    rate_factor = None
    for column in RATE_UNIT_COLUMNS:
        if column not in localized.columns:
            continue
        record_unknown(column)
        _ensure_metric_audit_column(localized, column)
        factors = _factors_for(localized[column])
        converted = factors.notna()
        if rate_factor is None:
            rate_factor = factors
            report["rate_basis_converted_rows"] = int(converted.sum())
        localized.loc[converted, column] = _target_units_for(localized.loc[converted, f"{column}_metric"])

    if rate_factor is not None:
        basis_size_in_metric = rate_factor.map(
            lambda value: 1 / float(value) if pd.notna(value) and float(value) else pd.NA
        )
        _multiply_numeric(localized, RESOURCE_QUANTITY_COLUMN, basis_size_in_metric)
        for column in POSITION_MONEY_COLUMNS:
            _multiply_numeric(localized, column, basis_size_in_metric)
        # Rebasing the position from "per 1 metric unit" to "per 1 target unit"
        # shrinks the resource quantities by the basis size, so the resource-level
        # money has to shrink by the same factor. Resource unit prices are untouched
        # here, so resource_cost must move with resource_quantity to keep
        # resource_cost == resource_quantity * resource_price_per_unit_current and to
        # keep the resource rows summing back to the rebased position total. Skipping
        # this left imperial catalogues with resource_cost 1/basis too large, which
        # broke estimate reconciliation on every converted position.
        for column in ROW_MONEY_COLUMNS_PRESERVED_BY_RESOURCE_UNIT:
            _multiply_numeric(localized, column, basis_size_in_metric)

    localized["unit_system"] = "us_customary"
    localized["unit_conversion_basis"] = "metric_to_us_customary_v1"
    return localized, report


def validate_resource_cost_invariance(before: pd.DataFrame, after: pd.DataFrame, *, epsilon: float = 0.01) -> dict:
    if RESOURCE_COST_COLUMN not in before.columns or RESOURCE_COST_COLUMN not in after.columns:
        return {"ok": True, "checked_rows": 0, "max_abs_delta": 0.0}
    left = pd.to_numeric(before[RESOURCE_COST_COLUMN], errors="coerce")
    right = pd.to_numeric(after[RESOURCE_COST_COLUMN], errors="coerce")
    mask = left.notna() & right.notna()
    delta = (left[mask] - right[mask]).abs()
    max_delta = float(delta.max()) if not delta.empty else 0.0
    return {
        "ok": bool(max_delta <= epsilon),
        "checked_rows": int(mask.sum()),
        "max_abs_delta": max_delta,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-parquet", type=Path, required=True)
    parser.add_argument("--output-parquet", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    source = pd.read_parquet(args.input_parquet)
    converted, report = convert_metric_to_us_customary(source)
    report["resource_cost_invariance"] = validate_resource_cost_invariance(source, converted)
    args.output_parquet.parent.mkdir(parents=True, exist_ok=True)
    converted.to_parquet(args.output_parquet, index=False)
    if args.output_csv:
        converted.to_csv(args.output_csv, index=False, encoding="utf-8-sig")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
