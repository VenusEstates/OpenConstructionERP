from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import pandas as pd
from openai_batch_resume import (
    CORPUS_PATH,
    MATRIX_STATUS_PATH,
    OUTPUTS_DIR,
    merge_with_existing_translations,
    refresh_matrix_status,
    stem_for_pair_dir,
)
from qa_translation_table import read_table, run_qa
from quarantine_qa_failures import quarantine
from run_openrouter_jsonl import load_dotenv_key, read_records, run_records
from run_openrouter_shards import expected_rows, is_complete, output_for

JOBS_PATH = OUTPUTS_DIR / "openrouter_jobs.json"


def discover_input_files(outputs_dir: Path = OUTPUTS_DIR) -> list[Path]:
    return sorted(
        path
        for path in outputs_dir.glob("llm_batches_*_*/*.jsonl")
        if not path.name.endswith(".requests.jsonl") and path.stat().st_size > 0
    )


def pair_dir_for_input(path: Path) -> Path:
    return path.parent


def pair_for_input(path: Path) -> tuple[str, str]:
    stem = stem_for_pair_dir(pair_dir_for_input(path))
    source_lang, target_lang = stem.split("_", 1)
    return source_lang, target_lang


def load_pair_priorities(
    matrix_path: Path = MATRIX_STATUS_PATH,
) -> dict[tuple[str, str], tuple[int, int, int]]:
    if not matrix_path.exists():
        return {}
    matrix = pd.read_csv(matrix_path)
    status_rank = {"partial": 0, "pending_api": 1}
    priorities: dict[tuple[str, str], tuple[int, int, int]] = {}
    for row in matrix.itertuples(index=False):
        status_value = str(row.status)
        if status_value not in status_rank:
            continue
        priorities[(str(row.source_lang), str(row.target_lang))] = (
            status_rank[status_value],
            int(row.missing),
            int(row.expected_free_text),
        )
    return priorities


def ordered_input_files(outputs_dir: Path = OUTPUTS_DIR, matrix_path: Path = MATRIX_STATUS_PATH) -> list[Path]:
    files = discover_input_files(outputs_dir)
    retry_chunk_pairs = {
        pair_for_input(path) for path in files if "retry" in path.name.lower() and "__part" in path.stem
    }
    if retry_chunk_pairs:
        files = [path for path in files if pair_for_input(path) not in retry_chunk_pairs or "__part" in path.stem]
    retry_pairs = {pair_for_input(path) for path in files if "retry" in path.name.lower()}
    if retry_pairs:
        files = [path for path in files if pair_for_input(path) not in retry_pairs or "retry" in path.name.lower()]
    priorities = load_pair_priorities(matrix_path)

    def sort_key(path: Path) -> tuple[int, int, int, str, str, str]:
        source_lang, target_lang = pair_for_input(path)
        priority = priorities.get((source_lang, target_lang), (9, 10**12, 10**12))
        return (*priority, source_lang, target_lang, path.name)

    return sorted(files, key=sort_key)


def load_jobs(path: Path = JOBS_PATH) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return list(data.get("jobs", []))
    return list(data)


def save_jobs(jobs: list[dict], path: Path = JOBS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"jobs": jobs, "updated_at_epoch": time.time()},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def jobs_by_input(jobs: list[dict]) -> dict[str, dict]:
    return {str(Path(job["input_file"])): job for job in jobs if job.get("input_file")}


def status() -> dict:
    files = ordered_input_files()
    jobs = load_jobs()
    known = jobs_by_input(jobs)
    statuses: dict[str, int] = {}
    for job in jobs:
        status_value = str(job.get("status", "unknown"))
        statuses[status_value] = statuses.get(status_value, 0) + 1
    return {
        "input_files": len(files),
        "completed_jobs": statuses.get("completed", 0),
        "submitted_jobs": len(jobs),
        "unprocessed_input_files": max(len(files) - len(known), 0),
        "job_statuses": statuses,
        "next_input_files": [str(path) for path in files if str(path) not in known][:10],
    }


def run_next(
    *,
    max_files: int,
    model: str,
    record_concurrency: int,
    max_retries: int,
    max_tokens: int,
    abort_http_codes: set[int],
) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY") or load_dotenv_key("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is not set.")

    jobs = load_jobs()
    known = jobs_by_input(jobs)
    failures: list[dict] = []
    completed: list[dict] = []
    for input_file in ordered_input_files():
        key = str(input_file)
        if key in known:
            continue
        if max_files and len(completed) >= max_files:
            break
        pair_dir = pair_dir_for_input(input_file)
        shard_dir = pair_dir / "openrouter_shards"
        out_path = output_for(input_file, shard_dir)
        try:
            if is_complete(input_file, out_path):
                rows = expected_rows(input_file)
            else:
                records = read_records([input_file])
                df = run_records(
                    records,
                    api_key=api_key,
                    model=model,
                    concurrency=record_concurrency,
                    max_retries=max_retries,
                    max_tokens=max_tokens,
                    abort_http_codes=abort_http_codes,
                )
                shard_dir.mkdir(parents=True, exist_ok=True)
                tmp = out_path.with_suffix(".tmp.parquet")
                df.to_parquet(tmp, index=False)
                tmp.replace(out_path)
                rows = int(len(df))
        except Exception as exc:
            failures.append({"input_file": key, "error": str(exc)})
            break
        job = {
            "input_file": key,
            "pair_dir": str(pair_dir),
            "output_file": str(out_path),
            "status": "completed",
            "rows": rows,
            "model": model,
        }
        jobs.append(job)
        known[key] = job
        completed.append(job)
        save_jobs(jobs)
    save_jobs(jobs)
    return {"completed": len(completed), "failures": failures, "jobs": len(jobs)}


def parse_outputs() -> dict:
    corpus = read_table(CORPUS_PATH)
    outputs: list[str] = []
    skipped: list[dict] = []
    for pair_dir in sorted(OUTPUTS_DIR.glob("llm_batches_*_*")):
        shard_files = sorted((pair_dir / "openrouter_shards").glob("*_openrouter.parquet"))
        if not shard_files:
            continue
        stem = stem_for_pair_dir(pair_dir)
        quarantined_path = pair_dir / f"{stem}_translations_quarantined.parquet"
        if quarantined_path.exists():
            newest = max(path.stat().st_mtime for path in shard_files)
            if quarantined_path.stat().st_mtime >= newest:
                skipped.append({"pair_dir": str(pair_dir), "reason": "up_to_date"})
                continue
        translations = pd.concat([pd.read_parquet(path) for path in shard_files], ignore_index=True)
        if translations.empty:
            skipped.append({"pair_dir": str(pair_dir), "reason": "empty_results"})
            continue
        if translations.duplicated(["tm_key", "target_lang"]).any():
            translations = translations.drop_duplicates(["tm_key", "target_lang"], keep="last")
        raw_path = pair_dir / f"{stem}_translations_openrouter.parquet"
        qa_path = pair_dir / f"{stem}_openrouter_qa_report.json"
        final_qa_path = pair_dir / f"{stem}_openrouter_qa_after_merge.json"
        translations.to_parquet(raw_path, index=False)
        qa_report = run_qa(corpus, translations)
        qa_path.write_text(json.dumps(qa_report, ensure_ascii=False, indent=2), encoding="utf-8")
        quarantined = quarantine(translations, qa_report)
        quarantined = merge_with_existing_translations(quarantined, quarantined_path)
        final_qa = run_qa(corpus, quarantined)
        final_qa_path.write_text(json.dumps(final_qa, ensure_ascii=False, indent=2), encoding="utf-8")
        quarantined.to_parquet(quarantined_path, index=False)
        outputs.append(str(quarantined_path))
    return {"parsed_pairs": len(outputs), "outputs": outputs, "skipped": skipped}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["status", "run", "parse", "all"])
    parser.add_argument("--max-files", type=int, default=1)
    parser.add_argument("--model", default="openai/gpt-4.1-mini")
    parser.add_argument("--record-concurrency", type=int, default=2)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--abort-http-codes", nargs="*", type=int, default=[402, 429])
    args = parser.parse_args()

    if args.command == "status":
        result = status()
    elif args.command == "run":
        result = run_next(
            max_files=args.max_files,
            model=args.model,
            record_concurrency=args.record_concurrency,
            max_retries=args.max_retries,
            max_tokens=args.max_tokens,
            abort_http_codes=set(args.abort_http_codes),
        )
    elif args.command == "parse":
        result = {"parse": parse_outputs(), "matrix": refresh_matrix_status()}
    else:
        result = {
            "run": run_next(
                max_files=args.max_files,
                model=args.model,
                record_concurrency=args.record_concurrency,
                max_retries=args.max_retries,
                max_tokens=args.max_tokens,
                abort_http_codes=set(args.abort_http_codes),
            ),
            "parse": parse_outputs(),
            "matrix": refresh_matrix_status(),
            "status": status(),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
