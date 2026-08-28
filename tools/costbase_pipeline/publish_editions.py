"""Build a published language edition from a base and a materialized translation.

This step had no script. It was reconstructed from the artifacts on 2026-08-01, by diffing
all eight canonical bases against the 208 published editions, and every rule below is a
measurement rather than a recollection. What the published files actually are:

  * the BASE's schema, all 95 columns, in the base's own column order
  * the base's numbers, untouched -- no PPP conversion and no unit localization ever reached
    a published edition. Checked on 24 editions across all 8 bases: zero numeric columns
    differ from base. Turkish rates stay in TRY at 206.25 in the German edition; the
    generator's DE_BERLIN output converts the same row to 9.04 EUR
  * free text replaced from the materialized edition
  * controlled labels replaced from a recovered vocabulary, see extract_controlled_vocabulary

So a published edition is the base with its text swapped, NOT a copy of anything under
translated_outputs/. The generator writes one file per economy (48 of them) carrying 124
columns including the PPP audit trail; publication takes text from it and keeps the base for
everything else. That is why the provenance columns never appeared in a published file: they
are written by the generator, and this step starts from the base, which has no such column.

The invariants at the end are the point of the whole file. The defect that reached a Turkish
user, a native edition shipping English content, is invisible to a schema check and to a row
count, but `assert_publishable` refuses a frame whose numbers moved or whose native edition
lost its own language.

Writes nothing by default beyond --out-root, and refuses to write inside a git working tree
unless explicitly allowed, because publication of cost data is a human decision.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from extract_controlled_vocabulary import CONTROLLED_COLUMNS
from extract_translation_corpus import SOURCE_LANG_BY_REGION, TEXT_COLUMNS

PIPELINE_DIR = Path(__file__).resolve().parent
DEFAULT_VOCABULARY = PIPELINE_DIR / "outputs" / "controlled_vocabulary.json"

# Region -> the human-readable folder it publishes into. Read off the published tree; there is
# no algorithm behind these names, they are editorial.
PUBLISHED_FOLDER = {
    "BR": "SouthAmerica-Brazil-SINAPI",
    "ES_ANDALUCIA": "Europe-Spain-BCCA",
    "GR": "Europe-Greece-GGDE",
    "ID": "Asia-Indonesia-AHSP",
    "IT_TOSCANA": "Europe-Italy-Prezzario-Toscana",
    "TR": "Europe-Turkey-Birim-Fiyat",
    "VN": "Asia-Vietnam-Dinh-Muc",
    "ZH_CHINA": "Asia-China-Dinge",
}

# Scripts used to tell "is this text still in the source language" apart from "was it
# replaced by English". Only bases whose language has a distinctive script can be checked
# this way; Indonesian is Latin with no diacritics and is deliberately absent.
NATIVE_SCRIPT = {
    "tr": r"[çğıöşüÇĞİÖŞÜ]",
    "es": r"[áéíóúñÁÉÍÓÚÑ]",
    "pt": r"[ãõáéíóúçÃÕÁÉÍÓÚÇ]",
    "vi": r"[ăâđêôơưạảấầẩậắằẳặẹẻẽếềểệỉịọỏốồổộớờởợụủứừửữựỳỵỷỹ]",
    "it": r"[àèéìòùÀÈÉÌÒÙ]",
    "el": r"[α-ωΑ-Ω]",
    "zh": r"[一-鿿]",
}


def published_path(out_root: Path, region: str, lang: str) -> Path:
    """Where a (region, lang) edition goes. Note the economy is NOT in the name."""
    folder = PUBLISHED_FOLDER.get(region)
    if folder is None:
        raise ValueError(f"no published folder is recorded for region {region!r}")
    return (
        out_root
        / folder
        / f"{lang.upper()}___DDC_CWICR"
        / f"{region}_{lang}_workitems_costs_resources_DDC_CWICR.parquet"
    )


def native_script_share(series: pd.Series, lang: str) -> float | None:
    """Share of non-empty rows carrying a character of `lang`'s script, or None if untestable."""
    pattern = NATIVE_SCRIPT.get(lang)
    if pattern is None:
        return None
    text = series.fillna("").astype(str)
    populated = int((text.str.strip() != "").sum())
    if not populated:
        return None
    return float(text.str.contains(pattern, regex=True).sum()) / populated * 100


def load_vocabulary(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"controlled vocabulary not found at {path}; run extract_controlled_vocabulary first")
    return json.loads(path.read_text(encoding="utf-8"))["vocabulary"]


def build_edition(
    base: pd.DataFrame,
    translated: pd.DataFrame,
    *,
    lang: str,
    vocabulary: dict,
) -> tuple[pd.DataFrame, dict]:
    """Base, with free text taken from `translated` and controlled labels mapped."""
    if len(base) != len(translated):
        raise ValueError(f"row counts differ: base {len(base)}, translated {len(translated)}")
    edition = base.copy(deep=True)
    report: dict = {"free_text": {}, "controlled": {}, "unmapped_labels": {}}

    for column in TEXT_COLUMNS:
        if column in CONTROLLED_COLUMNS or column not in base.columns or column not in translated.columns:
            continue
        source, target = base[column], translated[column]
        # An empty translated cell is a lost value, not a translation of an empty one, so the
        # base text is kept. This is the guard that stopped 509 populated Turkish cells being
        # blanked during the manual repair.
        keep = target.fillna("").astype(str).str.strip().eq("") & source.fillna("").astype(str).str.strip().ne("")
        edition[column] = target.where(~keep, source)
        report["free_text"][column] = int(
            (source.fillna("~").astype(str) != edition[column].fillna("~").astype(str)).sum()
        )

    for column in CONTROLLED_COLUMNS:
        if column not in base.columns:
            continue
        mapping = vocabulary.get(column, {}).get(lang, {})
        source = base[column].fillna("").astype(str).str.strip()
        mapped = source.map(mapping)
        edition[column] = mapped.where(mapped.notna(), base[column])
        report["controlled"][column] = int(mapped.notna().sum())
        missing = sorted(set(source[mapped.isna() & source.ne("")]))
        if missing:
            # A label with no entry stays English. Recorded, never silently accepted: this is
            # exactly the shape of the defect that shipped, and 46 row_type labels are
            # currently unmapped because editions of the same language disagree on them.
            report["unmapped_labels"][column] = missing[:20]
    return edition, report


def assert_publishable(base: pd.DataFrame, edition: pd.DataFrame, *, region: str, lang: str) -> dict:
    """Refuse anything that is not a text-only overlay, and catch the native-edition defect.

    Three separate laws, because the incident passed two checks that looked like three:
    schema was right, row count was right, and the text was English.
    """
    if list(base.columns) != list(edition.columns):
        raise ValueError("column set or order differs from the base")
    if len(base) != len(edition):
        raise ValueError(f"row count changed: {len(base)} -> {len(edition)}")

    moved = [c for c in base.columns if pd.api.types.is_numeric_dtype(base[c]) and not base[c].equals(edition[c])]
    if moved:
        raise ValueError(f"publication must not touch numbers, but these moved: {moved}")

    checks: dict = {
        "numeric_columns_unchanged": True,
        "native_edition": False,
        "script_share": {},
    }
    if lang != SOURCE_LANG_BY_REGION.get(region):
        return checks

    # Native edition: the target language IS the base's language, so every content column must
    # come back at least as native as the base. A drop means the source text was replaced,
    # which is what shipped to Turkey.
    checks["native_edition"] = True
    lost = []
    for column in TEXT_COLUMNS:
        if column in CONTROLLED_COLUMNS or column not in base.columns:
            continue
        before, after = (
            native_script_share(base[column], lang),
            native_script_share(edition[column], lang),
        )
        if before is None or after is None:
            continue
        checks["script_share"][column] = {
            "base": round(before, 1),
            "edition": round(after, 1),
        }
        if after < before - 1.0:
            lost.append(f"{column} {before:.1f}% -> {after:.1f}%")
    if lost:
        raise ValueError(f"native edition {region}_{lang} lost its own language in: {'; '.join(lost)}")
    return checks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clone", type=Path, required=True, help="published clone, read for the base")
    parser.add_argument("--region", required=True)
    parser.add_argument("--lang", required=True)
    parser.add_argument(
        "--translated",
        type=Path,
        required=True,
        help="materialized edition supplying free text",
    )
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--vocabulary", type=Path, default=DEFAULT_VOCABULARY)
    parser.add_argument(
        "--allow-writing-into-a-repository",
        action="store_true",
        help="permit --out-root inside a git working tree; publication is a human decision",
    )
    args = parser.parse_args()

    if not args.allow_writing_into_a_repository:
        probe = args.out_root.resolve()
        for directory in [probe, *probe.parents]:
            if (directory / ".git").exists():
                raise SystemExit(
                    f"refusing to write into the git working tree at {directory}. "
                    "Publication of cost data is a human decision; pass "
                    "--allow-writing-into-a-repository if that decision has been made."
                )

    base_path = (
        args.clone / PUBLISHED_FOLDER[args.region] / f"{args.region}_workitems_costs_resources_DDC_CWICR.parquet"
    )
    base = pd.read_parquet(base_path)
    translated = pd.read_parquet(args.translated)
    vocabulary = load_vocabulary(args.vocabulary)

    edition, report = build_edition(base, translated, lang=args.lang, vocabulary=vocabulary)
    checks = assert_publishable(base, edition, region=args.region, lang=args.lang)

    out_path = published_path(args.out_root, args.region, args.lang)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    edition.to_parquet(out_path, index=False)
    print(
        json.dumps(
            {
                "region": args.region,
                "lang": args.lang,
                "rows": int(len(edition)),
                "base": str(base_path),
                "free_text_source": str(args.translated),
                "cells_rewritten": report,
                "checks": checks,
                "out": str(out_path),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
