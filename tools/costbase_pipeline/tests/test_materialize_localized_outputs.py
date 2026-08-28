from __future__ import annotations

import json

import pandas as pd
from extract_translation_corpus import tm_key
from materialize_localized_outputs import (
    apply_ppp_conversion,
    build_translation_map,
    materialize_region,
    strip_pipeline_artifacts,
)


def test_strip_pipeline_artifacts_recovers_and_drops() -> None:
    # filled wrapper: strip tags, keep the value
    assert strip_pipeline_artifacts("pipe DN <protected_token>50 mm</protected_token> long") == "pipe DN 50 mm long"
    # multiple wrappers in one string
    assert (
        strip_pipeline_artifacts("<protected_token>A</protected_token>-<protected_token>B</protected_token>") == "A-B"
    )
    # empty wrapper lost its value -> unusable, fall back to source
    assert strip_pipeline_artifacts("diameter <protected_token></protected_token> mm") is None
    # unbalanced / unclosed tag -> residue remains -> drop
    assert strip_pipeline_artifacts("diameter <protected_token> mm") is None
    # unrestored curly substitution -> drop
    assert strip_pipeline_artifacts("wall {protected_tokens[0]} thick") is None
    assert strip_pipeline_artifacts("mesh {protected_tokens}") is None
    # clean text and empty/None pass through untouched
    assert strip_pipeline_artifacts("concrete C25/30 wall 24 cm") == "concrete C25/30 wall 24 cm"
    assert strip_pipeline_artifacts("") == ""
    assert strip_pipeline_artifacts(None) is None


def test_build_translation_map_uses_only_approved_statuses() -> None:
    translations = pd.DataFrame(
        [
            {
                "tm_key": "k1",
                "target_lang": "de",
                "target_text": "Beton",
                "status": "reviewed",
            },
            {
                "tm_key": "k2",
                "target_lang": "de",
                "target_text": "Bad",
                "status": "draft",
            },
        ]
    )
    mapping = build_translation_map(translations, "de")
    assert mapping == {"k1": "Beton"}


def test_materialize_region_preserves_empty_sources_and_identity_columns(tmp_path, monkeypatch) -> None:
    source = pd.DataFrame(
        [
            {
                "rate_code": "A",
                "resource_code": "",
                "is_scope": True,
                "rate_original_name": "Beton C25/30",
                "rate_final_name": "Beton C25/30",
                "resource_name": None,
            },
            {
                "rate_code": "A",
                "resource_code": "R1",
                "is_scope": False,
                "rate_original_name": "Beton C25/30",
                "rate_final_name": "Beton C25/30",
                "resource_name": "Yükleyici (100 HP)",
            },
        ]
    )
    source_path = tmp_path / "TR_workitems_costs_resources_DDC_CWICR.parquet"
    source.to_parquet(source_path, index=False)
    mapping = {
        tm_key("tr", "Beton C25/30"): "Concrete C25/30",
        tm_key("tr", "Yükleyici (100 HP)"): "Loader (100 HP)",
    }
    out = materialize_region(
        source_path=source_path,
        region="TR",
        target_lang="en",
        translation_by_key=mapping,
        translations_path=tmp_path / "en_all_sources_translations.parquet",
        out_dir=tmp_path / "out",
    )
    localized = pd.read_parquet(out)
    assert localized.loc[0, "rate_code"] == "A"
    assert pd.isna(localized.loc[0, "resource_name"])
    assert localized.loc[1, "resource_name"] == "Loader (100 HP)"
    assert localized.loc[1, "rate_final_name"] == "Concrete C25/30"
    # a translation for this text exists in the mapping, but rate_original_name is
    # preserved regardless so the native source stays traceable
    assert localized.loc[1, "rate_original_name"] == "Beton C25/30"
    report = json.loads(out.with_suffix(".qa.json").read_text(encoding="utf-8"))
    assert report["status_counts"]["translated"] == 3


def test_materialize_region_blocks_missing_translation(tmp_path) -> None:
    source = pd.DataFrame(
        [
            {
                "rate_code": "A",
                "resource_code": "",
                "is_scope": True,
                "rate_final_name": "Beton C25/30",
            }
        ]
    )
    source_path = tmp_path / "TR_workitems_costs_resources_DDC_CWICR.parquet"
    source.to_parquet(source_path, index=False)
    try:
        materialize_region(
            source_path=source_path,
            region="TR",
            target_lang="en",
            translation_by_key={},
            translations_path=tmp_path / "en_all_sources_translations.parquet",
            out_dir=tmp_path / "out",
        )
    except ValueError as exc:
        assert "missing_approved_translation" in str(exc)
    else:
        raise AssertionError("missing translation did not block materialization")


def test_materialize_region_can_apply_us_customary_units(tmp_path) -> None:
    source = pd.DataFrame(
        [
            {
                "rate_code": "A",
                "resource_code": "R1",
                "is_scope": False,
                "rate_original_name": "Tubo",
                "resource_unit": "m",
                "resource_quantity": 2.0,
                "resource_price_per_unit_current": 10.0,
                "resource_cost": 20.0,
            }
        ]
    )
    source_path = tmp_path / "TR_workitems_costs_resources_DDC_CWICR.parquet"
    source.to_parquet(source_path, index=False)
    out = materialize_region(
        source_path=source_path,
        region="TR",
        target_lang="en",
        target_region="USA_USD",
        unit_system="us_customary",
        translation_by_key={tm_key("tr", "Tubo"): "Pipe", tm_key("tr", "m"): "m"},
        translations_path=tmp_path / "en_all_sources_translations.parquet",
        out_dir=tmp_path / "out",
    )
    localized = pd.read_parquet(out)
    assert localized.loc[0, "resource_unit"] == "LF"
    assert localized.loc[0, "resource_unit_metric"] == "m"
    assert localized.loc[0, "unit_system"] == "us_customary"
    assert localized.loc[0, "resource_cost"] == 20.0
    report = json.loads(out.with_suffix(".qa.json").read_text(encoding="utf-8"))
    assert report["unit_localization"]["resource_unit_converted_rows"] == 1


def test_apply_ppp_conversion_multiplies_money_columns_only() -> None:
    source = pd.DataFrame(
        [
            {
                "resource_quantity": 4.0,
                "resource_price_per_unit_current": 10.0,
                "resource_cost": 40.0,
                "labor_rate_per_hr": 30.0,
                "currency": "TRY",
                "source_year": 2026,
            }
        ]
    )
    converted, report = apply_ppp_conversion(
        source,
        ppp_row={
            "source_region": "TR",
            "target_catalogue_region": "USA_USD",
            "source_currency": "TRY",
            "target_currency": "USD",
            "price_multiplier": 0.5,
            "formula": "test_multiplier",
            "ppp_provider": "test",
            "ppp_indicator": "TEST",
            "source_ppp_year": 2025,
            "target_ppp_year": 2025,
        },
    )

    assert converted.loc[0, "resource_quantity"] == 4.0
    assert converted.loc[0, "source_year"] == 2026
    assert converted.loc[0, "resource_price_per_unit_current"] == 5.0
    assert converted.loc[0, "resource_cost"] == 20.0
    assert converted.loc[0, "labor_rate_per_hr"] == 15.0
    assert converted.loc[0, "currency"] == "USD"
    assert converted.loc[0, "currency_source"] == "TRY"
    assert converted.loc[0, "resource_cost_source_currency"] == 40.0
    assert converted.loc[0, "ppp_target_currency"] == "USD"
    assert report["converted_columns"] == [
        "resource_price_per_unit_current",
        "resource_cost",
        "labor_rate_per_hr",
    ]


def test_materialize_region_never_translates_rate_original_name(tmp_path) -> None:
    # rate_original_name carries the native text of the national cost book. Every
    # localized output must keep it verbatim; only rate_final_name gets translated.
    # Without this guard the Italy variants shipped with rate_original_name holding
    # the target language ("Gegenstand: ABRISS", "항목: 철거") instead of Italian,
    # which loses the source text irrecoverably.
    source = pd.DataFrame(
        [
            {
                "rate_code": "A",
                "resource_code": "",
                "is_scope": True,
                "rate_original_name": "Oggetto: DEMOLIZIONE",
                "rate_final_name": "Item: Demolition",
                "resource_name": None,
            },
        ]
    )
    source_path = tmp_path / "IT_TOSCANA_workitems_costs_resources_DDC_CWICR.parquet"
    source.to_parquet(source_path, index=False)
    # a translation exists for the native text, so an unguarded run WOULD overwrite it
    mapping = {
        tm_key("it", "Oggetto: DEMOLIZIONE"): "Gegenstand: ABRISS",
        tm_key("it", "Item: Demolition"): "Position: Abbruch",
    }
    out = materialize_region(
        source_path=source_path,
        region="IT_TOSCANA",
        target_lang="de",
        translation_by_key=mapping,
        translations_path=tmp_path / "de_all_sources_translations.parquet",
        out_dir=tmp_path / "out",
        allow_missing=True,
    )
    localized = pd.read_parquet(out)
    assert localized.loc[0, "rate_original_name"] == "Oggetto: DEMOLIZIONE"
    assert localized.loc[0, "rate_final_name"] == "Position: Abbruch"


# Columns a native edition must return untouched. Spread across the free-text content
# columns a user reads plus one controlled column, so the test fails on any subset of the
# original damage rather than only on the worst column.
_NATIVE_CONTENT_COLUMNS = [
    "rate_original_name",
    "rate_final_name",
    "resource_name",
    "work_composition_text",
    "labor_title",
    "section_name",
    "collection_name",
]
# Deliberately NOT in the list above. row_type is a controlled column: the Turkish base holds
# our own English canon there, so the right value for a native edition is a translation, not
# the source. Today it is preserved and comes out English, which is a known gap the identity
# branch does not close. Asserted separately below so that fixing it moves one labelled line
# instead of quietly weakening the content-column check.
_NATIVE_CONTROLLED_COLUMNS = ["row_type"]


def _turkish_source_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "rate_code": "15.185.1001",
                "resource_code": "10.100.1001",
                "is_scope": False,
                "rate_original_name": "Beton santralinde üretilen C 20/25 beton",
                "rate_final_name": "Beton santralinde üretilen C 20/25 beton",
                "resource_name": "Düz işçi",
                "work_composition_text": "Kalıp yapılması ve sökülmesi",
                "labor_title": "Operatör makinist",
                "section_name": "İnşaat işleri",
                "collection_name": "Çevre ve Şehircilik Bakanlığı",
                "row_type": "Labour",
            },
        ]
    )


def test_native_edition_returns_content_columns_unchanged(tmp_path) -> None:
    # The Turkish edition of the Turkish base shipped with its content columns in English:
    # "Düz işçi" was published as "General laborer". The target language IS the source
    # language there, so the correct behaviour is to translate nothing, and a lookup keyed on
    # the source language can only ever return the same string or a corruption.
    #
    # The mapping below is the corruption, taken from the map that produced the published
    # file, so this test fails against the pre-fix code rather than merely describing it.
    source = _turkish_source_frame()
    source_path = tmp_path / "TR_workitems_costs_resources_DDC_CWICR.parquet"
    source.to_parquet(source_path, index=False)
    mapping = {
        tm_key("tr", "Düz işçi"): "General laborer",
        tm_key("tr", "Operatör makinist"): "Operator machinist",
        tm_key("tr", "Kalıp yapılması ve sökülmesi"): "Formwork erection and dismantling",
        tm_key("tr", "İnşaat işleri"): "Construction work",
        tm_key("tr", "Çevre ve Şehircilik Bakanlığı"): "Ministry of Environment and Urbanisation",
        tm_key("tr", "Beton santralinde üretilen C 20/25 beton"): "C 20/25 concrete from a batching plant",
        tm_key("tr", "Labour"): "Labour",
    }
    out = materialize_region(
        source_path=source_path,
        region="TR",
        target_lang="tr",
        translation_by_key=mapping,
        translations_path=tmp_path / "tr_all_sources_translations.parquet",
        out_dir=tmp_path / "out",
        allow_missing=True,
    )
    localized = pd.read_parquet(out)
    for column in _NATIVE_CONTENT_COLUMNS:
        assert localized.loc[0, column] == source.loc[0, column], (
            f"native edition rewrote {column}: {source.loc[0, column]!r} -> {localized.loc[0, column]!r}"
        )
    # Current behaviour on the controlled columns, pinned so a change to it is visible rather
    # than incidental. `Labour` is the English canon sitting in the Turkish base; the file a
    # user has today reads `İşçilik` here, so this is the gap and not the goal.
    for column in _NATIVE_CONTROLLED_COLUMNS:
        assert localized.loc[0, column] == source.loc[0, column]
    summary = json.loads(localized.loc[0, "translation_status_summary"])
    assert summary["identity_edition"] is True
    assert summary["translated"] == 0


def test_non_native_edition_still_translates_the_same_columns(tmp_path) -> None:
    # The guard above must be a branch on target_lang == source_lang, not a wider
    # PRESERVE_SOURCE_COLUMNS. Widening that set would freeze these columns in the 25 genuine
    # translations too, where translating them is the entire point. Same fixture, same
    # mapping, different target language: everything except rate_original_name must change.
    source = _turkish_source_frame()
    source_path = tmp_path / "TR_workitems_costs_resources_DDC_CWICR.parquet"
    source.to_parquet(source_path, index=False)
    mapping = {
        tm_key("tr", "Düz işçi"): "General laborer",
        tm_key("tr", "Operatör makinist"): "Operator machinist",
        tm_key("tr", "Kalıp yapılması ve sökülmesi"): "Formwork erection and dismantling",
        tm_key("tr", "İnşaat işleri"): "Construction work",
        tm_key("tr", "Çevre ve Şehircilik Bakanlığı"): "Ministry of Environment and Urbanisation",
        tm_key("tr", "Beton santralinde üretilen C 20/25 beton"): "C 20/25 concrete from a batching plant",
        tm_key("tr", "Labour"): "Labour",
    }
    out = materialize_region(
        source_path=source_path,
        region="TR",
        target_lang="en",
        translation_by_key=mapping,
        translations_path=tmp_path / "en_all_sources_translations.parquet",
        out_dir=tmp_path / "out",
        allow_missing=True,
    )
    localized = pd.read_parquet(out)
    assert localized.loc[0, "resource_name"] == "General laborer"
    assert localized.loc[0, "labor_title"] == "Operator machinist"
    assert localized.loc[0, "work_composition_text"] == "Formwork erection and dismantling"
    assert localized.loc[0, "section_name"] == "Construction work"
    assert localized.loc[0, "rate_final_name"] == "C 20/25 concrete from a batching plant"
    # the one column that is preserved in every locale, native or not
    assert localized.loc[0, "rate_original_name"] == "Beton santralinde üretilen C 20/25 beton"
    summary = json.loads(localized.loc[0, "translation_status_summary"])
    assert summary["identity_edition"] is False


def test_qa_report_names_the_translation_table_that_was_consumed(tmp_path) -> None:
    # Diagnosing the Turkish edition took a day because no artifact recorded which table the
    # text had resolved against. The three provenance columns could not have told anyone:
    # locale_language and translation_source_lang both read "tr" and both were correct, while
    # the values had come from the English table. The QA sidecar has to carry the table's
    # identity for that question to be answerable by reading rather than by inference.
    source = _turkish_source_frame()
    source_path = tmp_path / "TR_workitems_costs_resources_DDC_CWICR.parquet"
    source.to_parquet(source_path, index=False)
    wrong_table = tmp_path / "en_all_sources_translations.parquet"
    out = materialize_region(
        source_path=source_path,
        region="TR",
        target_lang="tr",
        translation_by_key={tm_key("tr", "Düz işçi"): "General laborer"},
        out_dir=tmp_path / "out",
        allow_missing=True,
        translations_path=wrong_table,
    )
    report = json.loads(out.with_suffix(".qa.json").read_text(encoding="utf-8"))
    assert report["translation_map"]["path"] == str(wrong_table)
    assert report["translation_map"]["entries"] == 1
