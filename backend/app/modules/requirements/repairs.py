# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Boot-path data repairs owned by the requirements module.

Imported by :func:`app.core.data_repairs.discover_data_repairs`, which is what
makes the registration below take effect. Nothing else imports this file, and
nothing needs to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.data_repairs import DataRepair, register_data_repair

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_ITEM = "oe_requirements_item"

#: The five columns ``v3285_requirements_cycle`` added, with the SQL literal
#: that revision gave each one. Copied from it deliberately rather than derived:
#: the whole defect is that a heal-built database and a migration-built one
#: diverged, so the repair has to restore what the revision declared, and the
#: values have to be readable side by side with it.
#:
#: Empty string, not NULL, is how this model has always spelled "not recorded".
#: Every one of the five is ``nullable=False, default=""`` in ``models.py`` and
#: plain ``str`` in ``RequirementResponse``, so a requirement imported from a
#: client document with no stated rationale already reads as ``""`` on every
#: path that writes through the ORM. The rows this repairs are the ones that
#: went in underneath it.
_ITEM_COLUMNS = {
    "rationale": "''",
    "originator": "''",
    "originator_role": "''",
    "phase": "''",
    "verification_method": "''",
}


async def _run(session: AsyncSession) -> int:
    """Backfill and tighten the five columns the heal could not carry NOT NULL.

    Imported inside the function so that importing this module costs only the
    registration, not the repair's own dependency tree.
    """
    from app.core.not_null_repair import tighten_not_null

    return await tighten_not_null(session, _ITEM, _ITEM_COLUMNS)


#: Nature ``always_wrong``: the models have declared these five NOT NULL since
#: the revision that added them, and ``RequirementResponse`` types them as
#: ``str``. A NULL here is not a value that used to be right - it is a value
#: no reader can accept, and every read of such a row raises ValidationError
#: rather than returning something stale. So there is no window to close and
#: nothing to supersede; the row is repaired in place.
REQUIREMENTS_NOT_NULL_BACKFILL = register_data_repair(
    DataRepair(
        repair_id="requirements_cycle_not_null",
        revision="v3312_heal_left_columns_nullable",
        summary="Backfill and re-tighten the five requirement provenance columns the boot heal left nullable",
        run=_run,
        nature="always_wrong",
    )
)
