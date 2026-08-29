# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""i18n: repair the Saudi work calendar seeded with a zero-based week.

``app/modules/i18n_foundation/seed_data/work_calendars.json`` shipped Saudi
Arabia as ``work_days: [0, 1, 2, 3, 4]``. Every other country in that file is
``[1, 2, 3, 4, 5]``, so the row was deliberately a different week - Sunday to
Thursday, written under a zero-based convention where Sunday is 0. The
consumer, ``I18nFoundationService.get_working_days``, matches these numbers
against ``date.isoweekday()``, which returns 1..7 and never 0. So the 0 matched
nothing and Saudi Arabia was counted as a four-day week: a duration converted
to a finish date stretched, a count of working days between two dates came up
short, and neither raised anything.

The seed file is fixed to ``[7, 1, 2, 3, 4]`` in the same change. That fix
reaches new installs only - ``seed.py`` returns early when the table already
has rows, so every deployment that has already seeded keeps the broken value
forever. This revision is the repair for those.

What it touches
---------------
Exactly one shape: a row whose ``country_code`` is ``SA`` and whose
``work_days`` is the set the seeder wrote, ``{0, 1, 2, 3, 4}``. That value
produces zero working days on every date, so no deployment can be relying on
it, and the intended week is documented in two other places in this repo -
``app/core/calendar.py`` lists SA under "Middle East - Sunday through Thursday"
and ``app/modules/middle_east_pack/config.py`` spells the same week out by
name. It is rewritten to ``[7, 1, 2, 3, 4]``, the ISO spelling of Sunday to
Thursday, matching the corrected seed byte for byte.

What it leaves alone, and why
-----------------------------
Any other row carrying a weekday outside 1..7 is reported by country and value
and left exactly as it is. It is not repaired, because the number cannot be
read without knowing which convention wrote it, and this platform has two
zero-based ones live at once: JavaScript's ``getDay()`` (Sunday = 0), which is
what the SA row was written in, and ``date.weekday()`` (Monday = 0), which is
what ``app/core/calendar.py``, ``app/core/cpm.py``, the schedule module and the
work-calendar UI all use. ``[0, 1, 2, 3, 4]`` means Sunday-Thursday under the
first and Monday-Friday under the second. Guessing would replace one silently
wrong week with another silently wrong week, which is worse than a row an
operator can see named in the upgrade output.

Rows already inside 1..7 are not touched at all, whatever they say. A
deployment that edited its calendars is a deployment whose calendars are
correct as far as this revision can tell.

Idempotent. The predicate is on the data, not on a version marker: after the
first run no row matches the shape any more, so a re-run against a partially
migrated - or fully migrated - database rewrites nothing.

Revision ID: v3303_work_calendar_iso_weekdays
Revises: v3302_tax_combination
Create Date: 2026-08-23
"""

from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision: str = "v3303_work_calendar_iso_weekdays"
down_revision: Union[str, Sequence[str], None] = "v3302_tax_combination"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "oe_i18n_work_calendar"

# The one row this revision repairs, and what it becomes. Written as a set on
# the left because the stored order carries no meaning - the service reads the
# column through ``set(...)`` - and as a list on the right because that is the
# literal the corrected seed file now carries.
_BROKEN_COUNTRY = "SA"
_BROKEN_WEEK = frozenset({0, 1, 2, 3, 4})
_REPAIRED_WEEK = [7, 1, 2, 3, 4]


def _classify(country_code: str, work_days: object) -> tuple[list[int] | None, str | None]:
    """Decide what to do with one row.

    Returns ``(repair, warning)``:

    * ``(None, None)`` - leave the row alone, it declares ISO weekdays.
    * ``([...], None)`` - the shipped Saudi row; rewrite it to this value.
    * ``(None, "...")`` - out of range, but not a shape this revision can read.
      The string names the country and the offending values, for the operator.

    Kept a plain function, taking the value rather than a connection, so the
    decision can be tested directly on every shape without a database.
    """
    if isinstance(work_days, str):
        # Some drivers hand back the text of a JSON column rather than the
        # value; a malformed one is reported, never guessed at.
        try:
            work_days = json.loads(work_days)
        except ValueError:
            return None, f"{country_code}: work_days is not readable JSON ({work_days!r})"

    if not isinstance(work_days, list) or not work_days:
        return None, f"{country_code}: work_days is not a non-empty list ({work_days!r})"

    if not all(isinstance(day, int) and not isinstance(day, bool) for day in work_days):
        return None, f"{country_code}: work_days holds something that is not a whole number ({work_days!r})"

    outside = sorted({day for day in work_days if not 1 <= day <= 7})
    if not outside:
        return None, None

    if country_code == _BROKEN_COUNTRY and set(work_days) == _BROKEN_WEEK:
        return list(_REPAIRED_WEEK), None

    return None, (
        f"{country_code}: work_days {work_days!r} carries {outside}, which is outside the ISO range 1..7 "
        f"and matches no date. Left unchanged - the intended week cannot be read from the number alone. "
        f"Set it by hand: Monday = 1 through Sunday = 7."
    )


# data-rewrite-ack: table=oe_i18n_work_calendar growth=bounded rows=30 as shipped, one per
# boot-repair: registry=work_calendar_iso_zero
# country and year we carry a calendar for; a deployment gains a row only when somebody
# adds a country or a year by hand, so the count tracks the catalogue rather than how long
# the install has run. At most one row is rewritten, and only where the value still matches
# the one the seeder wrote.
def _repair_rows(bind: Connection) -> tuple[int, int, list[str]]:
    """Repair every row that still carries the seeded Saudi week.

    Returns ``(rows inspected, rows repaired, warnings)``. Takes a connection
    rather than reaching for ``op.get_bind()`` itself so the repair can be run
    against a real database in a test, which is the only way to prove the SQL
    and the JSON round-trip rather than just the decision rule.
    """
    rows = bind.execute(sa.text(f"SELECT country_code, year, work_days FROM {_TABLE}")).fetchall()  # noqa: S608

    repaired = 0
    warnings: list[str] = []
    for country_code, year, work_days in rows:
        repair, warning = _classify(country_code, work_days)
        if warning is not None:
            warnings.append(warning)
        if repair is None:
            continue
        # ``work_days`` is bound as sa.JSON so SQLAlchemy owns the serialisation
        # rather than the driver guessing what a list of ints means to a JSON
        # column. The read above is left to arrive however the driver hands it
        # over, which is why ``_classify`` accepts a list or the text of one.
        bind.execute(
            sa.text(  # noqa: S608 - table name is a module constant, not input
                f"UPDATE {_TABLE} SET work_days = :work_days WHERE country_code = :country_code AND year = :year"
            ).bindparams(sa.bindparam("work_days", type_=sa.JSON)),
            {"work_days": repair, "country_code": country_code, "year": year},
        )
        repaired += 1
        print(f"  v3303: {country_code} {year} work_days {work_days!r} -> {repair} (Sunday to Thursday)")

    return len(rows), repaired, warnings


def upgrade() -> None:
    """Rewrite the seeded Saudi week to ISO weekdays; report anything else out of range."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        # Nothing has ever been seeded here; the corrected seed file is the
        # only thing this database will ever see.
        return

    inspected, repaired, warnings = _repair_rows(bind)

    for warning in warnings:
        print(f"  v3303: {warning}")
    print(f"  v3303: inspected {inspected} work calendars, repaired {repaired}.")


def downgrade() -> None:
    # No-op, deliberately. Reverting means writing 0 back into a column whose
    # only consumer matches it against isoweekday(), i.e. re-breaking the row -
    # and this revision does not record which rows it rewrote, so it could not
    # tell a row it repaired from one that always read [7, 1, 2, 3, 4]. An
    # operator who wants the old value can set it by hand.
    pass
