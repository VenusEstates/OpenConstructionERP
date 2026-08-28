from __future__ import annotations

import json

import pandas as pd
from ppp_fx_pipeline import (
    Economy,
    build_multiplier_table,
    build_worldbank_ppp_table,
    load_numbeo_snapshot,
    load_target_catalogues,
    load_worldbank_ppp_snapshot,
    parse_ecb_daily_xml,
    read_economies,
    require_fx_rates,
    require_numbeo_indices,
    require_worldbank_ppp,
)

ECB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
  xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube>
    <Cube time="2026-07-03">
      <Cube currency="USD" rate="1.1000"/>
      <Cube currency="TRY" rate="45.0000"/>
      <Cube currency="GBP" rate="0.8500"/>
    </Cube>
  </Cube>
</gesmes:Envelope>
"""


def test_parse_ecb_daily_xml_adds_eur_base_rate() -> None:
    rates, rate_date = parse_ecb_daily_xml(ECB_XML)
    assert rate_date == "2026-07-03"
    assert rates["EUR"] == 1.0
    assert rates["USD"] == 1.1
    assert rates["TRY"] == 45.0


def test_load_target_catalogues_blocks_missing_currency(tmp_path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"target_catalogues": [{"region": "DE_BERLIN", "country_iso": "DE", "city": "Berlin"}]}),
        encoding="utf-8",
    )
    try:
        load_target_catalogues(manifest)
    except ValueError as exc:
        assert "incomplete" in str(exc)
        assert "currency" in str(exc)
    else:
        raise AssertionError("missing target currency did not block")


def test_read_source_economies_blocks_missing_country(tmp_path) -> None:
    path = tmp_path / "source_economies.csv"
    pd.DataFrame([{"region": "TR", "city": "Istanbul", "currency": "TRY"}]).to_csv(path, index=False)
    try:
        read_economies(path)
    except ValueError as exc:
        assert "missing economy columns" in str(exc)
        assert "country_iso" in str(exc)
    else:
        raise AssertionError("missing source country did not block")


def test_load_numbeo_snapshot_requires_positive_index(tmp_path) -> None:
    path = tmp_path / "numbeo.csv"
    pd.DataFrame(
        [
            {
                "country_iso": "TR",
                "city": "Istanbul",
                "cost_of_living_index": 0,
                "snapshot_date": "2026-07",
            }
        ]
    ).to_csv(path, index=False)
    try:
        load_numbeo_snapshot(path)
    except ValueError as exc:
        assert "invalid Numbeo rows" in str(exc)
    else:
        raise AssertionError("invalid Numbeo index did not block")


def test_load_worldbank_ppp_snapshot_uses_latest_year(tmp_path) -> None:
    path = tmp_path / "wb_ppp.csv"
    pd.DataFrame(
        [
            {"country_iso": "TR", "ppp_conversion_factor": 10.0, "year": 2023},
            {"country_iso": "TR", "ppp_conversion_factor": 12.0, "year": 2025},
        ]
    ).to_csv(path, index=False)
    lookup, provenance = load_worldbank_ppp_snapshot(path)
    assert lookup["TR"]["ppp_conversion_factor"] == 12.0
    assert lookup["TR"]["year"] == 2025
    assert provenance["provider"] == "World Bank"


def test_missing_fx_or_numbeo_coverage_blocks() -> None:
    economies = [
        Economy("TR", "TR", "Istanbul", "TRY"),
        Economy("DE_BERLIN", "DE", "Berlin", "EUR"),
    ]
    try:
        require_fx_rates(economies, {"EUR": 1.0})
    except ValueError as exc:
        assert "TRY" in str(exc)
    else:
        raise AssertionError("missing ECB currency did not block")

    try:
        require_numbeo_indices(economies, {("TR", "istanbul"): {"cost_of_living_index": 40.0}})
    except ValueError as exc:
        assert "DE_BERLIN" in str(exc)
    else:
        raise AssertionError("missing Numbeo economy did not block")

    try:
        require_worldbank_ppp(economies, {"TR": {"ppp_conversion_factor": 12.0, "year": 2025}})
    except ValueError as exc:
        assert "DE_BERLIN" in str(exc)
    else:
        raise AssertionError("missing World Bank PPP country did not block")


def test_build_multiplier_table_with_provenance() -> None:
    source = [Economy("TR", "TR", "Istanbul", "TRY")]
    target = [
        Economy("DE_BERLIN", "DE", "Berlin", "EUR"),
        Economy("GB_LONDON", "GB", "London", "GBP"),
    ]
    table = build_multiplier_table(
        source_economies=source,
        target_economies=target,
        ecb_rates={"EUR": 1.0, "TRY": 45.0, "GBP": 0.85},
        numbeo_lookup={
            ("TR", "istanbul"): {
                "cost_of_living_index": 40.0,
                "city": "Istanbul",
                "snapshot_date": "2026-07",
            },
            ("DE", "berlin"): {
                "cost_of_living_index": 80.0,
                "city": "Berlin",
                "snapshot_date": "2026-07",
            },
            ("GB", "london"): {
                "cost_of_living_index": 100.0,
                "city": "London",
                "snapshot_date": "2026-07",
            },
        },
        fx_provenance={"provider": "ECB", "rate_date": "2026-07-03", "url": "official"},
        numbeo_provenance={
            "provider": "Numbeo",
            "kind": "imported_snapshot",
            "sha256": "abc",
        },
    )
    assert len(table) == 2
    berlin = table[table["target_catalogue_region"].eq("DE_BERLIN")].iloc[0]
    assert berlin["fx_multiplier_target_per_source"] == 1.0 / 45.0
    assert berlin["ppp_multiplier"] == 2.0
    assert berlin["price_multiplier"] == (1.0 / 45.0) * 2.0
    provenance = json.loads(berlin["provenance_json"])
    assert provenance["fx"]["provider"] == "ECB"
    assert provenance["cost_of_living"]["provider"] == "Numbeo"


def test_build_worldbank_ppp_table() -> None:
    source = [Economy("TR", "TR", "Ankara", "TRY")]
    target = [Economy("DE_BERLIN", "DE", "Berlin", "EUR")]
    table = build_worldbank_ppp_table(
        source_economies=source,
        target_economies=target,
        worldbank_lookup={
            "TR": {"ppp_conversion_factor": 12.0, "year": 2025},
            "DE": {"ppp_conversion_factor": 0.8, "year": 2025},
        },
        worldbank_provenance={"provider": "World Bank", "indicator": "PA.NUS.PPP"},
    )
    row = table.iloc[0]
    assert row["ppp_multiplier"] == 0.8 / 12.0
    assert row["price_multiplier"] == 0.8 / 12.0
    assert row["ppp_indicator"] == "PA.NUS.PPP"
