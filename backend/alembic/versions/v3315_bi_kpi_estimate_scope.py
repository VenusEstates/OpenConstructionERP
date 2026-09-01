# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""bi dashboards: an estimate dimension on KPI values

Adds ``boq_id`` to ``oe_bi_dashboards_kpi_value`` and ``scope`` to
``oe_bi_dashboards_kpi_definition``. A project holding several separately
quoted estimates could only be read as one number, and anything normalised -
area, duration, confidence, margin - has no meaning averaged across them.

``boq_id IS NULL`` keeps the project-level meaning every existing row already
has, so nothing is backfilled and nothing already stored changes what it says.
``scope`` defaults to ``project`` for the same reason.

Guarded the way this tree guards ``create_table``, and for the same failure:
the boot path runs ``Base.metadata.create_all`` before anybody can run
``alembic upgrade head``, so on every real install these columns already exist
by the time an operator runs the migration by hand. An unguarded ``ADD COLUMN``
then raises ``DuplicateColumn``, and because PostgreSQL runs DDL in a
transaction that rolls back the whole upgrade rather than this revision -
``alembic_version`` does not move and every later revision is skipped with it.

Worth saying plainly because ``scripts/check_migration_create_table_guarded.py``
does NOT cover this: its population is revisions that call ``op.create_table``.
The identical hazard on ``add_column`` is outside it.

Revision ID: v3315_bi_kpi_estimate_scope
Revises: v3314_user_session_revocation
Create Date: 2026-09-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.database import GUID

# revision identifiers, used by Alembic.
revision: str = "v3315_bi_kpi_estimate_scope"
down_revision: Union[str, Sequence[str], None] = "v3314_user_session_revocation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


VALUE_TABLE = "oe_bi_dashboards_kpi_value"
DEFINITION_TABLE = "oe_bi_dashboards_kpi_definition"


def _has_table(insp: sa.engine.reflection.Inspector, table: str) -> bool:
    return table in insp.get_table_names()


def _has_column(insp: sa.engine.reflection.Inspector, table: str, column: str) -> bool:
    if not _has_table(insp, table):
        return False
    return any(col["name"] == column for col in insp.get_columns(table))


def _has_index(insp: sa.engine.reflection.Inspector, table: str, index: str) -> bool:
    if not _has_table(insp, table):
        return False
    return any(ix["name"] == index for ix in insp.get_indexes(table))


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())

    if _has_table(insp, VALUE_TABLE) and not _has_column(insp, VALUE_TABLE, "boq_id"):
        # No FK: this module is a read-only consumer of oe_boq, the same
        # relationship it already has with oe_projects_project through
        # ``project_id``. Nullable with no backfill, because NULL is exactly
        # what every stored row means today.
        #
        # ``GUID()`` and not ``sa.Uuid()``. The decorator is VARCHAR(36) on
        # every dialect including PostgreSQL, so a native UUID column here
        # would not match the one ``create_all`` builds from the model, and
        # the two ways of arriving at this schema would disagree.
        op.add_column(VALUE_TABLE, sa.Column("boq_id", GUID(), nullable=True))

    if not _has_index(insp, VALUE_TABLE, "ix_oe_bi_dashboards_kpi_value_boq_id") and _has_column(
        insp, VALUE_TABLE, "boq_id"
    ):
        op.create_index(
            "ix_oe_bi_dashboards_kpi_value_boq_id",
            VALUE_TABLE,
            ["boq_id"],
        )

    if _has_table(insp, DEFINITION_TABLE) and not _has_column(insp, DEFINITION_TABLE, "scope"):
        # A server_default rather than a Python one: existing rows have to
        # come out of this migration saying "project", and a default that only
        # exists in the model would leave them NULL under a NOT NULL column.
        op.add_column(
            DEFINITION_TABLE,
            sa.Column(
                "scope",
                sa.String(length=16),
                nullable=False,
                server_default="project",
            ),
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())

    if _has_index(insp, VALUE_TABLE, "ix_oe_bi_dashboards_kpi_value_boq_id"):
        op.drop_index("ix_oe_bi_dashboards_kpi_value_boq_id", table_name=VALUE_TABLE)
    if _has_column(insp, VALUE_TABLE, "boq_id"):
        op.drop_column(VALUE_TABLE, "boq_id")
    if _has_column(insp, DEFINITION_TABLE, "scope"):
        op.drop_column(DEFINITION_TABLE, "scope")
