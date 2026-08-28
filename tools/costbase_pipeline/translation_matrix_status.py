from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from build_translation_batches import classify_string_kind

PIPELINE_DIR = Path(__file__).resolve().parent
DEFAULT_CORPUS = PIPELINE_DIR / "outputs" / "translation_corpus.parquet"
DEFAULT_MANIFEST = PIPELINE_DIR / "outputs" / "translation_manifest.json"
DEFAULT_OUT = PIPELINE_DIR / "outputs" / "translation_matrix_status.csv"
DEFAULT_PROGRESS_OUT = PIPELINE_DIR / "PROGRESS_STATUS.md"


def load_target_languages(path: Path) -> list[str]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return list(manifest["target_languages"])


def summarize_existing(outputs_dir: Path) -> dict[tuple[str, str], dict]:
    summary = {}
    for path in outputs_dir.glob("llm_batches_*_*/*_translations_quarantined.parquet"):
        if "partial" in path.name:
            continue
        stem = path.name.replace("_translations_quarantined.parquet", "")
        parts = stem.split("_")
        if len(parts) < 2:
            continue
        source_lang, target_lang = parts[0], parts[1]
        df = pd.read_parquet(path, columns=["status"])
        summary[(source_lang, target_lang)] = {
            "rows": int(len(df)),
            "accepted": int(df["status"].isin(["reviewed", "approved"]).sum()),
            "needs_review": int(df["status"].eq("needs_review").sum()),
            "artifact": str(path),
        }
    identity = outputs_dir / "identity_translations.parquet"
    if identity.exists():
        df = pd.read_parquet(identity, columns=["source_lang", "target_lang", "status"])
        for (source_lang, target_lang), group in df.groupby(["source_lang", "target_lang"]):
            summary[(source_lang, target_lang)] = {
                "rows": int(len(group)),
                "accepted": int(group["status"].isin(["reviewed", "approved"]).sum()),
                "needs_review": int(group["status"].eq("needs_review").sum()),
                "artifact": str(identity),
            }
    return summary


def build_matrix(corpus: pd.DataFrame, target_languages: list[str], outputs_dir: Path) -> pd.DataFrame:
    corpus = corpus.copy()
    corpus["kind"] = corpus["columns"].map(classify_string_kind)
    free_counts = corpus[corpus["kind"].eq("free_text")].groupby("source_lang").size().to_dict()
    existing = summarize_existing(outputs_dir)
    rows = []
    for source_lang in sorted(free_counts):
        expected = int(free_counts[source_lang])
        for target_lang in target_languages:
            current = existing.get((source_lang, target_lang))
            if current:
                accepted = current["accepted"]
                needs_review = current["needs_review"]
                status = "complete_green" if accepted >= expected or accepted + needs_review >= expected else "partial"
                artifact = current["artifact"]
            else:
                accepted = 0
                needs_review = 0
                status = "pending_api" if source_lang != target_lang else "pending_identity"
                artifact = ""
            rows.append(
                {
                    "source_lang": source_lang,
                    "target_lang": target_lang,
                    "expected_free_text": expected,
                    "accepted": accepted,
                    "needs_review": needs_review,
                    "missing": max(expected - accepted - needs_review, 0),
                    "status": status,
                    "artifact": artifact,
                }
            )
    return pd.DataFrame(rows)


def summarize_matrix(matrix: pd.DataFrame) -> dict:
    status_counts = matrix["status"].value_counts().to_dict()
    expected_total = int(matrix["expected_free_text"].sum())
    accepted_total = int(matrix["accepted"].sum())
    needs_review_total = int(matrix["needs_review"].sum())
    missing_total = int(matrix["missing"].sum())
    target_rows = []
    for target_lang, group in matrix.groupby("target_lang", sort=True):
        expected = int(group["expected_free_text"].sum())
        accepted = int(group["accepted"].sum())
        needs_review = int(group["needs_review"].sum())
        missing = int(group["missing"].sum())
        target_rows.append(
            {
                "target_lang": target_lang,
                "expected": expected,
                "accepted": accepted,
                "needs_review": needs_review,
                "missing": missing,
                "accepted_pct": (accepted / expected * 100) if expected else 0.0,
            }
        )
    target_rows.sort(key=lambda row: (-row["accepted"], row["target_lang"]))

    pending_api = matrix[matrix["status"].eq("pending_api")].copy()
    pending_api = pending_api.sort_values(["expected_free_text", "source_lang", "target_lang"])
    partial = matrix[matrix["status"].eq("partial")].copy()
    partial = partial.sort_values(["missing", "source_lang", "target_lang"])
    return {
        "total_pairs": int(len(matrix)),
        "status_counts": {str(key): int(value) for key, value in status_counts.items()},
        "expected_total": expected_total,
        "accepted_total": accepted_total,
        "needs_review_total": needs_review_total,
        "missing_total": missing_total,
        "accepted_pct": (accepted_total / expected_total * 100) if expected_total else 0.0,
        "target_rows": target_rows,
        "next_pending_api": pending_api.head(10).to_dict("records"),
        "next_partial": partial.head(10).to_dict("records"),
    }


def summarize_batch_readiness(outputs_dir: Path) -> dict:
    batch_files = [
        path for path in outputs_dir.glob("llm_batches_*_*/*.jsonl") if not path.name.endswith(".requests.jsonl")
    ]
    request_files = [
        path for path in outputs_dir.glob("llm_batches_*_*/requests/*.requests.jsonl") if path.stat().st_size > 0
    ]
    retry_pairs = {path.parent.parent.name for path in request_files if "retry" in path.name.lower()}
    effective_request_files = [
        path for path in request_files if path.parent.parent.name not in retry_pairs or "retry" in path.name.lower()
    ]
    missing_requests = 0
    effective_missing_requests = 0
    for path in batch_files:
        request_path = path.parent / "requests" / f"{path.stem}.requests.jsonl"
        if not request_path.exists() or request_path.stat().st_size == 0:
            missing_requests += 1
            if path.parent.name not in retry_pairs or "retry" in path.name.lower():
                effective_missing_requests += 1
    return {
        "batch_jsonl": len(batch_files),
        "request_jsonl": len(request_files),
        "effective_request_jsonl": len(effective_request_files),
        "retry_request_jsonl": sum(1 for path in request_files if "retry" in path.name.lower()),
        "missing_requests": missing_requests,
        "effective_missing_requests": effective_missing_requests,
        "batch_dirs": len({path.parent.name for path in batch_files}),
        "request_dirs": len({path.parent.parent.name for path in request_files}),
    }


def _format_int(value: int) -> str:
    return f"{value:,}"


def _format_pairs(rows: list[dict], value_column: str) -> str:
    if not rows:
        return "none"
    return ", ".join(
        f"`{row['source_lang']} -> {row['target_lang']}` ({_format_int(int(row[value_column]))})" for row in rows
    )


def render_progress_status(matrix: pd.DataFrame) -> str:
    summary = summarize_matrix(matrix)
    readiness = summarize_batch_readiness(PIPELINE_DIR / "outputs")
    status_counts = summary["status_counts"]
    lines = [
        "# Translation + PPP progress status",
        "",
        "Scope: local data pipeline only. Application files are untouched.",
        "",
        "Regenerated from `outputs/translation_matrix_status.csv` with no API calls.",
        "",
        "## Current Snapshot",
        "",
        f"- Total source-target pairs: {_format_int(summary['total_pairs'])}",
        f"- Complete green pairs: {_format_int(status_counts.get('complete_green', 0))}",
        f"- Partial pairs: {_format_int(status_counts.get('partial', 0))}",
        f"- Pending API pairs: {_format_int(status_counts.get('pending_api', 0))}",
        f"- Pending identity pairs: {_format_int(status_counts.get('pending_identity', 0))}",
        f"- Expected translation units across matrix: {_format_int(summary['expected_total'])}",
        f"- QA-clean accepted units: {_format_int(summary['accepted_total'])}",
        f"- Needs review units: {_format_int(summary['needs_review_total'])}",
        f"- Missing/not generated units: {_format_int(summary['missing_total'])}",
        f"- Overall QA-clean progress: {summary['accepted_pct']:.2f}%",
        "",
        "## Target Progress",
        "",
    ]
    for row in summary["target_rows"]:
        lines.append(
            f"- `{row['target_lang']}`: {_format_int(row['accepted'])}/{_format_int(row['expected'])} "
            f"accepted ({row['accepted_pct']:.2f}%), needs_review {_format_int(row['needs_review'])}, "
            f"missing {_format_int(row['missing'])}"
        )
    lines.extend(
        [
            "",
            "## Batch Readiness",
            "",
            f"- Source JSONL batch files: {_format_int(readiness['batch_jsonl'])}",
            f"- OpenAI request JSONL files: {_format_int(readiness['request_jsonl'])}",
            f"- Effective queued request files after retry de-duplication: {_format_int(readiness['effective_request_jsonl'])}",
            f"- Retry request files for partial pairs: {_format_int(readiness['retry_request_jsonl'])}",
            f"- Missing request files: {_format_int(readiness['missing_requests'])} raw, {_format_int(readiness['effective_missing_requests'])} effective",
            f"- Batch directories ready: {_format_int(readiness['request_dirs'])}/{_format_int(readiness['batch_dirs'])}",
            "- Resume runner: `python WORLD_COST_BASES/translation_pipeline/openai_batch_resume.py status`.",
            "",
            "## Unit Localization / PPP",
            "",
            "- PPP multipliers generated from World Bank PPP: `outputs/worldbank_ppp_multipliers.parquet`.",
            "- Localized materialization can apply PPP with `materialize_localized_outputs.py --target-region <REGION> --ppp-multipliers outputs/worldbank_ppp_multipliers.parquet`.",
            "- A3 metric -> US customary unit localization is implemented for `USA_USD` / `en-US` only via `unit_localization.py` and `materialize_localized_outputs.py --target-region USA_USD --unit-system us_customary`.",
            "- Other target economies remain metric unless manifest explicitly says `unit_system=us_customary`.",
            "",
            "## Verification",
            "",
            "- Translation QA uses `qa_translation_table.py` followed by `quarantine_qa_failures.py` before rows are counted as accepted.",
            "- Regenerate this status snapshot with `python WORLD_COST_BASES/translation_pipeline/translation_matrix_status.py`.",
            "",
            "## Next Fastest Closures",
            "",
            f"- Smallest pending API pairs by expected strings: {_format_pairs(summary['next_pending_api'], 'expected_free_text')}.",
            f"- Smallest partial completions by missing strings: {_format_pairs(summary['next_partial'], 'missing')}.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--outputs-dir", type=Path, default=PIPELINE_DIR / "outputs")
    parser.add_argument("--progress-out", type=Path, default=DEFAULT_PROGRESS_OUT)
    args = parser.parse_args()
    corpus = pd.read_parquet(args.corpus)
    targets = load_target_languages(args.manifest)
    matrix = build_matrix(corpus, targets, args.outputs_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    matrix.to_csv(args.out, index=False, encoding="utf-8-sig")
    args.progress_out.write_text(render_progress_status(matrix), encoding="utf-8")
    print(
        json.dumps(
            {
                "rows": int(len(matrix)),
                "complete_green": int(matrix["status"].eq("complete_green").sum()),
                "pending_api": int(matrix["status"].eq("pending_api").sum()),
                "pending_identity": int(matrix["status"].eq("pending_identity").sum()),
                "out": str(args.out),
                "progress_out": str(args.progress_out),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
