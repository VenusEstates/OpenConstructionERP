from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
from pathlib import Path

import pandas as pd
from run_openrouter_jsonl import load_dotenv_key, read_records, run_records


def expected_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def output_for(input_path: Path, shard_out_dir: Path) -> Path:
    return shard_out_dir / f"{input_path.stem}_openrouter.parquet"


def is_complete(input_path: Path, output_path: Path) -> bool:
    if not output_path.exists():
        return False
    try:
        return len(pd.read_parquet(output_path, columns=["tm_key"])) == expected_rows(input_path)
    except Exception:
        return False


def run_one(
    input_path: Path,
    *,
    shard_out_dir: Path,
    api_key: str,
    model: str,
    concurrency: int,
    max_retries: int,
    max_tokens: int,
    abort_http_codes: set[int] | None,
) -> dict:
    out = output_for(input_path, shard_out_dir)
    if is_complete(input_path, out):
        return {"input": str(input_path), "out": str(out), "status": "skipped_complete"}

    records = read_records([input_path])
    df = run_records(
        records,
        api_key=api_key,
        model=model,
        concurrency=concurrency,
        max_retries=max_retries,
        max_tokens=max_tokens,
        abort_http_codes=abort_http_codes,
    )
    shard_out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.parquet")
    df.to_parquet(tmp, index=False)
    tmp.replace(out)
    df.to_csv(out.with_suffix(".csv"), index=False, encoding="utf-8-sig")
    return {
        "input": str(input_path),
        "out": str(out),
        "status": "written",
        "rows": int(len(df)),
    }


def combine_outputs(
    input_paths: list[Path],
    shard_out_dir: Path,
    out_parquet: Path,
    out_csv: Path | None,
) -> dict:
    frames = []
    missing = []
    for input_path in input_paths:
        out = output_for(input_path, shard_out_dir)
        if not is_complete(input_path, out):
            missing.append(str(input_path))
            continue
        frames.append(pd.read_parquet(out))
    if missing:
        return {
            "combined": False,
            "complete_shards": len(frames),
            "missing_shards": missing,
        }
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_parquet, index=False)
    if out_csv:
        df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    return {"combined": True, "rows": int(len(df)), "out": str(out_parquet)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-jsonl", nargs="+", type=Path, required=True)
    parser.add_argument("--shard-out-dir", type=Path, required=True)
    parser.add_argument("--out-parquet", type=Path, required=True)
    parser.add_argument("--out-csv", type=Path)
    parser.add_argument("--model", default="openai/gpt-4.1-mini")
    parser.add_argument("--record-concurrency", type=int, default=4)
    parser.add_argument("--shard-workers", type=int, default=2)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--abort-http-codes", nargs="*", type=int, default=[])
    parser.add_argument("--max-shards", type=int, default=0)
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY") or load_dotenv_key("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is not set.")

    input_paths = sorted(args.input_jsonl)
    args.shard_out_dir.mkdir(parents=True, exist_ok=True)
    if args.max_shards:
        incomplete = [path for path in input_paths if not is_complete(path, output_for(path, args.shard_out_dir))]
        limited = set(incomplete[: args.max_shards])
        input_paths = [
            path for path in input_paths if path in limited or is_complete(path, output_for(path, args.shard_out_dir))
        ]
    summaries = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.shard_workers) as executor:
        futures = [
            executor.submit(
                run_one,
                input_path,
                shard_out_dir=args.shard_out_dir,
                api_key=api_key,
                model=args.model,
                concurrency=args.record_concurrency,
                max_retries=args.max_retries,
                max_tokens=args.max_tokens,
                abort_http_codes=set(args.abort_http_codes),
            )
            for input_path in input_paths
        ]
        for future in concurrent.futures.as_completed(futures):
            summary = future.result()
            summaries.append(summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)

    combined = combine_outputs(input_paths, args.shard_out_dir, args.out_parquet, args.out_csv)
    print(json.dumps({"shards": summaries, "combined": combined}, ensure_ascii=False, indent=2))
    if not combined.get("combined"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
