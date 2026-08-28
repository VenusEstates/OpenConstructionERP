from __future__ import annotations

import argparse
import hashlib
import json
import os
import unicodedata
from collections import defaultdict
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

PIPELINE_DIR = Path(__file__).resolve().parent

# Where the canonical base parquets live. Deliberately NOT derived from this file's location.
# It used to be `PIPELINE_DIR.parent`, which happened to be the data directory only because
# the code sat inside it; the day the code moved somewhere version-controlled, that expression
# started pointing at a directory with no parquet in it. Nothing would have failed loudly:
# every test passes `source_path=` explicitly, so the suite stays green while the production
# entry point silently resolves nothing. Data location is deployment configuration, so it
# comes from the environment or the command line and never from __file__.
BASE_DIR_ENV = "OE_COSTBASE_DIR"


def resolve_base_dir(explicit: Path | str | None = None) -> Path:
    """Directory holding the canonical `*_workitems_..._DDC_CWICR.parquet` bases."""
    value = explicit or os.environ.get(BASE_DIR_ENV)
    if not value:
        raise SystemExit(
            f"the cost base directory is not set. Pass --base-dir or export {BASE_DIR_ENV}. "
            "It is not inferred from the location of this file."
        )
    path = Path(value).expanduser()
    if not path.is_dir():
        raise SystemExit(f"cost base directory does not exist: {path}")
    return path


SOURCE_LANG_BY_REGION = {
    "BR": "pt",
    "ES_ANDALUCIA": "es",
    "GR": "el",
    "ID": "id",
    "IT_TOSCANA": "it",
    "TR": "tr",
    "VN": "vi",
    "ZH_CHINA": "zh",
}

TEXT_COLUMNS = [
    "rate_original_name",
    "rate_final_name",
    "resource_name",
    "work_composition_text",
    "labor_title",
    "machine_class2_name",
    "machine_class3_name",
    "collection_name",
    "department_name",
    "section_name",
    "subsection_name",
    "mass_name",
    "service_category",
    "service_type",
    "parameter_service_name",
    "abstract_resource_tech_group",
    "labor_class",
    "operator_class",
    "price_relocation_included",
    "row_type",
    "nrm2_section_title",
    "rate_unit",
    "rate_unit_copy",
    "resource_unit",
    "mass_unit",
    "parameter_service_unit",
    "price_abstract_resource_unit",
]

# Read from the platform registry on 2026-07-04. It is stored here as translation
# target metadata only; application files are not modified by this pipeline.
TARGET_CATALOGUES = [
    ("DE_BERLIN", "DE", "Berlin", "de", "EUR"),
    ("DE_MUNICH", "DE", "Munich", "de", "EUR"),
    ("AT_VIENNA", "AT", "Vienna", "de", "EUR"),
    ("CH_ZURICH", "CH", "Zurich", "de", "CHF"),
    ("USA_USD", "US", "National (USD)", "en", "USD"),
    ("GB_LONDON", "GB", "London", "en", "GBP"),
    ("CA_TORONTO", "CA", "Toronto", "en", "CAD"),
    ("AU_SYDNEY", "AU", "Sydney", "en", "AUD"),
    ("IN_MUMBAI", "IN", "Mumbai", "en", "INR"),
    ("NG_LAGOS", "NG", "Lagos", "en", "NGN"),
    ("ZA_JOHANNESBURG", "ZA", "Johannesburg", "en", "ZAR"),
    ("KE_NAIROBI", "KE", "Nairobi", "en", "KES"),
    ("GH_ACCRA", "GH", "Accra", "en", "GHS"),
    ("UG_KAMPALA", "UG", "Kampala", "en", "UGX"),
    ("TZ_DARESSALAAM", "TZ", "Dar es Salaam", "en", "TZS"),
    ("FR_PARIS", "FR", "Paris", "fr", "EUR"),
    ("SN_DAKAR", "SN", "Dakar", "fr", "XOF"),
    ("CI_ABIDJAN", "CI", "Abidjan", "fr", "XOF"),
    ("CM_DOUALA", "CM", "Douala", "fr", "XAF"),
    ("ES_MADRID", "ES", "Madrid", "es", "EUR"),
    ("IT_ROME", "IT", "Rome", "it", "EUR"),
    ("PT_LISBON", "PT", "Lisbon", "pt", "EUR"),
    ("BR_SAOPAULO", "BR", "Sao Paulo", "pt", "BRL"),
    ("AO_LUANDA", "AO", "Luanda", "pt", "AOA"),
    ("MX_MEXICO", "MX", "Mexico City", "es", "MXN"),
    ("AR_BUENOSAIRES", "AR", "Buenos Aires", "es", "ARS"),
    ("RU_STPETERSBURG", "RU", "St. Petersburg", "ru", "RUB"),
    ("RU_MOSCOW", "RU", "Moscow", "ru", "RUB"),
    ("PL_WARSAW", "PL", "Warsaw", "pl", "PLN"),
    ("CZ_PRAGUE", "CZ", "Prague", "cs", "CZK"),
    ("RO_BUCHAREST", "RO", "Bucharest", "ro", "RON"),
    ("NL_AMSTERDAM", "NL", "Amsterdam", "nl", "EUR"),
    ("SV_STOCKHOLM", "SE", "Stockholm", "sv", "SEK"),
    ("ZH_CHINA", "CN", "National", "zh", "CNY"),
    ("JP_TOKYO", "JP", "Tokyo", "ja", "JPY"),
    ("KR_SEOUL", "KR", "Seoul", "ko", "KRW"),
    ("TR_NATIONAL", "TR", "National", "tr", "TRY"),
    ("AE_DUBAI", "AE", "Dubai", "ar", "AED"),
    ("MA_CASABLANCA", "MA", "Casablanca", "ar", "MAD"),
    ("EG_CAIRO", "EG", "Cairo", "ar", "EGP"),
    ("TN_TUNIS", "TN", "Tunis", "ar", "TND"),
    ("ID_JAKARTA", "ID", "Jakarta", "id", "IDR"),
    ("MN_ULAANBAATAR", "MN", "Ulaanbaatar", "mn", "MNT"),
    ("BG_SOFIA", "BG", "Sofia", "bg", "BGN"),
    ("HR_ZAGREB", "HR", "Zagreb", "hr", "EUR"),
    ("NZ_AUCKLAND", "NZ", "Auckland", "en", "NZD"),
    ("TH_BANGKOK", "TH", "Bangkok", "th", "THB"),
    ("VI_HANOI", "VN", "Hanoi", "vi", "VND"),
]


def normalize_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = unicodedata.normalize("NFC", str(value))
    return " ".join(text.split()).strip()


def tm_key(source_lang: str, text: str) -> str:
    payload = f"{source_lang}\x1f{normalize_text(text)}".encode()
    return hashlib.sha256(payload).hexdigest()


def _load_classification(base_dir: Path) -> pd.DataFrame:
    path = base_dir / "WORLD_CWICR_classification_map.parquet"
    cols = [
        "region",
        "rate_code",
        "division",
        "trade_code",
        "trade_name",
        "nrm2_code",
        "nrm2_title",
        "din276_kg",
        "masterformat_div",
        "masterformat_title",
        "method",
    ]
    raw = pd.read_parquet(path, columns=cols)
    classification_fields = [c for c in cols if c not in {"region", "rate_code"}]
    conflicts = (
        raw.drop_duplicates(["region", "rate_code", *classification_fields]).groupby(["region", "rate_code"]).size()
    )
    conflicts = conflicts[conflicts.gt(1)]
    if not conflicts.empty:
        sample = [
            {"region": r, "rate_code": code, "variants": int(count)} for (r, code), count in conflicts.head(20).items()
        ]
        raise ValueError(f"Conflicting classification rows: {sample}")
    return raw.drop_duplicates(["region", "rate_code"], keep="first")


def build_corpus(base_dir: Path) -> tuple[pd.DataFrame, dict]:
    classification = _load_classification(base_dir)
    rows_by_key: dict[str, dict] = {}
    contexts: dict[str, list[dict]] = defaultdict(list)
    manifest = {
        "source_bases": {},
        "text_columns": TEXT_COLUMNS,
        "target_catalogues": [
            {
                "region": r,
                "country_iso": c,
                "city": city,
                "language": lang,
                "currency": cur,
            }
            for r, c, city, lang, cur in TARGET_CATALOGUES
        ],
        "target_languages": sorted({lang for _, _, _, lang, _ in TARGET_CATALOGUES}),
    }

    for path in sorted(base_dir.glob("*_workitems_costs_resources_DDC_CWICR.parquet")):
        region = path.name.split("_workitems", 1)[0]
        source_lang = SOURCE_LANG_BY_REGION[region]
        schema_cols = pq.read_schema(path).names
        requested_cols = [
            "rate_code",
            "resource_code",
            "row_type",
            "is_scope",
            "rate_original_name",
            "resource_name",
            "rate_unit",
            "resource_unit",
            *TEXT_COLUMNS,
        ]
        needed_cols = []
        for col in requested_cols:
            if col in schema_cols and col not in needed_cols:
                needed_cols.append(col)
        df = pd.read_parquet(path, columns=needed_cols)
        source_rows_before_merge = int(len(df))
        df["region"] = region
        if "is_scope" in df.columns:
            context_rate = df["rate_code"].where(df["is_scope"].fillna(False)).ffill()
        else:
            context_rate = df["rate_code"]
        df["context_rate_code"] = context_rate
        cls = classification[classification["region"].eq(region)].rename(columns={"rate_code": "context_rate_code"})
        df = df.merge(cls, on=["region", "context_rate_code"], how="left")
        source_rows_after_merge = int(len(df))
        leading_null_context_count = int(df["context_rate_code"].isna().sum())
        classification_missing_count = int(df["trade_code"].isna().sum())
        if source_rows_after_merge != source_rows_before_merge:
            raise ValueError(
                f"Classification join changed row count for {region}: "
                f"{source_rows_before_merge} -> {source_rows_after_merge}"
            )
        if leading_null_context_count or classification_missing_count:
            raise ValueError(
                f"Classification context failed for {region}: "
                f"context_missing={leading_null_context_count}, classification_missing={classification_missing_count}"
            )
        manifest["source_bases"][region] = {
            "file": path.name,
            "source_lang": source_lang,
            "rows": source_rows_before_merge,
            "rows_after_classification_join": source_rows_after_merge,
            "leading_null_context_count": leading_null_context_count,
            "classification_missing_count": classification_missing_count,
            "columns": int(len(schema_cols)),
        }

        example_cols = [
            c
            for c in [
                "rate_code",
                "context_rate_code",
                "resource_code",
                "row_type",
                "division",
                "trade_code",
                "trade_name",
                "nrm2_code",
                "din276_kg",
                "masterformat_div",
            ]
            if c in df.columns
        ]

        for column in [c for c in TEXT_COLUMNS if c in df.columns]:
            local_example_cols = [c for c in example_cols if c != column]
            temp = df[[column, *local_example_cols]].copy()
            temp["_text"] = temp[column].map(normalize_text)
            temp = temp[temp["_text"].ne("")]
            if temp.empty:
                continue

            counts = temp["_text"].value_counts()
            example_rows = temp.drop_duplicates("_text", keep="first").set_index("_text")
            examples_by_text = {
                text_value: [row.fillna("").astype(str).to_dict()]
                for text_value, row in example_rows[local_example_cols].iterrows()
            }

            for text, count in counts.items():
                key = tm_key(source_lang, text)
                if key not in rows_by_key:
                    rows_by_key[key] = {
                        "tm_key": key,
                        "source_lang": source_lang,
                        "source_text": text,
                        "source_text_len": len(text),
                    }
                contexts[key].append(
                    {
                        "region": region,
                        "column": column,
                        "count": int(count),
                        "examples": examples_by_text.get(text, []),
                    }
                )

    records = []
    for key, row in rows_by_key.items():
        ctx = contexts[key]
        row = dict(row)
        row["total_occurrences"] = int(sum(c["count"] for c in ctx))
        row["regions"] = ",".join(sorted({c["region"] for c in ctx}))
        row["columns"] = ",".join(sorted({c["column"] for c in ctx}))
        row["contexts_json"] = json.dumps(ctx, ensure_ascii=False, sort_keys=True)
        records.append(row)
    corpus = pd.DataFrame(records).sort_values(
        ["source_lang", "total_occurrences", "source_text"],
        ascending=[True, False, True],
    )
    manifest["translation_units"] = int(len(corpus))
    manifest["translation_units_by_source_lang"] = corpus.groupby("source_lang").size().to_dict()
    return corpus, manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=PIPELINE_DIR / "outputs")
    parser.add_argument(
        "--base-dir",
        type=Path,
        help="cost base directory; defaults to $" + BASE_DIR_ENV,
    )
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    corpus, manifest = build_corpus(resolve_base_dir(args.base_dir))
    corpus.to_parquet(args.out_dir / "translation_corpus.parquet", index=False)
    corpus.to_csv(args.out_dir / "translation_corpus.csv", index=False, encoding="utf-8-sig")
    (args.out_dir / "translation_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"wrote {len(corpus):,} translation units to {args.out_dir}")


if __name__ == "__main__":
    main()
