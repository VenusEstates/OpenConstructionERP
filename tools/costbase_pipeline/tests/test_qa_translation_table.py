from __future__ import annotations

import pandas as pd
from qa_translation_table import protected_tokens, run_qa, validate_materialized_frame


def _corpus() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "tm_key": "k1",
                "source_lang": "tr",
                "source_text": "DN100 boru, C25/30 beton, Ø140 delik",
            },
            {
                "tm_key": "k2",
                "source_lang": "el",
                "source_text": "Φορτοεκφόρτωση με τα χέρια",
            },
        ]
    )


def test_protected_tokens_keep_dimensions_and_codes() -> None:
    assert protected_tokens("DN100 boru, C25/30 beton, Ø140 delik") == [
        "C25/30",
        "DN100",
        "Ø140",
    ]


def test_protected_tokens_cover_construction_basis_and_symbols() -> None:
    assert protected_tokens("AF_03/2024 100m3/1km 40 X 40 CM E= 2,5 CM ≤ 2") == [
        "100m3/1km",
        "2",
        "2,5 CM",
        "40 X 40 CM",
        "AF_03/2024",
        "≤",
    ]


def test_qa_accepts_preserved_tokens() -> None:
    translations = pd.DataFrame(
        [
            {
                "tm_key": "k1",
                "target_lang": "de",
                "target_text": "DN100 Rohr, C25/30 Beton, Ø140 Bohrung",
                "status": "reviewed",
            }
        ]
    )
    report = run_qa(_corpus(), translations)
    assert report["ok"], report


def test_qa_rejects_changed_dimension_token() -> None:
    translations = pd.DataFrame(
        [
            {
                "tm_key": "k1",
                "target_lang": "de",
                "target_text": "DN150 Rohr, C25/30 Beton, Ø140 Bohrung",
                "status": "reviewed",
            }
        ]
    )
    report = run_qa(_corpus(), translations)
    assert not report["ok"]
    assert any(error["check"] == "protected_tokens" for error in report["errors"])


def test_qa_allows_approved_termbase_token_transform() -> None:
    corpus = pd.DataFrame(
        [
            {
                "tm_key": "k3",
                "source_lang": "el",
                "source_text": "Αναλυτικα Τιμολογια Εργων (ΓΓΔΕ)",
            }
        ]
    )
    translations = pd.DataFrame(
        [
            {
                "tm_key": "k3",
                "target_lang": "de",
                "target_text": "Analytische Baupreisverzeichnisse (GGDE)",
                "status": "reviewed",
            }
        ]
    )
    report = run_qa(corpus, translations)
    assert report["ok"], report


def test_qa_rejects_source_script_leak() -> None:
    translations = pd.DataFrame(
        [
            {
                "tm_key": "k2",
                "target_lang": "de",
                "target_text": "Φορτοεκφόρτωση mit der Hand",
                "status": "reviewed",
            }
        ]
    )
    report = run_qa(_corpus(), translations)
    assert not report["ok"]
    assert any(error["check"] == "source_script_leak" for error in report["errors"])


def test_qa_rejects_duplicate_translation_for_same_key_and_lang() -> None:
    translations = pd.DataFrame(
        [
            {
                "tm_key": "k1",
                "target_lang": "de",
                "target_text": "A",
                "status": "reviewed",
            },
            {
                "tm_key": "k1",
                "target_lang": "de",
                "target_text": "B",
                "status": "reviewed",
            },
        ]
    )
    report = run_qa(_corpus(), translations)
    assert not report["ok"]
    assert any(error["check"] == "tm_uniqueness" for error in report["errors"])


def test_qa_rejects_empty_target_for_nonempty_source() -> None:
    translations = pd.DataFrame(
        [
            {
                "tm_key": "k1",
                "target_lang": "de",
                "target_text": "   ",
                "status": "reviewed",
            },
        ]
    )
    report = run_qa(_corpus(), translations)
    assert not report["ok"]
    assert any(error["check"] == "empty_target" for error in report["errors"])


def test_qa_skips_needs_review_rows_for_content_checks() -> None:
    translations = pd.DataFrame(
        [
            {
                "tm_key": "k2",
                "target_lang": "de",
                "target_text": "Φορτοεκφόρτωση με τα χέρια",
                "status": "needs_review",
            }
        ]
    )
    report = run_qa(_corpus(), translations)
    assert report["ok"], report
    assert report["summary"]["checked_rows"] == 0
    assert report["summary"]["skipped_status_rows"] == 1


def test_materialized_frame_rejects_identity_drift() -> None:
    source = pd.DataFrame(
        [
            {
                "rate_code": "A",
                "resource_code": "R1",
                "is_scope": False,
                "source_year": 2026,
            },
            {
                "rate_code": "A",
                "resource_code": "R2",
                "is_scope": False,
                "source_year": 2026,
            },
        ]
    )
    localized = source.copy()
    localized.loc[1, "resource_code"] = "R3"
    report = validate_materialized_frame(source, localized)
    assert not report["ok"]
    assert any(error["check"] == "identity_column" for error in report["errors"])


def test_materialized_frame_rejects_row_order_drift_with_preserved_index() -> None:
    source = pd.DataFrame(
        [
            {"rate_code": "A", "resource_code": "R1", "is_scope": False},
            {"rate_code": "B", "resource_code": "R2", "is_scope": False},
        ]
    )
    localized = source.iloc[[1, 0]]
    report = validate_materialized_frame(source, localized)
    assert not report["ok"]
    assert any(error["check"] == "identity_column" for error in report["errors"])


def test_materialized_frame_rejects_surprise_columns() -> None:
    source = pd.DataFrame([{"rate_code": "A", "resource_code": "R1"}])
    localized = source.copy()
    localized["invented_app_column"] = "bad"
    report = validate_materialized_frame(source, localized)
    assert not report["ok"]
    assert any(error["check"] == "unexpected_columns" for error in report["errors"])
