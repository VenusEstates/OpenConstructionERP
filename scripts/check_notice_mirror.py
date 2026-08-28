#!/usr/bin/env python3
"""Fail when NOTICE and backend/NOTICE have drifted apart.

Why this exists
---------------
``backend/NOTICE`` is a manual byte copy of the root ``NOTICE``. It exists only
because PEP 639 cannot reach outside the project root, so the wheel can convey
the notice only from a copy that sits beside ``pyproject.toml``.

Nothing stopped an edit landing on one side alone. The enforcement was a single
pytest, ``test_licence_text_ships.py::test_the_two_notice_copies_are_identical``,
which runs in the backend lane after a push. That is too late: the person who
edits the root file has usually finished and moved on by the time it goes red,
and the failure surfaces to whoever pushes next.

It is not hypothetical. Commits 9489d2b96 and 891d56ef0 each edited the root
copy and not the backend one, and the lane went red until 863076d86 restored the
byte copy. This script is the local check that would have caught both before
they were committed.

What it does
------------
Compares the two files the way the test does, normalising CRLF to LF first, so
that a checkout with Windows line endings is not reported as drift when the test
that actually gates would pass. A gate stricter than the thing it guards is a
false alarm, not extra safety.

    python scripts/check_notice_mirror.py          # report
    python scripts/check_notice_mirror.py --fix    # copy root over backend

The root file is the source. ``--fix`` copies it over ``backend/NOTICE`` and
never the other way, because the root copy is the one people edit and the
backend copy is the artefact.

Exit codes
----------
0  the two copies agree, or --fix reconciled them
1  they have drifted (and --fix was not given)
2  one of them is missing
"""

from __future__ import annotations

import argparse
import difflib
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ROOT_NOTICE = REPO_ROOT / "NOTICE"
WHEEL_NOTICE = REPO_ROOT / "backend" / "NOTICE"


def _text(path: Path) -> str:
    """Read as the gating test reads: UTF-8, with CRLF normalised to LF."""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--fix",
        action="store_true",
        help="copy the root NOTICE over backend/NOTICE instead of reporting",
    )
    args = parser.parse_args()

    missing = [p for p in (ROOT_NOTICE, WHEEL_NOTICE) if not p.is_file()]
    if missing:
        for path in missing:
            print(f"[FAIL] missing: {path}", file=sys.stderr)
        return 2

    root, wheel = _text(ROOT_NOTICE), _text(WHEEL_NOTICE)
    if root == wheel:
        print(f"NOTICE mirror OK: both copies agree, {len(root.splitlines())} lines")
        return 0

    if args.fix:
        shutil.copyfile(ROOT_NOTICE, WHEEL_NOTICE)
        print("NOTICE mirror repaired: root NOTICE copied over backend/NOTICE.")
        print("Stage backend/NOTICE alongside NOTICE so both land in the same commit.")
        return 0

    diff = list(
        difflib.unified_diff(
            wheel.splitlines(),
            root.splitlines(),
            fromfile="backend/NOTICE",
            tofile="NOTICE",
            lineterm="",
            n=1,
        )
    )
    print("[FAIL] NOTICE and backend/NOTICE have drifted apart.", file=sys.stderr)
    print(
        "\nbackend/NOTICE is a byte copy of the root NOTICE, carried because PEP 639\n"
        "cannot reach outside the project root. Edit the root file, then copy it over:\n"
        "\n    python scripts/check_notice_mirror.py --fix\n"
        "\nand commit both paths together. A notice edited on one side only leaves the\n"
        "wheel describing a set of bundled binaries that is not the one it holds.\n",
        file=sys.stderr,
    )
    shown = diff[:60]
    print("\n".join(shown), file=sys.stderr)
    if len(diff) > len(shown):
        print(f"... {len(diff) - len(shown)} more diff line(s)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
