"""Tests for the reconstructed publication step.

The incident these guard against: a native edition shipped with its content columns in
English, and every check that ran on it was green. Schema was right, row count was right,
the numbers reconciled. Nothing asked whether the Turkish edition was still Turkish.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from publish_editions import (
    assert_publishable,
    build_edition,
    native_script_share,
    published_path,
)


def _turkish_base() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "rate_code": ["15.100.1001", "15.100.1002"],
            "rate_original_name": [
                "Beton santralinde üretilen C 20/25 beton",
                "Kalıp yapılması",
            ],
            "resource_name": ["Düz işçi", "Operatör makinist"],
            "work_composition_text": [
                "Kalıp yapılması ve sökülmesi",
                "Beton dökülmesi",
            ],
            "row_type": ["Labour", "Machinery"],
            "category_type": ["CONSTRUCTION WORK", "CONSTRUCTION WORK"],
            "total_cost_per_position": [206.25, 30.63],
            "resource_quantity": [1.5, 2.0],
        }
    )


def _vocabulary() -> dict:
    return {
        "row_type": {"tr": {"Labour": "İşçilik", "Machinery": "Makine"}},
        "category_type": {"tr": {"CONSTRUCTION WORK": "İNŞAAT ÇALIŞMASI"}},
    }


def test_native_edition_that_lost_its_language_is_refused() -> None:
    # The published Turkish edition, reduced to two rows. This is what shipped: the numbers
    # are right, the schema is right, and `Düz işçi` reads `Laborer`.
    base = _turkish_base()
    english = base.copy()
    english["resource_name"] = ["Laborer", "Operator machinist"]
    english["work_composition_text"] = [
        "Formwork erection and dismantling",
        "Concrete pouring",
    ]
    english["rate_original_name"] = [
        "C 20/25 concrete from a batching plant",
        "Formwork",
    ]

    with pytest.raises(ValueError, match="lost its own language"):
        assert_publishable(base, english, region="TR", lang="tr")


def test_native_edition_that_kept_its_language_passes() -> None:
    base = _turkish_base()
    edition, _ = build_edition(base, base, lang="tr", vocabulary=_vocabulary())
    checks = assert_publishable(base, edition, region="TR", lang="tr")
    assert checks["native_edition"] is True
    # controlled labels are the one thing a native edition SHOULD change: the base holds our
    # own English canon there, so preserving them would ship `Labour` to a Turkish reader
    assert list(edition["row_type"]) == ["İşçilik", "Makine"]
    assert list(edition["category_type"]) == ["İNŞAAT ÇALIŞMASI", "İNŞAAT ÇALIŞMASI"]
    # ... while the free text is untouched, because it is already Turkish
    assert list(edition["resource_name"]) == ["Düz işçi", "Operatör makinist"]


def test_a_foreign_edition_is_not_held_to_the_native_rule() -> None:
    # The German edition of the Turkish base is SUPPOSED to stop being Turkish. Applying the
    # native check here would reject every genuine translation we publish.
    base = _turkish_base()
    german = base.copy()
    german["resource_name"] = ["Hilfsarbeiter", "Maschinist"]
    checks = assert_publishable(base, german, region="TR", lang="de")
    assert checks["native_edition"] is False


def test_publication_may_not_move_a_number() -> None:
    # PPP conversion reaches translated_outputs but has never reached a published edition.
    # A published file carries the national book's own prices, so a moved number means the
    # wrong artifact was picked up.
    base = _turkish_base()
    converted = base.copy()
    converted["total_cost_per_position"] = [9.04, 1.34]
    with pytest.raises(ValueError, match="must not touch numbers"):
        assert_publishable(base, converted, region="TR", lang="de")


def test_an_empty_translation_never_blanks_a_populated_cell() -> None:
    base = _turkish_base()
    lossy = base.copy()
    lossy.loc[0, "resource_name"] = ""
    edition, _ = build_edition(base, lossy, lang="tr", vocabulary=_vocabulary())
    assert edition.loc[0, "resource_name"] == "Düz işçi"


def test_an_unmapped_controlled_label_is_reported_not_swallowed() -> None:
    # 46 row_type labels are unmapped today because editions of the same language disagree on
    # them. They fall back to the English canon, which is wrong but visible; silently
    # accepting them is how the original defect stayed hidden.
    base = _turkish_base()
    partial = {"row_type": {"tr": {"Labour": "İşçilik"}}}
    edition, report = build_edition(base, base, lang="tr", vocabulary=partial)
    assert edition.loc[1, "row_type"] == "Machinery"
    assert report["unmapped_labels"]["row_type"] == ["Machinery"]


def test_published_path_drops_the_economy_from_the_name() -> None:
    # The generator writes one file per economy, 48 of them. Publication keeps one per
    # language and the economy disappears, which is why no economy output matches a
    # published file byte for byte.
    path = published_path(Path("/out"), "TR", "tr")
    assert path.as_posix().endswith(
        "Europe-Turkey-Birim-Fiyat/TR___DDC_CWICR/TR_tr_workitems_costs_resources_DDC_CWICR.parquet"
    )


def test_script_share_is_none_where_it_cannot_decide() -> None:
    # Indonesian is Latin with no distinctive diacritic, so the native check must abstain
    # rather than return a confident zero.
    assert native_script_share(pd.Series(["Pekerja bangunan"]), "id") is None
    assert native_script_share(pd.Series(["Düz işçi"]), "tr") == 100.0
