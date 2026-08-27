# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Nova Scotia's rate cut, against the install that never received it.

What is under test
------------------
``tax_window_supersede`` closes a shipped tax window an old install still holds
open and inserts the rate that replaced it. The live case is Nova Scotia, which
cut its harmonised rate from 15 % to 14 % on 2025-04-01: the seed file carries
both windows, an install seeded before v15.5.0 holds only the 15 % one, and
until this repair existed it went on charging 15 % for ever while
``/api/health`` reported a clean boot.

Where the cohorts come from
---------------------------
From ``test_tax_seed_reconcile``, deliberately, rather than rebuilt here. Those
fixtures reconstruct the seed files two releases actually shipped and carry a
digest of the real file so the reconstruction cannot drift, and a second
hand-written copy of a database state is the thing that quietly stops matching
the one customers have. The important property for this file is in
``_RESTORED_TO_V15_4_0``: it puts Nova Scotia's 15 % window back to open, which
is the entire defect.

What every assertion here is written against
--------------------------------------------
The resolved rate, through ``resolve``, rather than the column the repair
wrote. Which column a repair writes says nothing about what a Canadian firm is
charged - the two questions came apart on this table once already - so the
claim being made is "Nova Scotia is billed 14 %", and that is what is asserted.
Ontario is carried through every whole-registry test as a control: same
country, same ``replaces_federal`` class, the same ``effective_from`` as Nova
Scotia's old window, one window only. A predicate that closed windows on
anything broader than the rate line moves it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.data_repairs import discover_data_repairs, run_data_repairs, snapshot_table, verify_supersede_shape
from app.modules.i18n_foundation.models import TaxConfiguration
from app.modules.i18n_foundation.seed import load_tax_seed_rows, tax_configuration_from_seed_row
from app.modules.i18n_foundation.tax_rules import TaxRuleError, resolve, row_from_orm
from app.modules.i18n_foundation.tax_window_supersede import REPAIR_ID
from tests.pg.test_tax_seed_reconcile import (  # noqa: F401 - repair_factory is a fixture used by name
    _install,
    _outcome,
    _own_rate,
    pre_v15_5_0,
    repair_factory,
    v15_9_1,
)

pytestmark = pytest.mark.asyncio

_TAX_TABLE = "oe_i18n_tax_config"

#: The day before Nova Scotia's cut and the day of it. Every money claim in
#: this file is made on one of these two dates, because "the old rate is kept
#: for work priced under it" is a statement about a boundary and nowhere else.
_LAST_DAY_AT_FIFTEEN = "2025-03-31"
_FIRST_DAY_AT_FOURTEEN = "2025-04-01"

#: Well after both windows open, so "what is billed today" is unambiguous.
_TODAY_IN_THE_FIXTURES = "2026-08-01"


def _repair():
    return next(r for r in discover_data_repairs() if r.repair_id == REPAIR_ID)


def _the_repairs_that_shipped_before_this_one() -> tuple:
    """Every registered repair except this one.

    The "before" state a Canadian firm is actually in is not the raw cohort.
    On that, every row reads ``national`` with no province, so Canada resolves
    to nothing at all for every province - the labelling repairs are what turn
    it back into a country that prices, and they have been shipping for
    releases. Nova Scotia only resolves at 15 % once they have run, so that is
    the state this repair has to be measured against. Measuring against the raw
    cohort would let a repair that did nothing look like it had moved a rate
    off zero.
    """
    return tuple(r for r in discover_data_repairs() if r.repair_id != REPAIR_ID)


async def _flat(factory) -> list:
    async with factory() as session:
        rows = (await session.execute(select(TaxConfiguration))).scalars().all()
        return [row_from_orm(row) for row in rows]


async def _rate(factory, subdivision: str | None, on_date: str = _TODAY_IN_THE_FIXTURES) -> str | None:
    """What one jurisdiction is billed, through the product's own resolver."""
    outcome = resolve(await _flat(factory), "CA", subdivision, on_date=on_date)
    return outcome.combined_rate_pct


async def _windows(factory, tax_code: str) -> list[tuple[str, str | None, str | None]]:
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    TaxConfiguration.rate_pct,
                    TaxConfiguration.effective_from,
                    TaxConfiguration.effective_to,
                )
                .where(TaxConfiguration.country_code == "CA", TaxConfiguration.tax_code == tax_code)
                .order_by(TaxConfiguration.effective_from)
            )
        ).all()
    return [tuple(row) for row in rows]


async def _count(factory) -> int:
    async with factory() as session:
        return (await session.execute(select(func.count()).select_from(TaxConfiguration))).scalar_one()


async def _edit_nova_scotia(factory, **values) -> None:
    """Change the shipped Nova Scotia row, the way an operator would have."""
    async with factory() as session:
        await session.execute(
            TaxConfiguration.__table__.update()
            .where(TaxConfiguration.country_code == "CA", TaxConfiguration.tax_code == "HST_NS")
            .values(**values)
        )
        await session.commit()


# ── The defect, and the fix ──────────────────────────────────────────────────


async def test_a_pre_v15_5_install_starts_charging_what_nova_scotia_actually_charges(repair_factory) -> None:
    """The whole defect and the whole fix, in the units the money is in.

    Run through the real registry rather than this repair alone, because the
    boot a customer gets runs all five and the answer they see is the one that
    comes out of the lot of them together.
    """
    await _install(repair_factory, pre_v15_5_0(), "2026-06-01")
    await run_data_repairs(repair_factory, repairs=_the_repairs_that_shipped_before_this_one())

    before = await _rate(repair_factory, "CA-NS")
    assert before == "15", (
        f"Nova Scotia already bills {before} with only the repairs that shipped before this one, so "
        "this fixture is not the broken cohort and everything below it would be measuring nothing"
    )
    control_before = await _rate(repair_factory, "CA-ON")
    assert control_before == "13"

    report = await run_data_repairs(repair_factory)

    outcome = _outcome(report, REPAIR_ID)
    assert outcome.status == "applied", f"the repair did nothing: {outcome}"
    assert outcome.rows_changed == 2, f"expected one window closed and one rate added, got {outcome.rows_changed}"

    assert await _rate(repair_factory, "CA-NS") == "14", "Nova Scotia is still billed its superseded rate"

    control_after = await _rate(repair_factory, "CA-ON")
    assert control_after == "13", f"Ontario moved to {control_after}; the repair closed a window it does not own"
    assert await _windows(repair_factory, "HST_ON") == [("13.0", "2010-07-01", None)], (
        "Ontario's window was closed - it ships one window, so nothing here has any business ending it"
    )


async def test_work_priced_before_the_cut_still_resolves_at_the_old_rate(repair_factory) -> None:
    """Close-and-add, asserted where it actually matters: either side of the boundary.

    This is the reason the 15 % row is closed rather than edited to say 14. An
    estimate or an invoice priced in March 2025 has to keep the rate it was
    priced at, and a repair that rewrote the rate in place would change the
    value of a document already sent to a customer, months later, silently.
    """
    await _install(repair_factory, pre_v15_5_0(), "2026-06-01")
    await run_data_repairs(repair_factory)

    assert await _rate(repair_factory, "CA-NS", _LAST_DAY_AT_FIFTEEN) == "15", (
        "a document priced on the last day of the old rate no longer resolves at 15%"
    )
    assert await _rate(repair_factory, "CA-NS", _FIRST_DAY_AT_FOURTEEN) == "14", (
        "the new rate is not in force on the day it took effect"
    )

    assert await _windows(repair_factory, "HST_NS") == [
        ("15.0", "2010-07-01", _LAST_DAY_AT_FIFTEEN),
        ("14.0", _FIRST_DAY_AT_FOURTEEN, None),
    ]


async def test_a_second_boot_changes_nothing(repair_factory) -> None:
    """Idempotence, as the registry requires it."""
    await _install(repair_factory, pre_v15_5_0(), "2026-06-01")

    first = _outcome(await run_data_repairs(repair_factory), REPAIR_ID)
    assert first.rows_changed == 2
    settled_windows = await _windows(repair_factory, "HST_NS")
    settled_count = await _count(repair_factory)

    second = _outcome(await run_data_repairs(repair_factory), REPAIR_ID)

    assert second.status == "clean"
    assert second.rows_changed == 0
    assert await _windows(repair_factory, "HST_NS") == settled_windows, "the second boot rewrote the windows"
    assert await _count(repair_factory) == settled_count, "the second boot inserted the replacement rate again"
    assert await _rate(repair_factory, "CA-NS") == "14"


async def test_the_repair_closes_and_adds_rather_than_rewriting(repair_factory) -> None:
    """The declared nature, held against the repair's real effect on the table.

    The length assertion is not decoration. ``verify_supersede_shape`` returns
    no violations for a repair that did nothing at all, so without proof that
    this pass actually wrote something the contract check below is vacuous.
    """
    await _install(repair_factory, pre_v15_5_0(), "2026-06-01")

    repair = _repair()
    assert repair.nature == "superseded"

    async with repair_factory() as session:
        before = await snapshot_table(session, _TAX_TABLE)
    await run_data_repairs(repair_factory, repairs=(repair,))
    async with repair_factory() as session:
        after = await snapshot_table(session, _TAX_TABLE)

    assert len(after) == len(before) + 1, "the pass under test added no row, so the check below is vacuous"
    assert verify_supersede_shape(repair, before, after) == ()


async def test_the_old_row_is_not_labelled_yet_and_is_carried_forward_anyway(repair_factory) -> None:
    """Run alone, against rows the sibling repairs have not reached yet.

    A database seeded before v15.7.0 carries no subdivision on any row and the
    boot heal has filled ``combination`` with its server default, so the Nova
    Scotia row reads ``national`` with no province until
    ``tax_subdivision_backfill`` gets to it. Two things are being measured.

    That the predicate does not depend on which repair the registry happens to
    run first - the module warns about exactly that class of bug, and an
    ordering dependency here would be a repair that works today and quietly
    stops working when somebody reorders a file.

    And that the ``UPDATE`` lands at all. The heal adds the subdivision check
    constraint ``NOT VALID``, which exempts rows already on file but never a
    statement, so writing to one of the rows it exempts is the trap this table
    has sprung before. ``national`` with no subdivision satisfies the
    constraint; a row the heal had left in the other broken shape would not.
    """
    await _install(repair_factory, pre_v15_5_0(), "2026-06-01")

    async with repair_factory() as session:
        shape = (
            await session.execute(
                select(TaxConfiguration.combination, TaxConfiguration.subdivision_code).where(
                    TaxConfiguration.country_code == "CA", TaxConfiguration.tax_code == "HST_NS"
                )
            )
        ).one()
    assert shape == ("national", None), (
        f"Nova Scotia's row is already labelled {shape} in this fixture, so running without the "
        "labelling repair proves nothing about the cohort that has not had it"
    )

    outcome = _outcome(await run_data_repairs(repair_factory, repairs=(_repair(),)), REPAIR_ID)

    assert outcome.status == "applied", f"the unlabelled row was not carried forward: {outcome}"
    assert outcome.rows_changed == 2
    assert await _rate(repair_factory, "CA-NS") == "14"
    # Ontario is checked as a row rather than as a rate here, because on a
    # cohort the labelling repairs have not reached no Canadian province
    # resolves to anything at all - which is the other defect, not this one.
    assert await _windows(repair_factory, "HST_ON") == [("13.0", "2010-07-01", None)], (
        "Ontario's window was closed while this repair ran on its own"
    )


async def test_a_modern_install_is_left_alone(repair_factory) -> None:
    """A database seeded with both windows must come out of the pass untouched."""
    await _install(repair_factory, v15_9_1(), "2026-08-25")
    before = await _windows(repair_factory, "HST_NS")
    assert len(before) == 2, "this cohort does not already carry both windows, so it is the wrong control"

    outcome = _outcome(await run_data_repairs(repair_factory), REPAIR_ID)

    assert outcome.status == "clean"
    assert outcome.rows_changed == 0
    assert await _windows(repair_factory, "HST_NS") == before
    assert await _rate(repair_factory, "CA-NS") == "14"


# ── The rows this repair must not touch ──────────────────────────────────────


async def test_a_rate_somebody_edited_is_left_alone(repair_factory) -> None:
    """A row that does not say what the seeder wrote is a row somebody manages themselves."""
    await _install(repair_factory, pre_v15_5_0(), "2026-06-01")
    await _edit_nova_scotia(repair_factory, rate_pct="15.5")

    outcome = _outcome(await run_data_repairs(repair_factory), REPAIR_ID)

    assert outcome.rows_changed == 0, "a rate the operator set was superseded by the shipped one"
    assert await _windows(repair_factory, "HST_NS") == [("15.5", "2010-07-01", None)]
    assert await _rate(repair_factory, "CA-NS") == "15.5", "Nova Scotia stopped charging the rate its owner set"


async def test_a_window_somebody_re_dated_is_left_alone(repair_factory) -> None:
    """The other half of the same predicate, and it fails differently.

    A row carrying our rate but somebody else's start date is a window they
    decided the shape of. Matching on the rate alone would close it on our
    date and take the difference away from every document priced in between.
    """
    await _install(repair_factory, pre_v15_5_0(), "2026-06-01")
    await _edit_nova_scotia(repair_factory, effective_from="2011-04-01")

    outcome = _outcome(await run_data_repairs(repair_factory), REPAIR_ID)

    assert outcome.rows_changed == 0, "a window the operator re-dated was closed on the shipped date"
    assert await _windows(repair_factory, "HST_NS") == [("15.0", "2011-04-01", None)]
    assert await _rate(repair_factory, "CA-NS") == "15"


async def test_a_window_flagged_as_the_default_is_left_alone(repair_factory) -> None:
    """``is_default`` is part of the predicate rather than something this may move.

    Romania's repair permits itself to take the flag off the row it closes, and
    declares that allowance so the contract test can see it. This one does not:
    both shipped Nova Scotia windows are unflagged, so there is nothing here to
    exercise such an allowance and an unexercised hole in the close-and-add
    contract is worth less than nothing. A row whose flag differs from the
    shipped window is simply not the row we shipped.
    """
    await _install(repair_factory, pre_v15_5_0(), "2026-06-01")
    await _edit_nova_scotia(repair_factory, is_default=True)

    outcome = _outcome(await run_data_repairs(repair_factory), REPAIR_ID)

    assert outcome.rows_changed == 0, "a row carrying a flag the seeder never wrote was rewritten"
    assert await _windows(repair_factory, "HST_NS") == [("15.0", "2010-07-01", None)]


async def test_a_rate_moved_to_another_province_is_left_alone(repair_factory) -> None:
    """The clobber a predicate keyed only on the rate line would not see.

    ``tax_subdivision_repair`` says in as many words that an operator who moved
    a rate to a different province keeps what they set. If this repair ignored
    ``subdivision_code`` it would close that row on Nova Scotia's date and hand
    Nova Scotia a rate, and the province they had actually moved it to would
    lose its rate altogether - additive on the table, destructive on the answer,
    and nothing in the row would look wrong afterwards.

    Run alone: the reconciler delivers Prince Edward Island to this cohort in
    the same boot, which would put a second harmonised rate in the province and
    change what is being measured.
    """
    await _install(repair_factory, pre_v15_5_0(), "2026-06-01")
    # Both halves together - the table's check constraint holds them to be one
    # statement, so a province cannot be written without the combination.
    await _edit_nova_scotia(repair_factory, subdivision_code="CA-PE", combination="replaces_federal")

    assert await _rate(repair_factory, "CA-PE") == "15", "the fixture did not move the rate to another province"

    outcome = _outcome(await run_data_repairs(repair_factory, repairs=(_repair(),)), REPAIR_ID)

    assert outcome.rows_changed == 0, "a rate the operator moved to another province was closed"
    assert await _rate(repair_factory, "CA-PE") == "15", "Prince Edward Island lost the rate it had been given"
    assert await _windows(repair_factory, "HST_NS") == [("15.0", "2010-07-01", None)]


async def test_a_province_that_has_a_hand_entered_harmonised_rate_is_left_alone(repair_factory) -> None:
    """The install most likely to hold its own Nova Scotia rate is this repair's own cohort.

    A province that has been billed the wrong rate for a year is one somebody
    may well have corrected by hand, under their own tax code. Two rates each
    replacing the federal one in one province is not a wrong number: ``resolve``
    raises, and the province stops pricing at all. It does so before this repair
    runs and it would do so afterwards, so applying would edit their data and
    buy nothing.

    Two things are asserted, and the second is the one that is easy to miss.
    The repair leaves the rows alone, and it comes back ``clean`` rather than
    ``failed`` - a guard that let the resolver's exception out would turn an
    install this repair had already decided not to touch into a red health
    check on every boot for the life of the install.
    """
    await _install(repair_factory, pre_v15_5_0(), "2026-06-01")
    # In the order it really happens: the install has been booting the shipped
    # repairs for releases, and at some point somebody typed the correct rate in
    # themselves because ours still said 15.
    await run_data_repairs(repair_factory, repairs=_the_repairs_that_shipped_before_this_one())
    await _own_rate(
        repair_factory,
        country_code="CA",
        tax_code="NS_HST_ENTERED",
        rate_pct="14.0",
        combination="replaces_federal",
        subdivision_code="CA-NS",
        is_default=False,
    )

    with pytest.raises(TaxRuleError):
        resolve(await _flat(repair_factory), "CA", "CA-NS", on_date=_TODAY_IN_THE_FIXTURES)

    outcome = _outcome(await run_data_repairs(repair_factory, repairs=(_repair(),)), REPAIR_ID)

    assert outcome.status == "clean", f"the repair failed the boot instead of declining the install: {outcome}"
    assert outcome.rows_changed == 0
    assert await _windows(repair_factory, "HST_NS") == [("15.0", "2010-07-01", None)], (
        "the shipped window was closed beside a rate the customer had entered themselves"
    )
    assert await _rate(repair_factory, "CA-ON") == "13", "one province holding its own rate disturbed another"


async def test_a_half_applied_install_is_finished_rather_than_left_broken(repair_factory) -> None:
    """The replacement already on file, beside a predecessor nobody closed.

    Reachable on any install where somebody added the correct rate by hand
    under our own tax code and did not know to end the old window. Nova Scotia
    then holds two rates that each replace the federal one and cannot price at
    all - so this is not a database to leave alone, it is one where closing the
    old window on its own is the entire remaining repair.

    Written because the first version of this module skipped the line whenever
    it had nothing to insert, on the reasoning that closing without replacing
    takes a rate away. That reasoning is only right when there is no
    replacement, and here there is one.
    """
    await _install(repair_factory, pre_v15_5_0(), "2026-06-01")
    await run_data_repairs(repair_factory, repairs=_the_repairs_that_shipped_before_this_one())
    async with repair_factory() as session:
        session.add(
            tax_configuration_from_seed_row(
                next(
                    row
                    for row in load_tax_seed_rows()
                    if row["country_code"] == "CA" and row["tax_code"] == "HST_NS" and row["rate_pct"] == "14.0"
                )
            )
        )
        await session.commit()

    with pytest.raises(TaxRuleError):
        resolve(await _flat(repair_factory), "CA", "CA-NS", on_date=_TODAY_IN_THE_FIXTURES)

    outcome = _outcome(await run_data_repairs(repair_factory, repairs=(_repair(),)), REPAIR_ID)

    assert outcome.rows_changed == 1, f"expected the old window closed and nothing inserted, got {outcome}"
    assert await _rate(repair_factory, "CA-NS") == "14", "Nova Scotia still cannot price"
    assert await _windows(repair_factory, "HST_NS") == [
        ("15.0", "2010-07-01", _LAST_DAY_AT_FIFTEEN),
        ("14.0", _FIRST_DAY_AT_FOURTEEN, None),
    ]
    assert await _rate(repair_factory, "CA-NS", _LAST_DAY_AT_FIFTEEN) == "15"


async def test_a_database_with_no_nova_scotia_row_is_given_nothing(repair_factory) -> None:
    """A rate line that is absent belongs to the reconciler, not to this repair.

    Deleting the rate is how an operator says they do not want it. There is no
    window to close, and inserting the replacement on its own would resurrect a
    line they removed - which this repair, having no delivery record of its
    own, could never stop doing again on the next boot.
    """
    await _install(repair_factory, pre_v15_5_0(), "2026-06-01")
    async with repair_factory() as session:
        await session.execute(
            TaxConfiguration.__table__.delete().where(
                TaxConfiguration.country_code == "CA", TaxConfiguration.tax_code == "HST_NS"
            )
        )
        await session.commit()

    outcome = _outcome(await run_data_repairs(repair_factory, repairs=(_repair(),)), REPAIR_ID)

    assert outcome.rows_changed == 0
    assert await _windows(repair_factory, "HST_NS") == [], "a rate line the operator removed was recreated"


async def test_two_rows_that_both_look_like_the_shipped_window_are_left_alone(repair_factory) -> None:
    """Which of two identical rows the seeder wrote cannot be told, so neither is closed.

    Reachable on a database where somebody duplicated the row rather than
    editing it. Closing one would leave the other open and Nova Scotia with two
    rates in force; closing both would be a decision about a row this repair has
    no evidence about.
    """
    await _install(repair_factory, pre_v15_5_0(), "2026-06-01")
    async with repair_factory() as session:
        original = (
            await session.execute(
                select(TaxConfiguration).where(
                    TaxConfiguration.country_code == "CA", TaxConfiguration.tax_code == "HST_NS"
                )
            )
        ).scalar_one()
        session.add(
            TaxConfiguration(
                country_code=original.country_code,
                tax_name=original.tax_name,
                tax_code=original.tax_code,
                rate_pct=original.rate_pct,
                tax_type=original.tax_type,
                combination=original.combination,
                subdivision_code=original.subdivision_code,
                effective_from=original.effective_from,
                effective_to=original.effective_to,
                is_default=original.is_default,
                metadata_={},
            )
        )
        await session.commit()
    assert len(await _windows(repair_factory, "HST_NS")) == 2

    outcome = _outcome(await run_data_repairs(repair_factory, repairs=(_repair(),)), REPAIR_ID)

    assert outcome.rows_changed == 0
    assert await _windows(repair_factory, "HST_NS") == [
        ("15.0", "2010-07-01", None),
        ("15.0", "2010-07-01", None),
    ]
