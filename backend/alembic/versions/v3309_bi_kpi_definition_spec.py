# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""bi_dashboards - where a custom KPI's definition lives.

Adds one column, ``spec_json``, to ``oe_bi_dashboards_kpi_definition``.

Until now every KPI in the library was a Python function: the row carried a
``formula_ref`` naming a key in an in-process registry, and the only way to add
one was to ship code. Somebody running the platform has no place to put code,
so they had no way to define a KPI at all. ``spec_json`` is that place - a
declarative aggregation over a whitelisted entity, validated before it is
written, evaluated by ``app.modules.bi_dashboards.kpi_spec``.

The two kinds of row stay apart without any new flag. A built-in leaves
``spec_json`` empty and is found by ``formula_ref``; a custom row carries a spec
and a ``formula_ref`` of ``spec``, which is deliberately a name no formula
registers, so the lookup misses and the spec path runs. ``is_system`` already
distinguished them for the starter pack, which upserts only the codes it owns
and so has never had a way to reach a user's row.

Default ``'{}'`` and NOT NULL rather than nullable: an empty spec and a missing
spec mean the same thing here - not a custom KPI - and one representation is
one branch fewer in the reader.

Safe on a populated database. ADD COLUMN with a constant default is a
catalogue-only change on PostgreSQL 11 and later; no rows are rewritten.

Inspector-guarded, so a fresh install whose tables ``env.py`` already built
through ``Base.metadata.create_all`` hits an idempotent no-op.

Revision ID: v3309_bi_kpi_definition_spec
Revises: v3308_romania_vat_2025
Create Date: 2026-08-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v3309_bi_kpi_definition_spec"
down_revision: Union[str, Sequence[str], None] = "v3308_romania_vat_2025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "oe_bi_dashboards_kpi_definition"
_COLUMN = "spec_json"


def _has_table(inspector: sa.engine.reflection.Inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def _has_column(inspector: sa.engine.reflection.Inspector, table: str, column: str) -> bool:
    if not _has_table(inspector, table):
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, _TABLE):
        return
    if _has_column(inspector, _TABLE, _COLUMN):
        return
    op.add_column(
        _TABLE,
        sa.Column(
            _COLUMN,
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, _TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
