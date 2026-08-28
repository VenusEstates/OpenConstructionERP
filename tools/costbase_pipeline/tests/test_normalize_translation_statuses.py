from __future__ import annotations

import pandas as pd
from normalize_translation_statuses import normalize_statuses


def test_normalize_statuses_moves_unknown_to_needs_review() -> None:
    df = pd.DataFrame({"status": ["reviewed", "partial", "approved", "needs_review"]})
    out = normalize_statuses(df)
    assert out["status"].tolist() == [
        "reviewed",
        "needs_review",
        "approved",
        "needs_review",
    ]
