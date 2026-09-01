"""v1.0.0 -- add BIM requirements import/export tables.

Creates tables:
    oe_bim_requirement_set -- container for imported requirement files
    oe_bim_requirement     -- individual BIM requirement (5-column universal model)

Revision ID: v100_bim_requirements
Revises: v090_new_modules
Create Date: 2026-04-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "v100_bim_requirements"
down_revision: Union[str, None] = "v090_new_modules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def _has_table(inspector: sa.engine.reflection.Inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def upgrade() -> None:
    """Create BIM requirements tables."""
    # The boot heal runs create_all before anything reads alembic_version, so on
    # every install this application has started these tables already exist by
    # the time an operator runs `alembic upgrade head`. Creating one again
    # raises DuplicateTable, PostgreSQL rolls the whole run back, and every
    # revision after this one is skipped. Each table is asked for separately so
    # a boot that failed midway still converges.
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "oe_bim_requirement_set"):
        op.create_table(
            "oe_bim_requirement_set",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "project_id",
                sa.String(36),
                sa.ForeignKey("oe_projects_project.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("name", sa.String(500), nullable=False),
            sa.Column("description", sa.Text, nullable=False, server_default=""),
            sa.Column("source_format", sa.String(50), nullable=False),
            sa.Column("source_filename", sa.String(500), nullable=False, server_default=""),
            sa.Column("created_by", sa.String(36), nullable=False, server_default=""),
            sa.Column("metadata", sa.JSON, nullable=False, server_default="{}"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )

    if not _has_table(inspector, "oe_bim_requirement"):
        op.create_table(
            "oe_bim_requirement",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "requirement_set_id",
                sa.String(36),
                sa.ForeignKey("oe_bim_requirement_set.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("element_filter", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("property_group", sa.String(255), nullable=True),
            sa.Column("property_name", sa.String(255), nullable=False),
            sa.Column("constraint_def", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("context", sa.JSON, nullable=True),
            sa.Column("source_format", sa.String(50), nullable=False, server_default=""),
            sa.Column("source_ref", sa.Text, nullable=False, server_default=""),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )


def downgrade() -> None:
    """Drop BIM requirements tables."""
    op.drop_table("oe_bim_requirement")
    op.drop_table("oe_bim_requirement_set")
