from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path

import pandas as pd
from extract_translation_corpus import (
    BASE_DIR_ENV,
    SOURCE_LANG_BY_REGION,
    TEXT_COLUMNS,
    resolve_base_dir,
    tm_key,
)
from qa_translation_table import read_table, run_qa, validate_materialized_frame
from unit_localization import apply_unit_localization

PIPELINE_DIR = Path(__file__).resolve().parent
DEFAULT_CORPUS = PIPELINE_DIR / "outputs" / "translation_corpus.parquet"
# No module-level default: it would have to be derived from a base directory this module is
# no longer allowed to guess. --out-dir now defaults relative to the resolved base instead.
DEFAULT_PPP_MULTIPLIERS = PIPELINE_DIR / "outputs" / "worldbank_ppp_multipliers.parquet"

APPROVED_STATUSES = {"approved", "reviewed"}
# Columns that must keep the source-language text verbatim in every localized output.
# `rate_original_name` is the position's name in the language of the national cost book it
# came from, and it is what lets a user in any locale trace a row back to that book. It
# appears in TEXT_COLUMNS because the corpus extractor harvests it for context, but
# translating it here overwrites the native text with the target language, after which the
# source text is no longer recoverable from the published files.
# This set is about ordinary translations, where every other text column is meant to change.
# The native edition (target_lang == source_lang) is a separate rule handled in
# materialize_region, and it preserves all of TEXT_COLUMNS. Do not merge the two by adding
# columns here: that would freeze them in the 25 genuine translations as well.
PRESERVE_SOURCE_COLUMNS = frozenset({"rate_original_name"})
MONEY_NAME_TOKENS = (
    "cost",
    "price",
    "value",
    "wage",
    "wages",
    "amount",
    "subtotal",
    "rate",
)
NON_MONEY_NAME_TOKENS = (
    "quantity",
    "code",
    "year",
    "date",
    "ratio",
    "factor",
    "index",
    "percent",
    "percentage",
    "mass",
    "weight",
)


def normalize_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return " ".join(unicodedata.normalize("NFC", str(value)).split()).strip()


def build_translation_map(translations: pd.DataFrame, target_lang: str) -> dict[str, str]:
    subset = translations[
        translations["target_lang"].astype(str).str.strip().eq(target_lang)
        & translations["status"].astype(str).str.strip().isin(APPROVED_STATUSES)
    ].copy()
    if subset.empty:
        raise ValueError(f"No approved translations for target_lang={target_lang}")
    if subset.duplicated(["tm_key", "target_lang"]).any():
        raise ValueError(f"Duplicate translations for target_lang={target_lang}")
    return dict(zip(subset["tm_key"], subset["target_text"].map(normalize_text), strict=False))


def infer_money_columns(frame: pd.DataFrame) -> list[str]:
    columns: list[str] = []
    for column in frame.columns:
        lowered = column.lower()
        if any(token in lowered for token in NON_MONEY_NAME_TOKENS):
            continue
        if not any(token in lowered for token in MONEY_NAME_TOKENS):
            continue
        if pd.api.types.is_numeric_dtype(frame[column]):
            columns.append(column)
    return columns


def infer_unconverted_money_like_columns(frame: pd.DataFrame, converted_columns: list[str]) -> list[str]:
    converted = set(converted_columns)
    skipped: list[str] = []
    for column in frame.columns:
        if column in converted:
            continue
        lowered = column.lower()
        if any(token in lowered for token in NON_MONEY_NAME_TOKENS):
            continue
        if not any(token in lowered for token in MONEY_NAME_TOKENS):
            continue
        if pd.api.types.is_numeric_dtype(frame[column]):
            continue
        non_empty = frame[column].dropna().astype(str).str.strip().ne("").any()
        if non_empty:
            skipped.append(column)
    return skipped


def load_ppp_row(ppp_multipliers: Path, *, source_region: str, target_region: str) -> dict:
    table = read_table(ppp_multipliers)
    required = {"source_region", "target_catalogue_region", "price_multiplier"}
    missing = required - set(table.columns)
    if missing:
        raise ValueError(f"{ppp_multipliers} missing PPP columns: {sorted(missing)}")
    match = table[
        table["source_region"].astype(str).str.strip().eq(source_region)
        & table["target_catalogue_region"].astype(str).str.strip().eq(target_region)
    ]
    if len(match) != 1:
        raise ValueError(
            json.dumps(
                {
                    "error": "ppp_multiplier_not_unique",
                    "source_region": source_region,
                    "target_region": target_region,
                    "matches": int(len(match)),
                },
                ensure_ascii=False,
            )
        )
    return match.iloc[0].to_dict()


def apply_ppp_conversion(
    frame: pd.DataFrame,
    *,
    ppp_row: dict | None,
    money_columns: list[str] | None = None,
) -> tuple[pd.DataFrame, dict]:
    localized = frame.copy(deep=True)
    if not ppp_row:
        localized["ppp_conversion_basis"] = "not_applied"
        return localized, {
            "applied": False,
            "basis": "not_applied",
            "money_columns": [],
        }

    multiplier = float(ppp_row["price_multiplier"])
    columns = money_columns or infer_money_columns(localized)
    converted_columns: list[str] = []
    for column in columns:
        if column not in localized.columns:
            continue
        values = pd.to_numeric(localized[column], errors="coerce")
        mask = values.notna()
        if not bool(mask.any()):
            continue
        source_column = f"{column}_source_currency"
        if source_column not in localized.columns:
            localized[source_column] = localized[column]
        localized.loc[mask, column] = values[mask] * multiplier
        converted_columns.append(column)

    target_currency = str(ppp_row.get("target_currency", ""))
    if target_currency and "currency" in localized.columns:
        if "currency_source" not in localized.columns:
            localized["currency_source"] = localized["currency"]
        localized["currency"] = target_currency

    localized["ppp_conversion_basis"] = str(ppp_row.get("formula", "worldbank_ppp"))
    localized["ppp_price_multiplier"] = multiplier
    localized["ppp_source_currency"] = str(ppp_row.get("source_currency", ""))
    localized["ppp_target_currency"] = target_currency
    localized["ppp_target_region"] = str(ppp_row.get("target_catalogue_region", ""))
    localized["ppp_provider"] = str(ppp_row.get("ppp_provider", ""))
    localized["ppp_indicator"] = str(ppp_row.get("ppp_indicator", ""))
    localized["ppp_source_year"] = ppp_row.get("source_ppp_year")
    localized["ppp_target_year"] = ppp_row.get("target_ppp_year")

    return localized, {
        "applied": True,
        "basis": localized["ppp_conversion_basis"].iloc[0] if len(localized) else str(ppp_row.get("formula", "")),
        "price_multiplier": multiplier,
        "source_currency": str(ppp_row.get("source_currency", "")),
        "target_currency": str(ppp_row.get("target_currency", "")),
        "target_region": str(ppp_row.get("target_catalogue_region", "")),
        "provider": str(ppp_row.get("ppp_provider", "")),
        "indicator": str(ppp_row.get("ppp_indicator", "")),
        "converted_columns": converted_columns,
        "skipped_money_like_columns": infer_unconverted_money_like_columns(localized, converted_columns),
    }


def _approved_transform_columns(localized: pd.DataFrame, source: pd.DataFrame) -> set[str]:
    approved = {
        "locale_code",
        "language",
        "translation_model",
        "translation_tm_key",
        "translation_status",
        "translation_issue_code",
        "translation_review_status",
        "locale_language",
        "translation_source_lang",
        "translation_status_summary",
        "ppp_conversion_basis",
        "ppp_price_multiplier",
        "ppp_source_currency",
        "ppp_target_currency",
        "ppp_target_region",
        "ppp_provider",
        "ppp_indicator",
        "ppp_source_year",
        "ppp_target_year",
        "currency_source",
        "unit_system",
        "unit_conversion_basis",
    }
    for column in localized.columns:
        if column.endswith("_source_currency") or column.endswith("_metric"):
            approved.add(column)
    return approved - set(source.columns)


def validate_resource_price_cost_relationship(
    frame: pd.DataFrame, *, abs_floor: float = 0.01, rel_tol: float = 0.005
) -> dict:
    """Resource reconciliation: resource_cost == resource_quantity * unit price.

    Tolerance is relative (max(abs_floor, rel_tol * |cost|)) so the check stays
    honest across currencies. A fixed absolute epsilon flags large-currency
    catalogues (RUB, IDR) on ordinary rounding while missing real drift in small
    ones; the relative band tracks the magnitude of each row.
    """
    required = {"resource_quantity", "resource_price_per_unit_current", "resource_cost"}
    if not required.issubset(frame.columns):
        return {"ok": True, "checked_rows": 0, "failed_rows": 0, "max_abs_delta": 0.0}
    quantity = pd.to_numeric(frame["resource_quantity"], errors="coerce")
    price = pd.to_numeric(frame["resource_price_per_unit_current"], errors="coerce")
    cost = pd.to_numeric(frame["resource_cost"], errors="coerce")
    mask = quantity.notna() & price.notna() & cost.notna()
    if not bool(mask.any()):
        return {"ok": True, "checked_rows": 0, "failed_rows": 0, "max_abs_delta": 0.0}
    delta = (quantity[mask] * price[mask] - cost[mask]).abs()
    tol = (rel_tol * cost[mask].abs()).clip(lower=abs_floor)
    failed = int((delta > tol).sum())
    max_delta = float(delta.max()) if not delta.empty else 0.0
    return {
        "ok": failed == 0,
        "checked_rows": int(mask.sum()),
        "failed_rows": failed,
        "max_abs_delta": max_delta,
    }


def validate_position_cost_reconciliation(
    frame: pd.DataFrame, *, abs_floor: float = 0.02, rel_tol: float = 0.005
) -> dict:
    """Position reconciliation: total_resource_cost_per_position == sum(resource_cost).

    Groups rows by rate_code and compares the position total against the sum of its
    resource rows. This is the law an estimator relies on when a position is expanded
    into its resource breakdown, and it must hold in both metric and imperial units.
    """
    if "rate_code" not in frame.columns or "total_resource_cost_per_position" not in frame.columns:
        return {
            "ok": True,
            "checked_positions": 0,
            "failed_positions": 0,
            "max_abs_delta": 0.0,
        }
    work = frame[["rate_code"]].copy()
    work["_pt"] = pd.to_numeric(frame["total_resource_cost_per_position"], errors="coerce")
    work["_rc"] = pd.to_numeric(frame.get("resource_cost"), errors="coerce")
    grouped = work.groupby("rate_code")
    pos_total = grouped["_pt"].max()
    sum_res = grouped["_rc"].sum(min_count=1)
    n_res = grouped["_rc"].count()
    recon = pos_total.notna() & (n_res > 0)
    if not bool(recon.any()):
        return {
            "ok": True,
            "checked_positions": 0,
            "failed_positions": 0,
            "max_abs_delta": 0.0,
        }
    delta = (pos_total[recon] - sum_res[recon]).abs()
    tol = (rel_tol * pos_total[recon].abs()).clip(lower=abs_floor)
    failed = int((delta > tol).sum())
    max_delta = float(delta.max()) if not delta.empty else 0.0
    return {
        "ok": failed == 0,
        "checked_positions": int(recon.sum()),
        "failed_positions": failed,
        "max_abs_delta": max_delta,
    }


_PROTECTED_WRAP = re.compile(r"<protected_token>(.*?)</protected_token>", re.DOTALL)
_PROTECTED_EMPTY = re.compile(r"<protected_token>\s*</protected_token>")
_PROTECTED_RESIDUE = re.compile(r"protected_token|\{[a-zA-Z_]+\[")


def strip_pipeline_artifacts(text: str | None) -> str | None:
    """Remove translation-pipeline placeholder artifacts from a translated string.

    During translation, spans that must survive verbatim (numbers, units, codes) were
    wrapped as ``<protected_token>value</protected_token>``. When a wrapper leaks into
    the output we strip the tags and keep the inner value, recovering the translation.
    If the value was lost (an empty wrapper, an unbalanced tag, or an unrestored
    ``{protected_tokens[..]}`` substitution) the cell is unusable, so we return None and
    let the caller fall back to source text rather than ship the artifact.
    """
    if not text:
        return text
    if _PROTECTED_EMPTY.search(text):
        return None
    cleaned = _PROTECTED_WRAP.sub(lambda m: m.group(1), text)
    if _PROTECTED_RESIDUE.search(cleaned):
        return None
    return cleaned


def materialize_region(
    *,
    source_path: Path,
    region: str,
    target_lang: str,
    translation_by_key: dict[str, str],
    # Required, not defaulted. A caller that omits it would still write a structurally valid
    # QA sidecar, one that answers "which table was this resolved against" with null -- and a
    # provenance file that passes inspection while recording nothing is worse than no file at
    # all. That is the shape of the failure this sidecar exists to catch.
    translations_path: Path,
    out_dir: Path,
    target_region: str | None = None,
    unit_system: str | None = None,
    ppp_row: dict | None = None,
    allow_missing: bool = False,
) -> Path:
    source_lang = SOURCE_LANG_BY_REGION[region]
    df = pd.read_parquet(source_path)
    localized = df.copy(deep=True)
    status_counts = {
        "translated": 0,
        "empty_source": 0,
        "missing_translation": 0,
        # Recorded so the artifact can answer "was this a native edition?" on its own. An
        # all-zero summary otherwise reads the same as a run that found nothing to do.
        "identity_edition": False,
    }
    missing_examples: list[dict] = []

    # The native edition of a base -- the one whose target language IS the language the base
    # was written in -- is the single case where the correct behaviour is to translate
    # nothing. Its text is already in the target language, so a lookup keyed on that same
    # language can only return one of two things: the identical string, or a corruption. It
    # returned corruptions. The Turkish edition of the Turkish base shipped with
    # `Duz isci` rewritten to `General laborer` because the run resolved against a map that
    # answered tr source text with English, and every content column went through it.
    #
    # This is deliberately a branch on target_lang == source_lang and NOT a wider
    # PRESERVE_SOURCE_COLUMNS. The two are different rules: PRESERVE_SOURCE_COLUMNS names a
    # column that must survive in EVERY locale, and widening it would freeze columns in the
    # genuine translations, which is where translating is the whole point.
    #
    # KNOWN GAP, and it is not closed by this branch. `row_type` and `category_type` are
    # controlled columns: a closed vocabulary that the Turkish base stores as our own English
    # canon (`Labour`, `Machinery`, `CONSTRUCTION WORK`), not as national text. For a native
    # edition the correct value there is therefore a TRANSLATION, the opposite of the rule
    # above, and preserving them yields English. That is the column an estimator reads first
    # when checking a breakdown, which is precisely what the Turkish user reported.
    #
    # The comparison that matters is against the published file, not against what an unfixed
    # run would emit. Published TR_tr carries `İşçilik`/`Makine`/`Malzeme` and differs from
    # base on 100% of `row_type` rows and 109,795 of 110,061 `category_type` rows. So any
    # regeneration loses them, with or without this branch, and no table in outputs/ can put
    # them back: tm_key("tr", "Labour") and tm_key("en", "Labour") both miss, and the Turkish
    # values that do exist in the tr map are keyed from vi/el/es/id sources. The only correct
    # canon-to-Turkish mapping we hold is inside the published artifact itself.
    #
    # Two consequences. Repairs must stay column copies onto the published file, never full
    # regenerations. And localizing controlled columns properly means routing them through
    # their canonical key (see extract_controlled_values.ROW_TYPE_ALIASES) rather than a
    # free-text tm_key, plus seeding en->target entries that do not exist today.
    identity_edition = target_lang == source_lang
    preserved = frozenset(TEXT_COLUMNS) if identity_edition else PRESERVE_SOURCE_COLUMNS
    status_counts["identity_edition"] = identity_edition

    # Resolve each UNIQUE source value once (normalize + sha256 tm_key + dict lookup are
    # the costly ops), cache across columns, then apply with a vectorized Series.map. The
    # old per-cell loop hashed all ~1.7M cells/source; low-cardinality columns (units,
    # categories) collapse to a handful of uniques, giving the same result far faster.
    key_cache: dict[str, str | None] = {}  # normalized source text -> target (None if no translation)
    for column in [c for c in TEXT_COLUMNS if c in localized.columns and c not in preserved]:
        col = df[column]
        resolved: dict = {}  # raw source value -> target text (only where a translation exists)
        for raw_value, count in col.value_counts(dropna=False).items():
            n = int(count)
            if raw_value is None or (isinstance(raw_value, float) and pd.isna(raw_value)):
                status_counts["empty_source"] += n
                continue
            src_text = normalize_text(raw_value)
            if not src_text:
                status_counts["empty_source"] += n
                continue
            if src_text not in key_cache:
                key_cache[src_text] = strip_pipeline_artifacts(translation_by_key.get(tm_key(source_lang, src_text)))
            target_text = key_cache[src_text]
            if target_text:
                resolved[raw_value] = target_text
                status_counts["translated"] += n
            else:
                status_counts["missing_translation"] += n
                if len(missing_examples) < 50:
                    missing_examples.append({"column": column, "source_text": src_text[:240]})
        if resolved:
            mapped = col.map(resolved)
            localized[column] = mapped.where(mapped.notna(), col)
        else:
            localized[column] = col

    localized["locale_language"] = target_lang
    localized["translation_source_lang"] = source_lang
    localized["translation_status_summary"] = json.dumps(status_counts, sort_keys=True)

    if status_counts["missing_translation"] and not allow_missing:
        raise ValueError(
            json.dumps(
                {
                    "error": "missing_approved_translation",
                    "region": region,
                    "target_lang": target_lang,
                    "missing_count": status_counts["missing_translation"],
                    "examples": missing_examples,
                },
                ensure_ascii=False,
            )
        )
    # allow_missing: the few hard cells (e.g. low-resource mn that nano can't produce)
    # stay as their source text and are counted in translation_status_summary for a
    # later dedicated pass - the catalogue still materializes rather than blocking.

    invariant_report = validate_materialized_frame(df, localized)
    if not invariant_report["ok"]:
        raise ValueError(f"Materialized frame invariant failure for {region}/{target_lang}: {invariant_report}")

    localized, ppp_report = apply_ppp_conversion(localized, ppp_row=ppp_row)

    localized, unit_report = apply_unit_localization(
        localized,
        target_region=target_region,
        target_lang=target_lang,
        unit_system=unit_system,
    )
    # The two laws an estimate is built on, checked after every currency and unit
    # transform: each resource row multiplies out (qty * price == cost) and each
    # position sums back from its resources. Both must hold in metric and imperial.
    price_cost_relationship = validate_resource_price_cost_relationship(localized)
    position_reconciliation = validate_position_cost_reconciliation(localized)
    final_invariant_report = validate_materialized_frame(
        df,
        localized,
        approved_added_columns=_approved_transform_columns(localized, df),
    )
    if not final_invariant_report["ok"]:
        raise ValueError(
            f"Final materialized frame invariant failure for {region}/{target_lang}: {final_invariant_report}"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    # Key the catalogue by target economy so the 48 economies that share a language
    # (Berlin/Vienna/Zurich all 'de') each keep their own PPP-priced, unit-localized
    # output instead of overwriting one another. Falls back to lang when no economy.
    econ = target_region or target_lang
    out_path = out_dir / econ / region / f"{econ}_{region}_{target_lang}_DDC_CWICR.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    localized.to_parquet(out_path, index=False)
    report_path = out_path.with_suffix(".qa.json")
    report_path.write_text(
        json.dumps(
            {
                "region": region,
                "target_lang": target_lang,
                "source_file": source_path.name,
                "rows": int(len(localized)),
                # Which table the text actually resolved against, and how big it was. The
                # three provenance columns cannot answer this: on the broken Turkish edition
                # locale_language and translation_source_lang both read `tr` and both were
                # correct, while the text had in fact been resolved against the English
                # table, which carried 35,957 rows of tr source text mapped to English. That
                # had to be reconstructed by reading the tables. Recorded here rather than as
                # a column so the published 95-column schema is unaffected.
                "translation_map": {
                    "path": str(translations_path),
                    "entries": len(translation_by_key),
                },
                "status_counts": status_counts,
                "ppp": ppp_report,
                "unit_localization": unit_report,
                "resource_price_cost_relationship": price_cost_relationship,
                "position_cost_reconciliation": position_reconciliation,
                "invariants": invariant_report,
                "final_invariants": final_invariant_report,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--translations", type=Path, required=True)
    parser.add_argument("--target-lang", required=True)
    parser.add_argument("--target-region")
    parser.add_argument("--unit-system", choices=["metric", "us_customary"])
    parser.add_argument("--ppp-multipliers", type=Path)
    parser.add_argument("--region", action="append", dest="regions")
    parser.add_argument("--out-dir", type=Path, help="defaults to <base-dir>/translated_outputs")
    parser.add_argument(
        "--base-dir",
        type=Path,
        help="cost base directory; defaults to $" + BASE_DIR_ENV,
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="emit flagged source-fallback for hard cells (and skip the hard QA gate) instead of blocking the catalogue",
    )
    args = parser.parse_args()

    base_dir = resolve_base_dir(args.base_dir)
    out_dir = args.out_dir or base_dir / "translated_outputs"

    translations = read_table(args.translations)
    # run_qa is O(corpus x translations) and pathologically slow (~minutes) on the full
    # 131k x 120k frames; under --allow-missing its verdict is only advisory (the hard
    # gate is disabled), so skip computing it entirely - the assembler already applied
    # the QA policy upstream. Only pay the cost when the strict gate is actually wanted.
    if not args.allow_missing:
        corpus = pd.read_parquet(args.corpus)
        qa_report = run_qa(
            corpus,
            translations[translations["target_lang"].astype(str).eq(args.target_lang)],
        )
        if not qa_report["ok"]:
            raise SystemExit(json.dumps(qa_report, ensure_ascii=False, indent=2))

    translation_by_key = build_translation_map(translations, args.target_lang)
    regions = args.regions or sorted(SOURCE_LANG_BY_REGION)
    outputs = []
    for region in regions:
        source_path = base_dir / f"{region}_workitems_costs_resources_DDC_CWICR.parquet"
        ppp_row = None
        if args.ppp_multipliers:
            if not args.target_region:
                raise SystemExit("--target-region is required when --ppp-multipliers is used")
            ppp_row = load_ppp_row(
                args.ppp_multipliers,
                source_region=region,
                target_region=args.target_region,
            )
        outputs.append(
            str(
                materialize_region(
                    source_path=source_path,
                    region=region,
                    target_lang=args.target_lang,
                    translation_by_key=translation_by_key,
                    out_dir=out_dir,
                    target_region=args.target_region,
                    unit_system=args.unit_system,
                    ppp_row=ppp_row,
                    allow_missing=args.allow_missing,
                    translations_path=args.translations,
                )
            )
        )
    print(json.dumps({"outputs": outputs}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
