from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd
from fetch_openai_batch import download_file, get_json
from parse_openai_batch_results import load_input_record_map, parse_results
from qa_translation_table import read_table, run_qa
from quarantine_qa_failures import quarantine
from submit_openai_batch import create_batch, load_dotenv_key, upload_file
from translation_matrix_status import (
    build_matrix,
    load_target_languages,
    render_progress_status,
)

PIPELINE_DIR = Path(__file__).resolve().parent
OUTPUTS_DIR = PIPELINE_DIR / "outputs"
CORPUS_PATH = OUTPUTS_DIR / "translation_corpus.parquet"
JOBS_PATH = OUTPUTS_DIR / "openai_batch_jobs.json"
RESULTS_DIRNAME = "openai_results"
MATRIX_STATUS_PATH = OUTPUTS_DIR / "translation_matrix_status.csv"
MANIFEST_PATH = OUTPUTS_DIR / "translation_manifest.json"
PROGRESS_STATUS_PATH = PIPELINE_DIR / "PROGRESS_STATUS.md"


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


def discover_request_files(outputs_dir: Path = OUTPUTS_DIR) -> list[Path]:
    return sorted(
        path for path in outputs_dir.glob("llm_batches_*_*/requests/*.requests.jsonl") if path.stat().st_size > 0
    )


def pair_dir_for_request(path: Path) -> Path:
    return path.parent.parent


def stem_for_pair_dir(pair_dir: Path) -> str:
    return pair_dir.name.replace("llm_batches_", "", 1)


def pair_for_request(path: Path) -> tuple[str, str]:
    stem = stem_for_pair_dir(pair_dir_for_request(path))
    source_lang, target_lang = stem.split("_", 1)
    return source_lang, target_lang


def load_pair_priorities(
    matrix_path: Path = MATRIX_STATUS_PATH,
) -> dict[tuple[str, str], tuple[int, int, int]]:
    if not matrix_path.exists():
        return {}
    matrix = pd.read_csv(matrix_path)
    priorities: dict[tuple[str, str], tuple[int, int, int]] = {}
    status_rank = {"partial": 0, "pending_api": 1}
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


def ordered_request_files(outputs_dir: Path = OUTPUTS_DIR, matrix_path: Path = MATRIX_STATUS_PATH) -> list[Path]:
    priorities = load_pair_priorities(matrix_path)
    request_files = discover_request_files(outputs_dir)
    retry_pairs = {pair_for_request(path) for path in request_files if "retry" in path.name.lower()}
    if retry_pairs:
        request_files = [
            path for path in request_files if pair_for_request(path) not in retry_pairs or "retry" in path.name.lower()
        ]

    def sort_key(path: Path) -> tuple[int, int, int, str, str, str]:
        source_lang, target_lang = pair_for_request(path)
        priority = priorities.get((source_lang, target_lang), (9, 10**12, 10**12))
        return (*priority, source_lang, target_lang, path.name)

    return sorted(request_files, key=sort_key)


def jobs_by_request(jobs: list[dict]) -> dict[str, dict]:
    return {str(Path(job["request_file"])): job for job in jobs if job.get("request_file")}


def merge_with_existing_translations(new_translations: pd.DataFrame, existing_path: Path) -> pd.DataFrame:
    if not existing_path.exists():
        return new_translations
    existing = pd.read_parquet(existing_path)
    if existing.empty:
        return new_translations
    replace_keys = set(new_translations["tm_key"].dropna().astype(str))
    if not replace_keys:
        return existing
    kept = existing[~existing["tm_key"].astype(str).isin(replace_keys)]
    merged = pd.concat([kept, new_translations], ignore_index=True)
    if merged.duplicated(["tm_key", "target_lang"]).any():
        merged = merged.drop_duplicates(["tm_key", "target_lang"], keep="last")
    return merged


def submit_missing(*, max_submit: int, sleep_seconds: float) -> dict:
    api_key = load_dotenv_key()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set.")

    jobs = load_jobs()
    known = jobs_by_request(jobs)
    submitted = 0
    failures: list[dict] = []
    for request_file in ordered_request_files():
        key = str(request_file)
        if key in known:
            continue
        if max_submit and submitted >= max_submit:
            break
        try:
            file_id = upload_file(request_file, api_key)
            batch = create_batch(file_id, api_key, "24h")
        except Exception as exc:
            failures.append({"request_file": key, "error": str(exc)})
            break
        job = {
            "request_file": key,
            "pair_dir": str(pair_dir_for_request(request_file)),
            "file_id": file_id,
            "batch_id": batch["id"],
            "status": batch.get("status", ""),
            "created_at": batch.get("created_at"),
        }
        jobs.append(job)
        known[key] = job
        submitted += 1
        save_jobs(jobs)
        if sleep_seconds:
            time.sleep(sleep_seconds)
    save_jobs(jobs)
    return {"submitted": submitted, "known_jobs": len(jobs), "failures": failures}


def fetch_jobs() -> dict:
    api_key = load_dotenv_key()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set.")

    jobs = load_jobs()
    status_counts: dict[str, int] = {}
    downloaded = 0
    failures: list[dict] = []
    for job in jobs:
        batch_id = job.get("batch_id")
        if not batch_id:
            continue
        try:
            batch = get_json(f"/batches/{batch_id}", api_key)
            status = str(batch.get("status", ""))
            job["status"] = status
            job["output_file_id"] = batch.get("output_file_id")
            job["error_file_id"] = batch.get("error_file_id")
            status_counts[status] = status_counts.get(status, 0) + 1
            result_dir = Path(job["pair_dir"]) / RESULTS_DIRNAME
            result_dir.mkdir(parents=True, exist_ok=True)
            (result_dir / f"{batch_id}.status.json").write_text(
                json.dumps(batch, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            if batch.get("output_file_id"):
                out = result_dir / f"{batch_id}.output.jsonl"
                if not out.exists() or out.stat().st_size == 0:
                    download_file(batch["output_file_id"], api_key, out)
                    downloaded += 1
            if batch.get("error_file_id"):
                err = result_dir / f"{batch_id}.errors.jsonl"
                if not err.exists() or err.stat().st_size == 0:
                    download_file(batch["error_file_id"], api_key, err)
                    downloaded += 1
        except Exception as exc:
            failures.append({"batch_id": batch_id, "error": str(exc)})
    save_jobs(jobs)
    return {
        "jobs": len(jobs),
        "statuses": status_counts,
        "downloaded_files": downloaded,
        "failures": failures,
    }


def parse_completed_pairs() -> dict:
    outputs: list[str] = []
    skipped: list[dict] = []
    corpus = read_table(CORPUS_PATH)
    for pair_dir in sorted(OUTPUTS_DIR.glob("llm_batches_*_*")):
        if not pair_dir.is_dir():
            continue
        result_files = sorted((pair_dir / RESULTS_DIRNAME).glob("*.output.jsonl"))
        if not result_files:
            continue
        stem = stem_for_pair_dir(pair_dir)
        quarantined_path = pair_dir / f"{stem}_translations_quarantined.parquet"
        if quarantined_path.exists():
            newest_result_mtime = max(path.stat().st_mtime for path in result_files)
            if quarantined_path.stat().st_mtime >= newest_result_mtime:
                skipped.append({"pair_dir": str(pair_dir), "reason": "up_to_date"})
                continue
        records = load_input_record_map(pair_dir)
        frames = [parse_results(path, records) for path in result_files]
        translations = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if translations.empty:
            skipped.append({"pair_dir": str(pair_dir), "reason": "empty_results"})
            continue
        if translations.duplicated(["tm_key", "target_lang"]).any():
            translations = translations.drop_duplicates(["tm_key", "target_lang"], keep="last")

        raw_path = pair_dir / f"{stem}_translations.parquet"
        qa_path = pair_dir / f"{stem}_qa_report.json"
        final_qa_path = pair_dir / f"{stem}_qa_after_merge.json"
        translations.to_parquet(raw_path, index=False)
        translations.to_csv(raw_path.with_suffix(".csv"), index=False, encoding="utf-8-sig")

        qa_report = run_qa(corpus, translations)
        qa_path.write_text(json.dumps(qa_report, ensure_ascii=False, indent=2), encoding="utf-8")
        quarantined = quarantine(translations, qa_report)
        quarantined = merge_with_existing_translations(quarantined, quarantined_path)
        final_qa_report = run_qa(corpus, quarantined)
        final_qa_path.write_text(json.dumps(final_qa_report, ensure_ascii=False, indent=2), encoding="utf-8")
        quarantined.to_parquet(quarantined_path, index=False)
        quarantined.to_csv(quarantined_path.with_suffix(".csv"), index=False, encoding="utf-8-sig")
        outputs.append(str(quarantined_path))
    return {"parsed_pairs": len(outputs), "outputs": outputs, "skipped": skipped}


def refresh_matrix_status() -> dict:
    corpus = pd.read_parquet(CORPUS_PATH)
    targets = load_target_languages(MANIFEST_PATH)
    matrix = build_matrix(corpus, targets, OUTPUTS_DIR)
    matrix.to_csv(MATRIX_STATUS_PATH, index=False, encoding="utf-8-sig")
    PROGRESS_STATUS_PATH.write_text(render_progress_status(matrix), encoding="utf-8")
    return {
        "rows": int(len(matrix)),
        "complete_green": int(matrix["status"].eq("complete_green").sum()),
        "pending_api": int(matrix["status"].eq("pending_api").sum()),
        "partial": int(matrix["status"].eq("partial").sum()),
    }


def status() -> dict:
    request_files = ordered_request_files()
    jobs = load_jobs()
    known = jobs_by_request(jobs)
    statuses: dict[str, int] = {}
    for job in jobs:
        status_value = str(job.get("status", "unknown"))
        statuses[status_value] = statuses.get(status_value, 0) + 1
    next_unsubmitted = [str(path) for path in request_files if str(path) not in known][:10]
    return {
        "request_files": len(request_files),
        "submitted_jobs": len(jobs),
        "unsubmitted_request_files": max(len(request_files) - len(known), 0),
        "job_statuses": statuses,
        "next_unsubmitted_request_files": next_unsubmitted,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["status", "submit", "fetch", "parse", "all"])
    parser.add_argument("--max-submit", type=int, default=0)
    parser.add_argument("--sleep-seconds", type=float, default=0.3)
    args = parser.parse_args()

    result: dict
    if args.command == "status":
        result = status()
    elif args.command == "submit":
        result = submit_missing(max_submit=args.max_submit, sleep_seconds=args.sleep_seconds)
    elif args.command == "fetch":
        result = fetch_jobs()
    elif args.command == "parse":
        result = {"parse": parse_completed_pairs(), "matrix": refresh_matrix_status()}
    else:
        result = {
            "submit": submit_missing(max_submit=args.max_submit, sleep_seconds=args.sleep_seconds),
            "fetch": fetch_jobs(),
            "parse": parse_completed_pairs(),
            "matrix": refresh_matrix_status(),
            "status": status(),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
