from __future__ import annotations

import pytest
import run_openrouter_jsonl
from run_openrouter_jsonl import restore_protected_tokens, result_to_row, run_records


def test_result_to_row_uses_source_record_identity() -> None:
    result = {
        "record": {
            "custom_id": "tm:k1:de:v1",
            "tm_key": "k1",
            "source_lang": "tr",
            "target_lang": "de",
        },
        "response": {"model": "openai/gpt-4.1-mini"},
        "payload": {
            "tm_key": "changed",
            "target_text": "Beton",
            "status": "translated",
        },
        "error": None,
    }
    row = result_to_row(result, "fallback")
    assert row["tm_key"] == "k1"
    assert row["status"] == "reviewed"
    assert row["translator"] == "openrouter"


def test_restore_protected_tokens_reverts_localized_arabic_units() -> None:
    text = "أنبوب PVC بقطر 40x1,9 مم وارتفاع 65 سم وحجم 1 م³"

    restored = restore_protected_tokens(text, ["40x1,9 mm", "65 cm", "1 m3"])

    assert "40x1,9 mm" in restored
    assert "65 cm" in restored
    assert "1 m3" in restored


def test_result_to_row_marks_missing_protected_tokens_needs_review() -> None:
    result = {
        "record": {
            "custom_id": "tm:k1:ar:v1",
            "tm_key": "k1",
            "source_lang": "id",
            "target_lang": "ar",
            "protected_tokens": ["19 mm"],
        },
        "response": {"model": "openai/gpt-4o-mini"},
        "payload": {
            "target_text": "خرسانة مع ركام 19 فقط",
            "status": "reviewed",
        },
        "error": None,
    }

    row = result_to_row(result, "fallback")

    assert row["status"] == "needs_review"
    assert "protected_tokens_missing" in row["review_notes"]


def test_run_records_aborts_on_configured_http_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        {
            "custom_id": "tm:k1:ar:v1",
            "tm_key": "k1",
            "source_lang": "es",
            "target_lang": "ar",
        }
    ]

    def fake_call_openrouter(*args, **kwargs):
        return {
            "record": records[0],
            "response": None,
            "payload": None,
            "error": "HTTP 402: no credits",
        }

    monkeypatch.setattr(run_openrouter_jsonl, "call_openrouter", fake_call_openrouter)

    with pytest.raises(RuntimeError, match="HTTP 402"):
        run_records(
            records,
            api_key="key",
            model="cheap-model",
            concurrency=1,
            max_retries=0,
            abort_http_codes={402, 429},
        )


def test_run_records_accepts_success_with_abort_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        {
            "custom_id": "tm:k1:ar:v1",
            "tm_key": "k1",
            "source_lang": "es",
            "target_lang": "ar",
        }
    ]

    def fake_call_openrouter(*args, **kwargs):
        return {
            "record": records[0],
            "response": {"model": "cheap-model"},
            "payload": {"target_text": "خرسانة", "status": "reviewed"},
            "error": None,
        }

    monkeypatch.setattr(run_openrouter_jsonl, "call_openrouter", fake_call_openrouter)

    df = run_records(
        records,
        api_key="key",
        model="cheap-model",
        concurrency=1,
        max_retries=0,
        abort_http_codes={402, 429},
    )

    assert df.loc[0, "status"] == "reviewed"
    assert df.loc[0, "target_text"] == "خرسانة"
