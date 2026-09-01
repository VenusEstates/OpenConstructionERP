# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""boq: what the estimator judged, beside how sure they were

Adds ``risk_dispersion`` and ``price_basis`` to ``oe_boq_position``.

A priced line already records how sure the estimator is (``confidence``) and
how the row was entered (``source``). Neither answers the two questions an
estimator is actually asked before an offer is signed: how wrong could this
line be, and what is the price standing on.

``risk_dispersion`` is the first. Confidence and dispersion are different
questions and only the second one decides signability: a margin that survives
the declared risk is ``target - z * sigma`` weighted by amount, and with a
confidence score alone there is no sigma to put in it.

``price_basis`` is the second, and it is deliberately NOT ``source``. Source
says how the row got here, defaults to ``manual`` and is written literally by
every ordinary create path, so a bill that was typed or imported from a
workbook comes out ``manual`` on nearly every row. Grouping money by it looks
like a price-evidence report and is a provenance report with one bar. A row
typed by hand can have an invoice behind it and a row imported from a
catalogue can rest on nothing but a guess, which is exactly why the two axes
cannot share a column.

Both are nullable with no backfill and no default. An unrecorded dispersion is
not a zero one, and an unrecorded basis is not judgement: a default here would
put a number on rows nobody has judged, which is the failure the columns exist
to prevent.

Guarded the way this tree guards ``create_table``, and for the same failure:
the boot path runs ``Base.metadata.create_all`` before anybody can run
``alembic upgrade head``, so on every real install these columns already exist
by the time an operator runs the migration by hand. An unguarded ``ADD COLUMN``
then raises ``DuplicateColumn``, and because PostgreSQL runs DDL in a
transaction that rolls back the whole upgrade rather than this revision -
``alembic_version`` does not move and every later revision is skipped with it.
``scripts/check_migration_create_table_guarded.py`` does not cover this: its
population is revisions that call ``op.create_table``.

Revision ID: v3316_boq_position_estimating_judgement
Revises: v3315_bi_kpi_estimate_scope
Create Date: 2026-09-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "v3316_boq_position_estimating_judgement"
down_revision: Union[str, Sequence[str], None] = "v3315_bi_kpi_estimate_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE = "oe_boq_position"


def _has_table(insp: sa.engine.reflection.Inspector, table: str) -> bool:
    return table in insp.get_table_names()


def _has_column(insp: sa.engine.reflection.Inspector, table: str, column: str) -> bool:
    if not _has_table(insp, table):
        return False
    return any(col["name"] == column for col in insp.get_columns(table))


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())

    if not _has_table(insp, TABLE):
        return

    if not _has_column(insp, TABLE, "risk_dispersion"):
        # String, not Numeric, and it is the same decision the money columns
        # on this table already made and documented: SQLite degrades a native
        # Numeric to REAL and loses digits, so the platform stores the exact
        # text and coerces at the point of arithmetic.
        op.add_column(TABLE, sa.Column("risk_dispersion", sa.String(length=20), nullable=True))

    if not _has_column(insp, TABLE, "price_basis"):
        # No CHECK constraint. The vocabulary is closed at the schema layer,
        # where a rejected value produces a 422 naming the seven it could
        # have been; a database-level CHECK would answer the same mistake
        # with a driver error and would have to be dropped and rebuilt every
        # time the vocabulary gains a value.
        op.add_column(TABLE, sa.Column("price_basis", sa.String(length=30), nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())

    if _has_column(insp, TABLE, "price_basis"):
        op.drop_column(TABLE, "price_basis")
    if _has_column(insp, TABLE, "risk_dispersion"):
        op.drop_column(TABLE, "risk_dispersion")
