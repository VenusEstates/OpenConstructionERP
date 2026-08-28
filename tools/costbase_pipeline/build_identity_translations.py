from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

PIPELINE_DIR = Path(__file__).resolve().parent
DEFAULT_CORPUS = PIPELINE_DIR / "outputs" / "translation_corpus.parquet"
DEFAULT_MANIFEST = PIPELINE_DIR / "outputs" / "translation_manifest.json"
DEFAULT_OUT = PIPELINE_DIR / "outputs" / "identity_translations.parquet"


def load_target_languages(manifest_path: Path) -> set[str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return set(manifest["target_languages"])


def build_identity_translations(corpus: pd.DataFrame, target_languages: set[str]) -> pd.DataFrame:
    rows = []
    eligible = corpus[corpus["source_lang"].isin(target_languages)].copy()
    for row in eligible.itertuples(index=False):
        rows.append(
            {
                "custom_id": f"identity:{row.tm_key[:12]}:{row.source_lang}:v1",
                "tm_key": row.tm_key,
                "source_lang": row.source_lang,
                "target_lang": row.source_lang,
                "target_text": row.source_text,
                "status": "reviewed",
                "translator": "identity",
                "model": "none",
                "review_notes": "Source language equals target language; NFC-normalized source retained.",
                "raw_payload": "",
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    corpus = pd.read_parquet(args.corpus)
    targets = load_target_languages(args.manifest)
    table = build_identity_translations(corpus, targets)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(args.out, index=False)
    table.to_csv(args.out.with_suffix(".csv"), index=False, encoding="utf-8-sig")
    print(json.dumps({"rows": int(len(table)), "out": str(args.out)}, indent=2))


if __name__ == "__main__":
    main()
