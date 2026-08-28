from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib import error, request

API_BASE = "https://api.openai.com/v1"


def load_dotenv_key() -> str | None:
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == "OPENAI_API_KEY":
            return value.strip().strip("\"'")
    return None


def _api_request(
    method: str,
    path: str,
    api_key: str,
    *,
    body: bytes | None = None,
    content_type: str = "application/json",
) -> dict:
    req = request.Request(f"{API_BASE}{path}", method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    if body is not None:
        req.add_header("Content-Type", content_type)
        req.data = body
    try:
        with request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API error {exc.code}: {body}") from exc


def upload_file(path: Path, api_key: str) -> str:
    boundary = "----cwicrTranslationBoundary"
    content = path.read_bytes()
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nbatch\r\n'.encode(),
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            "Content-Type: application/jsonl\r\n\r\n"
        ).encode(),
        content,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)
    result = _api_request(
        "POST",
        "/files",
        api_key,
        body=body,
        content_type=f"multipart/form-data; boundary={boundary}",
    )
    return result["id"]


def create_batch(file_id: str, api_key: str, completion_window: str) -> dict:
    payload = {
        "input_file_id": file_id,
        "endpoint": "/v1/chat/completions",
        "completion_window": completion_window,
    }
    return _api_request("POST", "/batches", api_key, body=json.dumps(payload).encode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests-jsonl", type=Path, required=True)
    parser.add_argument("--completion-window", default="24h")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY") or load_dotenv_key()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set. Set it in your shell; do not write it into files.")

    if args.dry_run:
        lines = sum(1 for line in args.requests_jsonl.read_text(encoding="utf-8").splitlines() if line.strip())
        print(
            json.dumps(
                {"dry_run": True, "requests": lines, "file": str(args.requests_jsonl)},
                indent=2,
            )
        )
        return

    file_id = upload_file(args.requests_jsonl, api_key)
    batch = create_batch(file_id, api_key, args.completion_window)
    print(
        json.dumps(
            {"file_id": file_id, "batch": batch},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
