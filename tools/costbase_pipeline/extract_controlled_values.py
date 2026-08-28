from __future__ import annotations

import argparse
import json
import unicodedata
from collections import Counter
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

PIPELINE_DIR = Path(__file__).resolve().parent
# Data location comes from configuration, never from where this file happens to sit.
# See extract_translation_corpus.resolve_base_dir for why.
from extract_translation_corpus import resolve_base_dir  # noqa: E402

CONTROLLED_COLUMNS = [
    "row_type",
    "rate_unit",
    "rate_unit_copy",
    "resource_unit",
    "mass_unit",
    "parameter_service_unit",
    "price_abstract_resource_unit",
]

ROW_TYPE_ALIASES = {
    "Scope of work": {
        "canonical_row_type_key": "scope_of_work",
        "ppp_category": "position_total",
        "audit_status": "approved",
    },
    "工作内容": {
        "canonical_row_type_key": "scope_of_work",
        "ppp_category": "position_total",
        "audit_status": "approved",
    },
    "Labour": {
        "canonical_row_type_key": "labour",
        "ppp_category": "labour",
        "audit_status": "approved",
    },
    "Plant Operator": {
        "canonical_row_type_key": "plant_operator",
        "ppp_category": "labour",
        "audit_status": "approved",
    },
    "Material": {
        "canonical_row_type_key": "material",
        "ppp_category": "material",
        "audit_status": "approved",
    },
    "Machinery": {
        "canonical_row_type_key": "machinery",
        "ppp_category": "machinery",
        "audit_status": "approved",
    },
    "Sub-composition": {
        "canonical_row_type_key": "sub_composition",
        "ppp_category": "sub_composition",
        "audit_status": "approved",
    },
    "Resource": {
        "canonical_row_type_key": "resource_unknown",
        "ppp_category": "unknown",
        "audit_status": "needs_review",
    },
}

SIMPLE_UNIT_ALIASES = {
    "m": ("m", "length", "simple_metric"),
    "mt": ("m", "length", "simple_metric"),
    "M": ("m", "length", "simple_metric"),
    "m2": ("m2", "area", "simple_metric"),
    "m²": ("m2", "area", "simple_metric"),
    "M2": ("m2", "area", "simple_metric"),
    "M²": ("m2", "area", "simple_metric"),
    "m3": ("m3", "volume", "simple_metric"),
    "m³": ("m3", "volume", "simple_metric"),
    "M3": ("m3", "volume", "simple_metric"),
    "M³": ("m3", "volume", "simple_metric"),
    "mm": ("mm", "length", "simple_metric"),
    "cm": ("cm", "length", "simple_metric"),
    "km": ("km", "length", "simple_metric"),
    "kg": ("kg", "mass", "simple_metric"),
    "KG": ("kg", "mass", "simple_metric"),
    "t": ("t", "mass", "simple_metric"),
    "ton": ("t", "mass", "simple_metric"),
    "Ton": ("t", "mass", "simple_metric"),
    "TON": ("t", "mass", "simple_metric"),
    "L": ("L", "volume", "simple_metric"),
    "l": ("L", "volume", "simple_metric"),
    "ha": ("ha", "area", "simple_metric"),
    "%": ("percent", "ratio", "do_not_convert"),
    "h": ("hour", "time", "do_not_convert"),
    "H": ("hour", "time", "do_not_convert"),
    "hora": ("hour", "time", "do_not_convert"),
    "ora": ("hour", "time", "do_not_convert"),
    "Saat": ("hour", "time", "do_not_convert"),
    "Sa": ("hour", "time", "do_not_convert"),
    "work-day": ("work_day", "time", "do_not_convert"),
    "machine-shift": ("machine_shift", "time", "do_not_convert"),
    "công": ("work_day", "time", "do_not_convert"),
    "ca": ("machine_shift", "time", "do_not_convert"),
    "CHP": ("productive_machine_hour", "time", "do_not_convert"),
    "CHI": ("idle_machine_hour", "time", "do_not_convert"),
    "UN": ("each", "count", "do_not_convert"),
    "un": ("each", "count", "do_not_convert"),
    "u": ("each", "count", "do_not_convert"),
    "pcs": ("each", "count", "do_not_convert"),
    "ad": ("each", "count", "do_not_convert"),
    "Ad": ("each", "count", "do_not_convert"),
    "AD": ("each", "count", "do_not_convert"),
    "cad": ("each", "count", "do_not_convert"),
    "set": ("set", "count", "do_not_convert"),
    "a corpo": ("lump_sum", "lump", "do_not_convert"),
}


def normalize_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return " ".join(unicodedata.normalize("NFC", str(value)).split()).strip()


def infer_unit_alias(label: str) -> dict:
    exact = normalize_text(label)
    if not exact:
        return {
            "canonical_unit_key": "",
            "canonical_dimension": "",
            "convertibility": "blank",
            "audit_status": "approved",
            "notes": "Blank source value; preserve blank/null semantics.",
        }
    if exact in SIMPLE_UNIT_ALIASES:
        key, dimension, convertibility = SIMPLE_UNIT_ALIASES[exact]
        return {
            "canonical_unit_key": key,
            "canonical_dimension": dimension,
            "convertibility": convertibility,
            "audit_status": "approved",
            "notes": "",
        }
    if any(ch.isdigit() for ch in exact) or "/" in exact:
        return {
            "canonical_unit_key": "compound_basis",
            "canonical_dimension": "compound",
            "convertibility": "needs_rule",
            "audit_status": "needs_review",
            "notes": "Compound or scaled basis; do not free-translate or convert blindly.",
        }
    return {
        "canonical_unit_key": "unknown_or_corrupt",
        "canonical_dimension": "unknown",
        "convertibility": "do_not_convert",
        "audit_status": "needs_review",
        "notes": "Needs controlled-unit review.",
    }


def build_controlled_values() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    row_counter: Counter[tuple[str, str]] = Counter()
    unit_counter: Counter[tuple[str, str, str]] = Counter()
    manifest = {"source_files": [], "controlled_columns": CONTROLLED_COLUMNS}

    for path in sorted(resolve_base_dir().glob("*_workitems_costs_resources_DDC_CWICR.parquet")):
        region = path.name.split("_workitems", 1)[0]
        schema_cols = pq.read_schema(path).names
        df = pd.read_parquet(path, columns=[c for c in CONTROLLED_COLUMNS if c in schema_cols])
        manifest["source_files"].append(path.name)

        if "row_type" in df.columns:
            for value, count in Counter(df["row_type"].map(normalize_text)).items():
                if value:
                    row_counter[(region, value)] += int(count)

        for column in [c for c in CONTROLLED_COLUMNS if c != "row_type" and c in df.columns]:
            for value, count in Counter(df[column].map(normalize_text)).items():
                if value:
                    unit_counter[(region, column, value)] += int(count)

    row_records = []
    for (region, value), count in sorted(row_counter.items()):
        alias = ROW_TYPE_ALIASES.get(
            value,
            {
                "canonical_row_type_key": "unknown",
                "ppp_category": "unknown",
                "audit_status": "needs_review",
            },
        )
        row_records.append(
            {
                "source_region": region,
                "source_row_type_exact": value,
                "observed_count": count,
                **alias,
            }
        )

    unit_records = []
    for (region, column, value), count in sorted(unit_counter.items()):
        unit_records.append(
            {
                "source_region": region,
                "source_column": column,
                "source_label_exact": value,
                "source_label_nfc": normalize_text(value),
                "observed_count": count,
                **infer_unit_alias(value),
            }
        )

    return pd.DataFrame(row_records), pd.DataFrame(unit_records), manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=PIPELINE_DIR / "outputs")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    row_types, units, manifest = build_controlled_values()
    row_types.to_csv(args.out_dir / "row_type_aliases.csv", index=False, encoding="utf-8-sig")
    units.to_csv(args.out_dir / "unit_aliases.csv", index=False, encoding="utf-8-sig")
    (args.out_dir / "controlled_values_manifest.json").write_text(
        json.dumps(
            {
                **manifest,
                "row_type_aliases": int(len(row_types)),
                "unit_aliases": int(len(units)),
                "unit_aliases_needing_review": int(units["audit_status"].eq("needs_review").sum()),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(f"wrote {len(row_types)} row-type aliases and {len(units)} unit aliases")


if __name__ == "__main__":
    main()
