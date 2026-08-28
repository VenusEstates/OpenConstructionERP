from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import time
from pathlib import Path
from urllib import error, request

import pandas as pd

PIPELINE_DIR = Path(__file__).resolve().parent
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """You are translating construction cost catalogue translation-memory units.

Return only valid JSON with these fields:
custom_id, tm_key, source_lang, target_lang, target_text, status, used_terms, preserved_tokens, qa, review.

Rules:
- Translate from source_lang, not from a rough English pivot.
- Preserve protected_tokens byte-identically.
- Protected tokens include the full unit/code string. Keep `40x1,9 mm` exactly as `40x1,9 mm`;
  never write `40x1,9 مم`, `40x1,9 mm.` inside the token, or translated unit symbols.
- Do not add, omit, infer, summarize, repair, convert units, or convert currencies.
- Use construction estimating / bill-of-quantities terminology.
- Use status="reviewed" when the translation is complete. Do not use "partial".
- Empty or untranslatable source must return target_text="" and status="needs_review".
- Stored digits remain Western Arabic digits.
"""


LOCALIZED_UNIT_VARIANTS = {
    "mm": ["مم", "ملم", "ميليمتر", "مليمتر"],
    "cm": ["سم", "سنتيمتر"],
    "m": ["م", "متر"],
    "m2": ["م2", "م²", "متر مربع", "مترًا مربعًا"],
    "m3": ["م3", "م³", "متر مكعب", "مترًا مكعبًا"],
    "kg": ["كغ", "كجم", "كيلوغرام"],
    "km": ["كم", "كيلومتر"],
    "HP": ["حصان"],
}


UNIT_TOKEN_RE = re.compile(
    r"^(?P<measure>.+?)\s*(?P<unit>m2|m3|mm|cm|km|kg|m|HP)$",
    re.IGNORECASE,
)


def load_dotenv_key(name: str) -> str | None:
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == name:
            return value.strip().strip("\"'")
    return None


def read_records(paths: list[Path]) -> list[dict]:
    records = []
    for path in paths:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    record = json.loads(line)
                    record["_source_file"] = str(path)
                    records.append(record)
    return records


def restore_protected_tokens(target_text: str, protected_tokens: list[str]) -> str:
    """Restore byte-exact protected unit tokens after Arabic unit localization.

    This deliberately handles only deterministic cases where the same numeric
    measure is present and the model localized the adjacent unit. It does not
    invent missing numbers or repair arbitrary code changes.
    """

    restored = target_text
    for token in sorted(set(protected_tokens), key=len, reverse=True):
        if token in restored:
            continue
        match = UNIT_TOKEN_RE.match(str(token))
        if not match:
            continue
        measure = " ".join(match.group("measure").split())
        unit = match.group("unit")
        variants = LOCALIZED_UNIT_VARIANTS.get(unit) or LOCALIZED_UNIT_VARIANTS.get(unit.lower(), [])
        if not variants:
            continue
        measure_pattern = "".join("[xX×]" if ch in {"x", "X", "×"} else re.escape(ch) for ch in measure)
        measure_pattern = measure_pattern.replace(r"\ ", r"\s+")
        unit_pattern = "|".join(re.escape(variant) for variant in sorted(variants, key=len, reverse=True))
        pattern = re.compile(rf"(?<![\w.,/])({measure_pattern})\s*(?:{unit_pattern})")
        restored = pattern.sub(token, restored, count=1)
    return restored


def missing_protected_tokens(target_text: str, protected_tokens: list[str]) -> list[str]:
    return [token for token in protected_tokens if token and token not in target_text]


def call_openrouter(record: dict, *, api_key: str, model: str, max_retries: int, max_tokens: int) -> dict:
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(record, ensure_ascii=False, sort_keys=True),
            },
        ],
    }
    payload = json.dumps(body).encode("utf-8")
    for attempt in range(max_retries + 1):
        req = request.Request(OPENROUTER_URL, method="POST", data=payload)
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("HTTP-Referer", "https://local.openconstructionerp.invalid")
        req.add_header("X-Title", "CWICR Translation Pipeline")
        try:
            with request.urlopen(req, timeout=120) as resp:
                response_body = json.loads(resp.read().decode("utf-8"))
            content = response_body["choices"][0]["message"]["content"]
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = {
                    "target_text": "",
                    "status": "needs_review",
                    "review": {"reason": "non_json"},
                }
            return {
                "record": record,
                "response": response_body,
                "payload": parsed,
                "error": None,
            }
        except error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            if exc.code in {429, 500, 502, 503, 504} and attempt < max_retries:
                time.sleep(min(30, 2**attempt))
                continue
            return {
                "record": record,
                "response": None,
                "payload": None,
                "error": f"HTTP {exc.code}: {err_body}",
            }
        except Exception as exc:  # network/timeouts
            if attempt < max_retries:
                time.sleep(min(30, 2**attempt))
                continue
            return {
                "record": record,
                "response": None,
                "payload": None,
                "error": str(exc),
            }
    raise AssertionError("unreachable")


def result_to_row(result: dict, model: str) -> dict:
    record = result["record"]
    payload = result.get("payload") or {}
    status = payload.get("status", "needs_review")
    if status in {"draft", "translated", "controlled"}:
        status = "reviewed"
    if status not in {"reviewed", "approved", "needs_review"}:
        status = "needs_review"
    if result.get("error"):
        status = "needs_review"
    target_text = payload.get("target_text", "") if not result.get("error") else ""
    protected = [str(token) for token in record.get("protected_tokens", []) if str(token)]
    if target_text and protected:
        target_text = restore_protected_tokens(str(target_text), protected)
        missing = missing_protected_tokens(target_text, protected)
        if missing:
            status = "needs_review"
            review = payload.get("review", {})
            if not isinstance(review, dict):
                review = {"model_review": review}
            review["protected_tokens_missing"] = missing
            payload["review"] = review
    return {
        "custom_id": record["custom_id"],
        "tm_key": record["tm_key"],
        "source_lang": record["source_lang"],
        "target_lang": record["target_lang"],
        "target_text": target_text,
        "status": status,
        "translator": "openrouter",
        "model": (result.get("response") or {}).get("model", model),
        "review_notes": json.dumps(
            payload.get("review", {"error": result.get("error")}),
            ensure_ascii=False,
            sort_keys=True,
        ),
        "raw_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
    }


def run_records(
    records: list[dict],
    *,
    api_key: str,
    model: str,
    concurrency: int,
    max_retries: int,
    max_tokens: int = 2048,
    abort_http_codes: set[int] | None = None,
) -> pd.DataFrame:
    rows = []
    abort_markers = tuple(f"HTTP {code}:" for code in sorted(abort_http_codes or set()))
    if abort_markers and concurrency == 1:
        for idx, record in enumerate(records, start=1):
            row = result_to_row(
                call_openrouter(
                    record,
                    api_key=api_key,
                    model=model,
                    max_retries=max_retries,
                    max_tokens=max_tokens,
                ),
                model,
            )
            if any(marker in row["review_notes"] for marker in abort_markers):
                raise RuntimeError(f"aborting on payment/rate-limit error: {row['review_notes']}")
            rows.append(row)
            if idx % 100 == 0:
                print(f"completed {idx}/{len(records)}", flush=True)
        return pd.DataFrame(rows)

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(
                call_openrouter,
                record,
                api_key=api_key,
                model=model,
                max_retries=max_retries,
                max_tokens=max_tokens,
            )
            for record in records
        ]
        for idx, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            row = result_to_row(future.result(), model)
            if any(marker in row["review_notes"] for marker in abort_markers):
                raise RuntimeError(f"aborting on payment/rate-limit error: {row['review_notes']}")
            rows.append(row)
            if idx % 100 == 0:
                print(f"completed {idx}/{len(records)}", flush=True)
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-jsonl", nargs="+", type=Path, required=True)
    parser.add_argument("--out-parquet", type=Path, required=True)
    parser.add_argument("--out-csv", type=Path)
    parser.add_argument("--model", default="openai/gpt-4.1-mini")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--abort-http-codes", nargs="*", type=int, default=[])
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY") or load_dotenv_key("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is not set.")
    records = read_records(args.input_jsonl)
    if args.limit:
        records = records[: args.limit]
    df = run_records(
        records,
        api_key=api_key,
        model=args.model,
        concurrency=args.concurrency,
        max_retries=args.max_retries,
        max_tokens=args.max_tokens,
        abort_http_codes=set(args.abort_http_codes),
    )
    args.out_parquet.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out_parquet, index=False)
    if args.out_csv:
        df.to_csv(args.out_csv, index=False, encoding="utf-8-sig")
    print(json.dumps({"rows": len(df), "out": str(args.out_parquet)}, indent=2))


if __name__ == "__main__":
    main()
