# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
"""Custom KPI definitions - issue #441.

The module shipped 35 built-in KPIs and no way to add a 36th without
shipping Python. These tests pin the way in that was added for the people
who cannot ship Python: a declarative spec over a whitelisted entity,
checked when the definition is created rather than when it is computed.

The three specs exercised here are the ones the reporter asked for, an
estimating SME with a per-position confidence score:

* amount-weighted bid confidence, ``sum(confidence * amount) / sum(amount)``
* the largest position of each bid
* how many positions are scored below a confidence threshold

Test isolation: a transaction-isolated PostgreSQL session on the shared
schema-loaded ``oe_test_unit`` database, rolled back on teardown.

Run:
    cd backend
    python -m pytest tests/unit/test_bi_dashboards_custom_kpi.py -v --tb=short
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.bi_dashboards import kpi_spec, kpis
from app.modules.bi_dashboards.models import DashboardWidget, KPIDefinition
from app.modules.bi_dashboards.schemas import KPIDefinitionCreate
from app.modules.bi_dashboards.service import (
    BIDashboardsService,
    CustomKPICodeInUse,
    CustomKPIInUse,
    CustomKPIIsSystem,
)
from tests._pg import transactional_session

OWNER_ID = uuid.uuid4()


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """Transaction-isolated PostgreSQL session (rolled back on teardown)."""
    async with transactional_session() as s:
        from app.modules.users.models import User

        s.add(
            User(
                id=OWNER_ID,
                email=f"kpi-{uuid.uuid4().hex[:6]}@test.io",
                hashed_password="x",
                full_name="O",
            ),
        )
        await s.flush()
        yield s


async def _seed_two_bids(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """One project, two bids, four priced positions.

    Bid A: 100 x 10 at confidence 0.9, 200 x 20 at confidence 0.4
        -> weighted confidence (900 + 1600) / 5000 = 0.5, top amount 4000
    Bid B: 10 x 100 at confidence 0.2, 1 x 3000 with confidence unset
        -> weighted confidence 200 / 1000 = 0.2, top amount 3000

    The unscored position in bid B is the interesting one: it must be left
    out of the average rather than read as a zero score, and it must not
    answer to "confidence below 0.5" either.

    Returns ``(project_id, bid_a_id, bid_b_id)``.
    """
    from app.modules.boq.models import BOQ, Position
    from app.modules.projects.models import Project

    project_id = uuid.uuid4()
    session.add(
        Project(
            id=project_id,
            name="Custom KPI project",
            owner_id=OWNER_ID,
            currency="EUR",
        ),
    )
    await session.flush()

    bid_a = BOQ(id=uuid.uuid4(), project_id=project_id, name="Bid A")
    bid_b = BOQ(id=uuid.uuid4(), project_id=project_id, name="Bid B")
    session.add_all([bid_a, bid_b])
    await session.flush()

    session.add_all(
        [
            Position(
                id=uuid.uuid4(),
                boq_id=bid_a.id,
                ordinal="A.001",
                description="Excavation",
                unit="m3",
                quantity="100",
                unit_rate="10",
                total="1000",
                confidence="0.9",
                sort_order=1,
            ),
            Position(
                id=uuid.uuid4(),
                boq_id=bid_a.id,
                ordinal="A.002",
                description="Concrete",
                unit="m3",
                quantity="200",
                unit_rate="20",
                total="4000",
                confidence="0.4",
                sort_order=2,
            ),
            Position(
                id=uuid.uuid4(),
                boq_id=bid_b.id,
                ordinal="B.001",
                description="Formwork",
                unit="m2",
                quantity="10",
                unit_rate="100",
                total="1000",
                confidence="0.2",
                sort_order=1,
            ),
            Position(
                id=uuid.uuid4(),
                boq_id=bid_b.id,
                ordinal="B.002",
                description="Provisional sum",
                unit="item",
                quantity="1",
                unit_rate="3000",
                total="3000",
                confidence=None,
                sort_order=2,
            ),
        ],
    )
    await session.flush()
    return project_id, bid_a.id, bid_b.id


def _weighted_confidence_payload(code: str = "bid_confidence") -> KPIDefinitionCreate:
    return KPIDefinitionCreate(
        code=code,
        name="Amount-weighted bid confidence",
        description="Confidence of the estimate, weighted by what each line is worth.",
        unit="ratio",
        category="quality",
        spec={
            "entity": "boq_position",
            "aggregation": "weighted_avg",
            "field": "confidence",
            "weight_field": "amount",
            "group_by": "boq_id",
        },
    )


# ── The whitelist itself ───────────────────────────────────────────────


def test_catalog_and_bindings_describe_the_same_fields() -> None:
    """The documented whitelist and the executable one must not drift.

    A field declared in the catalog but not built is a promise the API
    advertises and then rejects; a field built but not declared is a
    column reachable without ever being documented. Both are the kind of
    gap that survives review because each half reads correctly alone.
    """
    assert kpi_spec.check_catalog_binding_parity() == {}


def test_catalog_never_offers_a_measure_as_a_grouping_key() -> None:
    for entry in kpi_spec.ENTITY_CATALOG.values():
        overlap = set(entry.numeric_fields()) & set(entry.groupable_fields())
        assert overlap == set(), f"{entry.name} offers {sorted(overlap)} as both measure and key"


# ── Validation at creation time ────────────────────────────────────────


def test_unknown_field_is_rejected_and_the_error_names_it() -> None:
    with pytest.raises(kpi_spec.KPISpecError) as exc_info:
        kpi_spec.validate_spec(
            {
                "entity": "boq_position",
                "aggregation": "sum",
                "field": "profit_margin",
            },
        )
    err = exc_info.value
    assert err.path == "spec.field"
    assert err.value == "profit_margin"
    assert "profit_margin" in str(err)
    # The rejection has to tell the caller what would have worked.
    assert "amount" in (err.allowed or [])
    assert "profit_margin" not in (err.allowed or [])


def test_unknown_entity_is_rejected_by_name() -> None:
    with pytest.raises(kpi_spec.KPISpecError) as exc_info:
        kpi_spec.validate_spec({"entity": "oe_users_user", "aggregation": "count"})
    assert exc_info.value.path == "spec.entity"
    assert exc_info.value.allowed == sorted(kpi_spec.ENTITY_CATALOG)


def test_unknown_aggregation_is_rejected_by_name() -> None:
    with pytest.raises(kpi_spec.KPISpecError) as exc_info:
        kpi_spec.validate_spec(
            {"entity": "boq_position", "aggregation": "stddev", "field": "amount"},
        )
    assert exc_info.value.path == "spec.aggregation"
    assert exc_info.value.value == "stddev"


def test_text_field_cannot_be_summed() -> None:
    with pytest.raises(kpi_spec.KPISpecError) as exc_info:
        kpi_spec.validate_spec(
            {"entity": "boq_position", "aggregation": "sum", "field": "description"},
        )
    assert exc_info.value.path == "spec.field"
    assert "numeric" in str(exc_info.value)


def test_field_valid_on_one_entity_is_rejected_on_another() -> None:
    """The whitelist is per entity, not one flat pool of column names."""
    kpi_spec.validate_spec({"entity": "boq_position", "aggregation": "sum", "field": "amount"})
    with pytest.raises(kpi_spec.KPISpecError) as exc_info:
        kpi_spec.validate_spec({"entity": "boq", "aggregation": "sum", "field": "amount"})
    assert exc_info.value.path == "spec.field"
    assert exc_info.value.value == "amount"


def test_filter_operator_outside_the_whitelist_is_rejected_with_its_index() -> None:
    with pytest.raises(kpi_spec.KPISpecError) as exc_info:
        kpi_spec.validate_spec(
            {
                "entity": "boq_position",
                "aggregation": "count",
                "filters": [
                    {"field": "unit", "op": "eq", "value": "m3"},
                    {"field": "description", "op": "like", "value": "%concrete%"},
                ],
            },
        )
    assert exc_info.value.path == "spec.filters[1].op"
    assert exc_info.value.value == "like"


def test_filter_field_outside_the_whitelist_is_rejected() -> None:
    with pytest.raises(kpi_spec.KPISpecError) as exc_info:
        kpi_spec.validate_spec(
            {
                "entity": "boq_position",
                "aggregation": "count",
                "filters": [{"field": "hashed_password", "op": "eq", "value": "x"}],
            },
        )
    assert exc_info.value.path == "spec.filters[0].field"


def test_ordering_operator_needs_a_numeric_field() -> None:
    with pytest.raises(kpi_spec.KPISpecError) as exc_info:
        kpi_spec.validate_spec(
            {
                "entity": "boq_position",
                "aggregation": "count",
                "filters": [{"field": "unit", "op": "gt", "value": 3}],
            },
        )
    assert exc_info.value.path == "spec.filters[0].op"


def test_count_must_not_name_a_field() -> None:
    with pytest.raises(kpi_spec.KPISpecError) as exc_info:
        kpi_spec.validate_spec(
            {"entity": "boq_position", "aggregation": "count", "field": "amount"},
        )
    assert exc_info.value.path == "spec.field"


def test_weighted_avg_without_a_weight_is_rejected() -> None:
    with pytest.raises(kpi_spec.KPISpecError) as exc_info:
        kpi_spec.validate_spec(
            {"entity": "boq_position", "aggregation": "weighted_avg", "field": "confidence"},
        )
    assert exc_info.value.path == "spec.weight_field"


def test_validation_drops_keys_it_does_not_understand() -> None:
    """What is stored is what validation looked at, and nothing else."""
    normalised = kpi_spec.validate_spec(
        {
            "entity": "boq_position",
            "aggregation": "sum",
            "field": "amount",
            "having": "1=1",
            "raw_sql": "DROP TABLE oe_boq_position",
        },
    )
    assert normalised == {
        "entity": "boq_position",
        "aggregation": "sum",
        "field": "amount",
        "filters": [],
    }


# ── Creation ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_custom_kpi_persists_a_validated_spec(session: AsyncSession) -> None:
    service = BIDashboardsService(session)
    row = await service.create_custom_kpi(_weighted_confidence_payload())

    assert row.code == "bid_confidence"
    assert row.is_system is False
    # ``formula_ref`` names no Python function on purpose - the lookup in
    # KPI_FORMULAS is meant to miss so the spec path runs.
    assert row.formula_ref not in kpis.KPI_FORMULAS
    assert row.spec_json["entity"] == "boq_position"
    # The source module is derived from the entity, never taken on trust.
    assert row.source_modules == ["oe_boq"]


@pytest.mark.asyncio
async def test_create_refuses_a_code_a_builtin_already_owns(session: AsyncSession) -> None:
    """Otherwise the spec would be stored and never consulted.

    ``kpis.compute`` looks in ``KPI_FORMULAS`` first, so a custom row
    named ``cpi`` would sit in the table while every surface kept showing
    the built-in cost performance index.
    """
    service = BIDashboardsService(session)
    payload = _weighted_confidence_payload(code="cpi")
    with pytest.raises(CustomKPICodeInUse) as exc_info:
        await service.create_custom_kpi(payload)
    assert exc_info.value.code == "cpi"


@pytest.mark.asyncio
async def test_create_refuses_a_duplicate_custom_code(session: AsyncSession) -> None:
    service = BIDashboardsService(session)
    await service.create_custom_kpi(_weighted_confidence_payload())
    with pytest.raises(CustomKPICodeInUse):
        await service.create_custom_kpi(_weighted_confidence_payload())


# ── Computation, through the same entry point every surface uses ───────


@pytest.mark.asyncio
async def test_weighted_confidence_computes_per_bid(session: AsyncSession) -> None:
    project_id, bid_a, bid_b = await _seed_two_bids(session)
    service = BIDashboardsService(session)
    await service.create_custom_kpi(_weighted_confidence_payload())

    result = await kpis.compute("bid_confidence", session, project_id=project_id)

    # (0.9*1000 + 0.4*4000 + 0.2*1000) / (1000 + 4000 + 1000) = 2700/6000
    assert result.value == pytest.approx(Decimal("0.45"), abs=Decimal("0.0001"))
    assert result.unit == "ratio"
    assert Decimal(result.breakdown[str(bid_a)]) == pytest.approx(Decimal("0.5"), abs=Decimal("0.0001"))
    # Bid B's unscored provisional sum is excluded rather than counted as
    # zero confidence, which would have dragged the bid to 0.1.
    assert Decimal(result.breakdown[str(bid_b)]) == pytest.approx(Decimal("0.2"), abs=Decimal("0.0001"))
    assert result.source_record_count == 3


@pytest.mark.asyncio
async def test_top_position_by_amount_per_bid(session: AsyncSession) -> None:
    project_id, bid_a, bid_b = await _seed_two_bids(session)
    service = BIDashboardsService(session)
    await service.create_custom_kpi(
        KPIDefinitionCreate(
            code="top_position_amount",
            name="Largest position per bid",
            unit="currency",
            category="financial",
            spec={
                "entity": "boq_position",
                "aggregation": "top_by",
                "field": "amount",
                "label_field": "description",
                "group_by": "boq_id",
            },
        ),
    )

    result = await kpis.compute("top_position_amount", session, project_id=project_id)

    assert result.value == pytest.approx(Decimal("4000"), abs=Decimal("0.01"))
    assert result.breakdown[str(bid_a)]["label"] == "Concrete"
    assert result.breakdown[str(bid_b)]["label"] == "Provisional sum"
    assert Decimal(result.breakdown[str(bid_b)]["value"]) == pytest.approx(
        Decimal("3000"),
        abs=Decimal("0.01"),
    )


@pytest.mark.asyncio
async def test_low_confidence_count_leaves_unscored_positions_out(session: AsyncSession) -> None:
    """An unset confidence is not a low confidence.

    ``numeric_value`` reads a NULL text column as 0 on PostgreSQL, so
    without an explicit NULL guard "below 0.5" would sweep in every
    position nobody has scored yet - three instead of two here.
    """
    project_id, _bid_a, _bid_b = await _seed_two_bids(session)
    service = BIDashboardsService(session)
    await service.create_custom_kpi(
        KPIDefinitionCreate(
            code="low_confidence_positions",
            name="Positions below 0.5 confidence",
            unit="count",
            category="quality",
            spec={
                "entity": "boq_position",
                "aggregation": "count",
                "filters": [{"field": "confidence", "op": "lt", "value": 0.5}],
            },
        ),
    )

    result = await kpis.compute("low_confidence_positions", session, project_id=project_id)
    assert result.value == Decimal("2")
    assert result.unit == "count"


@pytest.mark.asyncio
async def test_portfolio_call_honours_the_callers_accessible_projects(session: AsyncSession) -> None:
    """A custom KPI is not allowed to be the read that ignores scoping.

    Same ``allowed_project_ids`` narrowing the built-in formulas get: an
    empty set means the caller can reach nothing, which must read as zero
    rather than as every project in the deployment.
    """
    project_id, _a, _b = await _seed_two_bids(session)
    service = BIDashboardsService(session)
    await service.create_custom_kpi(
        KPIDefinitionCreate(
            code="portfolio_bid_value",
            name="Portfolio bid value",
            unit="currency",
            category="financial",
            spec={"entity": "boq_position", "aggregation": "sum", "field": "amount"},
        ),
    )

    unrestricted = await kpis.compute("portfolio_bid_value", session, allowed_project_ids=None)
    assert unrestricted.value >= Decimal("9000")

    permitted = await kpis.compute(
        "portfolio_bid_value",
        session,
        allowed_project_ids={project_id},
    )
    assert permitted.value == pytest.approx(Decimal("9000"), abs=Decimal("0.01"))

    blind = await kpis.compute("portfolio_bid_value", session, allowed_project_ids=set())
    assert blind.value == Decimal("0")
    assert blind.source_record_count == 0

    stranger = await kpis.compute(
        "portfolio_bid_value",
        session,
        allowed_project_ids={uuid.uuid4()},
    )
    assert stranger.value == Decimal("0")


@pytest.mark.asyncio
async def test_a_code_with_neither_formula_nor_spec_still_reads_zero(session: AsyncSession) -> None:
    """The pre-existing contract for a misconfigured widget is unchanged."""
    result = await kpis.compute("no_such_kpi_anywhere", session)
    assert result.value == Decimal("0")
    assert result.source_record_count == 0


# ── Starter pack isolation ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_starter_pack_reinstall_leaves_custom_definitions_intact(
    session: AsyncSession,
) -> None:
    """Reinstalling the 35 built-ins must not touch a user's own KPI.

    The starter pack upserts by code and only ever names the codes it
    owns, so the isolation is structural rather than a flag anybody has to
    remember to check. This test is what keeps it structural.
    """
    from app.modules.bi_dashboards.seed import seed_all

    service = BIDashboardsService(session)
    await service.bootstrap_system_kpis()

    created = await service.create_custom_kpi(_weighted_confidence_payload())
    custom_id = created.id

    await seed_all(session)
    await seed_all(session)

    row = await service.repo.get_kpi_definition_by_code("bid_confidence")
    assert row is not None
    assert row.id == custom_id
    assert row.is_system is False
    assert row.spec_json["aggregation"] == "weighted_avg"
    assert row.spec_json["weight_field"] == "amount"

    # And the built-ins are still marked as such, so the two populations
    # stay separable after the reinstall.
    systems = (
        (await session.execute(select(KPIDefinition.code).where(KPIDefinition.is_system.is_(True)))).scalars().all()
    )
    assert "cpi" in systems
    assert "bid_confidence" not in systems


# ── Deletion ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_is_refused_while_a_widget_points_at_the_kpi(
    session: AsyncSession,
) -> None:
    """Nothing in the schema stops the delete, so the service does.

    ``DashboardWidget.kpi_code`` is a plain string with no foreign key -
    it has to be, because a code may equally be served by a Python formula
    that owns no row. Letting the definition go would leave the tile
    rendering a permanent zero that looks exactly like a measurement.
    """
    from app.modules.bi_dashboards.schemas import DashboardCreate

    service = BIDashboardsService(session)
    await service.create_custom_kpi(_weighted_confidence_payload())

    dashboard = await service.create_dashboard(
        DashboardCreate(name="Bids", scope="personal"),
        owner_user_id=OWNER_ID,
    )
    widget = DashboardWidget(
        dashboard_id=dashboard.id,
        widget_type="kpi_card",
        kpi_code="bid_confidence",
    )
    session.add(widget)
    await session.flush()

    with pytest.raises(CustomKPIInUse) as exc_info:
        await service.delete_custom_kpi("bid_confidence")
    assert exc_info.value.referrers["widgets"] == [widget.id]
    assert exc_info.value.referrers["alerts"] == []
    assert str(widget.id) not in str(exc_info.value)  # the message counts, the payload names
    assert "1 widget(s)" in str(exc_info.value)

    # Still there, still computable.
    assert await service.repo.get_kpi_definition_by_code("bid_confidence") is not None

    # Repointing the widget releases the KPI.
    widget.kpi_code = "cpi"
    await session.flush()
    await service.delete_custom_kpi("bid_confidence")
    assert await service.repo.get_kpi_definition_by_code("bid_confidence") is None


@pytest.mark.asyncio
async def test_delete_is_refused_while_an_alert_rule_points_at_the_kpi(
    session: AsyncSession,
) -> None:
    from app.modules.bi_dashboards.models import AlertRule

    service = BIDashboardsService(session)
    await service.create_custom_kpi(_weighted_confidence_payload())
    alert = AlertRule(
        name="Bid confidence dropped",
        kpi_code="bid_confidence",
        condition="below",
        threshold_value=Decimal("0.6"),
    )
    session.add(alert)
    await session.flush()

    with pytest.raises(CustomKPIInUse) as exc_info:
        await service.delete_custom_kpi("bid_confidence")
    assert exc_info.value.referrers["alerts"] == [alert.id]


@pytest.mark.asyncio
async def test_delete_refuses_a_builtin_definition(session: AsyncSession) -> None:
    service = BIDashboardsService(session)
    await service.bootstrap_system_kpis()
    with pytest.raises(CustomKPIIsSystem):
        await service.delete_custom_kpi("cpi")
    assert await service.repo.get_kpi_definition_by_code("cpi") is not None


@pytest.mark.asyncio
async def test_deleted_custom_kpi_stops_computing(session: AsyncSession) -> None:
    project_id, _a, _b = await _seed_two_bids(session)
    service = BIDashboardsService(session)
    await service.create_custom_kpi(_weighted_confidence_payload())
    assert (await kpis.compute("bid_confidence", session, project_id=project_id)).source_record_count == 3

    await service.delete_custom_kpi("bid_confidence")
    after = await kpis.compute("bid_confidence", session, project_id=project_id)
    assert after.value == Decimal("0")
    assert after.source_record_count == 0


# ── Reach: the surfaces the reporter named ─────────────────────────────


async def _dashboard_with_custom_widget(
    session: AsyncSession,
    service: BIDashboardsService,
    widget_type: str,
) -> tuple[uuid.UUID, DashboardWidget]:
    from app.modules.bi_dashboards.schemas import DashboardCreate

    dashboard = await service.create_dashboard(
        DashboardCreate(name=f"Bids {widget_type}", scope="personal"),
        owner_user_id=OWNER_ID,
    )
    widget = DashboardWidget(
        dashboard_id=dashboard.id,
        widget_type=widget_type,
        kpi_code="bid_confidence",
    )
    session.add(widget)
    await session.flush()
    return dashboard.id, widget


@pytest.mark.asyncio
async def test_custom_kpi_reaches_a_kpi_card_widget(session: AsyncSession) -> None:
    """Surface 1 of 3 - a rendered kpi_card carries the custom value."""
    project_id, _a, _b = await _seed_two_bids(session)
    service = BIDashboardsService(session)
    await service.create_custom_kpi(_weighted_confidence_payload())
    dashboard_id, widget = await _dashboard_with_custom_widget(session, service, "kpi_card")

    rendered = await service.render_dashboard(dashboard_id, allowed_project_ids={project_id})

    assert rendered is not None
    tile = next(w for w in rendered.widgets if w.widget.id == widget.id)
    assert tile.value == pytest.approx(Decimal("0.45"), abs=Decimal("0.0001"))
    assert tile.unit == "ratio"
    assert tile.breakdown != {}


@pytest.mark.asyncio
async def test_custom_kpi_reaches_a_chart_widget_headline_and_series(
    session: AsyncSession,
) -> None:
    """Surface 2 of 3, with the caveat the code actually carries.

    A chart's headline value comes from ``kpis.compute`` and works at
    once. Its ``series`` does not: ``evaluate_dashboard`` reads the series
    from stored ``KPIValue`` history, so it is empty until something has
    persisted a point. That is not a custom-KPI limitation - a built-in
    with no history behaves identically - but it does mean a custom chart
    looks flat until the KPI has been computed with ``persist=True`` at
    least once.
    """
    project_id, _a, _b = await _seed_two_bids(session)
    service = BIDashboardsService(session)
    await service.create_custom_kpi(_weighted_confidence_payload())
    dashboard_id, widget = await _dashboard_with_custom_widget(session, service, "line_chart")

    first = await service.evaluate_dashboard(dashboard_id, allowed_project_ids={project_id})
    assert first is not None
    chart = next(w for w in first.widgets if w.id == widget.id)
    assert chart.value == pytest.approx(Decimal("0.45"), abs=Decimal("0.0001"))
    assert chart.series == []

    await service.compute_kpi(
        "bid_confidence",
        project_id=project_id,
        persist=True,
        include_trend=False,
        include_benchmark=False,
    )

    second = await service.evaluate_dashboard(dashboard_id, allowed_project_ids={project_id})
    assert second is not None
    chart = next(w for w in second.widgets if w.id == widget.id)
    assert len(chart.series) == 1
    assert Decimal(chart.series[0]["value"]) == pytest.approx(Decimal("0.45"), abs=Decimal("0.0001"))


@pytest.mark.asyncio
async def test_custom_kpi_reaches_an_alert_rule(session: AsyncSession) -> None:
    """Surface 3 of 3 - a threshold alert fires on the custom value."""
    from app.modules.bi_dashboards.models import AlertRule

    project_id, _a, _b = await _seed_two_bids(session)
    service = BIDashboardsService(session)
    await service.create_custom_kpi(_weighted_confidence_payload())

    below = AlertRule(
        name="Bid confidence below 0.6",
        kpi_code="bid_confidence",
        condition="below",
        threshold_value=Decimal("0.6"),
        scope_project_id=project_id,
        throttle_seconds=0,
    )
    above = AlertRule(
        name="Bid confidence above 0.9",
        kpi_code="bid_confidence",
        condition="above",
        threshold_value=Decimal("0.9"),
        scope_project_id=project_id,
        throttle_seconds=0,
    )
    session.add_all([below, above])
    await session.flush()

    # 0.45 is below 0.6 and not above 0.9 - the rule reads the real value
    # rather than the zero an unresolved code would have produced.
    assert await service.evaluate_alert(below) is True
    assert await service.evaluate_alert(above) is False


@pytest.mark.asyncio
async def test_custom_kpi_reaches_a_report_run(session: AsyncSession) -> None:
    """Not one of the three named, but the same entry point serves it."""
    from app.modules.bi_dashboards.schemas import ReportDefinitionCreate

    project_id, _a, _b = await _seed_two_bids(session)
    service = BIDashboardsService(session)
    await service.create_custom_kpi(_weighted_confidence_payload())
    report = await service.create_report(
        ReportDefinitionCreate(
            code=f"bids_{uuid.uuid4().hex[:6]}",
            name="Bid confidence",
            query_spec_json={"kpis": ["bid_confidence"], "project_id": str(project_id)},
            output_format="csv",
        ),
        owner_user_id=OWNER_ID,
    )

    run = await service.run_report(report.id, produce_file=False)
    assert run is not None
    row = next(r for r in run.rows if r["kpi_code"] == "bid_confidence")
    assert Decimal(row["value"]) == pytest.approx(Decimal("0.45"), abs=Decimal("0.0001"))
