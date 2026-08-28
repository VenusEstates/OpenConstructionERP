#!/usr/bin/env python3
"""Regenerate the case country-portrait manifest from the asset folder.

The Cases feature prefers a portrait shot for the market a case is written
for: ``prf-<country>-<stem>.webp`` beside the pooled ``prf-<stem>.webp``. Which
of those files have been bought is a property of
``frontend/public/assets/people`` and of nothing else, so this script reads the
folder and writes the answer into a TypeScript module the feature imports.

That is the whole reason the module is generated rather than typed. A list of
"markets we have art for" maintained by hand drifts the moment somebody drops a
webp in without editing it, and nothing compares the two - which is the state
``COMPANY_ART_IDS`` in ``CompanyArt.tsx`` is in. Here the folder stays the
source of truth, the manifest is derived from it, and
``frontend/src/features/cases/caseFaces.test.ts`` fails when the two disagree
in either direction.

Usage::

    python scripts/gen_case_country_portraits.py           # write the module
    python scripts/gen_case_country_portraits.py --check    # exit 1 if stale

The output is deterministic: filenames sorted, one per line, so running it
twice produces no diff and a review sees only the webp that arrived.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PEOPLE_DIR = REPO_ROOT / "frontend" / "public" / "assets" / "people"
MANIFEST = REPO_ROOT / "frontend" / "src" / "features" / "cases" / "countryPortraits.generated.ts"

# A country portrait is a pooled portrait name with a two-letter lowercase
# country code inserted after the ``prf-`` prefix. The shape is the whole rule:
# the stem that follows is never parsed, so a stem the cast has not heard of yet
# still lands in the manifest and is usable the moment a case reaches it.
#
# The shape can only be read one way while no pooled stem starts with a
# two-letter segment of its own (``bim``, ``hse`` and ``mep`` are three). The
# test asserts that, against the real ``ROLE_CAST``, rather than this script
# guessing at it.
COUNTRY_PORTRAIT_RE = re.compile(r"^prf-[a-z]{2}-[a-z0-9-]+\.webp$")

HEADER = """// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// GENERATED FILE - do not edit by hand.
// Regenerate with: python scripts/gen_case_country_portraits.py
//
// The country portraits that exist under frontend/public/assets/people, as
// bare filenames. `caseFaces.ts` consults this before it asks for one, so a
// market nobody has been photographed for costs nothing instead of costing a
// 404 per tile.
//
// Adding art is a folder operation: drop `prf-<country>-<stem>.webp` in beside
// the pooled portraits and run the script above. No TypeScript is written by
// hand here, and `caseFaces.test.ts` fails when this list and the folder
// disagree in either direction, so the step cannot be skipped quietly.
"""

BODY_OPEN = """
/** Filenames only, no path: the folder is `PEOPLE_ASSETS_BASE`. Sorted, so a
 *  regeneration shows only the webp that arrived. */
export const COUNTRY_PORTRAITS: ReadonlySet<string> = new Set<string>([
"""

BODY_CLOSE = """]);
"""


def country_portraits() -> list[str]:
    """The country portrait filenames on disk, sorted."""
    if not PEOPLE_DIR.is_dir():
        raise SystemExit(f"asset folder not found: {PEOPLE_DIR}")
    return sorted(p.name for p in PEOPLE_DIR.iterdir() if COUNTRY_PORTRAIT_RE.match(p.name))


def render(names: list[str]) -> str:
    """The TypeScript module for a list of filenames."""
    entries = "".join(f"  '{name}',\n" for name in names)
    return f"{HEADER}{BODY_OPEN}{entries}{BODY_CLOSE}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 when the committed manifest is stale",
    )
    args = parser.parse_args()

    names = country_portraits()
    wanted = render(names)
    current = MANIFEST.read_text(encoding="utf-8") if MANIFEST.exists() else None

    if args.check:
        if current == wanted:
            print(f"manifest is current ({len(names)} country portraits)")
            return 0
        print(
            f"{MANIFEST.relative_to(REPO_ROOT).as_posix()} is stale - "
            f"the folder holds {len(names)} country portraits. "
            "Run: python scripts/gen_case_country_portraits.py",
            file=sys.stderr,
        )
        return 1

    if current == wanted:
        print(f"manifest already current ({len(names)} country portraits)")
        return 0

    MANIFEST.write_text(wanted, encoding="utf-8", newline="\n")
    print(f"wrote {MANIFEST.relative_to(REPO_ROOT).as_posix()} ({len(names)} country portraits)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
