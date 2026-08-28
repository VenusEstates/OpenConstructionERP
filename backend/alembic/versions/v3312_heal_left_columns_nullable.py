# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""schema: re-tighten nine columns the boot heal had to add nullable.

Nine columns across three tables are NOT NULL in the models and nullable in any
database that was bootstrapped by ``create_all`` plus the boot heal rather than
by migrations. The heal adds a missing column with ADD COLUMN, and until
``ac29c96a2`` (2026-08-24) it could only carry the model's NOT NULL when the
column declared a *server* default. All nine declare their default in Python
only, so all nine went in bare - nullable, and with no DEFAULT either.

The revisions that would have added them correctly did run, and correctly did
nothing: both guard on the column being present, and the heal had already put
it there. That guard is the mechanism. This revision therefore guards on
NULLABILITY instead, which is the property actually in question:

* ``oe_formwork_system.erect_strike_rate``, ``strip_time_days`` and
  ``oe_formwork_assignment.material_unit_cost``, ``labour_unit_cost`` from
  ``v3262_formwork_rate_buildup``
* ``oe_requirements_item.rationale``, ``originator``, ``originator_role``,
  ``phase`` and ``verification_method`` from ``v3285_requirements_cycle``

Values are taken from those two revisions rather than chosen here, so that a
database repaired by this one and a database built by migrations hold the same
values and not merely the same constraints. Both revisions argue their choice in
their own docstrings; the short form is that zero reproduces the total a legacy
formwork row was priced with, and empty string is how these models have always
spelled "not recorded".

The DEFAULT is restored alongside the NOT NULL. ``not_null_divergences`` does
not report a missing default and no reader trips over one, but a heal-built
database and a migration-built one would otherwise still disagree about what an
INSERT omitting the column does.

This is not the half that reaches an ordinary install. The product moves its
schema at boot and stamps head without running revision bodies, so the work is
also registered as two boot-path data repairs - see the ``boot-repair`` lines
below. This revision is what a database upgraded with ``alembic upgrade`` gets,
and it is idempotent against one the repairs already reached.

Revision ID: v3312_heal_left_columns_nullable
Revises: v3310_variation_request_boq
Create Date: 2026-08-29
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v3312_heal_left_columns_nullable"
down_revision: Union[str, Sequence[str], None] = "v3310_variation_request_boq"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_SYSTEM = "oe_formwork_system"
_ASSIGNMENT = "oe_formwork_assignment"
_ITEM = "oe_requirements_item"

#: Column to the SQL literal it is backfilled with and given as its DEFAULT.
#: Held per table, and each table's statements are written out against the
#: module-level constant below rather than against a loop variable. That is not
#: style: ``scripts/check_migration_data_rewrites.py`` resolves the table a
#: statement touches by reading the source, and a table name arriving through a
#: loop variable resolves to nothing, which lands the statement in the
#: unresolved bucket and blocks Repo hygiene on its own terms.
_SYSTEM_COLUMNS: dict[str, str] = {
    "erect_strike_rate": "0",
    "strip_time_days": "1",
}
_ASSIGNMENT_COLUMNS: dict[str, str] = {
    "material_unit_cost": "0",
    "labour_unit_cost": "0",
}
_ITEM_COLUMNS: dict[str, str] = {
    "rationale": "''",
    "originator": "''",
    "originator_role": "''",
    "phase": "''",
    "verification_method": "''",
}


def _state(table: str) -> dict[str, tuple[bool, object]]:
    """Column name to (is nullable, current default) for ``table``.

    Empty when the table does not exist, which is an ordinary case: a module
    that was never installed here has no table to repair.
    """
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return {}
    return {col["name"]: (bool(col["nullable"]), col.get("default")) for col in inspector.get_columns(table)}


def _todo(table: str, columns: dict[str, str]) -> list[tuple[str, str, bool, bool]]:
    """Which of ``columns`` still need work: (column, literal, backfill, default).

    Reading the state is separated from issuing the statements so that the three
    blocks in :func:`upgrade` differ only in the table they name, while the
    decision about what to do is written once.
    """
    live = _state(table)
    plan: list[tuple[str, str, bool, bool]] = []
    for column, literal in columns.items():
        if column not in live:
            continue  # Module never installed here, or the column not built yet.
        is_nullable, default = live[column]
        if not is_nullable and default is not None:
            continue  # Already what the models declare.
        plan.append((column, literal, is_nullable, default is None))
    return plan


def _alter(literal: str, backfill: bool, set_default: bool) -> dict[str, object]:
    """Keyword arguments for the one ``alter_column`` that repairs a column.

    Alembic's own API rather than a raw ``ALTER TABLE`` string, because
    ``scripts/check_migration_data_rewrites.py`` reads ``execute()`` arguments
    as SQL and cannot resolve a table out of a DDL statement, which puts a
    hand-written ALTER in its unresolved bucket and blocks the gate. Going
    through ``alter_column`` keeps the DDL out of that scan entirely, which is
    correct rather than evasive: the scan exists to find statements that rewrite
    rows, and setting a default rewrites none.

    ``server_default`` is omitted rather than passed as ``None`` when the
    default is already in place - Alembic reads an explicit ``None`` as "drop
    the default", which is the opposite of what this revision is for.
    """
    kwargs: dict[str, object] = {}
    if backfill:
        kwargs["nullable"] = False
    if set_default:
        kwargs["server_default"] = sa.text(literal)
    return kwargs


# data-rewrite-ack: table=oe_requirements_item growth=bounded rows=one row per requirement authored on a project, bounded by the project's requirement register rather than by transaction history
# data-rewrite-ack: table=oe_formwork_system growth=bounded rows=formwork catalogue entries, one per system the contractor buys or hires
# data-rewrite-ack: table=oe_formwork_assignment growth=bounded rows=one row per formwork system assigned to a project or BOQ position
# boot-repair: registry=requirements_cycle_not_null
# boot-repair: registry=formwork_rate_buildup_not_null
def upgrade() -> None:
    for column, literal, backfill, set_default in _todo(_SYSTEM, _SYSTEM_COLUMNS):
        if backfill:
            filled = op.get_bind().execute(
                sa.text(f"UPDATE {_SYSTEM} SET {column} = {literal} WHERE {column} IS NULL")  # noqa: S608
            )
            logger.info("v3312: backfilled %s row(s) in %s.%s", filled.rowcount, _SYSTEM, column)
        op.alter_column(_SYSTEM, column, **_alter(literal, backfill, set_default))

    for column, literal, backfill, set_default in _todo(_ASSIGNMENT, _ASSIGNMENT_COLUMNS):
        if backfill:
            filled = op.get_bind().execute(
                sa.text(f"UPDATE {_ASSIGNMENT} SET {column} = {literal} WHERE {column} IS NULL")  # noqa: S608
            )
            logger.info("v3312: backfilled %s row(s) in %s.%s", filled.rowcount, _ASSIGNMENT, column)
        op.alter_column(_ASSIGNMENT, column, **_alter(literal, backfill, set_default))

    for column, literal, backfill, set_default in _todo(_ITEM, _ITEM_COLUMNS):
        if backfill:
            filled = op.get_bind().execute(
                sa.text(f"UPDATE {_ITEM} SET {column} = {literal} WHERE {column} IS NULL")  # noqa: S608
            )
            logger.info("v3312: backfilled %s row(s) in %s.%s", filled.rowcount, _ITEM, column)
        op.alter_column(_ITEM, column, **_alter(literal, backfill, set_default))


def downgrade() -> None:
    """Widen the nine again.

    The backfilled values stay. There is no record of which rows held NULL
    before the upgrade, and re-NULLing every row carrying the default value
    would erase the ones that legitimately hold it.
    """
    for column in _ITEM_COLUMNS:
        if column in _state(_ITEM):
            op.alter_column(_ITEM, column, nullable=True, server_default=None)

    for column in _ASSIGNMENT_COLUMNS:
        if column in _state(_ASSIGNMENT):
            op.alter_column(_ASSIGNMENT, column, nullable=True, server_default=None)

    for column in _SYSTEM_COLUMNS:
        if column in _state(_SYSTEM):
            op.alter_column(_SYSTEM, column, nullable=True, server_default=None)
