"""EAC block graph: persistence for the visual methodology editor.

Adds the three tables behind the block editor's saved documents:

* ``oe_eac_block_graph``      - the document itself
* ``oe_eac_block``            - one node on the canvas
* ``oe_eac_block_connection`` - one wire between two block slots

Connections reference blocks by the editor's own ``client_id`` rather than by
foreign key. That is deliberate and is documented on the model: a wire pointing
at a block that is not on the canvas is an authoring mistake the ``eac_graph``
validation rule set must be able to report, and a database constraint would
instead refuse the write outright and cost the estimator their unsaved work.

Revision ID: v3259_eac_block_graph
Revises: v3258_progress_entry_seq
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "v3259_eac_block_graph"
down_revision: Union[str, Sequence[str], None] = "v3258_progress_entry_seq"
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

    if not _has_table(inspector, "oe_eac_block_graph"):
        op.create_table(
            "oe_eac_block_graph",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("output_mode", sa.String(32), server_default="boolean", nullable=False),
            sa.Column("rule_id", sa.String(36), nullable=True),
            sa.Column("ruleset_id", sa.String(36), nullable=True),
            sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
            sa.Column("validation_status", sa.String(16), server_default="pending", nullable=False),
            sa.Column("validation_findings", sa.JSON(), server_default="[]", nullable=False),
            sa.Column("validation_score", sa.Float(), nullable=True),
            sa.Column("tags", sa.JSON(), server_default="[]", nullable=False),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("project_id", sa.String(36), nullable=True),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_oe_eac_block_graph")),
            sa.ForeignKeyConstraint(
                ["rule_id"],
                ["oe_eac_rule.id"],
                name=op.f("fk_oe_eac_block_graph_rule_id_oe_eac_rule"),
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["ruleset_id"],
                ["oe_eac_ruleset.id"],
                name=op.f("fk_oe_eac_block_graph_ruleset_id_oe_eac_ruleset"),
                ondelete="SET NULL",
            ),
        )
        op.create_index("ix_eac_block_graph_tenant_project", "oe_eac_block_graph", ["tenant_id", "project_id"])
        op.create_index("ix_eac_block_graph_tenant_updated", "oe_eac_block_graph", ["tenant_id", "updated_at"])
        op.create_index(op.f("ix_oe_eac_block_graph_name"), "oe_eac_block_graph", ["name"])
        op.create_index(op.f("ix_oe_eac_block_graph_rule_id"), "oe_eac_block_graph", ["rule_id"])
        op.create_index(op.f("ix_oe_eac_block_graph_ruleset_id"), "oe_eac_block_graph", ["ruleset_id"])
        op.create_index(op.f("ix_oe_eac_block_graph_tenant_id"), "oe_eac_block_graph", ["tenant_id"])
        op.create_index(op.f("ix_oe_eac_block_graph_project_id"), "oe_eac_block_graph", ["project_id"])

    if not _has_table(inspector, "oe_eac_block"):
        op.create_table(
            "oe_eac_block",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("graph_id", sa.String(36), nullable=False),
            sa.Column("client_id", sa.String(64), nullable=False),
            sa.Column("ordinal", sa.Integer(), server_default="0", nullable=False),
            sa.Column("kind", sa.String(64), nullable=False),
            sa.Column("color", sa.String(32), server_default="selector", nullable=False),
            sa.Column("title", sa.String(255), server_default="", nullable=False),
            sa.Column("position_x", sa.Float(), server_default="0", nullable=False),
            sa.Column("position_y", sa.Float(), server_default="0", nullable=False),
            sa.Column("slots", sa.JSON(), server_default="[]", nullable=False),
            sa.Column("params", sa.JSON(), server_default="{}", nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_oe_eac_block")),
            sa.ForeignKeyConstraint(
                ["graph_id"],
                ["oe_eac_block_graph.id"],
                name=op.f("fk_oe_eac_block_graph_id_oe_eac_block_graph"),
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint("graph_id", "client_id", name="uq_eac_block_graph_client"),
        )
        op.create_index("ix_eac_block_graph_ordinal", "oe_eac_block", ["graph_id", "ordinal"])
        op.create_index(op.f("ix_oe_eac_block_graph_id"), "oe_eac_block", ["graph_id"])

    if not _has_table(inspector, "oe_eac_block_connection"):
        op.create_table(
            "oe_eac_block_connection",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("graph_id", sa.String(36), nullable=False),
            sa.Column("client_id", sa.String(64), nullable=False),
            sa.Column("ordinal", sa.Integer(), server_default="0", nullable=False),
            sa.Column("source_block_client_id", sa.String(64), nullable=False),
            sa.Column("source_slot_id", sa.String(64), nullable=False),
            sa.Column("target_block_client_id", sa.String(64), nullable=False),
            sa.Column("target_slot_id", sa.String(64), nullable=False),
            sa.Column("data_type", sa.String(32), server_default="any", nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_oe_eac_block_connection")),
            sa.ForeignKeyConstraint(
                ["graph_id"],
                ["oe_eac_block_graph.id"],
                name=op.f("fk_oe_eac_block_connection_graph_id_oe_eac_block_graph"),
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint("graph_id", "client_id", name="uq_eac_block_conn_graph_client"),
        )
        op.create_index("ix_eac_block_conn_graph_ordinal", "oe_eac_block_connection", ["graph_id", "ordinal"])
        op.create_index(op.f("ix_oe_eac_block_connection_graph_id"), "oe_eac_block_connection", ["graph_id"])


def downgrade() -> None:
    op.drop_table("oe_eac_block_connection")
    op.drop_table("oe_eac_block")
    op.drop_table("oe_eac_block_graph")
