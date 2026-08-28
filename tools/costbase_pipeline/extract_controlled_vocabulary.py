"""Recover the controlled-vocabulary mapping from the published editions.

Some columns hold a closed vocabulary that the canonical bases store as an English label:
`category_type` is `CONSTRUCTION WORK`, `row_type` is `Labour`. Every published edition
carries those labels rendered into its own language (`BAUARBEIT`, `İşçilik`), and nothing in
this repository can reproduce that. The translation tables cannot: tm_key("tr", "Labour") and
tm_key("en", "Labour") both miss, and the Turkish words that do appear in the tr table are
keyed from vi/el/es/id sources, so they are translations of other bases' native vocabularies
rather than of our English canon. materialize_localized_outputs leaves these columns at the
base value.

So the mapping exists in exactly one place: inside the artifacts we already shipped. This
reads it back out and writes it down, which turns an undocumented transformation into a
checked-in table. It is a recovery, not a translation. Nothing here invents a value; every
pair is observed in a published file, and a label that two editions of the same language
disagree about is reported rather than resolved.

Read-only against the published clone. Writes one JSON to outputs/.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

PIPELINE_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = PIPELINE_DIR / "outputs" / "controlled_vocabulary.json"

# Columns whose value is a label from a closed set rather than text from the national book.
# Established by diffing every base against its published editions: these are the columns the
# publication step rewrites that the free-text corpus (TEXT_COLUMNS) does not cover, plus
# `row_type`, which is in TEXT_COLUMNS but is controlled by value in every base except the
# Chinese one. The class marker is the value, not the column name -- the Chinese base stores
# native text in these columns, so its rows contribute a zh mapping rather than an English one.
CONTROLLED_COLUMNS = (
    "row_type",
    "category_type",
    "department_type",
    "section_type",
    "personnel_operator_grade",
)


def _norm(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def harvest(clone: Path, columns: tuple[str, ...] = CONTROLLED_COLUMNS) -> dict:
    """Walk every published edition and collect base label -> target label per language."""
    # pairs[column][lang][source_label] -> {target_label: [regions that show it]}
    pairs: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(list))))
    editions = 0
    for base_path in sorted(clone.glob("*/[A-Z]*_workitems_costs_resources_DDC_CWICR.parquet")):
        region = base_path.name.split("_workitems")[0]
        base = pd.read_parquet(base_path)
        present = [c for c in columns if c in base.columns]
        if not present:
            continue
        for edition in sorted(base_path.parent.glob(f"*___DDC_CWICR/{region}_*_workitems_*.parquet")):
            if ".bak" in edition.name:
                continue
            lang = edition.name[len(region) + 1 :].split("_workitems")[0]
            frame = pd.read_parquet(edition, columns=present)
            editions += 1
            for column in present:
                src, tgt = _norm(base[column]), _norm(frame[column])
                # value_counts over the pair is far cheaper than iterating 110k rows, and a
                # closed vocabulary collapses to a handful of distinct pairs per column.
                for (s, t), _ in pd.Series(list(zip(src, tgt, strict=False))).value_counts().items():
                    if not s or not t:
                        continue
                    pairs[column][lang][s][t].append(region)
    return pairs, editions


def resolve(pairs: dict) -> tuple[dict, list[dict]]:
    """Collapse to one target per (column, lang, source), reporting every disagreement."""
    table: dict = {}
    conflicts: list[dict] = []
    for column, by_lang in pairs.items():
        for lang, by_source in by_lang.items():
            for source, targets in by_source.items():
                if len(targets) > 1:
                    # Two editions rendered the same label differently. Recording a winner
                    # here would be the guess this script exists to avoid, so the pair is
                    # left out of the table and surfaced for a human to settle.
                    conflicts.append(
                        {
                            "column": column,
                            "lang": lang,
                            "source": source,
                            "targets": {t: sorted(set(r)) for t, r in targets.items()},
                        }
                    )
                    continue
                target = next(iter(targets))
                table.setdefault(column, {}).setdefault(lang, {})[source] = target
    return table, conflicts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clone", type=Path, required=True, help="published repository clone")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    pairs, editions = harvest(args.clone)
    table, conflicts = resolve(pairs)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "_source": "recovered from published editions; see extract_controlled_vocabulary.py",
                "_editions_read": editions,
                "conflicts": conflicts,
                "vocabulary": table,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    summary = {
        "editions_read": editions,
        "columns": {c: {"languages": len(v), "labels": len(next(iter(v.values())))} for c, v in table.items()},
        "conflicts": len(conflicts),
        "out": str(args.out),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
