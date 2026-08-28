from __future__ import annotations

import argparse
import json
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent
DEFAULT_PROMPT = """You are translating construction cost catalogue translation-memory units.

Return only valid JSON with these fields:
custom_id, tm_key, source_lang, target_lang, target_text, status, translator, model,
termbase_version, prompt_version, used_terms, preserved_tokens, qa, review.

Rules:
- Translate from source_lang, not from a rough English pivot.
- Preserve protected_tokens byte-identically.
- Do not add, omit, infer, summarize, repair, convert units, or convert currencies.
- Use construction estimating / bill-of-quantities terminology.
- Empty or untranslatable source must return target_text="" and status="needs_review".
- Stored digits remain Western Arabic digits.
"""


def make_request(record: dict, model: str) -> dict:
    return {
        "custom_id": record["custom_id"],
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": DEFAULT_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(record, ensure_ascii=False, sort_keys=True),
                },
            ],
        },
    }


def convert_batch(input_path: Path, output_path: Path, model: str) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with (
        input_path.open("r", encoding="utf-8") as src,
        output_path.open("w", encoding="utf-8", newline="\n") as dst,
    ):
        for line in src:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            dst.write(json.dumps(make_request(record, model), ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-jsonl", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--model", default="gpt-4.1-mini")
    args = parser.parse_args()
    count = convert_batch(args.input_jsonl, args.output_jsonl, args.model)
    print(json.dumps({"output": str(args.output_jsonl), "requests": count}, indent=2))


if __name__ == "__main__":
    main()
