from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def quarantine(translations: pd.DataFrame, qa_report: dict) -> pd.DataFrame:
    failed_keys = set(qa_report.get("failed_tm_keys") or [])
    if not failed_keys:
        failed_keys = {error["tm_key"] for error in qa_report.get("errors", []) if "tm_key" in error}
    out = translations.copy()
    mask = out["tm_key"].isin(failed_keys)
    out.loc[mask, "status"] = "needs_review"
    existing_notes = out.loc[mask, "review_notes"].fillna("").astype(str) if "review_notes" in out.columns else ""
    if "review_notes" not in out.columns:
        out["review_notes"] = ""
    out.loc[mask, "review_notes"] = existing_notes + " QA quarantine: protected/script/terminology failure."
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--translations", type=Path, required=True)
    parser.add_argument("--qa-report", type=Path, required=True)
    parser.add_argument("--output-parquet", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path)
    args = parser.parse_args()

    translations = pd.read_parquet(args.translations)
    report = json.loads(args.qa_report.read_text(encoding="utf-8"))
    out = quarantine(translations, report)
    args.output_parquet.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(args.output_parquet, index=False)
    if args.output_csv:
        out.to_csv(args.output_csv, index=False, encoding="utf-8-sig")
    print(
        json.dumps(
            {
                "rows": int(len(out)),
                "quarantined": int(
                    out["status"].eq("needs_review").sum() - translations["status"].eq("needs_review").sum()
                ),
                "output": str(args.output_parquet),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
