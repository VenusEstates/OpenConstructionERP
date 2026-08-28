# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""rebar_schedule - imported reinforcement bending schedules.

Two new tables, nothing existing touched.

``oe_rebar_schedule_import`` is one ABS file taken into a project. It carries
the SHA-256 of the bytes, unique per project, so that re-sending a bending
schedule - which happens as a matter of routine - is recognised instead of
duplicating several hundred shapes. It also carries the outcome of the
``bvbs_abs`` validation rule set, because a file that fails validation is still
stored: refusing it would leave the operator holding a report with nothing to
point it at.

``oe_rebar_shape`` is one bending shape, one row of the schedule. Beside the
parsed columns it keeps ``raw``, the exact source line. The format's checksum
covers those exact characters, so a line rebuilt from the parsed columns would
be a different record; and when a discrepancy with the bending shop is argued
later, the row has to be comparable against what was actually sent rather than
against our reading of it.

Both steps are inspector-guarded and therefore idempotent, so a fresh install
whose tables ``env.py`` already built through ``Base.metadata.create_all``
reaches this revision and does nothing.

Revision ID: v3311_rebar_schedule
Revises: v3309_bi_kpi_definition_spec
Create Date: 2026-08-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.database import GUID

revision: str = "v3311_rebar_schedule"
down_revision: Union[str, Sequence[str], None] = "v3309_bi_kpi_definition_spec"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_IMPORT_TABLE = "oe_rebar_schedule_import"
_SHAPE_TABLE = "oe_rebar_shape"
_PROJECT_TABLE = "oe_projects_project"


def _has_table(inspector: sa.engine.reflection.Inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def _index_names(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    if not _has_table(inspector, table):
        return set()
    return {ix["name"] for ix in inspector.get_indexes(table)}


def upgrade() -> None:
    """Create the two rebar schedule tables (idempotent)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, _IMPORT_TABLE):
        op.create_table(
            _IMPORT_TABLE,
            sa.Column("id", GUID(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("project_id", GUID(), nullable=False),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("content_sha256", sa.String(length=64), nullable=False),
            sa.Column("encoding", sa.String(length=16), nullable=False, server_default="ascii"),
            sa.Column("record_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_weight_kg", sa.Numeric(16, 3), nullable=True),
            sa.Column("validation_status", sa.String(length=16), nullable=False, server_default="passed"),
            sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_by", sa.String(length=36), nullable=True),
            sa.ForeignKeyConstraint(
                ["project_id"],
                [f"{_PROJECT_TABLE}.id"],
                name="fk_oe_rebar_schedule_import_project_id_oe_projects_project",
                ondelete="CASCADE",
            ),
            # The same bytes imported twice into one project is the duplicate
            # the service recognises, so the database is what makes it true
            # rather than a check the service could race against itself.
            sa.UniqueConstraint("project_id", "content_sha256", name="uq_rebar_import_project_content"),
        )

    if not _has_table(inspector, _SHAPE_TABLE):
        op.create_table(
            _SHAPE_TABLE,
            sa.Column("id", GUID(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("import_id", GUID(), nullable=False),
            # Denormalised from the import so a project-wide read of shapes
            # needs no join, and an access check can be made on the row itself.
            sa.Column("project_id", GUID(), nullable=False),
            sa.Column("line_no", sa.Integer(), nullable=False),
            sa.Column("super_group", sa.String(length=4), nullable=False),
            sa.Column("project_ref", sa.String(length=64), nullable=True),
            sa.Column("drawing_ref", sa.String(length=64), nullable=True),
            sa.Column("drawing_index", sa.String(length=16), nullable=True),
            sa.Column("position", sa.String(length=32), nullable=True),
            sa.Column("length_mm", sa.Numeric(12, 2), nullable=True),
            sa.Column("quantity", sa.Integer(), nullable=True),
            sa.Column("weight_kg", sa.Numeric(12, 4), nullable=True),
            sa.Column("diameter_mm", sa.Numeric(8, 2), nullable=True),
            sa.Column("steel_grade", sa.String(length=32), nullable=True),
            sa.Column("bending_roller_mm", sa.Numeric(8, 2), nullable=True),
            sa.Column("mesh_type", sa.String(length=64), nullable=True),
            sa.Column("width_mm", sa.Numeric(12, 2), nullable=True),
            sa.Column("height_mm", sa.Numeric(12, 2), nullable=True),
            sa.Column("layer", sa.Integer(), nullable=True),
            sa.Column("stagger_group", sa.String(length=32), nullable=True),
            sa.Column("geometry", sa.JSON(), nullable=True),
            sa.Column("block_layout", sa.String(length=32), nullable=True),
            sa.Column("checksum_ok", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("raw", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(
                ["import_id"],
                [f"{_IMPORT_TABLE}.id"],
                name="fk_oe_rebar_shape_import_id_oe_rebar_schedule_import",
                ondelete="CASCADE",
            ),
        )

    inspector = sa.inspect(bind)
    existing = _index_names(inspector, _IMPORT_TABLE)
    for name, columns in (
        ("ix_oe_rebar_schedule_import_project_id", ["project_id"]),
        ("ix_oe_rebar_schedule_import_content_sha256", ["content_sha256"]),
        ("ix_oe_rebar_schedule_import_validation_status", ["validation_status"]),
    ):
        if name not in existing:
            op.create_index(name, _IMPORT_TABLE, columns)

    existing = _index_names(inspector, _SHAPE_TABLE)
    for name, columns in (
        ("ix_oe_rebar_shape_import_id", ["import_id"]),
        ("ix_oe_rebar_shape_project_id", ["project_id"]),
        ("ix_oe_rebar_shape_super_group", ["super_group"]),
        ("ix_oe_rebar_shape_drawing_ref", ["drawing_ref"]),
        ("ix_oe_rebar_shape_position", ["position"]),
        ("ix_oe_rebar_shape_stagger_group", ["stagger_group"]),
    ):
        if name not in existing:
            op.create_index(name, _SHAPE_TABLE, columns)


def downgrade() -> None:
    """Drop both tables. The shapes go first; they reference the import."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_table(inspector, _SHAPE_TABLE):
        op.drop_table(_SHAPE_TABLE)
    inspector = sa.inspect(bind)
    if _has_table(inspector, _IMPORT_TABLE):
        op.drop_table(_IMPORT_TABLE)
