"""‌⁠‍Apply per-lang patch JSONs back into i18n-fallbacks.ts.

Reads ``./tmp/i18n/patch-{lang}.json`` files (each is ``{key: translation}``)
and rewrites the file in place — for each lang block, MISSING keys are
appended just before the trailing ``},`` of that block, and BLEED keys
(values still equal to EN at the same key) are replaced inline.

Idempotent: applying the same patch twice produces the same file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src" / "app" / "i18n-fallbacks.ts"
TMP = ROOT / "tmp" / "i18n"


def _ts_quote(value: str) -> str:
    """‌⁠‍Quote a value for the i18n-fallbacks.ts single-quoted-string format.

    The file uses single-quoted JS literals; we escape backslashes and the
    quote char itself, leave everything else literal (including non-Latin
    UTF-8 since the file is UTF-8). Newlines are not allowed in entries —
    flatten them.
    """
    s = value.replace("\\", "\\\\").replace("'", "\\'")
    s = s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return f"'{s}'"


def _block_bounds(source: str, lang: str) -> tuple[int, int]:
    """‌⁠‍Return (translation_start, translation_end_brace_index) for ``lang``.

    The translation_end_brace_index is the index of the ``}`` that closes
    the ``translation: { ... }`` map.
    """
    # Find the lang header.
    header = re.search(rf"^  {re.escape(lang)}:\s*\{{\s*$", source, re.MULTILINE)
    if not header:
        raise SystemExit(f"lang block {lang} not found")
    # Find the next 'translation: {' after the header.
    t_start = source.find("translation: {", header.end())
    if t_start < 0:
        raise SystemExit(f"translation block for {lang} not found")
    body_start = source.find("{", t_start) + 1
    # Walk the body counting braces until we hit the matching '}'.
    depth = 1
    i = body_start
    while i < len(source) and depth > 0:
        c = source[i]
        if c == "'":
            # Skip the string literal.
            i += 1
            while i < len(source):
                if source[i] == "\\":
                    i += 2
                    continue
                if source[i] == "'":
                    i += 1
                    break
                i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return body_start, i
        i += 1
    raise SystemExit(f"unbalanced translation block for {lang}")


def _replace_or_append(
    source: str,
    lang: str,
    patch: dict[str, str],
    en: dict[str, str],
) -> str:
    body_start, body_end = _block_bounds(source, lang)
    body = source[body_start:body_end]
    appended_lines: list[str] = []

    new_body = body
    for key, translation in patch.items():
        if not isinstance(translation, str) or not translation.strip():
            continue
        # If the key already exists in the body, replace its value.
        # Match the line `      'key': '...',`
        # The trailing run is matched with a lookahead so the line's own ending
        # stays outside the match. A plain `\s*$` swallows it - and, being
        # greedy, any blank lines after it - and the replacement then writes an
        # ending of its own choosing back in its place.
        key_pattern = re.compile(
            r"^(\s+)'" + re.escape(key) + r"':\s*'((?:\\'|[^'])*)',?[^\S\r\n]*(?=\r?\n|\Z)",
            re.MULTILINE,
        )
        m = key_pattern.search(new_body)
        if m:
            new_line = f"{m.group(1)}'{key}': {_ts_quote(translation)},"
            new_body = new_body[: m.start()] + new_line + new_body[m.end() :]
        else:
            appended_lines.append(f"      '{key}': {_ts_quote(translation)},")

    if appended_lines:
        # Append before the closing brace, ensure the final char before '}'
        # is a newline. New lines get the ending the block already uses, so a
        # CRLF file does not come back with a handful of LF lines in it.
        eol = "\r\n" if "\r\n" in body else "\n"
        tail = new_body.rstrip()
        if not tail.endswith(","):
            tail += ","
        new_body = tail + eol + eol.join(appended_lines) + eol + "    "

    return source[:body_start] + new_body + source[body_end:]


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"source not found: {SRC}")
    if not TMP.exists():
        raise SystemExit(f"tmp dir missing: {TMP}; run i18n_extract.py first")
    en_path = TMP / "en-source.json"
    if not en_path.exists():
        raise SystemExit("en-source.json missing; run i18n_extract.py first")
    en = json.loads(en_path.read_text(encoding="utf-8"))

    # newline="" on both ends. Path.read_text/write_text translate line endings,
    # so this file came back rewritten end to end - to CRLF on Windows, to LF
    # everywhere else - whether or not a single value had changed.
    with open(SRC, encoding="utf-8", newline="") as fh:
        source = fh.read()

    # Merge chunked patches (`patch-vi-1.json` + `patch-vi-2.json` → vi).
    # Lang code is the leading segment after `patch-`; anything after a
    # second hyphen is treated as a chunk index. Lang codes themselves
    # don't contain hyphens in this codebase, so this is unambiguous.
    by_lang: dict[str, dict[str, str]] = {}
    for patch_path in sorted(TMP.glob("patch-*.json")):
        rest = patch_path.stem[len("patch-") :]
        lang = rest.split("-", 1)[0]
        try:
            chunk = json.loads(patch_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"skipping {patch_path.name}: invalid JSON ({e})")
            continue
        if not isinstance(chunk, dict) or not chunk:
            continue
        merged = by_lang.setdefault(lang, {})
        merged.update(chunk)

    applied = 0
    for lang, patch in sorted(by_lang.items()):
        # Drop any patch keys that don't exist in EN — these are typos /
        # leftover scratch entries; we only want to add legit translations.
        clean: dict[str, str] = {k: v for k, v in patch.items() if k in en}
        skipped = len(patch) - len(clean)
        before = source
        source = _replace_or_append(source, lang, clean, en)
        delta = len(source) - len(before)
        applied += 1
        print(
            f"applied {lang}: {len(clean)} keys"
            + (f" (skipped {skipped} unknown)" if skipped else "")
            + f"  delta_size={delta:+}"
        )
    if applied == 0:
        print("no patches found under tmp/i18n/patch-*.json")
        return
    with open(SRC, "w", encoding="utf-8", newline="") as fh:
        fh.write(source)
    print(f"rewrote {SRC.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
