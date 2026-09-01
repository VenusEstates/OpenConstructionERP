# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""variations: which pricing state was submitted, and what was agreed

Adds ``submitted_boq_id``, ``submitted_boq_total``, ``agreed_cost_impact``,
``agreed_basis`` and ``agreed_variance_note`` to ``oe_variations_request``.

Issue #435. A variation request already carried a headline estimate and could
own a priced bill of its own, and the two are allowed to differ while the
change is being priced. What nothing recorded was the boundary between them:
which pricing state was actually put in front of the approver, and what
commercial amount that approver actually agreed to.

Without those two facts the agreed value is inherited implicitly from whatever
figure happened to be on the request, and a negotiated amount is
indistinguishable from a stale headline. Both are plausible numbers of the
right order of magnitude, so nothing downstream can tell them apart either.

``submitted_boq_total`` is frozen at submission on purpose: the bill can go on
being revised afterwards, and the point of the column is to say what the
approver was looking at, not what the bill says now.

``agreed_basis`` is why the agreed amount is what it is - ``negotiated`` when a
person named it, ``priced_boq`` when it is the submitted bill's own total,
``headline_estimate`` when there was no bill to price. Empty until a decision
is taken. It exists so that "the agreed value equals the bill total" and "the
agreed value was never actually decided" cannot look the same in the record.

Nullable with no backfill. A request approved before this migration has no
recorded agreement, and inventing one for it would be putting a decision in a
person's mouth years after the fact.

Guarded the way this tree guards ``create_table``, and for the same failure:
the boot path runs ``Base.metadata.create_all`` before anybody can run
``alembic upgrade head``, so on a real install these columns already exist by
the time an operator upgrades by hand. An unguarded ``ADD COLUMN`` raises
DuplicateColumn, rolls back the whole upgrade rather than this revision, and
takes every later revision with it. The create_table gate does not cover this.

Revision ID: v3317_variation_agreed_value
Revises: v3316_boq_position_estimating_judgement
Create Date: 2026-09-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.db_types import MoneyType
from app.database import GUID

# revision identifiers, used by Alembic.
revision: str = "v3317_variation_agreed_value"
down_revision: Union[str, Sequence[str], None] = "v3316_boq_position_estimating_judgement"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE = "oe_variations_request"


def _has_table(insp: sa.engine.reflection.Inspector, table: str) -> bool:
    return table in insp.get_table_names()


def _has_column(insp: sa.engine.reflection.Inspector, table: str, column: str) -> bool:
    if not _has_table(insp, table):
        return False
    return any(col["name"] == column for col in insp.get_columns(table))


#: Name, type and server default, in the order they are added.
#:
#: The two money columns are ``MoneyType`` and not a String, which is the whole
#: reason this list names types at all. ``MoneyType`` compiles to
#: ``NUMERIC(18, 2)`` on PostgreSQL, so a String here would build a different
#: column from the one ``create_all`` builds on a fresh volume - and both
#: schemas would look right on their own, which is the failure that makes a
#: model-versus-migration disagreement expensive to find.
COLUMNS: list[tuple[str, object, str | None]] = [
    ("submitted_boq_id", GUID(), None),
    ("submitted_boq_total", MoneyType(), None),
    ("agreed_cost_impact", MoneyType(), None),
    ("agreed_basis", sa.String(length=30), ""),
    ("agreed_variance_note", sa.Text(), ""),
]


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not _has_table(insp, TABLE):
        return
    for name, type_, default in COLUMNS:
        if _has_column(insp, TABLE, name):
            continue
        # A column with no server default here is one that has to be able to
        # say "nobody has decided", and the split is the point rather than a
        # style: an empty string is a poor way to say that about a number, so
        # the three values are nullable and the two strings are NOT NULL with
        # an empty default.
        op.add_column(
            TABLE,
            sa.Column(name, type_, nullable=default is None, server_default=default),
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for column in reversed(COLUMNS):
        if _has_column(insp, TABLE, column[0]):
            op.drop_column(TABLE, column[0])
