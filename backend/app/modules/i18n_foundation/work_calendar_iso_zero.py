# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Correct a work calendar whose week was written on the Monday-zero axis.

``oe_i18n_work_calendar.work_days`` is read with ``date.isoweekday()``, which
returns 1 through 7 and never 0. A 0 in that column therefore matches no day
that exists: it does not shorten the week by moving a day, it removes one
silently, and the row goes on looking like a perfectly ordinary list of five
numbers. Saudi Arabia shipped as ``[0, 1, 2, 3, 4]`` from the seed file's first
release until commit ``0d2632c3d`` and was counted as a four-day week for that
whole time.

Why the guard is the value and not the country
----------------------------------------------
This repair edits a pre-existing row, which is the thing the other seed repair
in this module is forbidden to do, so what licenses it has to be stronger than
"we think this row is wrong". It is: **0 is not a choice anyone can have made.**
On the axis this column is read on there is no day numbered 0, so the value
cannot express a preference, cannot express an unusual working week, and cannot
be a customer's deliberate configuration. It is provably meaningless from the
row alone, without knowing who wrote it or when - which is exactly what
``always_wrong`` means and why no seed-date evidence is needed here.

Targeting the value rather than the country also means a second country that
ever acquires the same error is already covered, instead of needing a third
repair. And it keeps the repair honest about its own scope: a four-day
Monday-to-Thursday week of ``[1, 2, 3, 4]`` is unusual but entirely legal on
this axis, so it is left exactly as written. Unusual is not the same as
impossible, and only the impossible one is repaired.

Nothing writes a 0 here today
-----------------------------
Checked rather than assumed, because if a live writer emitted 0 this repair
would be papering over it every boot. Three write paths reach this column and
none of them can:

* The API. ``WorkCalendarCreate`` and ``WorkCalendarUpdate`` both type the field
  as ``list[IsoWeekday]``, which is ``Field(ge=1, le=7)``, so a 0 is rejected at
  the boundary. (``WorkCalendarResponse`` is ``list[int]``, but that is the read
  model.)
* The frontend. Nothing in it calls this module's work-calendar endpoints at
  all. The calendar editor that *does* write a Monday-zero week - ``Mon=0`` by
  its own constant - writes to ``oe_schedule_advanced_calendar``, a different
  table whose column is correctly typed ``Mon0Weekday``. Same field name, two
  tables, two axes, and they are not wired together.
* The seeder, which is the one path with no validation, since it puts the file
  onto the ORM directly. Every version of ``work_calendars.json`` in git holds
  exactly one row that ever contained a 0, and that is the Saudi row this
  repair exists for.

So on an install that has one, the expected count is 1. A larger number means a
writer nobody has found yet, and the count is logged for exactly that reason.
"""

from __future__ import annotations

import logging
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.i18n_foundation.models import WorkCalendar

logger = logging.getLogger(__name__)

#: The id this repair is registered and recorded under.
REPAIR_ID: Final = "work_calendar_iso_zero"

#: The value that cannot mean anything on this axis, and the day it was meant to
#: be. Sunday is 7 in ISO numbering and 0 in the Monday-zero one, so a week
#: written under the wrong convention lands its Sunday here.
_IMPOSSIBLE: Final = 0
_SUNDAY: Final = 7


def _is_weekday_number(value: object) -> bool:
    """Whether an entry is an integer this repair is entitled to reason about.

    ``bool`` is excluded deliberately. It is a subclass of ``int`` and ``False``
    compares equal to 0, so a JSON ``false`` in this column would otherwise be
    read as the impossible weekday and quietly rewritten to Sunday. A row of
    that shape is a different defect and not one this repair understands.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def repaired_week(work_days: object) -> list[int] | None:
    """The week with the impossible weekday corrected, or None to leave it alone.

    Pure, so the whole decision can be tested without a database.

    Args:
        work_days: The stored column, in whatever shape it is actually in.

    Returns:
        The corrected week, or ``None`` when there is nothing to correct - which
        covers the ordinary case of a valid week, and also a row whose shape
        this repair does not understand. Order is preserved so the repaired row
        reads the way the seed file writes it, and a 7 that was already present
        is not duplicated by the one arriving from the 0.
    """
    if not isinstance(work_days, list):
        return None
    if not all(_is_weekday_number(day) for day in work_days):
        return None
    if _IMPOSSIBLE not in work_days:
        return None

    corrected: list[int] = []
    for day in work_days:
        value = _SUNDAY if day == _IMPOSSIBLE else day
        if value not in corrected:
            corrected.append(value)
    return corrected


async def repair_iso_zero_weeks(session: AsyncSession) -> int:
    """Rewrite every work calendar whose week carries the impossible weekday.

    Args:
        session: An open session. The caller commits; the repair registry does.

    Returns:
        Number of calendars corrected. Zero on any install that never held the
        defect, and zero on every boot after the one that fixed it, because the
        predicate is the data itself rather than a version marker.
    """
    calendars = (await session.execute(select(WorkCalendar))).scalars().all()

    corrected = 0
    for calendar in calendars:
        week = repaired_week(calendar.work_days)
        if week is None:
            continue
        logger.info(
            "Work calendar ISO zero: %s %s held %s, which counts a weekday that isoweekday() never "
            "returns; rewriting to %s.",
            calendar.country_code,
            calendar.year,
            calendar.work_days,
            week,
        )
        # Reassigned rather than mutated: a JSON column does not track an
        # in-place edit, so mutating the list would leave the row unchanged.
        calendar.work_days = week
        corrected += 1

    if corrected:
        await session.flush()
        logger.info(
            "Work calendar ISO zero: corrected %d calendar(s). One is the expected number on an "
            "install carrying the shipped Saudi row; more than that means something is writing 0 "
            "into this column and the write path needs finding.",
            corrected,
        )
    return corrected
