#!/usr/bin/env python3
# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Refuse a migration that creates a table without first asking whether it exists.

Why this has to be a gate rather than a convention
--------------------------------------------------
The boot path builds the schema with ``Base.metadata.create_all`` before
anything reads ``alembic_version``. That is deliberate and documented in
``app/main.py``: the baseline revision is a no-op, the quickstart entrypoint
never runs ``alembic upgrade head``, and without ``create_all`` a fresh volume
comes up with no tables at all.

The consequence is that on every install this application has ever started,
every table in the model metadata already exists by the time an operator runs
``alembic upgrade head`` by hand. An unguarded ``op.create_table`` then raises
``DuplicateTable``. PostgreSQL runs DDL inside a transaction, so the failure
rolls back the entire run rather than one revision, ``alembic_version`` does not
move, and every later revision is skipped along with the data backfills inside
them. An operator who is twenty-six revisions behind can never catch up, and
each attempt fails at the same table.

That is not hypothetical. It was reported from an installation carried across
four releases, stamped at ``v3258`` while head was ``v3284``, failing on
``oe_eac_block_graph`` every time.

The house pattern was already there and nearly universal: at the time this gate
was written 169 of the 173 revisions that create tables asked an inspector
first. Four did not, and three of those four were consecutive. A convention
followed by 98 percent of the population is one nobody notices breaking, which
is the case for a gate rather than an argument for leaving it to review.

What counts as guarded
----------------------
The revision must consult the database about a table before creating it. In
practice that means ``inspector.get_table_names()`` or ``inspector.has_table``,
which is what the existing revisions use. The check is deliberately shallow: it
asks whether the revision looks at the database at all, not whether the guard is
correctly placed around every statement, because a precise dataflow answer would
cost far more than it buys and the shallow question already catches the whole
observed failure mode.

Read with an AST rather than by grepping. ``op.create_table`` appears inside
docstrings in this tree, and a docstring is not a call: grepping for the text
counted nine revisions that create nothing.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSIONS = ROOT / "backend" / "alembic" / "versions"

# Any one of these means the revision asked the database before it acted.
GUARD_MARKERS = ("get_table_names", "has_table")


def creates_tables(tree: ast.Module) -> bool:
    """Does this module actually call ``op.create_table``?"""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_table"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "op"
        ):
            return True
    return False


def main() -> int:
    if not VERSIONS.is_dir():
        print(f"No migrations directory at {VERSIONS}", file=sys.stderr)
        return 0

    creating = 0
    unguarded: list[str] = []
    unparseable: list[str] = []

    for path in sorted(VERSIONS.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            unparseable.append(f"{path.name}: {exc}")
            continue
        if not creates_tables(tree):
            continue
        creating += 1
        if not any(marker in source for marker in GUARD_MARKERS):
            unguarded.append(path.name)

    # Print the population beside the verdict. A gate that reports "0 problems"
    # without saying how many files it looked at cannot be told apart from a
    # gate whose glob stopped matching anything.
    print(
        f"check_migration_create_table_guarded: {creating} revisions create tables, "
        f"{creating - len(unguarded)} ask the database first"
    )

    if unparseable:
        for line in unparseable:
            print(f"  UNPARSEABLE {line}", file=sys.stderr)
        return 1

    if unguarded:
        print(
            "\nThese revisions call op.create_table without asking whether the table\n"
            "is already there. The boot heal runs create_all before any operator can\n"
            "run alembic, so on a real install these raise DuplicateTable, roll back\n"
            "the whole upgrade, and silently skip every revision that follows.\n",
            file=sys.stderr,
        )
        for name in unguarded:
            print(f"  {name}", file=sys.stderr)
        print(
            "\nFollow the pattern the other revisions use:\n\n"
            "    def _has_table(inspector: sa.engine.reflection.Inspector, name: str) -> bool:\n"
            "        return name in inspector.get_table_names()\n\n"
            "    def upgrade() -> None:\n"
            "        bind = op.get_bind()\n"
            "        inspector = sa.inspect(bind)\n"
            '        if not _has_table(inspector, "oe_your_table"):\n'
            "            op.create_table(...)\n",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
