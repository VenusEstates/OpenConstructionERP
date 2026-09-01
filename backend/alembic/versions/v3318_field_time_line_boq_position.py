# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""field_time: hours can name the bill position they were spent on

Adds a nullable ``boq_position_id`` to ``oe_field_time_line``.

The module's own docstring has claimed since it was written that ``cost_code``
and ``wbs`` "code the line to a BOQ position so the hours flow into the right
cost line". Nothing implemented that. A cost code is a free-text label on a
project's own chart and a WBS path is a tree address; neither resolves to a
position id, so recorded hours could be grouped by cost code and never
compared with the estimate line that predicted them.

That comparison is the point. An estimate written from productivity norms
predicts labour hours per unit, and until the hours the crew actually booked
can be read back against the same position, the norm library cannot learn:
a ceiling estimated at 0.30 h/m2 that took 0.42 has nowhere for the 0.42 to
land, and the next estimate uses 0.30 again.

Nullable, no default, no backfill, and it does not replace the cost-code path.
A line nobody has attributed stays unattributed, which is the honest state for
a day's work that covered six positions; the column only makes it possible to
say which one when somebody knows.

Indexed because the reader is ``costmodel.position_actuals``, which asks for
the hours of a page of positions at a time with ``boq_position_id IN (...)``.

Guarded the way this tree guards ``create_table``, and for the same failure:
the boot path runs ``Base.metadata.create_all`` before anybody can run
``alembic upgrade head``, so on every real install this column already exists
by the time an operator runs the migration by hand. An unguarded ``ADD COLUMN``
then raises ``DuplicateColumn``, and because PostgreSQL runs DDL in a
transaction that rolls back the whole upgrade rather than this revision -
``alembic_version`` does not move and every later revision is skipped with it.
``scripts/check_migration_create_table_guarded.py`` does not cover this: its
population is revisions that call ``op.create_table``.

Revision ID: v3318_field_time_line_boq_position
Revises: v3317_variation_agreed_value
Create Date: 2026-09-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.database import GUID

# revision identifiers, used by Alembic.
revision: str = "v3318_field_time_line_boq_position"
down_revision: Union[str, Sequence[str], None] = "v3317_variation_agreed_value"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE = "oe_field_time_line"
COLUMN = "boq_position_id"
INDEX = "ix_oe_field_time_line_boq_position"


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

    if not _has_table(insp, TABLE):
        return

    if not _has_column(insp, TABLE, COLUMN):
        # GUID(), not sa.Uuid: GUID is what the model declares and what
        # create_all builds, so a tree that reached this table through boot
        # and a tree that reached it through alembic end up with the same
        # column type rather than two that only agree on PostgreSQL.
        #
        # No foreign key, matching every other cross-module position link in
        # the tree (costmodel.CostLine, costmodel.BudgetLine,
        # site_inventory.StockMovement). A timesheet is a signed statutory
        # record of somebody's working day and has to survive the estimate it
        # was coded against being deleted or re-imported.
        op.add_column(TABLE, sa.Column(COLUMN, GUID(), nullable=True))

    if not _has_index(insp, TABLE, INDEX):
        op.create_index(INDEX, TABLE, [COLUMN])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())

    if _has_index(insp, TABLE, INDEX):
        op.drop_index(INDEX, table_name=TABLE)
    if _has_column(insp, TABLE, COLUMN):
        op.drop_column(TABLE, COLUMN)
