"""Inbox item state: let somebody actually clear a row off their inbox.

Adds ``oe_dashboard_inbox_item_state``, one row per (user, inbox item) saying
what that person did with it - ``acknowledged`` (seen, still listed) or
``dismissed`` (off the list). The inbox itself still owns no data; it aggregates
approvals and notifications from their own modules. This table is only the
per-person overlay that turns a read-only list into one you can work through.

``item_id`` is deliberately not a foreign key. It carries the aggregated id
(``notification:<uuid>``, ``file_approval:<uuid>``,
``change_order_approval:<uuid>``) and the row it names lives in whichever module
produced it, so no single table could be referenced. The ``inbox_action``
validation rule set is what checks the id is addressable.

Revision ID: v3261_inbox_item_state
Revises: v3260_retrieval_saved_search
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "v3261_inbox_item_state"
down_revision: Union[str, Sequence[str], None] = "v3260_retrieval_saved_search"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(inspector: sa.engine.reflection.Inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def upgrade() -> None:
    # The boot heal runs create_all before anything reads alembic_version, so on
    # every install this application has started these tables already exist by
    # the time an operator runs `alembic upgrade head`. Creating one again
    # raises DuplicateTable, PostgreSQL rolls the whole run back, and every
    # revision after this one is skipped. Each table is asked for separately so
    # a boot that failed midway still converges.
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "oe_dashboard_inbox_item_state"):
        op.create_table(
            "oe_dashboard_inbox_item_state",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("item_id", sa.String(128), nullable=False),
            sa.Column("source", sa.String(32), nullable=False),
            sa.Column("source_id", sa.String(64), nullable=False),
            sa.Column("state", sa.String(16), nullable=False),
            sa.Column("findings", sa.JSON(), server_default="[]", nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_oe_dashboard_inbox_item_state")),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["oe_users_user.id"],
                name=op.f("fk_oe_dashboard_inbox_item_state_user_id_oe_users_user"),
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint("user_id", "item_id", name="uq_dashboard_inbox_state_item"),
        )
        op.create_index(
            "ix_dashboard_inbox_state_user_state",
            "oe_dashboard_inbox_item_state",
            ["user_id", "state"],
        )
        op.create_index(
            op.f("ix_oe_dashboard_inbox_item_state_user_id"),
            "oe_dashboard_inbox_item_state",
            ["user_id"],
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_oe_dashboard_inbox_item_state_user_id"), "oe_dashboard_inbox_item_state")
    op.drop_index("ix_dashboard_inbox_state_user_state", "oe_dashboard_inbox_item_state")
    op.drop_table("oe_dashboard_inbox_item_state")
