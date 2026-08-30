# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""signing - record the capability the provider actually delivered.

``oe_signing_session.provider_capability`` is what the caller REQUIRES of the
signature: qualified_electronic, advanced_electronic, simple_electronic or
digital_certificate. Nothing recorded what was actually delivered. Core ships a
single provider that performs no cryptography and reports simple_electronic,
and the registry falls back to it for any capability no adapter has claimed, so
a session requiring a qualified signature resolved to it silently. The row then
carried the requirement, and the interface rendered the requirement, with
nothing beside it to say the requirement had not been met.

This adds ``delivered_capability`` next to it. It is nullable on purpose and is
NOT backfilled: every row that exists today was derived before the field
existed, and copying the requirement into it would manufacture exactly the
claim this column was added to stop making. NULL means "not recorded", and the
API and the interface render it as that.

Additive and inspector-guarded, so re-running it does nothing.

Revision ID: v3313_signing_delivered_capability
Revises: v3312_heal_left_columns_nullable
Create Date: 2026-08-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v3313_signing_delivered_capability"
down_revision: Union[str, Sequence[str], None] = "v3312_heal_left_columns_nullable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "oe_signing_session"
_COLUMN = "delivered_capability"


def _columns(inspector: sa.engine.reflection.Inspector) -> set[str]:
    """Column names on the signing session table, empty when it is absent.

    An absent table is an ordinary case: a deployment that never installed the
    signing module has nothing to alter here.
    """
    if _TABLE not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    """Add the nullable delivered_capability column (idempotent)."""
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in inspector.get_table_names():
        return
    if _COLUMN in _columns(inspector):
        return
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=48), nullable=True))


def downgrade() -> None:
    """Drop the column again."""
    inspector = sa.inspect(op.get_bind())
    if _COLUMN in _columns(inspector):
        op.drop_column(_TABLE, _COLUMN)
