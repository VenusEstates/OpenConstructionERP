from __future__ import annotations

import json

import pandas as pd
from build_translation_batches import build_batches, classify_string_kind


def test_build_batches_writes_contextual_jsonl(tmp_path) -> None:
    corpus = pd.DataFrame(
        [
            {
                "tm_key": "k1",
                "source_lang": "tr",
                "source_text": "Yükleyici (100 HP)",
                "columns": "resource_name",
                "regions": "TR",
                "total_occurrences": 3,
                "contexts_json": json.dumps(
                    [
                        {
                            "examples": [
                                {
                                    "trade_code": "EARTHWORK",
                                    "trade_name": "Earthworks",
                                    "division": "Site",
                                    "row_type": "Machinery",
                                }
                            ]
                        }
                    ]
                ),
            }
        ]
    )
    manifest = build_batches(corpus, ["de"], tmp_path, batch_size=1)
    assert len(manifest["batches"]) == 1
    lines = (tmp_path / manifest["batches"][0]["file"]).read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    assert record["tm_key"] == "k1"
    assert record["custom_id"] == "tm:k1:de:v1"
    assert record["target_lang"] == "de"
    assert record["protected_tokens"] == ["100 HP"]
    assert "Turkish" in record["source_language_note"]
    assert record["schema_version"] == "cwicr-translation-batch-v1"
    assert record["context"]["trade_code"] == "EARTHWORK"


def test_build_batches_includes_matched_termbase_terms(tmp_path) -> None:
    corpus = pd.DataFrame(
        [
            {
                "tm_key": "k1",
                "source_lang": "el",
                "source_text": "Αναλυτικα Τιμολογια Εργων (ΓΓΔΕ)",
                "columns": "collection_name",
                "regions": "GR",
                "total_occurrences": 3,
                "contexts_json": "[]",
            }
        ]
    )
    manifest = build_batches(corpus, ["de"], tmp_path, batch_size=1)
    record = json.loads((tmp_path / manifest["batches"][0]["file"]).read_text(encoding="utf-8"))
    assert record["matched_terms"][0]["source_term"] == "ΓΓΔΕ"
    assert record["matched_terms"][0]["approved_target_term"] == "GGDE"


def test_build_batches_excludes_controlled_values_by_default(tmp_path) -> None:
    corpus = pd.DataFrame(
        [
            {
                "tm_key": "k1",
                "source_lang": "tr",
                "source_text": "Ad",
                "columns": "rate_unit,resource_unit",
                "regions": "TR",
                "total_occurrences": 3,
                "contexts_json": "[]",
            }
        ]
    )
    manifest = build_batches(corpus, ["de"], tmp_path, batch_size=1)
    assert manifest["batches"] == []


def test_classify_string_kind() -> None:
    assert classify_string_kind("resource_name") == "free_text"
    assert classify_string_kind("row_type") == "controlled_enum"
    assert classify_string_kind("rate_unit,resource_unit") == "unit_label"
    assert classify_string_kind("rate_final_name") == "pivot_reference"
