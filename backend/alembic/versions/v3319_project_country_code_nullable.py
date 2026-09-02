# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""projects: a project with no country stops being a German one

``oe_projects_project.country_code`` was ``NOT NULL DEFAULT 'DE'``. The API has
always accepted the field as optional, so a create that omitted it stored the
project as German, and "nobody chose a country" and "somebody chose Germany"
became the same row. The model has carried a comment saying exactly that, and
saying that fixing it was a migration and a product decision rather than a
comment. This is that migration.

It matters more now than it did. The column is about to decide which national
markup stack a bill is seeded with, so the ambiguity stops being a wrong
working calendar and starts being a wrong price: an unstated market would be
quoted with German site overheads, German profit and German VAT, and nothing on
the screen would say a country had been assumed.

What this does NOT do, deliberately: it does not touch a single existing row.
Every project written before today carries 'DE', and there is no signal in the
data that separates the ones where somebody meant it from the ones where nobody
chose. Guessing which is which and writing NULL over the difference would
destroy real answers to remove fake ones. The distinction is recoverable going
forward and unrecoverable backwards, and that is the honest state.

So the effect is only on rows written from here on. A create that names a
country stores it; a create that does not store NULL, and every consumer that
reads the column now gets to tell the two apart. The three consumers are the
CPM working calendar, the AIA payment-application gate and, from this release,
markup-region resolution, and all three already treat an unknown country as
"no opinion" rather than as an error.

``server_default`` is dropped along with the NOT NULL. Leaving it would keep a
bare INSERT writing 'DE' and make the nullability cosmetic, which is the same
defect in a quieter form.

Guarded the way this tree guards DDL, and for the same failure: the boot path
runs ``Base.metadata.create_all`` before an operator can run ``alembic upgrade
head``, so on a fresh install the column is already nullable by the time this
revision runs. Re-issuing ``ALTER COLUMN`` on an already-nullable column is
harmless on PostgreSQL, but the reflection check keeps the revision a no-op
rather than a no-op that happens to work, and it also means a SQLite dev
database, where ALTER COLUMN of this shape is not supported at all, is skipped
instead of raising.

Revision ID: v3319_project_country_code_nullable
Revises: v3318_field_time_line_boq_position
Create Date: 2026-09-02
"""

from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "v3319_project_country_code_nullable"
down_revision: Union[str, Sequence[str], None] = "v3318_field_time_line_boq_position"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE = "oe_projects_project"
COLUMN = "country_code"


def _column(insp: sa.engine.reflection.Inspector, table: str, column: str) -> Any | None:
    if table not in insp.get_table_names():
        return None
    for col in insp.get_columns(table):
        if col["name"] == column:
            return col
    return None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite cannot ALTER a column's nullability in place, and the dev
        # databases that use it are built by create_all from the model, which
        # already declares the column nullable.
        return

    col = _column(sa.inspect(bind), TABLE, COLUMN)
    if col is None:
        return
    if col.get("nullable") and col.get("default") is None:
        return

    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=sa.String(length=2),
        nullable=True,
        server_default=None,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    col = _column(sa.inspect(bind), TABLE, COLUMN)
    if col is None:
        return

    # Going back means the column cannot hold NULL again, and the only value
    # available to put there is the one whose ambiguity this revision exists to
    # remove. Rows created since the upgrade with no country are written to
    # 'DE' on the way down, which is lossy in exactly the way the upgrade note
    # describes. That is what reverting this decision means, and it is stated
    # here rather than discovered.
    op.execute(sa.text(f"UPDATE {TABLE} SET {COLUMN} = 'DE' WHERE {COLUMN} IS NULL"))
    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=sa.String(length=2),
        nullable=False,
        server_default="DE",
    )
