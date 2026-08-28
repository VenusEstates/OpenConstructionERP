from __future__ import annotations

import json
from pathlib import Path

import openai_batch_resume
import pandas as pd


def test_discover_request_files_finds_nested_openai_requests(tmp_path: Path) -> None:
    first = tmp_path / "llm_batches_de_fr" / "requests" / "part_001.requests.jsonl"
    second = tmp_path / "llm_batches_tr_ar" / "requests" / "part_001.requests.jsonl"
    empty = tmp_path / "llm_batches_bg_cs" / "requests" / "part_001.requests.jsonl"
    ignored_suffix = tmp_path / "llm_batches_es_it" / "requests" / "part_001.jsonl"
    ignored_depth = tmp_path / "llm_batches_nl_sv" / "part_001.requests.jsonl"
    for path in [first, second, ignored_suffix, ignored_depth, empty]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("" if path == empty else "{}", encoding="utf-8")

    discovered = openai_batch_resume.discover_request_files(tmp_path)

    assert discovered == sorted([first, second])


def test_status_counts_submitted_and_unsubmitted_jobs(tmp_path: Path, monkeypatch) -> None:
    submitted_request = tmp_path / "llm_batches_de_fr" / "requests" / "part_001.requests.jsonl"
    pending_request = tmp_path / "llm_batches_tr_ar" / "requests" / "part_001.requests.jsonl"
    jobs = [
        {"request_file": str(submitted_request), "status": "completed"},
        {"batch_id": "batch_without_request", "status": "unknown"},
    ]

    monkeypatch.setattr(
        openai_batch_resume,
        "ordered_request_files",
        lambda: [submitted_request, pending_request],
    )
    monkeypatch.setattr(openai_batch_resume, "load_jobs", lambda: jobs)

    assert openai_batch_resume.status() == {
        "request_files": 2,
        "submitted_jobs": 2,
        "unsubmitted_request_files": 1,
        "job_statuses": {"completed": 1, "unknown": 1},
        "next_unsubmitted_request_files": [str(pending_request)],
    }


def test_save_jobs_persists_wrapped_job_state(tmp_path: Path) -> None:
    path = tmp_path / "outputs" / "openai_batch_jobs.json"
    jobs = [
        {
            "request_file": "outputs/llm_batches_de_fr/requests/part_001.requests.jsonl",
            "pair_dir": "outputs/llm_batches_de_fr",
            "file_id": "file_123",
            "batch_id": "batch_123",
            "status": "validating",
            "created_at": 1783365600,
        }
    ]

    openai_batch_resume.save_jobs(jobs, path)

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert set(persisted) == {"jobs", "updated_at_epoch"}
    assert persisted["jobs"] == jobs
    assert isinstance(persisted["updated_at_epoch"], float)
    assert openai_batch_resume.load_jobs(path) == jobs


def test_load_jobs_accepts_legacy_list_format(tmp_path: Path) -> None:
    path = tmp_path / "openai_batch_jobs.json"
    legacy_jobs = [{"request_file": "a.requests.jsonl", "status": "completed"}]
    path.write_text(json.dumps(legacy_jobs), encoding="utf-8")

    assert openai_batch_resume.load_jobs(path) == legacy_jobs


def test_ordered_request_files_prioritizes_small_partial_pairs(tmp_path: Path) -> None:
    zh_es = tmp_path / "llm_batches_zh_es" / "requests" / "zh__to__es__0001.requests.jsonl"
    zh_bg = tmp_path / "llm_batches_zh_bg" / "requests" / "zh__to__bg__0001.requests.jsonl"
    el_ar = tmp_path / "llm_batches_el_ar" / "requests" / "el__to__ar__0001.requests.jsonl"
    for path in [zh_es, zh_bg, el_ar]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    matrix = tmp_path / "translation_matrix_status.csv"
    matrix.write_text(
        "\n".join(
            [
                "source_lang,target_lang,expected_free_text,accepted,needs_review,missing,status,artifact",
                "el,ar,3241,0,0,3241,pending_api,",
                "zh,es,3122,0,0,3122,pending_api,",
                "zh,bg,3122,197,3,2922,partial,",
            ]
        ),
        encoding="utf-8",
    )

    assert openai_batch_resume.ordered_request_files(tmp_path, matrix) == [
        zh_bg,
        zh_es,
        el_ar,
    ]


def test_ordered_request_files_prefers_retry_files_for_same_pair(
    tmp_path: Path,
) -> None:
    full = tmp_path / "llm_batches_zh_bg" / "requests" / "zh__to__bg__0001.requests.jsonl"
    retry = tmp_path / "llm_batches_zh_bg" / "requests" / "zh__to__bg__retry_missing.requests.jsonl"
    other = tmp_path / "llm_batches_zh_fr" / "requests" / "zh__to__fr__0001.requests.jsonl"
    for path in [full, retry, other]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    matrix = tmp_path / "translation_matrix_status.csv"
    matrix.write_text(
        "\n".join(
            [
                "source_lang,target_lang,expected_free_text,accepted,needs_review,missing,status,artifact",
                "zh,bg,3122,197,3,2922,partial,",
                "zh,fr,3122,196,4,2922,partial,",
            ]
        ),
        encoding="utf-8",
    )

    assert openai_batch_resume.ordered_request_files(tmp_path, matrix) == [retry, other]


def test_merge_with_existing_translations_replaces_only_retry_keys(
    tmp_path: Path,
) -> None:
    existing_path = tmp_path / "zh_bg_translations_quarantined.parquet"
    existing = pd.DataFrame(
        [
            {
                "tm_key": "keep",
                "target_lang": "bg",
                "target_text": "старо",
                "status": "reviewed",
            },
            {
                "tm_key": "replace",
                "target_lang": "bg",
                "target_text": "лошо",
                "status": "needs_review",
            },
        ]
    )
    retry = pd.DataFrame(
        [
            {
                "tm_key": "replace",
                "target_lang": "bg",
                "target_text": "добро",
                "status": "reviewed",
            },
        ]
    )
    existing.to_parquet(existing_path, index=False)

    merged = openai_batch_resume.merge_with_existing_translations(retry, existing_path)

    assert len(merged) == 2
    by_key = {row.tm_key: row.target_text for row in merged.itertuples(index=False)}
    assert by_key == {"keep": "старо", "replace": "добро"}
