from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

PIPELINE_DIR = Path(__file__).resolve().parent
DEFAULT_MANIFEST = PIPELINE_DIR / "outputs" / "translation_manifest.json"
DEFAULT_OUT = PIPELINE_DIR / "outputs" / "ppp_fx_multipliers.parquet"
ECB_DAILY_XML_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
NUMBEO_INDICES_URL = "https://www.numbeo.com/api/indices"
NUMBEO_COUNTRY_INDICES_URL = "https://www.numbeo.com/api/country_indices"
WORLD_BANK_PPP_URL_TEMPLATE = "https://api.worldbank.org/v2/country/{countries}/indicator/PA.NUS.PPP"


REQUIRED_ECONOMY_COLUMNS = {
    "region",
    "country_iso",
    "city",
    "currency",
}
REQUIRED_NUMBEO_COLUMNS = {
    "country_iso",
    "city",
    "cost_of_living_index",
}
REQUIRED_WORLDBANK_COLUMNS = {
    "country_iso",
    "ppp_conversion_factor",
    "year",
}


@dataclass(frozen=True)
class Economy:
    region: str
    country_iso: str
    city: str
    currency: str


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_target_catalogues(manifest_path: Path) -> list[Economy]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    catalogues = manifest.get("target_catalogues") or []
    economies: list[Economy] = []
    missing: list[dict] = []
    for row in catalogues:
        absent = [col for col in REQUIRED_ECONOMY_COLUMNS if not str(row.get(col, "")).strip()]
        if absent:
            missing.append({"row": row, "missing": absent})
            continue
        economies.append(
            Economy(
                region=str(row["region"]).strip(),
                country_iso=str(row["country_iso"]).strip().upper(),
                city=str(row["city"]).strip(),
                currency=str(row["currency"]).strip().upper(),
            )
        )
    if missing:
        raise ValueError(f"Target catalogue metadata is incomplete: {missing[:10]}")
    if not economies:
        raise ValueError("No target catalogues found in manifest")
    return economies


def read_economies(path: Path) -> list[Economy]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("source_economies", [])
        frame = pd.DataFrame(rows)
    else:
        frame = pd.read_csv(path)
    missing_cols = REQUIRED_ECONOMY_COLUMNS - set(frame.columns)
    if missing_cols:
        raise ValueError(f"{path} is missing economy columns: {sorted(missing_cols)}")
    missing_rows = []
    economies: list[Economy] = []
    for idx, row in frame.iterrows():
        absent = [col for col in REQUIRED_ECONOMY_COLUMNS if pd.isna(row[col]) or not str(row[col]).strip()]
        if absent:
            missing_rows.append({"row_index": int(idx), "missing": absent})
            continue
        economies.append(
            Economy(
                region=str(row["region"]).strip(),
                country_iso=str(row["country_iso"]).strip().upper(),
                city=str(row["city"]).strip(),
                currency=str(row["currency"]).strip().upper(),
            )
        )
    if missing_rows:
        raise ValueError(f"{path} has incomplete economy rows: {missing_rows[:10]}")
    if not economies:
        raise ValueError(f"{path} contains no economies")
    return economies


def parse_ecb_daily_xml(xml_text: str) -> tuple[dict[str, float], str | None]:
    root = ET.fromstring(xml_text)
    rates = {"EUR": 1.0}
    rate_date: str | None = None
    for element in root.iter():
        attrs = element.attrib
        if "time" in attrs:
            rate_date = attrs["time"]
        if "currency" in attrs and "rate" in attrs:
            rates[str(attrs["currency"]).upper()] = float(attrs["rate"])
    if len(rates) == 1:
        raise ValueError("ECB XML did not contain currency rates")
    return rates, rate_date


def load_ecb_rates(
    *, ecb_xml_path: Path | None = None, ecb_xml_url: str = ECB_DAILY_XML_URL
) -> tuple[dict[str, float], dict]:
    if ecb_xml_path:
        xml_text = ecb_xml_path.read_text(encoding="utf-8")
        source = {
            "provider": "ECB",
            "kind": "local_xml",
            "path": str(ecb_xml_path),
            "sha256": file_sha256(ecb_xml_path),
            "url": ecb_xml_url,
        }
    else:
        with urllib.request.urlopen(ecb_xml_url, timeout=30) as response:
            body = response.read()
        xml_text = body.decode("utf-8")
        source = {
            "provider": "ECB",
            "kind": "official_xml_url",
            "url": ecb_xml_url,
            "sha256": hashlib.sha256(body).hexdigest(),
        }
    rates, rate_date = parse_ecb_daily_xml(xml_text)
    source["rate_date"] = rate_date
    return rates, source


def _normalized_city(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def load_numbeo_snapshot(path: Path) -> tuple[dict[tuple[str, str], dict], dict]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("indices", [])
        frame = pd.DataFrame(rows)
    else:
        frame = pd.read_csv(path)
    missing_cols = REQUIRED_NUMBEO_COLUMNS - set(frame.columns)
    if missing_cols:
        raise ValueError(f"{path} is missing Numbeo snapshot columns: {sorted(missing_cols)}")
    lookup: dict[tuple[str, str], dict] = {}
    bad_rows = []
    for idx, row in frame.iterrows():
        country_iso = str(row["country_iso"]).strip().upper()
        city = str(row["city"]).strip()
        try:
            col_index = float(row["cost_of_living_index"])
        except (TypeError, ValueError):
            bad_rows.append({"row_index": int(idx), "reason": "invalid_cost_of_living_index"})
            continue
        if not country_iso or not city or col_index <= 0:
            bad_rows.append({"row_index": int(idx), "reason": "missing_or_non_positive_value"})
            continue
        lookup[(country_iso, _normalized_city(city))] = {
            "country_iso": country_iso,
            "city": city,
            "cost_of_living_index": col_index,
            "source": str(row.get("source", "numbeo_snapshot")),
            "snapshot_date": str(row.get("snapshot_date", "")),
        }
    if bad_rows:
        raise ValueError(f"{path} has invalid Numbeo rows: {bad_rows[:10]}")
    if not lookup:
        raise ValueError(f"{path} contains no Numbeo indices")
    return lookup, {
        "provider": "Numbeo",
        "kind": "imported_snapshot",
        "path": str(path),
        "sha256": file_sha256(path),
    }


def fetch_numbeo_indices(economies: Iterable[Economy], api_key: str) -> tuple[dict[tuple[str, str], dict], dict]:
    if not api_key:
        raise ValueError("Numbeo API key is required when no snapshot is provided")
    lookup: dict[tuple[str, str], dict] = {}
    for economy in economies:
        params = {"api_key": api_key}
        if economy.city.lower().startswith("national"):
            params["country"] = economy.country_iso
            url = NUMBEO_COUNTRY_INDICES_URL + "?" + urllib.parse.urlencode(params)
        else:
            params["query"] = f"{economy.city}, {economy.country_iso}"
            url = NUMBEO_INDICES_URL + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        value = payload.get("cost_of_living_index") or payload.get("costOfLivingIndex") or payload.get("cpi")
        if value is None:
            raise ValueError(f"Numbeo response missing cost_of_living_index for {economy.region}")
        col_index = float(value)
        if col_index <= 0:
            raise ValueError(f"Numbeo response has non-positive cost_of_living_index for {economy.region}")
        lookup[(economy.country_iso, _normalized_city(economy.city))] = {
            "country_iso": economy.country_iso,
            "city": economy.city,
            "cost_of_living_index": col_index,
            "source": "numbeo_api",
            "snapshot_date": str(payload.get("date") or payload.get("last_update") or ""),
        }
    return lookup, {
        "provider": "Numbeo",
        "kind": "api",
        "url": NUMBEO_INDICES_URL,
        "fetched_at_utc": datetime.now(UTC).isoformat(),
    }


def load_worldbank_ppp_snapshot(path: Path) -> tuple[dict[str, dict], dict]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("ppp", [])
        frame = pd.DataFrame(rows)
    else:
        frame = pd.read_csv(path)
    missing_cols = REQUIRED_WORLDBANK_COLUMNS - set(frame.columns)
    if missing_cols:
        raise ValueError(f"{path} is missing World Bank PPP snapshot columns: {sorted(missing_cols)}")
    lookup: dict[str, dict] = {}
    bad_rows = []
    for idx, row in frame.iterrows():
        country_iso = str(row["country_iso"]).strip().upper()
        try:
            ppp = float(row["ppp_conversion_factor"])
            year = int(row["year"])
        except (TypeError, ValueError):
            bad_rows.append({"row_index": int(idx), "reason": "invalid_ppp_or_year"})
            continue
        if not country_iso or ppp <= 0:
            bad_rows.append({"row_index": int(idx), "reason": "missing_or_non_positive_value"})
            continue
        previous = lookup.get(country_iso)
        if previous is None or year > int(previous["year"]):
            lookup[country_iso] = {
                "country_iso": country_iso,
                "ppp_conversion_factor": ppp,
                "year": year,
                "source": str(row.get("source", "worldbank_snapshot")),
            }
    if bad_rows:
        raise ValueError(f"{path} has invalid World Bank PPP rows: {bad_rows[:10]}")
    if not lookup:
        raise ValueError(f"{path} contains no World Bank PPP factors")
    return lookup, {
        "provider": "World Bank",
        "indicator": "PA.NUS.PPP",
        "kind": "imported_snapshot",
        "path": str(path),
        "sha256": file_sha256(path),
    }


def fetch_worldbank_ppp(economies: Iterable[Economy]) -> tuple[dict[str, dict], dict]:
    country_codes = sorted({economy.country_iso.upper() for economy in economies})
    lookup: dict[str, dict] = {}
    fetched_at = datetime.now(UTC).isoformat()
    # Keep URLs short enough for proxy stacks.
    for start in range(0, len(country_codes), 35):
        chunk = country_codes[start : start + 35]
        params = urllib.parse.urlencode({"format": "json", "per_page": 20000})
        url = WORLD_BANK_PPP_URL_TEMPLATE.format(countries=";".join(chunk)) + "?" + params
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, list) or len(payload) < 2:
            raise ValueError(f"Unexpected World Bank response for countries {chunk}")
        meta, rows = payload[0], payload[1]
        for row in rows:
            value = row.get("value")
            if value is None:
                continue
            country_iso = str(row.get("country", {}).get("id", "")).strip().upper()
            if not country_iso:
                continue
            year = int(row["date"])
            ppp = float(value)
            if ppp <= 0:
                continue
            previous = lookup.get(country_iso)
            if previous is None or year > int(previous["year"]):
                lookup[country_iso] = {
                    "country_iso": country_iso,
                    "country_name": row.get("country", {}).get("value", ""),
                    "countryiso3code": row.get("countryiso3code", ""),
                    "ppp_conversion_factor": ppp,
                    "year": year,
                    "source": "worldbank_api",
                }
    return lookup, {
        "provider": "World Bank",
        "indicator": "PA.NUS.PPP",
        "kind": "api",
        "url_template": WORLD_BANK_PPP_URL_TEMPLATE,
        "fetched_at_utc": fetched_at,
    }


def require_worldbank_ppp(economies: Iterable[Economy], lookup: dict[str, dict]) -> None:
    missing = [
        {
            "region": economy.region,
            "country_iso": economy.country_iso,
            "currency": economy.currency,
        }
        for economy in economies
        if economy.country_iso.upper() not in lookup
    ]
    if missing:
        raise ValueError(f"World Bank PPP factors missing required countries: {missing[:30]}")


def require_fx_rates(economies: Iterable[Economy], rates: dict[str, float]) -> None:
    missing = sorted({economy.currency for economy in economies if economy.currency not in rates})
    if missing:
        raise ValueError(f"ECB FX rates missing required currencies: {missing}")


def require_numbeo_indices(economies: Iterable[Economy], lookup: dict[tuple[str, str], dict]) -> None:
    missing = [
        {
            "region": economy.region,
            "country_iso": economy.country_iso,
            "city": economy.city,
        }
        for economy in economies
        if (economy.country_iso, _normalized_city(economy.city)) not in lookup
    ]
    if missing:
        raise ValueError(f"Numbeo cost-of-living indices missing required economies: {missing[:20]}")


def build_multiplier_table(
    *,
    source_economies: list[Economy],
    target_economies: list[Economy],
    ecb_rates: dict[str, float],
    numbeo_lookup: dict[tuple[str, str], dict],
    fx_provenance: dict,
    numbeo_provenance: dict,
) -> pd.DataFrame:
    all_economies = [*source_economies, *target_economies]
    require_fx_rates(all_economies, ecb_rates)
    require_numbeo_indices(all_economies, numbeo_lookup)
    rows = []
    built_at = datetime.now(UTC).isoformat()
    for source in source_economies:
        source_col = numbeo_lookup[(source.country_iso, _normalized_city(source.city))]
        for target in target_economies:
            target_col = numbeo_lookup[(target.country_iso, _normalized_city(target.city))]
            source_fx_per_eur = float(ecb_rates[source.currency])
            target_fx_per_eur = float(ecb_rates[target.currency])
            fx_multiplier = target_fx_per_eur / source_fx_per_eur
            ppp_multiplier = float(target_col["cost_of_living_index"]) / float(source_col["cost_of_living_index"])
            rows.append(
                {
                    "source_region": source.region,
                    "target_catalogue_region": target.region,
                    "source_country_iso": source.country_iso,
                    "target_country_iso": target.country_iso,
                    "source_city": source.city,
                    "target_city": target.city,
                    "source_currency": source.currency,
                    "target_currency": target.currency,
                    "source_fx_per_eur": source_fx_per_eur,
                    "target_fx_per_eur": target_fx_per_eur,
                    "fx_multiplier_target_per_source": fx_multiplier,
                    "source_cost_of_living_index": float(source_col["cost_of_living_index"]),
                    "target_cost_of_living_index": float(target_col["cost_of_living_index"]),
                    "ppp_multiplier": ppp_multiplier,
                    "price_multiplier": fx_multiplier * ppp_multiplier,
                    "formula": "source_price * (target_fx_per_eur/source_fx_per_eur) * (target_col_index/source_col_index)",
                    "fx_provider": "ECB",
                    "col_provider": "Numbeo",
                    "fx_rate_date": fx_provenance.get("rate_date"),
                    "source_col_snapshot_date": source_col.get("snapshot_date", ""),
                    "target_col_snapshot_date": target_col.get("snapshot_date", ""),
                    "built_at_utc": built_at,
                    "provenance_json": json.dumps(
                        {
                            "fx": fx_provenance,
                            "cost_of_living": numbeo_provenance,
                            "source_col": source_col,
                            "target_col": target_col,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                }
            )
    return pd.DataFrame(rows)


def build_worldbank_ppp_table(
    *,
    source_economies: list[Economy],
    target_economies: list[Economy],
    worldbank_lookup: dict[str, dict],
    worldbank_provenance: dict,
) -> pd.DataFrame:
    all_economies = [*source_economies, *target_economies]
    require_worldbank_ppp(all_economies, worldbank_lookup)
    rows = []
    built_at = datetime.now(UTC).isoformat()
    for source in source_economies:
        source_ppp = worldbank_lookup[source.country_iso.upper()]
        for target in target_economies:
            target_ppp = worldbank_lookup[target.country_iso.upper()]
            source_factor = float(source_ppp["ppp_conversion_factor"])
            target_factor = float(target_ppp["ppp_conversion_factor"])
            multiplier = target_factor / source_factor
            rows.append(
                {
                    "source_region": source.region,
                    "target_catalogue_region": target.region,
                    "source_country_iso": source.country_iso,
                    "target_country_iso": target.country_iso,
                    "source_city": source.city,
                    "target_city": target.city,
                    "source_currency": source.currency,
                    "target_currency": target.currency,
                    "source_ppp_lcu_per_international_dollar": source_factor,
                    "target_ppp_lcu_per_international_dollar": target_factor,
                    "source_ppp_year": int(source_ppp["year"]),
                    "target_ppp_year": int(target_ppp["year"]),
                    "ppp_multiplier": multiplier,
                    "price_multiplier": multiplier,
                    "formula": "source_price * (target_worldbank_pa_nus_ppp/source_worldbank_pa_nus_ppp)",
                    "ppp_provider": "World Bank",
                    "ppp_indicator": "PA.NUS.PPP",
                    "built_at_utc": built_at,
                    "provenance_json": json.dumps(
                        {
                            "ppp": worldbank_provenance,
                            "source_ppp": source_ppp,
                            "target_ppp": target_ppp,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                }
            )
    return pd.DataFrame(rows)


def write_outputs(table: pd.DataFrame, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.suffix.lower() == ".csv":
        table.to_csv(out_path, index=False, quoting=csv.QUOTE_MINIMAL)
    else:
        table.to_parquet(out_path, index=False)
        table.to_csv(out_path.with_suffix(".csv"), index=False, quoting=csv.QUOTE_MINIMAL)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build PPP/FX price multipliers for target catalogues.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source-economies", type=Path, required=True)
    parser.add_argument("--ppp-provider", choices=["numbeo", "worldbank"], default="numbeo")
    parser.add_argument("--numbeo-snapshot", type=Path)
    parser.add_argument("--worldbank-ppp-snapshot", type=Path)
    parser.add_argument("--ecb-xml", type=Path)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    source_economies = read_economies(args.source_economies)
    target_economies = load_target_catalogues(args.manifest)
    if args.ppp_provider == "worldbank":
        if args.worldbank_ppp_snapshot:
            worldbank_lookup, worldbank_provenance = load_worldbank_ppp_snapshot(args.worldbank_ppp_snapshot)
        else:
            worldbank_lookup, worldbank_provenance = fetch_worldbank_ppp([*source_economies, *target_economies])
        table = build_worldbank_ppp_table(
            source_economies=source_economies,
            target_economies=target_economies,
            worldbank_lookup=worldbank_lookup,
            worldbank_provenance=worldbank_provenance,
        )
    else:
        ecb_rates, fx_provenance = load_ecb_rates(ecb_xml_path=args.ecb_xml)
        if args.numbeo_snapshot:
            numbeo_lookup, numbeo_provenance = load_numbeo_snapshot(args.numbeo_snapshot)
        else:
            api_key = os.environ.get("NUMBEO_API_KEY", "").strip()
            numbeo_lookup, numbeo_provenance = fetch_numbeo_indices([*source_economies, *target_economies], api_key)
        table = build_multiplier_table(
            source_economies=source_economies,
            target_economies=target_economies,
            ecb_rates=ecb_rates,
            numbeo_lookup=numbeo_lookup,
            fx_provenance=fx_provenance,
            numbeo_provenance=numbeo_provenance,
        )
    write_outputs(table, args.out)
    print(
        json.dumps(
            {"rows": int(len(table)), "out": str(args.out)},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
