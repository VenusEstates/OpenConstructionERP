from __future__ import annotations

import pandas as pd
from build_identity_translations import build_identity_translations
from translation_matrix_status import build_matrix, render_progress_status


def test_build_identity_translations_for_matching_target_language() -> None:
    corpus = pd.DataFrame(
        [
            {
                "tm_key": "k1",
                "source_lang": "tr",
                "source_text": "Beton",
                "columns": "resource_name",
            },
            {
                "tm_key": "k2",
                "source_lang": "el",
                "source_text": "Σκυρόδεμα",
                "columns": "resource_name",
            },
        ]
    )
    table = build_identity_translations(corpus, {"tr", "de"})
    assert len(table) == 1
    assert table.iloc[0]["target_lang"] == "tr"
    assert table.iloc[0]["target_text"] == "Beton"
    assert table.iloc[0]["translator"] == "identity"


def test_translation_matrix_marks_identity_pending_without_artifact(tmp_path) -> None:
    corpus = pd.DataFrame(
        [
            {
                "tm_key": "k1",
                "source_lang": "tr",
                "source_text": "Beton",
                "columns": "resource_name",
            },
        ]
    )
    matrix = build_matrix(corpus, ["tr", "de"], tmp_path)
    by_target = {row.target_lang: row.status for row in matrix.itertuples(index=False)}
    assert by_target["tr"] == "pending_identity"
    assert by_target["de"] == "pending_api"


def test_render_progress_status_from_matrix() -> None:
    matrix = pd.DataFrame(
        [
            {
                "source_lang": "tr",
                "target_lang": "tr",
                "expected_free_text": 2,
                "accepted": 0,
                "needs_review": 0,
                "missing": 2,
                "status": "pending_identity",
                "artifact": "",
            },
            {
                "source_lang": "tr",
                "target_lang": "de",
                "expected_free_text": 2,
                "accepted": 1,
                "needs_review": 0,
                "missing": 1,
                "status": "partial",
                "artifact": "tr_de.parquet",
            },
            {
                "source_lang": "el",
                "target_lang": "de",
                "expected_free_text": 3,
                "accepted": 0,
                "needs_review": 0,
                "missing": 3,
                "status": "pending_api",
                "artifact": "",
            },
        ]
    )

    progress = render_progress_status(matrix)

    assert "- Total source-target pairs: 3" in progress
    assert "- Pending identity pairs: 1" in progress
    assert "- `de`: 1/5 accepted (20.00%), needs_review 0, missing 4" in progress
    assert "`el -> de` (3)" in progress
    assert "`tr -> de` (1)" in progress
