from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path

import pandas as pd

REQUIRED_TRANSLATION_COLUMNS = {
    "tm_key",
    "target_lang",
    "target_text",
    "status",
}

TARGET_LANGS = {
    "ar",
    "bg",
    "cs",
    "de",
    "en",
    "es",
    "fr",
    "hr",
    "id",
    "it",
    "ja",
    "ko",
    "mn",
    "nl",
    "pl",
    "pt",
    "ro",
    "ru",
    "sv",
    "th",
    "tr",
    "vi",
    "zh",
}
ACCEPTED_QA_STATUSES = {"reviewed", "approved"}

PIPELINE_DIR = Path(__file__).resolve().parent

NON_LATIN_TARGETS = {"ar", "bg", "ja", "ko", "mn", "ru", "th", "zh"}
RTL_TARGETS = {"ar"}

TOKEN_PATTERNS = [
    re.compile(r"\b[A-Z]{1,8}[_-]?\d[A-Z0-9./_-]*\b"),
    re.compile(
        r"\b\d+(?:[.,]\d+)?\s*(?:m2|m3|m²|m³|mm|cm|m|km|kg|t|kWh|HP)"
        r"\s*/\s*\d+(?:[.,]\d+)?\s*(?:m2|m3|m²|m³|mm|cm|m|km|kg|t|kWh|HP)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d+(?:[.,]\d+)?(?:\s*[xX×χΧ]\s*\d+(?:[.,]\d+)?)+(?:\s*(?:mm|cm|m|CM|MM|M))?\b"),
    re.compile(r"[ØøΦφ]\s*\d+(?:[.,]\d+)?"),
    re.compile(r"[≤≥±×]"),
    re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:m2|m3|m²|m³|mm|cm|m|km|kg|t|kWh|HP)\b", re.IGNORECASE),
    re.compile(r"\d+(?:[.,]\d+)?(?:/\d+(?:[.,]\d+)?)*"),
    re.compile(r"\b(?:DIN|ISO|FIDIC|GAEB|NRM|CLSM|HP|CNY|EUR|TRY|BRL|GGDE)\b"),
]

SCRIPT_RANGES = {
    "ar": re.compile(r"[\u0600-\u06ff]"),
    "bg": re.compile(r"[\u0400-\u04ff]"),
    "el": re.compile(r"[\u0370-\u03ff]"),
    "ja": re.compile(r"[\u3040-\u30ff\u3400-\u9fff]"),
    "ko": re.compile(r"[\uac00-\ud7af]"),
    "mn": re.compile(r"[\u1800-\u18af\u0400-\u04ff]"),
    "ru": re.compile(r"[\u0400-\u04ff]"),
    "th": re.compile(r"[\u0e00-\u0e7f]"),
    "zh": re.compile(r"[\u3400-\u9fff]"),
}

SOURCE_SCRIPT_BY_LANG = {
    "el": "el",
    "zh": "zh",
    "ru": "ru",
    "bg": "bg",
    "ar": "ar",
    "th": "th",
    "ja": "ja",
    "ko": "ko",
}


def normalize_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return " ".join(unicodedata.normalize("NFC", str(value)).split()).strip()


def protected_tokens(text: str) -> list[str]:
    normalized = normalize_text(text)
    matches: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []
    for pattern in TOKEN_PATTERNS:
        for match in pattern.finditer(normalized):
            start, end = match.span()
            if any(not (end <= used_start or start >= used_end) for used_start, used_end in occupied):
                continue
            occupied.append((start, end))
            matches.append((start, end, match.group(0)))
    return sorted(token for _, _, token in matches)


def load_approved_token_transforms() -> dict[tuple[str, str, str], str]:
    path = PIPELINE_DIR / "termbase_seed.json"
    if not path.exists():
        return {}
    terms = json.loads(path.read_text(encoding="utf-8"))
    transforms = {}
    for term in terms:
        if term.get("audit_status") != "approved":
            continue
        transforms[(term["source_lang"], term["target_lang"], term["source_term"])] = term["approved_target_term"]
    return transforms


def normalize_tokens_for_target(tokens: list[str], source_lang: str, target_lang: str) -> list[str]:
    transforms = load_approved_token_transforms()
    return sorted(canonical_token(transforms.get((source_lang, target_lang, token), token)) for token in tokens)


def canonical_token(token: str) -> str:
    return " ".join(token.replace("×", "x").replace("χ", "x").replace("Χ", "x").split())


def approved_terms_for(source_lang: str, target_lang: str) -> list[tuple[str, str]]:
    transforms = load_approved_token_transforms()
    return [
        (source, target)
        for (src_lang, tgt_lang, source), target in transforms.items()
        if src_lang == source_lang and tgt_lang == target_lang
    ]


def has_script(text: str, script: str) -> bool:
    pattern = SCRIPT_RANGES.get(script)
    return bool(pattern and pattern.search(text))


def read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() in {".csv", ".tsv"}:
        return pd.read_csv(path, sep="\t" if path.suffix.lower() == ".tsv" else ",")
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    raise ValueError(f"Unsupported table extension: {path}")


def run_qa(corpus: pd.DataFrame, translations: pd.DataFrame) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []

    missing_cols = REQUIRED_TRANSLATION_COLUMNS - set(translations.columns)
    if missing_cols:
        errors.append({"check": "schema", "missing_columns": sorted(missing_cols)})
        return {"ok": False, "errors": errors, "warnings": warnings}

    translations = translations.copy()
    translations["target_text"] = translations["target_text"].map(normalize_text)
    translations["target_lang"] = translations["target_lang"].astype(str).str.strip()
    translations["status"] = translations["status"].astype(str).str.strip()
    skipped_status_count = int((~translations["status"].isin(ACCEPTED_QA_STATUSES)).sum())
    translations_for_checks = translations[translations["status"].isin(ACCEPTED_QA_STATUSES)].copy()

    bad_langs = sorted(set(translations["target_lang"]) - TARGET_LANGS)
    if bad_langs:
        errors.append({"check": "target_lang", "invalid": bad_langs})

    dupes = translations_for_checks.groupby(["tm_key", "target_lang"]).size()
    dupes = dupes[dupes.gt(1)]
    for (tm_key, target_lang), count in dupes.head(100).items():
        errors.append(
            {
                "check": "tm_uniqueness",
                "tm_key": tm_key,
                "target_lang": target_lang,
                "count": int(count),
            }
        )

    merged = translations_for_checks.merge(
        corpus[["tm_key", "source_lang", "source_text"]],
        on="tm_key",
        how="left",
        suffixes=("_translation", ""),
        validate="many_to_one",
    )
    missing_source = merged[merged["source_text"].isna()]
    for row in missing_source.head(100).itertuples(index=False):
        errors.append(
            {
                "check": "unknown_tm_key",
                "tm_key": row.tm_key,
                "target_lang": row.target_lang,
            }
        )

    merged = merged[merged["source_text"].notna()]
    for row in merged.itertuples(index=False):
        src = normalize_text(row.source_text)
        tgt = normalize_text(row.target_text)
        if src and not tgt:
            errors.append(
                {
                    "check": "empty_target",
                    "tm_key": row.tm_key,
                    "target_lang": row.target_lang,
                }
            )
            continue

        src_tokens = normalize_tokens_for_target(protected_tokens(src), row.source_lang, row.target_lang)
        approved_targets_in_row = {
            canonical_token(target_term)
            for source_term, target_term in approved_terms_for(row.source_lang, row.target_lang)
            if source_term in src
        }
        tgt_tokens = sorted(
            canonical_token(token)
            for token in protected_tokens(tgt)
            if canonical_token(token) not in approved_targets_in_row
        )
        if src_tokens != tgt_tokens:
            errors.append(
                {
                    "check": "protected_tokens",
                    "tm_key": row.tm_key,
                    "target_lang": row.target_lang,
                    "source_tokens": src_tokens,
                    "target_tokens": tgt_tokens,
                    "source_text": src[:240],
                    "target_text": tgt[:240],
                }
            )

        for source_term, target_term in approved_terms_for(row.source_lang, row.target_lang):
            if source_term in src and target_term not in tgt:
                errors.append(
                    {
                        "check": "terminology",
                        "tm_key": row.tm_key,
                        "target_lang": row.target_lang,
                        "source_term": source_term,
                        "required_target_term": target_term,
                        "target_text": tgt[:240],
                    }
                )

        source_script = SOURCE_SCRIPT_BY_LANG.get(row.source_lang)
        # Han is shared by Chinese, Japanese (kanji) and Korean (hanja), so Han
        # characters in a ja/ko target are legitimate, not a zh source-script leak.
        leak_exempt = {source_script, "zh"}
        if source_script == "zh":
            leak_exempt |= {"ja", "ko"}
        if source_script and row.target_lang != row.source_lang and row.target_lang not in leak_exempt:
            if has_script(tgt, source_script):
                errors.append(
                    {
                        "check": "source_script_leak",
                        "tm_key": row.tm_key,
                        "source_lang": row.source_lang,
                        "target_lang": row.target_lang,
                        "target_text": tgt[:240],
                    }
                )

        if row.target_lang in NON_LATIN_TARGETS and row.target_lang not in RTL_TARGETS:
            # Non-Latin targets may contain Latin code tokens, but should not be mostly untranslated Latin prose.
            letters = [ch for ch in tgt if ch.isalpha()]
            latin_letters = [ch for ch in letters if "LATIN" in unicodedata.name(ch, "")]
            if len(letters) >= 12 and len(latin_letters) / max(len(letters), 1) > 0.65:
                warnings.append(
                    {
                        "check": "latin_heavy_non_latin_target",
                        "tm_key": row.tm_key,
                        "target_lang": row.target_lang,
                        "target_text": tgt[:240],
                    }
                )

    return {
        "ok": not errors,
        "summary": {
            "translation_rows": int(len(translations)),
            "checked_rows": int(len(translations_for_checks)),
            "skipped_status_rows": skipped_status_count,
            "error_count": int(len(errors)),
            "warning_count": int(len(warnings)),
        },
        "failed_tm_keys": sorted({error["tm_key"] for error in errors if "tm_key" in error}),
        "errors": errors[:1000],
        "warnings": warnings[:1000],
    }


def validate_materialized_frame(
    source: pd.DataFrame,
    localized: pd.DataFrame,
    *,
    approved_added_columns: set[str] | None = None,
) -> dict:
    """Validate row/schema invariants after translations are materialized.

    This deliberately checks data identity only. Language quality is checked on the
    translation table before materialization.
    """

    approved_added_columns = approved_added_columns or {
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
    }
    errors: list[dict] = []
    if len(source) != len(localized):
        errors.append(
            {
                "check": "row_count",
                "source": int(len(source)),
                "localized": int(len(localized)),
            }
        )

    source_cols = list(source.columns)
    localized_cols = list(localized.columns)
    unexpected = sorted(set(localized_cols) - set(source_cols) - approved_added_columns)
    missing = sorted(set(source_cols) - set(localized_cols))
    if unexpected:
        errors.append({"check": "unexpected_columns", "columns": unexpected})
    if missing:
        errors.append({"check": "missing_columns", "columns": missing})

    identity_cols = [
        c
        for c in [
            "rate_code",
            "resource_code",
            "is_scope",
            "is_abstract",
            "is_machine",
            "is_material",
            "is_labor",
            "source_institution",
            "source_year",
        ]
        if c in source.columns and c in localized.columns
    ]
    for col in identity_cols:
        left = source[col].reset_index(drop=True).astype("string").fillna("<NA>")
        right = localized[col].reset_index(drop=True).astype("string").fillna("<NA>")
        mismatches = left.ne(right)
        if mismatches.any():
            first_idx = int(mismatches[mismatches].index[0])
            errors.append(
                {
                    "check": "identity_column",
                    "column": col,
                    "mismatch_count": int(mismatches.sum()),
                    "first_index": first_idx,
                }
            )

    return {"ok": not errors, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--translations", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    corpus = read_table(args.corpus)
    translations = read_table(args.translations)
    report = run_qa(corpus, translations)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
