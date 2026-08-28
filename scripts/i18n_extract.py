"""‌⁠‍Extract EN source + per-lang missing/bleed maps from i18n-fallbacks.ts.

Output written under ``./tmp/i18n/``:
  - en-source.json                    — { key: en_value } (all EN keys)
  - missing-{lang}.json               — { key: en_value } for keys lang is
                                        missing OR has bleed (same as EN
                                        and looks like English).
  - state.json                        — summary stats per lang.

Sets up the parallel-agent pipeline: each agent reads its missing-{lang}.json,
translates, writes patch-{lang}.json. The merge step (``i18n_apply.py``)
reads all patches and rewrites i18n-fallbacks.ts in one pass.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src" / "app" / "i18n-fallbacks.ts"
OUT = ROOT / "tmp" / "i18n"


# Each language block in i18n-fallbacks.ts looks like:
#   <code>: {
#     translation: {
#       <kebab-key>: '<value>',
#       ...
#     },
#   },
#
# Capture the language block boundaries by their two-letter code header.
LANG_BLOCK_RE = re.compile(
    r"^\s{2}([a-z]{2,3}):\s*\{\s*$\s+translation:\s*\{",
    re.MULTILINE,
)
# A single key:value entry inside a translation block. Values are usually
# single-quoted; we tolerate escaped quotes and multi-line via a non-greedy
# match terminated at the unescaped trailing quote + comma. Keys are the
# usual i18next dot/underscore form.
KEY_RE = re.compile(
    r"^\s+'([^']+)':\s*'((?:\\'|[^'])*)',?\s*$",
    re.MULTILINE,
)

# Undo the escapes a JS single-quoted literal can carry, and touch nothing else.
#
# This used to read ``m.group(2).encode().decode("unicode_escape")``. That codec
# takes the value's UTF-8 bytes and reads them back as latin-1, so every
# character above U+007F came out as the two or three characters its UTF-8
# encoding happens to spell: Cyrillic, CJK and Arabic values were destroyed, and
# a zero-width character - invisible, so nobody saw the damage - came out as
# three visible ones. The file is already decoded text by the time it reaches
# here, so nothing above U+007F needs decoding at all.
#
# The surrogate-pair alternative comes first so that an astral character written
# as two \u escapes is rebuilt as one code point instead of two lone surrogates,
# which cannot be encoded back to UTF-8.
_ESCAPE_RE = re.compile(
    r"\\u([dD][89abAB][0-9a-fA-F]{2})\\u([dD][c-fC-F][0-9a-fA-F]{2})"
    r"|\\u([0-9a-fA-F]{4})"
    r"|\\x([0-9a-fA-F]{2})"
    r"|\\(.)",
    re.DOTALL,
)
# No entry for "0": in JS `\0` is NUL only when no digit follows it, and `\01`
# is a legacy octal escape. An escape that is not in this table falls through to
# the character itself, which is the safer way to be wrong about one.
_SHORT_ESCAPES = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}


def _unescape(literal: str) -> str:
    """Decode a JS string-literal body, leaving every literal character alone."""

    def replace(m: re.Match[str]) -> str:
        high, low, short_u, hex_x, other = m.groups()
        if high is not None:
            return chr(0x10000 + ((int(high, 16) - 0xD800) << 10) + (int(low, 16) - 0xDC00))
        if short_u is not None:
            return chr(int(short_u, 16))
        if hex_x is not None:
            return chr(int(hex_x, 16))
        return _SHORT_ESCAPES.get(other, other)

    return _ESCAPE_RE.sub(replace, literal)


def parse_blocks(source: str) -> dict[str, dict[str, str]]:
    """‌⁠‍Return ``{lang: {key: value}}`` for every language in the file."""
    blocks: dict[str, dict[str, str]] = {}

    starts = [(m.group(1), m.start()) for m in LANG_BLOCK_RE.finditer(source)]
    for i, (lang, pos) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(source)
        chunk = source[pos:end]
        kv: dict[str, str] = {}
        for m in KEY_RE.finditer(chunk):
            key = m.group(1)
            # strip trailing/leading whitespace; preserve internal
            kv[key] = _unescape(m.group(2))
        blocks[lang] = kv
    return blocks


# Heuristic: a value is "English bleed" when it equals the EN value AND
# looks like English prose — at least 2 ASCII word tokens, none of which
# are obvious code/identifiers/URLs/placeholders. Cyrillic, Han, Hiragana,
# Katakana, Hangul, Devanagari, Thai, Arabic etc. are non-Latin so any
# such char in the value disqualifies it from "bleed."
WORD_RE = re.compile(r"[A-Za-z]{2,}")
NON_LATIN_RE = re.compile(r"[Ѐ-ӿ֐-׿؀-ۿऀ-ॿ฀-๿぀-ヿ㐀-䶿一-鿿가-힯]")


def looks_english(value: str) -> bool:
    if not value or not value.strip():
        return False
    if NON_LATIN_RE.search(value):
        return False
    # ignore values that are mostly placeholders/code/URLs
    stripped = re.sub(r"\{\{[^}]+\}\}|\{[^}]+\}|<[^>]+>|https?://\S+", "", value)
    words = WORD_RE.findall(stripped)
    if len(words) < 2:
        return False
    # Skip identifiers / acronyms-only
    real_words = [w for w in words if not w.isupper()]
    return len(real_words) >= 1


def main() -> None:
    # Created here rather than at import time: importing this module for its
    # parser should not leave a directory behind in the working tree.
    OUT.mkdir(parents=True, exist_ok=True)
    source = SRC.read_text(encoding="utf-8")
    blocks = parse_blocks(source)
    if "en" not in blocks:
        raise SystemExit("EN block not found")
    en = blocks["en"]
    (OUT / "en-source.json").write_text(json.dumps(en, ensure_ascii=False, indent=2), encoding="utf-8")
    state: dict[str, dict[str, int]] = {}
    for lang, kv in blocks.items():
        if lang == "en":
            continue
        missing: dict[str, str] = {}
        bleed_count = 0
        for k, en_v in en.items():
            v = kv.get(k)
            if v is None:
                missing[k] = en_v
                continue
            if v == en_v and looks_english(v):
                missing[k] = en_v
                bleed_count += 1
        state[lang] = {
            "total": len(kv),
            "bleed": bleed_count,
            "missing_or_bleed": len(missing),
        }
        (OUT / f"missing-{lang}.json").write_text(
            json.dumps(missing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    (OUT / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote en-source.json + {len(state)} missing-{{lang}}.json files to {OUT}")
    for lang, s in sorted(state.items(), key=lambda kv: -kv[1]["missing_or_bleed"]):
        print(f"  {lang}: {s['missing_or_bleed']:>4} keys to fix (bleed={s['bleed']})")


if __name__ == "__main__":
    main()
