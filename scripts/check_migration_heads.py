#!/usr/bin/env python3
"""Fail if the Alembic revision graph has more than one head.

Written the day three agents added a revision each in parallel and two of them
chained off the same parent. Every one of them checked ``alembic heads`` inside
its own task and every one of them truthfully reported a single head, because
each ran before the others had written their file. The fork was only visible to
someone looking at all three at once, which is exactly the kind of thing a gate
should be doing instead of a person.

A second head is not a cosmetic problem. ``alembic upgrade head`` refuses to
run against an ambiguous head, so the fork is discovered by whoever upgrades
first, which on a release is the user.

Why this reads the files instead of asking Alembic
--------------------------------------------------
``ScriptDirectory`` would answer the same question and would answer it more
authoritatively. It also imports the Alembic and SQLAlchemy stack, which on at
least one developer machine here wedges for minutes on an unrelated Windows
management call during ``import sqlalchemy``. A gate that hangs is a gate people
disable. This one reads text and needs no database, no driver and no config.

The cost of that choice is that the parser has to be right, and the first draft
was not: it read ``down_revision`` with a single-line regex, so every merge
revision whose parents are a tuple spread over several lines lost its parents
and was counted as a base. It reported 43 heads and 18 bases. The heads number
looked alarming but plausible; the bases number is what gave it away, because an
Alembic graph has exactly one base. That is why this script prints bases and
edge counts rather than only the answer it was asked for: the number that
refutes a broken scan is usually not the number you were looking at.
"""

from __future__ import annotations

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSIONS = os.path.join(REPO_ROOT, "backend", "alembic", "versions")

# A scan over an empty or mostly-missing directory reports "one head" for the
# same reason a test suite with no tests reports success. The tree held 324
# revisions when this was written; anything under this floor means the scan did
# not see the migration tree and must say so rather than pass.
MIN_EXPECTED_REVISIONS = 100

REVISION = re.compile(r"^revision(?::[^=]+)?\s*=\s*[\"']([^\"']+)[\"']", re.M)
# Capture the whole ``down_revision`` assignment, up to the next module-level
# name, so a tuple written over several lines is read in full.
DOWN_REVISION = re.compile(
    r"^down_revision(?::[^=]+)?\s*=\s*(.*?)(?=^[A-Za-z_]\w*\s*[:=]|^def |^class |\Z)",
    re.M | re.S,
)
QUOTED = re.compile(r"[\"']([^\"']+)[\"']")


def read_graph(
    versions_dir: str,
) -> tuple[dict[str, str], dict[str, list[str]], list[str]]:
    """Return {revision: filename}, {revision: [parents]}, [files with no revision]."""
    revisions: dict[str, str] = {}
    parents: dict[str, list[str]] = {}
    unparsed: list[str] = []

    for name in sorted(os.listdir(versions_dir)):
        if not name.endswith(".py") or name.startswith("__"):
            continue
        with open(os.path.join(versions_dir, name), encoding="utf-8") as fh:
            text = fh.read()
        found = REVISION.search(text)
        if found is None:
            unparsed.append(name)
            continue
        revision = found.group(1)
        if revision in revisions:
            # Two files claiming one id is its own defect: Alembic would load
            # whichever it walked last and silently drop the other.
            unparsed.append(f"{name} (duplicate id {revision}, also in {revisions[revision]})")
            continue
        revisions[revision] = name
        block = DOWN_REVISION.search(text)
        parents[revision] = QUOTED.findall(block.group(1)) if block else []

    return revisions, parents, unparsed


def heads_of(revisions: dict[str, str], parents: dict[str, list[str]]) -> list[str]:
    """Revisions nobody names as a parent. More than one means a fork."""
    referenced = {p for ps in parents.values() for p in ps}
    return sorted(r for r in revisions if r not in referenced)


def main() -> int:
    if not os.path.isdir(VERSIONS):
        print(f"ERROR: no versions directory at {VERSIONS}")
        return 1

    revisions, parents, unparsed = read_graph(VERSIONS)
    edges = sum(len(p) for p in parents.values())
    referenced = {p for ps in parents.values() for p in ps}
    heads = heads_of(revisions, parents)
    bases = sorted(r for r, ps in parents.items() if not ps)
    dangling = sorted(p for p in referenced if p not in revisions)

    print(
        f"migration graph: {len(revisions)} revisions, {edges} parent edges, {len(heads)} head(s), {len(bases)} base(s)"
    )

    failed = False

    if len(revisions) < MIN_EXPECTED_REVISIONS:
        print(
            f"ERROR: only {len(revisions)} revisions found under {VERSIONS}, expected at "
            f"least {MIN_EXPECTED_REVISIONS}. The scan did not see the migration tree, so "
            f"its answer about heads means nothing."
        )
        failed = True

    if unparsed:
        print(f"ERROR: {len(unparsed)} file(s) in versions/ carry no usable revision id:")
        for name in unparsed:
            print(f"  {name}")
        failed = True

    if dangling:
        print(f"ERROR: {len(dangling)} revision(s) named as a parent but not present:")
        for revision in dangling:
            print(f"  {revision}")
        failed = True

    if len(bases) != 1:
        print(f"ERROR: expected exactly one base, found {len(bases)}:")
        for revision in bases:
            print(f"  {revision}   ({revisions[revision]})")
        failed = True

    if len(heads) != 1:
        print(f"ERROR: expected exactly one head, found {len(heads)}:")
        for revision in heads:
            print(f"  {revision}   ({revisions[revision]})")
        print(
            "\nRe-chain the newer revision onto the older one so the line stays linear. "
            "Do not add a merge revision to close a fork that has not shipped yet: it is "
            "permanent, and it records a branch nobody ever ran."
        )
        failed = True

    if failed:
        return 1

    print(f"single head: {heads[0]}   ({revisions[heads[0]]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
