from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

import pandas as pd
from qa_translation_table import protected_tokens

PIPELINE_DIR = Path(__file__).resolve().parent
DEFAULT_CORPUS = PIPELINE_DIR / "outputs" / "translation_corpus.parquet"
DEFAULT_OUT = PIPELINE_DIR / "outputs" / "llm_batches"
TERMBASE_PATH = PIPELINE_DIR / "termbase_seed.json"

SOURCE_LANGUAGE_NOTES = {
    "tr": "Translate from Turkish. The English pivot in source data is not reliable for TR.",
    "zh": "Translate from Chinese or approved English resource names only where native is blank; keep terse catalogue style.",
    "el": "Translate Greek where present. Code-only Greek tariff rows must stay code-like and be flagged if semantic title is absent.",
    "vi": "Translate from Vietnamese. Preserve grades such as 3,0/7 and compound bases such as 100m3/1km exactly.",
    "id": "Translate from Indonesian. Preserve AHSP method terms and labour/equipment abbreviations.",
    "pt": "Translate from Portuguese construction terms; preserve SINAPI codes, dimensions, and CHP/CHI machine-hour states.",
    "es": "Translate from Spanish construction terms; preserve BCCA code fragments, dimensions, and negative-credit semantics.",
    "it": "Translate from Italian construction terms; preserve Toscana 'Oggetto/Compreso/Escluso' structure faithfully.",
}

PROMPT_TEMPLATE = """You are translating construction cost catalogue strings for CWICR.

Rules:
- Translate from the source language, not from a rough English pivot.
- Preserve every protected token byte-identically: numbers, dimensions, codes, grades, standards, units inside names.
- Do not add facts. If the source is code-only or untranslatable, return an empty target_text and set status=human_review_required.
- Use construction-domain terminology and the supplied trade context.
- Keep stored digits Western-Arabic. Local digit shaping is display-only.
- Return strict JSONL, one output object for each input object.

Output fields:
tm_key, target_lang, target_text, status, confidence, protected_tokens_preserved,
terminology_notes, review_reason
"""


def record_for_batch(row: pd.Series, target_lang: str) -> dict:
    contexts = json.loads(row["contexts_json"])
    first_context = contexts[0] if contexts else {}
    examples = first_context.get("examples", [])
    example = examples[0] if examples else {}
    columns = row["columns"].split(",") if isinstance(row["columns"], str) else []
    string_kind = classify_string_kind(row["columns"])
    matched_terms = match_terms(row["source_lang"], target_lang, row["source_text"])

    return {
        "custom_id": f"tm:{row['tm_key'][:12]}:{target_lang}:v1",
        "task": "cwicr_translate_tm_unit",
        "schema_version": "cwicr-translation-batch-v1",
        "tm_key": row["tm_key"],
        "source_lang": row["source_lang"],
        "target_lang": target_lang,
        "source_text": row["source_text"],
        "english_pivot": "",
        "string_kind": string_kind,
        "source_language_note": SOURCE_LANGUAGE_NOTES.get(row["source_lang"], ""),
        "columns_seen": columns,
        "regions": row["regions"].split(",") if isinstance(row["regions"], str) else [],
        "occurrence_count": int(row["total_occurrences"]),
        "context": {
            "trade_code": example.get("trade_code", ""),
            "trade_name": example.get("trade_name", ""),
            "division": example.get("division", ""),
            "nrm2_code": example.get("nrm2_code", ""),
            "din276_kg": example.get("din276_kg", ""),
            "masterformat_div": example.get("masterformat_div", ""),
            "example_rate_codes": [example.get("rate_code", "")] if example.get("rate_code", "") else [],
            "example_resource_codes": [example.get("resource_code", "")] if example.get("resource_code", "") else [],
            "row_types_seen": [example.get("row_type", "")] if example.get("row_type", "") else [],
        },
        "protected_tokens": protected_tokens(row["source_text"]),
        "matched_terms": matched_terms,
        "controlled_mapping": None,
        "warnings": [SOURCE_LANGUAGE_NOTES.get(row["source_lang"], "")],
        "output_requirements": {
            "preserve_tokens": True,
            "western_digits": True,
            "no_unit_conversion": True,
            "empty_source_empty_target": True,
            "return_json_only": True,
        },
    }


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9_=-]+", "_", value.lower()).strip("_")


def load_termbase() -> list[dict]:
    if not TERMBASE_PATH.exists():
        return []
    return json.loads(TERMBASE_PATH.read_text(encoding="utf-8"))


def match_terms(source_lang: str, target_lang: str, source_text: str) -> list[dict]:
    matches = []
    for term in load_termbase():
        if term.get("audit_status") != "approved":
            continue
        if term.get("source_lang") != source_lang or term.get("target_lang") != target_lang:
            continue
        if term.get("source_term", "") in source_text:
            matches.append(
                {
                    "term_id": term["term_id"],
                    "source_term": term["source_term"],
                    "approved_target_term": term["approved_target_term"],
                    "required": bool(term.get("required", True)),
                }
            )
    return matches


def classify_string_kind(columns_value: object) -> str:
    columns = columns_value.split(",") if isinstance(columns_value, str) else []
    if "rate_final_name" in columns:
        return "pivot_reference"
    if any(c in {"row_type", "nrm2_section_title"} for c in columns):
        return "controlled_enum"
    if any(c.endswith("_unit") or c == "rate_unit_copy" for c in columns):
        return "unit_label"
    return "free_text"


def build_batches(
    corpus: pd.DataFrame,
    target_langs: list[str],
    out_dir: Path,
    batch_size: int,
    *,
    include_controlled: bool = False,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    corpus = corpus.copy()
    corpus["string_kind"] = corpus["columns"].map(classify_string_kind)
    excluded_non_free = int(corpus["string_kind"].ne("free_text").sum())
    if not include_controlled:
        corpus = corpus[corpus["string_kind"].eq("free_text")]
    manifest = {
        "prompt_template": PROMPT_TEMPLATE,
        "batch_size": batch_size,
        "include_controlled": include_controlled,
        "excluded_non_free_records": 0 if include_controlled else excluded_non_free,
        "batches": [],
    }
    for target_lang in target_langs:
        for source_lang, group in corpus.groupby("source_lang", sort=True):
            # Same-language rows still need review for controlled terminology, but can be
            # skipped by callers that want only cross-language translation.
            records = [record_for_batch(row, target_lang) for _, row in group.iterrows()]
            total_batches = math.ceil(len(records) / batch_size)
            for idx in range(total_batches):
                chunk = records[idx * batch_size : (idx + 1) * batch_size]
                name = f"{slug(source_lang)}__to__{slug(target_lang)}__{idx + 1:04d}.jsonl"
                path = out_dir / name
                with path.open("w", encoding="utf-8", newline="\n") as fh:
                    for record in chunk:
                        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                manifest["batches"].append(
                    {
                        "file": name,
                        "source_lang": source_lang,
                        "target_lang": target_lang,
                        "records": len(chunk),
                    }
                )
    (out_dir / "batch_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--target-lang", action="append", dest="target_langs")
    parser.add_argument("--source-lang", action="append", dest="source_langs")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--limit", type=int, default=0, help="Debug limit per source language.")
    parser.add_argument("--include-controlled", action="store_true")
    args = parser.parse_args()

    corpus = pd.read_parquet(args.corpus)
    if args.source_langs:
        corpus = corpus[corpus["source_lang"].isin(args.source_langs)]
    if args.limit:
        corpus = corpus.groupby("source_lang", group_keys=False).head(args.limit)
    target_langs = args.target_langs or sorted(
        json.loads((PIPELINE_DIR / "outputs" / "translation_manifest.json").read_text(encoding="utf-8"))[
            "target_languages"
        ]
    )
    manifest = build_batches(
        corpus,
        target_langs,
        args.out_dir,
        args.batch_size,
        include_controlled=args.include_controlled,
    )
    print(f"wrote {len(manifest['batches'])} batch files to {args.out_dir}")


if __name__ == "__main__":
    main()
