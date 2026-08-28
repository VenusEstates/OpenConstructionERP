# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Boot-path data repairs owned by the formwork module.

Imported by :func:`app.core.data_repairs.discover_data_repairs`, which is what
makes the registration below take effect. Nothing else imports this file, and
nothing needs to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.data_repairs import DataRepair, register_data_repair

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_SYSTEM = "oe_formwork_system"
_ASSIGNMENT = "oe_formwork_assignment"

#: The four columns ``v3262_formwork_rate_buildup`` added, with the SQL literal
#: that revision gave each one. Copied from it deliberately rather than chosen
#: here - see that revision's docstring, which is where these values are argued.
#:
#: Zero is a factual claim about money and this one is defensible because the
#: revision already made it: an erect/strike rate of zero reproduces exactly the
#: single-component total a row was priced with before the column existed, so a
#: legacy row keeps the number it has always shown. The same reasoning is why
#: the assignment's two halves go in at zero rather than being split out of
#: ``computed_unit_cost``: splitting would invent a labour component that was
#: never priced, and the honest way to populate them is to re-derive from the
#: catalogue through ``POST /api/v1/formwork/reprice``, which reports how much
#: moved.
#:
#: ``strip_time_days`` at one day is the weakest of the four and is worth stating
#: plainly. It is not the quietest value available: the cycle check raises a
#: conflict only when a gap is *shorter* than the striking time
#: (``service.py`` ``gap < strip_time_days``), so zero would flag nothing at all
#: while one still flags two pours dated the same day. One is taken because it is
#: what ``v3262_formwork_rate_buildup`` declared, and because a panel set that can
#: be struck and re-erected inside a day is not a claim worth carrying by default.
#: It is a floor, not a measurement of any real cycle - a legacy row is left
#: un-contradicted rather than asserted to be buildable.
_SYSTEM_COLUMNS = {
    "erect_strike_rate": "0",
    "strip_time_days": "1",
}
_ASSIGNMENT_COLUMNS = {
    "material_unit_cost": "0",
    "labour_unit_cost": "0",
}


async def _run(session: AsyncSession) -> int:
    """Rename the trademarked formwork catalogue rows an old seed left behind.

    Imported inside the function so that importing this module costs only the
    registration, not the repair's own dependency tree.
    """
    from app.modules.formwork.debrand import repair_branded_catalogue

    return await repair_branded_catalogue(session)


async def _run_not_null(session: AsyncSession) -> int:
    """Backfill and tighten the four rate columns the heal could not carry NOT NULL."""
    from app.core.not_null_repair import tighten_not_null

    rewritten = await tighten_not_null(session, _SYSTEM, _SYSTEM_COLUMNS)
    rewritten += await tighten_not_null(session, _ASSIGNMENT, _ASSIGNMENT_COLUMNS)
    return rewritten


#: Nature ``always_wrong``: the catalogue rows carried product names that were
#: never ours to ship, so there is no date on which the old value was correct
#: and no document that is entitled to keep reading it. Rewriting in place is
#: the whole repair - which is exactly what makes this the opposite case to the
#: tax repairs in ``i18n_foundation/repairs.py``, and why the registry insists
#: each repair says which of the two it is.
FORMWORK_DEBRAND = register_data_repair(
    DataRepair(
        repair_id="formwork_debrand",
        revision="v3271_formwork_debrand",
        summary="Rename trademarked formwork catalogue rows seeded before the de-brand",
        run=_run,
        nature="always_wrong",
    )
)

#: Nature ``always_wrong`` for the same reason as the debrand above, arrived at
#: differently: there is no date on which a NULL rate was the right answer,
#: because the models have declared all four NOT NULL since the revision that
#: introduced them and ``FormworkSystemResponse`` types them as ``Decimal`` and
#: ``int``. Nothing priced a job off a NULL - it fails to read at all.
FORMWORK_NOT_NULL_BACKFILL = register_data_repair(
    DataRepair(
        repair_id="formwork_rate_buildup_not_null",
        revision="v3312_heal_left_columns_nullable",
        summary="Backfill and re-tighten the four formwork rate columns the boot heal left nullable",
        run=_run_not_null,
        nature="always_wrong",
    )
)
