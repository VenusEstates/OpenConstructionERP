"""Saved searches: server-side persistence for the Find Records history panel.

Adds ``oe_retrieval_saved_search``, one row per search a user pinned on a
project. The six facets get their own columns rather than one JSON blob so the
unique constraint, the indexes and the validation rules can address them
directly; ``signature`` is the hash of the canonical facet set that makes
re-saving the same search an update of the existing pin instead of a duplicate
row.

Until now these lived in the browser's ``localStorage``, which meant a pin was
invisible to the same person on a second machine and gone with the profile.

Revision ID: v3260_retrieval_saved_search
Revises: v3259_eac_block_graph
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "v3260_retrieval_saved_search"
down_revision: Union[str, Sequence[str], None] = "v3259_eac_block_graph"
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

    if not _has_table(inspector, "oe_retrieval_saved_search"):
        op.create_table(
            "oe_retrieval_saved_search",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("project_id", sa.String(36), nullable=False),
            sa.Column("label", sa.String(200), nullable=False),
            sa.Column("text", sa.String(500), server_default="", nullable=False),
            sa.Column("party", sa.String(200), server_default="", nullable=False),
            sa.Column("record_type", sa.String(50), server_default="", nullable=False),
            sa.Column("date_from", sa.String(10), server_default="", nullable=False),
            sa.Column("date_to", sa.String(10), server_default="", nullable=False),
            sa.Column("entity", sa.String(200), server_default="", nullable=False),
            sa.Column("signature", sa.String(64), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("use_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("validation_status", sa.String(16), server_default="pending", nullable=False),
            sa.Column("validation_findings", sa.JSON(), server_default="[]", nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_oe_retrieval_saved_search")),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["oe_users_user.id"],
                name=op.f("fk_oe_retrieval_saved_search_user_id_oe_users_user"),
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["project_id"],
                ["oe_projects_project.id"],
                name=op.f("fk_oe_retrieval_saved_search_project_id_oe_projects_project"),
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint("user_id", "project_id", "signature", name="uq_retrieval_saved_search_sig"),
        )
        op.create_index(
            "ix_retrieval_saved_search_owner",
            "oe_retrieval_saved_search",
            ["user_id", "project_id"],
        )
        op.create_index(
            op.f("ix_oe_retrieval_saved_search_user_id"),
            "oe_retrieval_saved_search",
            ["user_id"],
        )
        op.create_index(
            op.f("ix_oe_retrieval_saved_search_project_id"),
            "oe_retrieval_saved_search",
            ["project_id"],
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_oe_retrieval_saved_search_project_id"), "oe_retrieval_saved_search")
    op.drop_index(op.f("ix_oe_retrieval_saved_search_user_id"), "oe_retrieval_saved_search")
    op.drop_index("ix_retrieval_saved_search_owner", "oe_retrieval_saved_search")
    op.drop_table("oe_retrieval_saved_search")
