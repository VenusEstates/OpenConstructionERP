"""Strip stray zero-width Unicode characters from source files.

Originally written for the R6 zero-width regression fix (task #135), and still
usable as a maintenance helper when rogue invisibles reappear.

WHAT THIS TOOL WILL NOT TOUCH, AND WHY
--------------------------------------
Four of the characters in this range are not stray formatting. They are
letters, or marks a script cannot be written correctly without, and deleting
them misspells the text:

  U+200C ZERO WIDTH NON-JOINER  Persian (fa) needs it inside a single word to
                                keep a prefix or suffix from fusing into the
                                stem. "poshtiban-giri" (backup) is written
                                with one; without it the word is a misspelling.
                                Arabic (ar) and Urdu (ur) use it the same way.
  U+200D ZERO WIDTH JOINER      Bengali (bn) needs it to form conjuncts, and
                                Devanagari scripts such as Hindi (hi) use it
                                for the same purpose.
  U+200E LEFT-TO-RIGHT MARK     Hebrew (he) and Arabic (ar) need it so a Latin
                                token embedded in a right-to-left sentence -
                                a file extension like ".ifc", a product name,
                                a number - renders on the correct side.
  U+200F RIGHT-TO-LEFT MARK     The mirror case, for the same reason.

An earlier version of this file stripped all four and excepted a single
locale, frontend/src/app/locales/ar.ts, by name. That exception saved Arabic
and nothing else: on 2026-08-17 a run took 35211 U+200C out of fa.ts, 54
U+200D out of bn.ts and 25 U+200E out of he.ts, and the platform shipped
misspelled Persian, Bengali and Hebrew from that day on.

The lesson is not "add three more files to the exception list". A tool whose
safe use depends on the caller knowing something the tool does not is not a
safe tool, so the knowledge lives here now and there is no exception list at
all. If a U+200C really is stray - pasted into an English string, say - remove
it by hand where you can see the word it sits in.

WHAT IT DOES STRIP
------------------
Only characters that carry no spelling in any language we ship: U+200B, U+2060,
U+2061-U+2064, U+2066-U+2069 and U+FEFF.

That list is not written here. It is imported from scripts/check_zero_width.py,
which is also what the CI job and `npm run lint:unicode` call, so detection and
remediation cannot say different things. They used to, and that is how the
2026-08-17 loss happened.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Imported, not restated. STRAY_CHARS is what this tool removes and SPELLING_CHARS
# is what it counts and leaves alone, and both are defined once, in the detector.
# A remediation tool that keeps its own copy of the rule is how the 2026-08-17
# loss happened.
from check_zero_width import SPELLING_CHARS, STRAY_CHARS  # noqa: E402

TARGETS = [
    ("frontend/src", (".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".md", ".snap")),
    ("marketing-site", (".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".md", ".py")),
]


def strip_file(path: str, stats: dict[str, int], kept: dict[str, int]) -> bool:
    """Strip the stray characters in place; return True if the file changed."""
    with open(path, encoding="utf-8", newline="") as fh:
        data = fh.read()

    for ch in SPELLING_CHARS:
        n = data.count(ch)
        if n:
            kept[ch] = kept.get(ch, 0) + n

    if not any(c in data for c in STRAY_CHARS):
        return False

    new_data = data
    for ch in STRAY_CHARS:
        n = new_data.count(ch)
        if n:
            stats[ch] = stats.get(ch, 0) + n
            new_data = new_data.replace(ch, "")

    if new_data != data:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_data)
        return True
    return False


def main() -> int:
    stats: dict[str, int] = {}
    kept: dict[str, int] = {}
    files_modified: list[str] = []
    total_audited = 0

    for base, exts in TARGETS:
        if not os.path.isdir(base):
            continue
        for dirpath, _, files in os.walk(base):
            norm_dir = dirpath.replace(os.sep, "/")
            if "/node_modules" in norm_dir or "/dist" in norm_dir:
                continue
            for f in files:
                if not f.endswith(exts):
                    continue
                p = os.path.normpath(os.path.join(dirpath, f))
                total_audited += 1
                if strip_file(p, stats, kept):
                    files_modified.append(p)

    sys.stdout.reconfigure(encoding="utf-8")
    print(f"TOTAL FILES AUDITED: {total_audited}")
    print(f"FILES MODIFIED: {len(files_modified)}")

    print("\nSTRIPPED COUNTS BY CHARACTER:")
    if not stats:
        print("  (nothing stray found)")
    for ch, name in STRAY_CHARS.items():
        if stats.get(ch):
            print(f"  {name}: {stats[ch]}")

    print("\nLEFT ALONE, THESE ARE LETTERS AND MARKS, NOT STRAY FORMATTING:")
    if not kept:
        print("  (none present)")
    for ch, name in SPELLING_CHARS.items():
        if kept.get(ch):
            print(f"  {name}: {kept[ch]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
