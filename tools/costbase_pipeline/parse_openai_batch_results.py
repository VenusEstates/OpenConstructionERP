from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def load_input_record_map(records_dir: Path | None) -> dict[str, dict]:
    if records_dir is None:
        return {}
    mapping = {}
    for path in records_dir.glob("*.jsonl"):
        if path.name.endswith(".requests.jsonl"):
            continue
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                record = json.loads(line)
                mapping[record["custom_id"]] = record
    return mapping


def parse_results(path: Path, input_record_map: dict[str, dict] | None = None) -> pd.DataFrame:
    input_record_map = input_record_map or {}
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            envelope = json.loads(line)
            body = envelope.get("response", {}).get("body", {})
            choices = body.get("choices") or []
            content = choices[0]["message"]["content"] if choices else "{}"
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                payload = {
                    "custom_id": envelope.get("custom_id", ""),
                    "target_text": "",
                    "status": "needs_review",
                    "review_notes": "Model returned non-JSON content.",
                }
            model = body.get("model", "")
            status = payload.get("status", "needs_review")
            if status in {"draft", "translated", "controlled"}:
                status = "reviewed"
            if status not in {"reviewed", "approved", "needs_review"}:
                status = "needs_review"
            custom_id = envelope.get("custom_id", "")
            source_record = input_record_map.get(custom_id, {})
            rows.append(
                {
                    "custom_id": custom_id,
                    "tm_key": source_record.get("tm_key", payload.get("tm_key", "")),
                    "source_lang": source_record.get("source_lang", payload.get("source_lang", "")),
                    "target_lang": source_record.get("target_lang", payload.get("target_lang", "")),
                    "target_text": payload.get("target_text", ""),
                    "status": status,
                    "translator": "openai_batch",
                    "model": model,
                    "review_notes": json.dumps(payload.get("review", {}), ensure_ascii=False, sort_keys=True),
                    "raw_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-jsonl", type=Path, required=True)
    parser.add_argument("--records-dir", type=Path)
    parser.add_argument("--output-parquet", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path)
    args = parser.parse_args()
    df = parse_results(args.input_jsonl, load_input_record_map(args.records_dir))
    args.output_parquet.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.output_parquet, index=False)
    if args.output_csv:
        df.to_csv(args.output_csv, index=False, encoding="utf-8-sig")
    print(json.dumps({"rows": len(df), "output": str(args.output_parquet)}, indent=2))


if __name__ == "__main__":
    main()
