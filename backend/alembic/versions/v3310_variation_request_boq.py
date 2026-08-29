# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""variations - a variation request can own a dedicated bill of quantities.

Two structural changes, both additive.

``oe_boq_boq.variation_request_id`` says which variation request a bill was
raised for. NULL is the whole world that existed before it: a bill of the
project at large. Every row on every existing database gets NULL, so the three
places that now exclude variation bills - the project's bill register, the
core "which bill does this land in" resolver, and the change-order writeback -
exclude nothing that is there today. That is what makes this safe to run on a
populated database in the middle of a job: no bill changes what it is, no
listing changes what it shows, no total changes what it sums.

The link is stored on the bill rather than as a ``boq_id`` on the request
because it has to be usable as a filter. A column on the owning record can
only be filtered by a subquery from the BOQ tables into the owning module's
tables, which is the dependency the shared resolver in ``app/core/boq_target``
exists to avoid. ``oe_design_option.boq_id`` is that shape, and design-option
bills consequently appear, unannounced, in every project-wide money aggregate
in the tree.

``oe_variations_boq_trace`` records where each line of such a bill came from:
the contract schedule-of-values line the change affects, the estimating
position the scope was taken from, or neither for a line entered by hand. Only
``variation_request_id`` is a real foreign key, because that row is in the
variations module; the references into ``oe_boq_*`` and ``oe_contracts_*`` are
plain GUIDs, the same convention ``oe_variations_order.affected_contract_id``
already follows, so neither module carries a DB-level dependency on the other.

Both steps are inspector-guarded and therefore idempotent, so a fresh install
whose tables ``env.py`` already built through ``Base.metadata.create_all``
reaches this revision and does nothing.

Revision ID: v3310_variation_request_boq
Revises: v3311_rebar_schedule
Create Date: 2026-08-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.database import GUID

revision: str = "v3310_variation_request_boq"
down_revision: Union[str, Sequence[str], None] = "v3311_rebar_schedule"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_BOQ_TABLE = "oe_boq_boq"
_BOQ_COLUMN = "variation_request_id"
_BOQ_INDEX = "ix_oe_boq_boq_variation_request_id"

_TRACE_TABLE = "oe_variations_boq_trace"
_REQUEST_TABLE = "oe_variations_request"


def _has_table(inspector: sa.engine.reflection.Inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def _has_column(inspector: sa.engine.reflection.Inspector, table: str, column: str) -> bool:
    if not _has_table(inspector, table):
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def _has_index(inspector: sa.engine.reflection.Inspector, table: str, index: str) -> bool:
    if not _has_table(inspector, table):
        return False
    return index in {i["name"] for i in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── 1. The bill learns which variation request it belongs to ─────────
    if _has_table(inspector, _BOQ_TABLE) and not _has_column(inspector, _BOQ_TABLE, _BOQ_COLUMN):
        # Nullable with no default: ADD COLUMN is a catalogue-only change and
        # every existing row reads as "a bill of the project", which is what
        # it is.
        op.add_column(_BOQ_TABLE, sa.Column(_BOQ_COLUMN, GUID(), nullable=True))
        inspector = sa.inspect(bind)
    if _has_column(inspector, _BOQ_TABLE, _BOQ_COLUMN) and not _has_index(inspector, _BOQ_TABLE, _BOQ_INDEX):
        # "This request's bills" is the only read of the column, and the
        # three exclusion filters scan on it on every project-scoped resolve.
        op.create_index(_BOQ_INDEX, _BOQ_TABLE, [_BOQ_COLUMN])

    # ── 2. Where each line of such a bill came from ──────────────────────
    inspector = sa.inspect(bind)
    if _has_table(inspector, _REQUEST_TABLE) and not _has_table(inspector, _TRACE_TABLE):
        op.create_table(
            _TRACE_TABLE,
            sa.Column("id", GUID(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("variation_request_id", GUID(), nullable=False),
            sa.Column("boq_id", GUID(), nullable=False),
            sa.Column("position_id", GUID(), nullable=False),
            sa.Column("origin", sa.String(length=20), nullable=False, server_default="manual"),
            sa.Column("source_boq_id", GUID(), nullable=True),
            sa.Column("source_position_id", GUID(), nullable=True),
            sa.Column("contract_id", GUID(), nullable=True),
            sa.Column("contract_line_id", GUID(), nullable=True),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.ForeignKeyConstraint(
                ["variation_request_id"],
                [f"{_REQUEST_TABLE}.id"],
                name="fk_oe_variations_boq_trace_variation_request",
                ondelete="CASCADE",
            ),
            # One trace row per line: the line is the thing whose provenance
            # is recorded, so two rows about it would be two answers.
            sa.UniqueConstraint("position_id", name="uq_oe_variations_boq_trace_position"),
        )
        op.create_index(
            "ix_oe_variations_boq_trace_variation_request_id",
            _TRACE_TABLE,
            ["variation_request_id"],
        )
        op.create_index("ix_oe_variations_boq_trace_boq_id", _TRACE_TABLE, ["boq_id"])
        op.create_index(
            "ix_oe_variations_boq_trace_source_position_id",
            _TRACE_TABLE,
            ["source_position_id"],
        )
        op.create_index(
            "ix_oe_variations_boq_trace_contract_line_id",
            _TRACE_TABLE,
            ["contract_line_id"],
        )
        op.create_index(
            "ix_oe_variations_boq_trace_request_boq",
            _TRACE_TABLE,
            ["variation_request_id", "boq_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, _TRACE_TABLE):
        op.drop_table(_TRACE_TABLE)
    inspector = sa.inspect(bind)
    if _has_index(inspector, _BOQ_TABLE, _BOQ_INDEX):
        op.drop_index(_BOQ_INDEX, table_name=_BOQ_TABLE)
    if _has_column(inspector, _BOQ_TABLE, _BOQ_COLUMN):
        op.drop_column(_BOQ_TABLE, _BOQ_COLUMN)
