# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""user sessions - give an issued token a record, so one session can be revoked.

Creates one table and touches no existing one:

    oe_users_session - one login session, named by the ``sid`` claim

Why the table has to exist at all
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Every token this product mints already carries a ``jti``, and nothing has ever
read it. A name with no record behind it cannot be revoked: there was no place
to write down that a particular session should stop being honoured, so ending
one session was not merely unimplemented, it was inexpressible. The only lever
was rotating the signing secret, which ends every session for every user at
once. The access token and the refresh token minted together now share a
``sid``, and this row is what that claim points at.

Why revocation is a column and not a missing row
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
A token whose session row cannot be found is honoured rather than refused
(the reasoning is written out beside the check, in
``app.dependencies.reject_revoked_session``). That choice makes deleting a row
the opposite of revoking it, so ``revoked_at`` is set on a row that stays.
``expires_at`` exists so pruning has a boundary it cannot cross: only rows
already past it may be deleted, and no live credential can point at one.

Sessions issued before this table existed carry no ``sid`` and are not
represented here. They keep working until they expire, which is at most the
refresh horizon, and the coarse lever over them remains
``oe_users_user.password_changed_at``. Backfilling was never an option: there
is no record of them anywhere to backfill from.

Safe on a populated database: one CREATE TABLE and three CREATE INDEX on a
table that did not exist, so no existing row is read, rewritten or locked.

Inspector-guarded, so a fresh install whose tables env.py already created
through ``Base.metadata.create_all`` hits an idempotent no-op.

Revision ID: v3314_user_session_revocation
Revises: v3313_signing_delivered_capability
Create Date: 2026-08-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v3314_user_session_revocation"
down_revision: Union[str, Sequence[str], None] = "v3313_signing_delivered_capability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SESSION = "oe_users_session"

# ``GUID`` renders as String(36) on every dialect and the ORM's timestamps are
# TIMESTAMP WITH TIME ZONE. Neither is a native ``uuid`` column.
_GUID = sa.String(36)
_TS = sa.DateTime(timezone=True)


def _has_table(inspector: sa.engine.reflection.Inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def upgrade() -> None:
    """Create the session table (idempotent)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_table(inspector, _SESSION):
        return

    op.create_table(
        _SESSION,
        sa.Column("id", _GUID, primary_key=True, nullable=False),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("sid", sa.String(64), nullable=False),
        sa.Column("user_id", _GUID, sa.ForeignKey("oe_users_user.id", ondelete="CASCADE"), nullable=False),
        # No server_default: every insert supplies it, and any value invented
        # here would be a lie about when a session ends. It is the pruning
        # boundary, so a wrong default would let pruning reach a live session.
        sa.Column("expires_at", _TS, nullable=False),
        sa.Column("revoked_at", _TS, nullable=True),
        sa.Column("last_used_at", _TS, nullable=True),
    )
    # Unique: the claim names exactly one session. Also the lookup every
    # authenticated request performs, so it has to be indexed either way.
    op.create_index("ix_oe_users_session_sid", _SESSION, ["sid"], unique=True)
    op.create_index("ix_oe_users_session_user_id", _SESSION, ["user_id"])
    # Listing and revoking are both "this user's sessions, newest first", and
    # pruning walks expires_at across all users.
    op.create_index("ix_oe_users_session_expires_at", _SESSION, ["expires_at"])


def downgrade() -> None:
    """Drop the session table again."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_table(inspector, _SESSION):
        op.drop_table(_SESSION)
