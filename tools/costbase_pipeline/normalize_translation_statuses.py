from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

ACCEPTED = {"reviewed", "approved", "needs_review"}


def normalize_statuses(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["status"] = out["status"].astype(str).str.strip()
    out.loc[~out["status"].isin(ACCEPTED), "status"] = "needs_review"
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.paths:
        df = pd.read_parquet(path)
        out = normalize_statuses(df)
        out.to_parquet(path, index=False)
        csv_path = path.with_suffix(".csv")
        if csv_path.exists():
            out.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"normalized {path}")


if __name__ == "__main__":
    main()
