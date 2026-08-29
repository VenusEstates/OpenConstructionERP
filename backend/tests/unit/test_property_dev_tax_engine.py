"""Unit tests for :mod:`app.modules.property_dev.tax_engine`.

Pure-function coverage — no DB, no HTTP. Each test pins one of the
edge cases enumerated in the task brief: UK SDLT bands + first-home +
additional-property, DE state-specific Grunderwerbsteuer, UAE DLD
transfer fee, IN GST + state stamp duty, RU state-duty,
SG BSD + ABSD, AU state stamp duty, late-interest accrual,
rate-effective-date behaviour, and unsupported-jurisdiction handling.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.core.provenance import Source
from app.modules.property_dev import tax_engine
from app.modules.property_dev.schemas import ContractTaxQuote
from app.modules.property_dev.tax_engine import (
    VAT_ABSENCE_KEY,
    VAT_ABSENCE_VALUES,
    VAT_ABSENT_BY_LAW,
    VAT_ABSENT_NOT_MODELLED,
    VAT_AXIS,
    VAT_STANDIN_NO_VAT_IN_LAW,
    MissingRegionSubcodeError,
    NoVatBlockError,
    RateNotInForceError,
    TaxEngineError,
    UnknownRateClassError,
    UnsupportedJurisdictionError,
    compute_absd,
    compute_late_interest,
    compute_registration_fee,
    compute_stamp_duty,
    compute_total_taxes_for_contract,
    compute_transfer_fee,
    compute_vat,
    gross_from_net,
    net_from_gross,
    supported_jurisdictions,
    vat_absence,
)

# ── 0. Smoke ────────────────────────────────────────────────────────────


def test_supported_jurisdictions_includes_core_set() -> None:
    codes = supported_jurisdictions()
    # All jurisdictions listed in the task brief must be loaded.
    for code in ("GB", "DE", "AE", "IN", "RU", "BR", "SG", "US", "AU"):
        assert code in codes, f"Missing jurisdiction {code} in tax table"


# ── 1. UK SDLT — bands, first-home, additional-property ─────────────────


def test_uk_sdlt_zero_band_400k_standard() -> None:
    # 0 % up to 250k + 5 % on 250k-400k = 7500.
    assert compute_stamp_duty(Decimal("400000"), "GB") == Decimal("7500.00")


def test_uk_sdlt_first_home_under_425k_zero() -> None:
    # First-time-buyer relief: 0 % up to £425k.
    assert compute_stamp_duty(Decimal("400000"), "GB", is_first_home=True) == Decimal("0.00")


def test_uk_sdlt_first_home_500k_partial_relief() -> None:
    # First-time-buyer: 0 % up to 425k + 5 % on 425k-500k = 3750.
    assert compute_stamp_duty(Decimal("500000"), "GB", is_first_home=True) == Decimal("3750.00")


def test_uk_sdlt_first_home_above_625k_falls_back_to_standard() -> None:
    # Above £625k the relief disappears entirely.
    # 0 (250k) + 5% × 675k (33750) + 10% × 75k (7500) = 41250.
    assert compute_stamp_duty(Decimal("1000000"), "GB", is_first_home=True) == compute_stamp_duty(
        Decimal("1000000"), "GB"
    )


def test_uk_sdlt_additional_property_3pct_surcharge() -> None:
    # Standard 400k = 7500; +3 % × 400k = 12000 → 19500.
    assert compute_stamp_duty(Decimal("400000"), "GB", is_additional_property=True) == Decimal("19500.00")


def test_uk_sdlt_top_band_above_1_5m() -> None:
    # Bands: 0 (250k) + 33750 (5% × 675k) + 57500 (10% × 575k)
    #       + 12000 (12% × 100k) = 103250.
    assert compute_stamp_duty(Decimal("1600000"), "GB") == Decimal("103250.00")


def test_uk_sdlt_zero_at_or_below_250k() -> None:
    assert compute_stamp_duty(Decimal("250000"), "GB") == Decimal("0.00")
    assert compute_stamp_duty(Decimal("100000"), "GB") == Decimal("0.00")


# ── 2. DE Grunderwerbsteuer — state-specific ────────────────────────────


def test_de_grunderwerbsteuer_bw_5pct() -> None:
    # Baden-Württemberg = 5 %.
    assert compute_stamp_duty(Decimal("500000"), "DE", region_subcode="BW") == Decimal("25000.00")


def test_de_grunderwerbsteuer_by_lowest() -> None:
    # Bayern = 3.5 % — lowest in DE.
    assert compute_stamp_duty(Decimal("500000"), "DE", region_subcode="BY") == Decimal("17500.00")


def test_de_grunderwerbsteuer_nw_6_5pct() -> None:
    # NRW = 6.5 % — common DE state.
    assert compute_stamp_duty(Decimal("500000"), "DE", region_subcode="NW") == Decimal("32500.00")


def test_de_missing_state_raises() -> None:
    with pytest.raises(MissingRegionSubcodeError) as exc:
        compute_stamp_duty(Decimal("500000"), "DE")
    assert exc.value.jurisdiction == "DE"
    assert "BE" in exc.value.supported  # Berlin must be listed.


def test_de_unknown_state_raises() -> None:
    with pytest.raises(MissingRegionSubcodeError):
        compute_stamp_duty(Decimal("500000"), "DE", region_subcode="XX")


# ── 3. UAE — transfer fee + zero-rated VAT ──────────────────────────────


def test_ae_dubai_transfer_fee_4pct() -> None:
    assert compute_transfer_fee(Decimal("1000000"), "AE", emirate="dubai") == Decimal("40000.00")


def test_ae_abu_dhabi_transfer_fee_2pct() -> None:
    assert compute_transfer_fee(Decimal("1000000"), "AE", emirate="abu_dhabi") == Decimal("20000.00")


def test_ae_first_residential_sale_zero_rated_vat() -> None:
    # Zero-rated VAT class returns 0 even on a 5M purchase.
    assert compute_vat(Decimal("5000000"), "AE", rate_class="zero_rated") == Decimal("0.00")


def test_ae_standard_vat_5pct() -> None:
    assert compute_vat(Decimal("1000000"), "AE") == Decimal("50000.00")


def test_ae_unknown_emirate_raises() -> None:
    with pytest.raises(MissingRegionSubcodeError):
        compute_transfer_fee(Decimal("1000000"), "AE", emirate="atlantis")


# ── 4. IN — affordable vs premium vs commercial GST ─────────────────────


def test_in_affordable_gst_1pct() -> None:
    # 50 Lakh × 1 % = 50,000.
    assert compute_vat(Decimal("5000000"), "IN", rate_class="affordable") == Decimal("50000.00")


def test_in_premium_gst_5pct() -> None:
    # 1 Cr × 5 % = 5,00,000.
    assert compute_vat(Decimal("10000000"), "IN", rate_class="premium") == Decimal("500000.00")


def test_in_commercial_gst_12pct() -> None:
    assert compute_vat(Decimal("10000000"), "IN", rate_class="commercial") == Decimal("1200000.00")


def test_in_state_stamp_duty_maharashtra_6pct() -> None:
    assert compute_stamp_duty(Decimal("10000000"), "IN", region_subcode="MH") == Decimal("600000.00")


def test_in_state_stamp_duty_karnataka_5pct() -> None:
    assert compute_stamp_duty(Decimal("10000000"), "IN", region_subcode="KA") == Decimal("500000.00")


def test_in_registration_fee_1pct() -> None:
    assert compute_registration_fee(Decimal("10000000"), "IN") == Decimal("100000.00")


# ── 5. RU — escrow flag + flat state duty ───────────────────────────────


def test_ru_state_duty_flat_2000_rub() -> None:
    # Stamp_duty path falls through to ``state_duty``.
    assert compute_stamp_duty(Decimal("10000000"), "RU") == Decimal("2000.00")


def test_ru_escrow_flag_exposed_in_metadata() -> None:
    from app.modules.property_dev.tax_engine import jurisdiction_metadata

    meta = jurisdiction_metadata("RU")
    assert meta.get("escrow_required") is True


def test_ru_vat_standard_20pct() -> None:
    assert compute_vat(Decimal("1000000"), "RU") == Decimal("200000.00")


# ── 6. SG — BSD progressive bands + ABSD ────────────────────────────────


def test_sg_bsd_2m_progressive() -> None:
    # 1%×180k (1800) + 2%×180k (3600) + 3%×640k (19200)
    # + 4%×500k (20000) + 5%×500k (25000) = 69,600.
    assert compute_stamp_duty(Decimal("2000000"), "SG") == Decimal("69600.00")


def test_sg_bsd_180k_first_band_only() -> None:
    assert compute_stamp_duty(Decimal("180000"), "SG") == Decimal("1800.00")


def test_sg_absd_foreign_buyer_60pct() -> None:
    assert compute_absd(Decimal("1000000"), "SG", buyer_profile="foreigner") == Decimal("600000.00")


def test_sg_absd_sc_second_20pct() -> None:
    assert compute_absd(Decimal("1000000"), "SG", buyer_profile="sc_second") == Decimal("200000.00")


def test_sg_absd_sc_first_zero() -> None:
    assert compute_absd(Decimal("1000000"), "SG", buyer_profile="sc_first") == Decimal("0.00")


def test_sg_absd_unknown_profile_raises() -> None:
    with pytest.raises(UnknownRateClassError):
        compute_absd(Decimal("1000000"), "SG", buyer_profile="alien")


# ── 7. Late interest ────────────────────────────────────────────────────


def test_uk_late_interest_30d_100k_at_7_5_pct() -> None:
    # 100,000 × 0.075 × 30/365 = 616.4383... → 616.44.
    assert compute_late_interest(Decimal("100000"), "GB", days_overdue=30) == Decimal("616.44")


def test_de_late_interest_30d_100k_at_6_12pct() -> None:
    # 100,000 × 0.0612 × 30/365 = 503.0136... → 503.01.
    assert compute_late_interest(Decimal("100000"), "DE", days_overdue=30) == Decimal("503.01")


def test_late_interest_zero_when_not_overdue() -> None:
    assert compute_late_interest(Decimal("100000"), "GB", days_overdue=0) == Decimal("0.00")
    assert compute_late_interest(Decimal("100000"), "GB", days_overdue=-5) == Decimal("0.00")


def test_late_interest_from_dates() -> None:
    # Same answer via due_date + paid_date as via days_overdue.
    via_days = compute_late_interest(Decimal("50000"), "DE", days_overdue=60)
    via_dates = compute_late_interest(
        Decimal("50000"),
        "DE",
        due_date=date(2026, 1, 1),
        paid_date=date(2026, 3, 2),
    )
    assert via_days == via_dates


# ── 8. Rate-effective-date behaviour ────────────────────────────────────


def test_vat_before_the_earliest_band_refuses_rather_than_returning_zero() -> None:
    """A date the table cannot price is refused, not answered with zero.

    This test once asserted the opposite, with the comment "should return 0
    (no band yet in force)", and its date was 2010-12-31 because GB standard
    VAT then held a single band starting 2011-01-04. Both of those have moved
    on: zero was never a lenient answer but a wrong one, and 2010-12-31 is now
    a date the table can price, at the 17.5 per cent actually in force that
    day. What is left here is the case the table still cannot answer, a
    contract predating the earliest band it holds, and the refusal has to
    survive the arrival of the history rather than be dissolved by it.
    """
    with pytest.raises(RateNotInForceError) as exc:
        compute_vat(Decimal("100000"), "GB", effective_on=date(1990, 1, 1))
    # The caller has to be able to act on this: either ask about a date the
    # table can speak for, or extend the history in the YAML. Both need dates.
    assert exc.value.jurisdiction == "GB"
    assert exc.value.rate_class == "standard"
    assert exc.value.effective_on == date(1990, 1, 1)
    # The earliest date the class can speak for, which is what makes the
    # refusal actionable. Not the start of any band that was being considered:
    # when nothing is in force there is no such band.
    assert exc.value.effective_from == date(1991, 4, 1)


def test_a_genuinely_zero_rated_supply_still_returns_zero_and_does_not_raise() -> None:
    """The control in the other direction: not everything may raise.

    Without this, the change above could be "passed" by refusing every zero,
    which would break every zero-rated jurisdiction in the table and still
    show a green suite for the case that motivated the work.
    """
    assert compute_vat(Decimal("5000000"), "AE", rate_class="zero_rated") == Decimal("0.00")
    assert net_from_gross(Decimal("1000.00"), "AE", rate_class="zero_rated") == Decimal("1000.00")


# ── 8b. The class axis: no block is not the same as an unknown class ────

# Jurisdictions whose stamp duty needs a subcode. Supplied so that nothing can
# refuse on an unrelated axis before the call under test is reached; the first
# version of this measurement used US without one and read MissingRegionSubcodeError
# from compute_stamp_duty as though it were the answer.
_SUBCODE = {"US": "TX", "IN": "MH", "DE": "BE", "AU": "NSW"}


def _quote_outcome(jurisdiction: str, rate_class: str, price_field: str) -> tuple[str, object]:
    """What the summariser does, in a form two call shapes can be compared by.

    The second slot carries the VAT provenance source on a successful quote,
    which puts that field inside this invariant rather than beside it. Step 1
    and step 2 of the summariser both catch NoVatBlockError on a ``total_value``
    contract, so a provenance set in the wrong place is exactly the defect this
    test already exists for, told about a newer field. Comparing only
    quoted-against-raised would not have seen it.
    """
    try:
        quote = compute_total_taxes_for_contract(
            {price_field: Decimal("100000"), "currency": "USD"},
            jurisdiction,
            vat_rate_class=rate_class,
            region_subcode=_SUBCODE.get(jurisdiction),
        )
        return ("quoted", quote["vat_provenance"].source)
    except TaxEngineError as exc:
        return ("raised", type(exc).__name__)


def test_no_vat_block_is_a_different_event_from_an_unknown_rate_class() -> None:
    """Two situations that raised the same error and meant different things.

    US has no vat or gst block at all. RU has one holding exempt and standard,
    and the caller asked for reduced. The first is a fact about what the table
    holds, the second is a caller naming something that does not exist.
    """
    with pytest.raises(NoVatBlockError) as no_block:
        compute_vat(Decimal("100000"), "US")
    assert no_block.value.jurisdiction == "US"

    with pytest.raises(UnknownRateClassError):
        compute_vat(Decimal("100000"), "RU", rate_class="reduced")

    # The split has to be real rather than nominal. If either were a subclass of
    # the other, every existing `except` on the parent would still swallow the
    # child and nothing about the old behaviour would have changed.
    assert not issubclass(NoVatBlockError, UnknownRateClassError)
    assert not issubclass(UnknownRateClassError, NoVatBlockError)


@pytest.mark.parametrize(
    ("jurisdiction", "rate_class", "expected"),
    [
        ("US", "standard", "quoted"),
        ("BR", "standard", "quoted"),
        ("RU", "reduced", "raised"),
        ("GB", "standard", "quoted"),
    ],
    ids=["us-no-block", "br-no-block", "ru-unknown-class", "gb-control"],
)
def test_a_quote_answers_alike_whichever_price_field_carries_it(
    jurisdiction: str, rate_class: str, expected: str
) -> None:
    """The same contract must not get two different answers by field name.

    ``net`` and ``total_value`` are two ways of stating the same contract's
    price. Before this, the summariser guarded its compute_vat call and left its
    net_from_gross call bare, so a jurisdiction with no rate class answered 200
    with a silent zero through one field and 422 through the other. The
    difference was not in the question.
    """
    by_net = _quote_outcome(jurisdiction, rate_class, "net")
    by_gross = _quote_outcome(jurisdiction, rate_class, "total_value")

    assert by_net == by_gross, f"{jurisdiction}/{rate_class} answers {by_net} by net and {by_gross} by total_value"
    # Asserted against the expected kind as well, because equality alone is
    # satisfied by refusing everything or by quoting everything, and each of
    # those breaks one of the four rows here while leaving the other three.
    assert by_net[0] == expected, f"{jurisdiction}/{rate_class} was expected to be {expected}, got {by_net}"


def _outcome(call: Callable[[], Decimal]) -> tuple[str, object]:
    """What a call did, in a form two calls can be compared by."""
    try:
        return ("returned", call())
    except TaxEngineError as exc:
        return ("raised", type(exc).__name__)


@pytest.mark.parametrize(
    ("fn", "zero_rated", "no_rate_yet"),
    [
        (
            compute_vat,
            lambda f: f(Decimal("100000"), "AE", rate_class="zero_rated"),
            lambda f: f(Decimal("100000"), "GB", effective_on=date(1990, 1, 1)),
        ),
        (
            net_from_gross,
            lambda f: f(Decimal("100000"), "AE", rate_class="zero_rated"),
            lambda f: f(Decimal("100000"), "GB", effective_on=date(1990, 1, 1)),
        ),
    ],
    ids=["compute_vat", "net_from_gross"],
)
def test_a_zero_rated_supply_and_an_unpriceable_date_cannot_come_back_alike(
    fn: Callable[..., Decimal],
    zero_rated: Callable[[Callable[..., Decimal]], Decimal],
    no_rate_yet: Callable[[Callable[..., Decimal]], Decimal],
) -> None:
    """The invariant, asserted on the pair rather than on a number.

    A test that pins a particular return value is blind to the thing that was
    wrong here, because the wrong answer was a perfectly ordinary zero. What
    must hold is that these two branches cannot produce the same output: they
    were once identical down to as_tuple(), so no caller could separate a
    tax-free sale from one the engine could not price.

    Asserted in both directions, because "they differ" is satisfied by any
    change that breaks one of them, including making everything raise.
    """
    a = _outcome(lambda: zero_rated(fn))
    b = _outcome(lambda: no_rate_yet(fn))

    assert a[0] == "returned", f"a zero-rated supply must still be priced, got {a}"
    assert b[0] == "raised", f"a date with no rate in force must be refused, got {b}"
    assert a != b


def test_vat_effective_from_on_or_after_uses_current_rate() -> None:
    # On the effective date itself the rate is active.
    assert compute_vat(
        Decimal("100000"),
        "GB",
        effective_on=date(2011, 1, 4),
    ) == Decimal("20000.00")
    assert compute_vat(
        Decimal("100000"),
        "GB",
        effective_on=date(2025, 6, 1),
    ) == Decimal("20000.00")


# ── 8c. Rate histories - the band in force on the contract's date ───────

# GB standard VAT, one row per era of the shipped history. Sources: 17.5 %
# from 1 April 1991 (Budget 1991), 15 % from 1 December 2008 (the thirteen-
# month Pre-Budget Report reduction), 17.5 % again from 1 January 2010, 20 %
# from 4 January 2011. Each pair is a day inside an era and the day the next
# era opens, so the table is read both away from the boundaries and on them.
_GB_ERAS = [
    (date(1991, 4, 1), "17500.00"),
    (date(2008, 11, 30), "17500.00"),
    (date(2008, 12, 1), "15000.00"),
    (date(2009, 6, 1), "15000.00"),
    (date(2009, 12, 31), "15000.00"),
    (date(2010, 1, 1), "17500.00"),
    (date(2011, 1, 3), "17500.00"),
    (date(2011, 1, 4), "20000.00"),
    (date(2026, 1, 1), "20000.00"),
]

# DE Regelsteuersatz: 16 % from 1 April 1998, 19 % from 1 January 2007, the
# Corona reduction back to 16 % for the second half of 2020, and 19 % again
# when it expired on 31 December 2020.
_DE_ERAS = [
    (date(1998, 4, 1), "16000.00"),
    (date(2006, 12, 31), "16000.00"),
    (date(2007, 1, 1), "19000.00"),
    (date(2020, 6, 30), "19000.00"),
    (date(2020, 7, 1), "16000.00"),
    (date(2020, 9, 1), "16000.00"),
    (date(2020, 12, 31), "16000.00"),
    (date(2021, 1, 1), "19000.00"),
]


@pytest.mark.parametrize(("signed_on", "expected"), _GB_ERAS, ids=[str(day) for day, _ in _GB_ERAS])
def test_a_gb_contract_is_quoted_at_the_rate_in_force_when_it_was_signed(signed_on: date, expected: str) -> None:
    """Every era of the GB standard rate, on a 100k net contract.

    The row that matters most is 2009-06-01. A contract signed that day sits
    inside the temporary 15 per cent reduction, so quoting it at 17.5 per cent
    would be wrong by two and a half points while looking entirely plausible,
    and quoting it at today's 20 per cent would be wrong by five. Before the
    history existed the same contract could not be quoted at all.
    """
    assert compute_vat(Decimal("100000"), "GB", effective_on=signed_on) == Decimal(expected)


@pytest.mark.parametrize(("signed_on", "expected"), _DE_ERAS, ids=[str(day) for day, _ in _DE_ERAS])
def test_a_de_contract_is_quoted_at_the_rate_in_force_when_it_was_signed(signed_on: date, expected: str) -> None:
    """Every era of the DE Regelsteuersatz, on a 100k net contract.

    The 2020 half-year at 16 per cent is the window people forget, and it is
    the only one here bounded on both sides by the same rate, so a resolver
    that took the newest band regardless, or the oldest band before the date
    and stopped, would still pass the rows either side of it.
    """
    assert compute_vat(Decimal("100000"), "DE", effective_on=signed_on) == Decimal(expected)


@pytest.mark.parametrize(
    ("jurisdiction", "opens_on", "on_the_day", "the_day_before"),
    [
        ("GB", date(2011, 1, 4), "20000.00", "17500.00"),
        ("GB", date(2008, 12, 1), "15000.00", "17500.00"),
        ("DE", date(2021, 1, 1), "19000.00", "16000.00"),
        ("DE", date(2020, 7, 1), "16000.00", "19000.00"),
    ],
    ids=["gb-2011-rise", "gb-2008-cut", "de-2021-restore", "de-2020-cut"],
)
def test_a_date_on_a_bands_first_day_takes_that_band_and_not_the_one_before(
    jurisdiction: str, opens_on: date, on_the_day: str, the_day_before: str
) -> None:
    """``effective_from`` is inclusive, and the off-by-one is asserted in both directions.

    Asserting only the new rate on the day would pass for a resolver that took
    the newest band for every date; asserting only the previous day would pass
    for one that never advanced. The pair is what pins the boundary, and each
    row here is a real day on which a real contract changed price.
    """
    assert compute_vat(Decimal("100000"), jurisdiction, effective_on=opens_on) == Decimal(on_the_day)
    assert compute_vat(Decimal("100000"), jurisdiction, effective_on=opens_on - timedelta(days=1)) == Decimal(
        the_day_before
    )


def test_a_date_before_the_earliest_band_is_still_refused_for_both_histories() -> None:
    """Adding a history moves the refusal back; it does not remove it.

    Both jurisdictions, because the whole point of the error is that it names
    the earliest date the table can speak for, and a single-jurisdiction test
    would pass for an implementation that hard-coded one.
    """
    for jurisdiction, earliest in (("GB", date(1991, 4, 1)), ("DE", date(1998, 4, 1))):
        with pytest.raises(RateNotInForceError) as exc:
            compute_vat(Decimal("100000"), jurisdiction, effective_on=earliest - timedelta(days=1))
        assert exc.value.effective_from == earliest, f"{jurisdiction} names the wrong earliest date"
        assert earliest.isoformat() in str(exc.value), "the message has to carry the date the caller must act on"


def test_no_date_at_all_still_means_the_current_rate() -> None:
    """The default, which a history must not quietly change.

    ``effective_on=None`` is most callers, and a resolver that reached for the
    first band written rather than the newest would answer 17.5 per cent for
    every GB quote in the app while every dated test above stayed green.
    """
    assert compute_vat(Decimal("100000"), "GB") == Decimal("20000.00")
    assert compute_vat(Decimal("100000"), "DE") == Decimal("19000.00")


def test_net_from_gross_reads_the_same_history_as_compute_vat() -> None:
    """One contract, two price fields, one rate.

    The date rule used to be written out twice, once per function, which is
    two places for a history to land in and one of them to be forgotten. A
    2009 GB contract quoted inclusive of 15 per cent VAT splits back to its
    net; if this function still read only the newest band it would divide by
    1.20 and return 95833.33.
    """
    assert net_from_gross(Decimal("115000"), "GB", effective_on=date(2009, 6, 1)) == Decimal("100000.00")
    assert gross_from_net(Decimal("100000"), "GB", effective_on=date(2009, 6, 1)) == Decimal("115000.00")


@pytest.mark.parametrize(
    ("jurisdiction", "rate_class", "expected"),
    [
        ("GB", "reduced", "5000.00"),
        ("GB", "zero", "0.00"),
        ("DE", "reduced", "7000.00"),
        ("RU", "standard", "20000.00"),
        ("SG", "standard", "9000.00"),
        ("AE", "exempt", "0.00"),
    ],
    ids=["gb-reduced", "gb-zero", "de-reduced", "ru-standard", "sg-gst", "ae-exempt-no-rate-key"],
)
def test_a_single_mapping_rate_class_answers_exactly_as_it_did_before(
    jurisdiction: str, rate_class: str, expected: str
) -> None:
    """Most of the table is one undated mapping and none of it was migrated.

    Two properties at once, on a date far outside any history. The rate is
    unchanged, and the class still answers rather than refusing: an undated
    mapping is in force for every date, so GB ``reduced`` prices a 1900
    contract at 5 per cent. That is not an oversight to fix here. Only the
    standard rate of each of the two jurisdictions carries a history, so the
    refusal is scoped to a rate class and never to a jurisdiction, and this
    test is where that scope is written down.

    ``AE.exempt`` is in the list because it carries ``applies_to`` and no
    ``rate`` key at all, so it measures the "no rate means zero" default that
    the normaliser must not turn into a missing-key error.
    """
    assert compute_vat(Decimal("100000"), jurisdiction, rate_class=rate_class, effective_on=date(1900, 1, 1)) == (
        Decimal(expected)
    )


# ── 8d. Where a missing VAT block lives: in the law, or in this table ───


def _blockless_codes() -> list[str]:
    """Codes in the SHIPPED table that carry no VAT or GST block.

    Asks ``_has_vat_block`` rather than re-deriving the rule inline. An
    inline copy would be a second home for the one decision this section is
    about, and if the two ever disagreed this helper would quietly widen or
    narrow the population it tests while still passing.
    """
    table = tax_engine._load_table()
    return [
        code
        for code, jur in (table.get("jurisdictions") or {}).items()
        if isinstance(jur, dict) and not tax_engine._has_vat_block(jur)
    ]


def _well_formed() -> dict[str, Any]:
    """The smallest table that passes: one row with a block, one without."""
    return {
        "format_version": 1,
        "jurisdictions": {
            "GB": {"name": "United Kingdom", "vat": {"standard": {"rate": 0.2}}},
            "US": {"name": "United States", VAT_ABSENCE_KEY: VAT_ABSENT_BY_LAW},
        },
    }


@contextmanager
def _table_on_disk(tmp_path: Path, table: dict[str, Any]) -> Iterator[None]:
    """Point the engine at a synthetic table, and always put the real one back.

    These tests go through the loader rather than calling
    ``_validate_vat_absence`` directly, deliberately: a validator that nothing
    calls would satisfy every assertion below while the shipped table went
    unchecked. Testing the function alone would prove the rule exists, not
    that it runs.
    """
    path = tmp_path / "tax_rates.yaml"
    path.write_text(yaml.safe_dump(table), encoding="utf-8")
    original = tax_engine._TABLE_PATH
    tax_engine._TABLE_PATH = path
    try:
        yield
    finally:
        tax_engine._TABLE_PATH = original
        tax_engine.reload_tax_table()


def test_every_jurisdiction_without_a_vat_block_says_where_the_gap_lives() -> None:
    """The property, rather than the pair that satisfies it today.

    Asserting "US and BR" would be an exact-set detector: it goes red the day
    a third blockless jurisdiction is added, which teaches whoever added it to
    edit the test instead of reading it. This asks the question the field
    exists to answer, so a new row either satisfies it or is the bug.
    """
    codes = _blockless_codes()
    assert codes, "no blockless jurisdiction is left in the table, so this test now asserts nothing"
    for code in codes:
        assert vat_absence(code) in VAT_ABSENCE_VALUES


def test_the_two_markers_do_not_collapse_onto_one_answer() -> None:
    """The defect this field repairs, stated as an inequality.

    Each assertion names one jurisdiction rather than the whole set, so a
    third blockless row does not touch this test.
    """
    assert vat_absence("US") == VAT_ABSENT_BY_LAW
    assert vat_absence("BR") == VAT_ABSENT_NOT_MODELLED
    assert vat_absence("US") != vat_absence("BR")


def test_asking_why_a_block_is_missing_from_a_jurisdiction_that_has_one_raises() -> None:
    """GB has a VAT block, so the question does not apply to it."""
    with pytest.raises(TaxEngineError, match="has a VAT/GST block"):
        vat_absence("GB")


def test_a_well_formed_synthetic_table_still_loads(tmp_path: Path) -> None:
    """The control for the three refusals below.

    Without it, a loader that rejected every table would pass all three and
    the suite would report a working guard while nothing could load at all.
    """
    with _table_on_disk(tmp_path, _well_formed()):
        tax_engine.reload_tax_table()
        assert vat_absence("US") == VAT_ABSENT_BY_LAW


def test_a_blockless_jurisdiction_that_says_nothing_is_refused_at_load(tmp_path: Path) -> None:
    """The row somebody adds next year, which is what the guard is for."""
    table = _well_formed()
    del table["jurisdictions"]["US"][VAT_ABSENCE_KEY]
    with _table_on_disk(tmp_path, table), pytest.raises(TaxEngineError, match="does not say why"):
        tax_engine.reload_tax_table()


@pytest.mark.parametrize("value", ["partial", "mostly", "", "BY_LAW", True])
def test_a_marker_outside_the_permitted_pair_is_refused_at_load(tmp_path: Path, value: object) -> None:
    """``partial`` is the specific one to keep out, and it is first for a reason.

    It answers a different question from the one the field asks. The field
    asks where the gap lives, which has two answers; ``partial`` says how
    completely something is modelled, which is a degree, and no caller can
    derive a provenance from a degree. ``BY_LAW`` is here because a value that
    differs only in case is the near-miss a set membership test catches and a
    truthiness check does not.
    """
    table = _well_formed()
    table["jurisdictions"]["US"][VAT_ABSENCE_KEY] = value
    with _table_on_disk(tmp_path, table), pytest.raises(TaxEngineError, match="not one of"):
        tax_engine.reload_tax_table()


def test_a_jurisdiction_with_a_block_may_not_also_declare_the_key(tmp_path: Path) -> None:
    """The contradiction, in the family the provenance type refuses."""
    table = _well_formed()
    table["jurisdictions"]["GB"][VAT_ABSENCE_KEY] = VAT_ABSENT_BY_LAW
    with _table_on_disk(tmp_path, table), pytest.raises(TaxEngineError, match="also declares"):
        tax_engine.reload_tax_table()


def test_a_refused_table_does_not_replace_the_good_one_already_cached(tmp_path: Path) -> None:
    """The loader validates before it caches, and this is that claim under test.

    A guard that raised *after* assigning would be worse than no guard: it
    would record the refusal and then leave the refused table behind for the
    next caller to read as though it had passed. The refusal tests above all
    end at the exception and would not notice.

    BR is the probe because it is in the shipped table and not in the
    synthetic one, so a poisoned cache cannot answer for it at all.
    """
    table = _well_formed()
    del table["jurisdictions"]["US"][VAT_ABSENCE_KEY]
    with _table_on_disk(tmp_path, table):
        with pytest.raises(TaxEngineError):
            tax_engine.reload_tax_table()
        assert vat_absence("BR") == VAT_ABSENT_NOT_MODELLED


# ── 8e. A history the loader will not accept as one ─────────────────────

# A history that is valid, to be broken one way at a time below. Oldest first,
# two periods, both dated - the shape the shipped GB and DE rows use.
_HISTORY = [
    {"rate": 0.175, "effective_from": "1991-04-01"},
    {"rate": 0.20, "effective_from": "2011-01-04"},
]


def _table_with_history(history: object) -> dict[str, Any]:
    """The well-formed synthetic table, with GB standard replaced by ``history``."""
    table = _well_formed()
    table["jurisdictions"]["GB"]["vat"]["standard"] = history
    return table


def test_a_synthetic_history_loads_and_resolves(tmp_path: Path) -> None:
    """The control for the refusals below.

    Without it, a loader that rejected every list would pass all of them and
    the suite would report a working guard while no history could load at all.
    The assertion goes through ``compute_vat`` rather than stopping at the
    load, so the synthetic table is measured the way the shipped one is.
    """
    with _table_on_disk(tmp_path, _table_with_history(_HISTORY)):
        tax_engine.reload_tax_table()
        assert compute_vat(Decimal("100000"), "GB", effective_on=date(1995, 1, 1)) == Decimal("17500.00")
        assert compute_vat(Decimal("100000"), "GB", effective_on=date(2015, 1, 1)) == Decimal("20000.00")


def test_a_history_written_newest_first_is_refused_at_load(tmp_path: Path) -> None:
    """The convention is enforced rather than merely documented.

    Nothing computes a wrong number from a reversed history - the resolver
    picks by greatest date, not by position - which is exactly why this needs
    a test of its own. What a reversed list breaks is the person who scans it
    for the current rate and reads the last line, and only the loader is in a
    position to catch that.
    """
    with _table_on_disk(tmp_path, _table_with_history(list(reversed(_HISTORY)))):
        with pytest.raises(TaxEngineError, match="ascending date order"):
            tax_engine.reload_tax_table()


def test_two_periods_starting_the_same_day_are_refused_at_load(tmp_path: Path) -> None:
    """Ascending is strict, because a tie has no answer and would be resolved silently."""
    twice = [dict(_HISTORY[0]), {"rate": 0.20, "effective_from": _HISTORY[0]["effective_from"]}]
    with _table_on_disk(tmp_path, _table_with_history(twice)):
        with pytest.raises(TaxEngineError, match="ascending date order"):
            tax_engine.reload_tax_table()


def test_a_period_with_no_date_is_refused_at_load(tmp_path: Path) -> None:
    """A history whose earliest date is unknown cannot refuse anything honestly.

    An undated single mapping is in force for every date, which is fine and is
    what most of the table relies on. Inside a list it is different: it would
    make one period answer for all dates and leave the error with no earliest
    date to name.
    """
    undated = [{"rate": 0.175}, _HISTORY[1]]
    with _table_on_disk(tmp_path, _table_with_history(undated)):
        with pytest.raises(TaxEngineError, match="no readable effective_from"):
            tax_engine.reload_tax_table()


def test_an_empty_history_is_refused_at_load(tmp_path: Path) -> None:
    """A rate class that names itself and then says nothing."""
    with _table_on_disk(tmp_path, _table_with_history([])):
        with pytest.raises(TaxEngineError, match="is empty"):
            tax_engine.reload_tax_table()


def test_price_bands_are_not_mistaken_for_a_rate_history(tmp_path: Path) -> None:
    """The scope of the validator, measured rather than asserted in a comment.

    This table is full of lists of mappings on a completely different axis:
    ``stamp_duty.bands`` and its relatives carry a price ceiling and no date
    at all. A validator keyed on "a list of mappings" would refuse the shipped
    table on its first load and take the whole module down, so the check is
    scoped to the classes under ``vat`` and ``gst``. Stamp duty is computed
    here as well, because a refusal at load is not the only way to break it.
    """
    table = _well_formed()
    table["jurisdictions"]["GB"]["stamp_duty"] = {
        "bands": [
            {"up_to": 250000, "rate": 0.0},
            {"up_to": None, "rate": 0.05},
        ]
    }
    with _table_on_disk(tmp_path, table):
        tax_engine.reload_tax_table()
        assert compute_stamp_duty(Decimal("400000"), "GB") == Decimal("7500.00")


def test_a_single_undated_mapping_never_refuses_a_date(tmp_path: Path) -> None:
    """The shape most of the shipped table uses, asserted where it is unambiguous.

    Every class in the shipped table is now either a history or an undated
    mapping, so this property has no dated single-mapping row left to be
    confused with. A synthetic table is the honest place to pin it.
    """
    with _table_on_disk(tmp_path, _well_formed()):
        tax_engine.reload_tax_table()
        assert compute_vat(Decimal("100000"), "GB", effective_on=date(1066, 10, 14)) == Decimal("20000.00")


def test_a_single_mapping_that_does_carry_a_date_still_refuses_an_earlier_one(tmp_path: Path) -> None:
    """The path the shipped table no longer exercises, kept under test anyway.

    GB and DE standard were the only two dated single mappings and both are
    now histories, so this branch of the resolver has no coverage left from
    the shipped data at all. It is still live code and a jurisdiction added
    tomorrow may well use it: one band, dated, nothing before it.
    """
    dated = {"rate": 0.2, "effective_from": "2011-01-04"}
    with _table_on_disk(tmp_path, _table_with_history(dated)):
        tax_engine.reload_tax_table()
        assert compute_vat(Decimal("100000"), "GB", effective_on=date(2011, 1, 4)) == Decimal("20000.00")
        with pytest.raises(RateNotInForceError) as exc:
            compute_vat(Decimal("100000"), "GB", effective_on=date(2011, 1, 3))
        assert exc.value.effective_from == date(2011, 1, 4)


# ── 8f. The quote says how its VAT figure was arrived at ───────────────


def _quote(jurisdiction: str, rate_class: str = "standard") -> dict[str, Any]:
    """A quote for a 100k contract, with whatever else that jurisdiction needs."""
    return compute_total_taxes_for_contract(
        {"net": Decimal("100000"), "currency": "USD"},
        jurisdiction,
        vat_rate_class=rate_class,
        region_subcode=_SUBCODE.get(jurisdiction),
        emirate="dubai" if jurisdiction == "AE" else None,
    )


def test_three_quotes_with_the_same_vat_amount_do_not_have_the_same_provenance() -> None:
    """The defect this field exists to close, stated as a test.

    A zero-rated first sale in the UAE, a US quote, and a Brazilian quote all
    put the same bytes in ``vat``. One is a rate of zero that a real row
    declared, one is a jurisdiction that levies no VAT at all, and one is a
    jurisdiction whose indirect taxes this table does not carry. Only the first
    two are safe to add to a total.

    Both halves are asserted. The amounts being equal is what makes the field
    necessary, and without that assertion a reader cannot tell whether the
    sources differ because the situations differ or because the amounts do.
    """
    ae = _quote("AE", rate_class="zero_rated")
    us = _quote("US")
    br = _quote("BR")

    # The amount cannot discriminate. That is the whole problem.
    assert ae["vat"] == us["vat"] == br["vat"] == Decimal("0.00")

    sources = [q["vat_provenance"].source for q in (ae, us, br)]
    # Pairwise distinct, asserted as a set rather than three equality checks:
    # checking each one against its expected value individually stays green if
    # two of them later collapse onto a single source, which is the regression
    # this test is here to catch.
    assert len(set(sources)) == 3, f"three different situations, sources {sources}"
    assert sources == [Source.DECLARED, Source.FALLBACK, Source.UNAVAILABLE]


def test_the_two_absences_differ_in_whether_the_figure_may_be_used() -> None:
    """``usable`` is the question a caller summing a total actually has.

    The US zero is an answer: no VAT is levied, so nothing is missing from a
    total that adds it. The Brazilian zero is the absence of an answer, and a
    total that adds it understates itself. ``answered`` is False for both,
    correctly, because neither found a row of its own; that is why it is the
    wrong field to sum on and ``usable`` is the right one.
    """
    us = _quote("US")["vat_provenance"]
    br = _quote("BR")["vat_provenance"]

    assert us.usable is True
    assert br.usable is False
    assert us.answered is False
    assert br.answered is False


def test_the_by_law_token_names_what_answered_and_the_other_names_nothing() -> None:
    """A stand-in token that would also be true of Brazil would be too weak.

    ``app.core.provenance`` requires the token to name the thing that answered
    rather than the slot it fills, and rejects one that would be equally true
    of a different stand-in. ``NO_VAT_IN_LAW`` is false of Brazil, which levies
    indirect taxes that this table simply does not carry, so it discriminates.
    A token describing the table rather than the law, which is what the older
    comment in the summariser said, would have covered both rows.

    Brazil carries no token at all, and that is the type's doing rather than a
    style choice: an unavailable with a ``used`` value raises, because nothing
    stood in.
    """
    us_quote = _quote("US")
    us = us_quote["vat_provenance"]
    br = _quote("BR")["vat_provenance"]

    assert us.axis == br.axis == VAT_AXIS
    assert us.used == VAT_STANDIN_NO_VAT_IN_LAW
    assert br.used == ""
    # The requested side is the jurisdiction as the quote itself reports it, so
    # the two fields of one response cannot disagree about what was asked.
    # Both read off the SAME quote. Two separate calls would stay green even if
    # these two fields were computed from different expressions, which is the
    # only disagreement worth pinning here.
    assert us.requested == us_quote["jurisdiction"] == "US"


def test_a_jurisdiction_with_a_rate_declares_it() -> None:
    """The control. Without it the three tests above are satisfied by a
    function that never returns DECLARED at all."""
    gb = _quote("GB")["vat_provenance"]

    assert gb.source is Source.DECLARED
    assert gb.answered is True
    assert gb.requested == gb.used == "GB"


def test_the_provenance_survives_the_response_model_and_its_json() -> None:
    """The engine emitting it and the endpoint dropping it would both be green.

    The router builds this with ``model_validate`` over the engine's dict, so
    the field travels only as long as the schema declares it. Asserted through
    the JSON rather than the model, because that is what a client receives.
    """
    payload = ContractTaxQuote.model_validate(_quote("BR")).model_dump(mode="json")

    assert payload["vat_provenance"]["source"] == Source.UNAVAILABLE.value
    assert payload["vat_provenance"]["axis"] == VAT_AXIS
    # Two decimal places, not "0": the money serialiser keeps the quantum, so
    # the placeholder is indistinguishable on the wire from a real zero rate.
    # That is the point of the field beside it.
    assert payload["vat"] == "0.00"
    # The ContractTaxQuote docstring makes this claim about all three zero
    # paths, so all three are measured. Pinning only the one this test happens
    # to build would leave the other two thirds of the sentence unchecked.
    alike = {
        code: ContractTaxQuote.model_validate(quote).model_dump(mode="json")["vat"]
        for code, quote in (("AE", _quote("AE", rate_class="zero_rated")), ("US", _quote("US")))
    }
    assert alike == {"AE": "0.00", "US": "0.00"}
    # answered and usable are properties, so they are deliberately NOT on the
    # wire; a client derives them from source. Pinned because adding them later
    # would be an API change made by accident.
    assert "usable" not in payload["vat_provenance"]
    assert "answered" not in payload["vat_provenance"]


def test_grand_total_still_adds_a_vat_it_has_just_called_unusable() -> None:
    """A known cost, pinned so that changing it is a decision rather than a drift.

    Marking the axis unusable does not stop the arithmetic, and deliberately so:
    ``usable`` labels the answer, and moving an amount because a label makes it
    look wrong would be choosing behaviour for a consequence. So a Brazilian
    grand total is net plus the other taxes plus a placeholder zero.

    That was equally true before this field existed; the field is what makes it
    visible. Fixing it means deciding what a quote for an unmodelled
    jurisdiction should say, which is a breakdown question and belongs with the
    zero-rated line work, not here.
    """
    br = _quote("BR")

    assert br["vat_provenance"].usable is False
    assert br["grand_total"] == br["net"] + br["vat"] + br["subtotal_taxes"]
    assert br["vat"] == Decimal("0.00")


# ── 9. Unsupported jurisdiction handling ────────────────────────────────


def test_unsupported_jurisdiction_raises_with_supported_list() -> None:
    with pytest.raises(UnsupportedJurisdictionError) as exc:
        compute_vat(Decimal("100"), "XX")
    assert exc.value.jurisdiction == "XX"
    # The error must enumerate supported codes so the UI can guide the user.
    assert "GB" in exc.value.supported
    assert "DE" in exc.value.supported


def test_unsupported_jurisdiction_lowercase_is_normalised() -> None:
    # Mixed case must still be looked up.
    assert compute_vat(Decimal("100"), "gb") == Decimal("20.00")


def test_unknown_rate_class_raises() -> None:
    with pytest.raises(UnknownRateClassError):
        compute_vat(Decimal("100"), "RU", rate_class="reduced")


# ── 10. Gross/net round-trip ────────────────────────────────────────────


def test_gross_from_net_roundtrip_with_uk_20pct() -> None:
    net = Decimal("1000.00")
    gross = gross_from_net(net, "GB")
    assert gross == Decimal("1200.00")


def test_net_from_gross_roundtrip_with_de_19pct() -> None:
    gross = Decimal("1190.00")
    net = net_from_gross(gross, "DE")
    assert net == Decimal("1000.00")


def test_net_from_gross_zero_rate_class_returns_gross() -> None:
    # Zero-rated → no VAT subtracted.
    assert net_from_gross(Decimal("1000.00"), "AE", rate_class="zero_rated") == Decimal("1000.00")


# ── 11. AU state-specific bands ─────────────────────────────────────────


def test_au_nsw_stamp_duty_300k() -> None:
    # NSW bands: 1.25%×17k (212.5) + 1.5%×19k (285) + 1.75%×61k (1067.5)
    # + 3.5%×203k (7105) = 8670.
    result = compute_stamp_duty(Decimal("300000"), "AU", region_subcode="NSW")
    assert result == Decimal("8670.00")


def test_au_vic_stamp_duty_500k() -> None:
    # VIC bands: 1.4%×25k (350) + 2.4%×105k (2520) + 5.5%×370k (20350) = 23220.
    result = compute_stamp_duty(Decimal("500000"), "AU", region_subcode="VIC")
    assert result == Decimal("23220.00")


def test_au_missing_state_raises() -> None:
    with pytest.raises(MissingRegionSubcodeError):
        compute_stamp_duty(Decimal("500000"), "AU")


# ── 12. US state-specific transfer tax ──────────────────────────────────


def test_us_ny_transfer_tax_0_4pct() -> None:
    assert compute_stamp_duty(Decimal("1000000"), "US", region_subcode="NY") == Decimal("4000.00")


def test_us_texas_no_transfer_tax() -> None:
    assert compute_stamp_duty(Decimal("1000000"), "US", region_subcode="TX") == Decimal("0.00")


# ── 13. compute_total_taxes_for_contract — high-level integration ───────


def test_total_taxes_uk_first_time_buyer() -> None:
    quote = compute_total_taxes_for_contract(
        {"net": Decimal("400000"), "currency": "GBP"},
        "GB",
        is_first_home=True,
    )
    # No SDLT under £425k for first-time buyer.
    assert quote["stamp_duty"] == Decimal("0.00")
    # GB VAT 20 % on residential new-build is technically zero-rated
    # but our default 'standard' class returns 20 %; verify roll-up
    # math regardless of policy.
    assert quote["vat"] == Decimal("80000.00")
    # Grand total = 400k + 80k VAT + 0 SDLT.
    assert quote["grand_total"] == Decimal("480000.00")
    # Breakdown must include the net line.
    assert any(line["line"] == "Net price" for line in quote["breakdown"])


def test_total_taxes_de_berlin_full_chain() -> None:
    quote = compute_total_taxes_for_contract(
        {"net": Decimal("500000"), "currency": "EUR"},
        "DE",
        region_subcode="BE",
    )
    assert quote["vat"] == Decimal("95000.00")  # 19 % VAT
    # Berlin Grunderwerbsteuer = 6 % on net price (500k × 6%).
    assert quote["stamp_duty"] == Decimal("30000.00")
    # 7500 notary (1.5 %) — registration fallback.
    assert quote["registration_fee"] == Decimal("7500.00")
    # Grand total = 500k + 95k + 30k + 7.5k = 632,500.
    assert quote["grand_total"] == Decimal("632500.00")


def test_total_taxes_unsupported_jurisdiction_raises() -> None:
    with pytest.raises(UnsupportedJurisdictionError):
        compute_total_taxes_for_contract(
            {"net": Decimal("100000"), "currency": "USD"},
            "ZZ",
        )


def test_total_taxes_with_overdue_instalments_accrues_late_interest() -> None:
    overdue = [
        {
            "sequence": 1,
            "amount": "100000",
            "days_overdue": 60,
        }
    ]
    quote = compute_total_taxes_for_contract(
        {"net": Decimal("500000"), "currency": "EUR"},
        "DE",
        region_subcode="BE",
        overdue_instalments=overdue,
    )
    # 100k × 6.12 % × 60/365 = 1006.0274 → 1006.03.
    assert quote["late_interest"] == Decimal("1006.03")
    assert any("Late interest" in line["line"] for line in quote["breakdown"])


def test_total_taxes_currency_passthrough() -> None:
    quote = compute_total_taxes_for_contract(
        {"net": Decimal("100000"), "currency": "AED"},
        "AE",
        vat_rate_class="standard",
        emirate="dubai",
    )
    assert quote["currency"] == "AED"
    assert quote["jurisdiction"] == "AE"
    assert quote["transfer_fee"] == Decimal("4000.00")
