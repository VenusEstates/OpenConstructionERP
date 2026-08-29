#!/usr/bin/env python3
"""Case module chip guard: the module name on a case step, in every language.

Every step of a case playbook names the module it runs in, and that name is
drawn twice:

    moduleLabel: "Payment Clock",
    moduleLabelKey: "nav.payment_clock",

The label is the English fallback and the key is what everybody else reads.
All four places that render a chip resolve it the same way - CasesPage,
ModuleHive, CaseConstellation and ProjectJourney all write

    m.labelKey ? t(m.labelKey, { defaultValue: m.label }) : m.label

so i18next reaches the label only when the key resolves nowhere. A key no
locale can answer therefore renders the English label to that language with
nothing on screen to say so, which is the failure the sibling guards in this
directory exist to stop. Neither of them can see it here.

check_i18n_orphan_keys.py needs a quoted literal at the call site, and the
key at these call sites arrives as a variable off a data row. That is the
boundary of what it can resolve, not a bug in it.

check_i18n_computed_keys.py owns exactly that shape, and its own docstring
names `t(item.labelKey, { defaultValue: item.defaultLabel })` as the reason
it exists. It resolves a key/default pair out of a table by anchoring on the
English field and requiring it to be named default-something - defaultLabel,
defaultName, defaultDesc, defaultText, defaultTitle or defaultHelp - with the
key field found nearby. This table names its English `moduleLabel`, which is
a seventh name, so no pair forms and all 842 chips fall out of the scan. The
guard reports "259 resolvable key/default pair(s)" and not one of them is a
module chip. Its comment predicted this exactly: a guard keyed to one name
would call the class clean while its siblings went unread. Widening that
anchor there is not the repair, because the same widening would pull in the
6277 `cases.<slug>.*` content keys, and those keep their English in the data
file on purpose and are absent from en.ts by design, so that guard's en.ts
question is the wrong question to ask about them. This script asks the one
question those keys do not raise and the chips do.

Presence, never sameness. A chip key is checked for being answerable, not
for reading differently from English: nav.bim is "BIM" in all 41 non-English
locales and nav.crm is "CRM" in 31 of them, both correct and both permanent
findings for any check that compares rendered text against the English. What
a translator has not been asked for is decidable; what a translator decided
to leave alone is not.

A regional variant carries only the words it changes, so es-MX, es-CL, es-CO,
pt-BR and en-US are covered when their base language answers the key, the
same rule the orphan guard applies to a literal. Plural forms are not
consulted: a module name is never a counted string, so the bare key is the
whole question here.

There is no baseline, and there is nothing for one to hold: all 137 distinct
chip keys are answered by all 43 locale files. The three this script was
written to surface, nav.payment_clock, nav.tax_withholding and
nav.einvoice_clearance, have since been translated. If a gap ever has to
ship, the entry belongs in the same shape the sibling baselines use, key to
the set of locales that cannot answer it, and it may only shrink.

The step is wired into repo-hygiene.yml, added once those three went green so
that it started green and every later failure means a chip really did
regress. A blocking lane that stops work it was not written to stop gets
switched off by whoever it inconveniences, which is why the order was that
way round and not the other.

Parser desync is a failure, not a pass, on the same reasoning as the sibling
guards: no playbooks, no chips or no locale keys exits 2 rather than
reporting a clean scan of nothing.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from collections import defaultdict

DEFAULT_DATA_GLOB = "frontend/src/features/cases/data/*.playbook.ts"
DEFAULT_LOCALE_GLOB = "frontend/src/app/locales/*.ts"

# `moduleLabel: "..."` and `moduleLabelKey: "..."`, each on its own line, the
# shape every playbook is written in. Both quote styles are accepted because a
# hand-edited file is the likely source of a new chip and the count tripwire
# below cannot tell a missing chip from an unparsed one.
_LABEL = re.compile(r"""^\s*moduleLabel:\s*(["'])(.*?)\1""")
_LABEL_KEY = re.compile(r"""^\s*moduleLabelKey:\s*(["'])(.*?)\1""")

# `"key": ` at the head of a line, the shape the locale files are generated in.
_KEY_LINE = re.compile(r'^\s*"([A-Za-z0-9_.\-]+)"\s*:', re.MULTILINE)


class Chip:
    """One module chip: where it is written and what it names."""

    def __init__(self, path: str, line: int, label: str, key: str | None) -> None:
        self.path = path
        self.line = line
        self.label = label
        self.key = key


def read_chips(data_glob: str) -> list[Chip]:
    """Every module chip in the case playbooks, in file order.

    The key is read from the line after the label, which is how all 842 chips
    in the tree are written. A chip whose key is missing is not skipped; it is
    carried with key None and reported, because a chip with no key at all is
    the same English-forever failure arriving one step earlier.
    """
    chips: list[Chip] = []
    for path in sorted(glob.glob(data_glob)):
        posix = path.replace(os.sep, "/")
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        for i, line in enumerate(lines):
            label = _LABEL.match(line)
            if not label:
                continue
            following = lines[i + 1] if i + 1 < len(lines) else ""
            key = _LABEL_KEY.match(following)
            chips.append(Chip(posix, i + 1, label.group(2), key.group(2) if key else None))
    return chips


def _base_of(stem: str, by_locale: dict[str, set[str]]) -> str | None:
    """The language file a regional variant resolves through, if there is one."""
    if "-" not in stem:
        return None
    base = stem.split("-", 1)[0]
    return base if base in by_locale else None


def read_locale_keys(locale_glob: str) -> dict[str, set[str]]:
    """Map locale stem to the set of keys that locale file declares."""
    by_locale: dict[str, set[str]] = {}
    for path in sorted(glob.glob(locale_glob)):
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem in {"index", "types"}:
            continue
        with open(path, encoding="utf-8") as fh:
            by_locale[stem] = set(_KEY_LINE.findall(fh.read()))
    return by_locale


def missing_locales(key: str, by_locale: dict[str, set[str]], bases: dict[str, str | None]) -> list[str]:
    """Locales whose reader would see this chip's English label."""
    reach = {stem for stem, keys in by_locale.items() if key in keys}
    return sorted(stem for stem in by_locale if stem not in reach and bases[stem] not in reach)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default=DEFAULT_DATA_GLOB)
    parser.add_argument("--locales", default=DEFAULT_LOCALE_GLOB)
    args = parser.parse_args(argv)

    chips = read_chips(args.data)
    by_locale = read_locale_keys(args.locales)
    files = {chip.path for chip in chips}

    if not chips or not by_locale:
        print(
            f"case module chips: parsed {len(chips)} chip(s) from {len(files)} playbook(s) and "
            f"{len(by_locale)} locale file(s). One of those is zero, so this scan read nothing "
            f"and cannot report a clean tree.",
            file=sys.stderr,
        )
        return 2
    empty = sorted(stem for stem, keys in by_locale.items() if not keys)
    if empty:
        print(
            f"case module chips: {len(empty)} locale file(s) parsed to zero keys: {', '.join(empty)}. "
            f"The key regex no longer matches how they are written; fix that before trusting this.",
            file=sys.stderr,
        )
        return 2

    bases = {stem: _base_of(stem, by_locale) for stem in by_locale}
    keyless = [chip for chip in chips if chip.key is None]

    # key -> the chips that name it, so a finding can name every page it reaches
    users: dict[str, list[Chip]] = defaultdict(list)
    for chip in chips:
        if chip.key is not None:
            users[chip.key].append(chip)

    gaps = {key: missing_locales(key, by_locale, bases) for key in sorted(users)}
    gaps = {key: miss for key, miss in gaps.items() if miss}

    print(
        f"case module chips: {len(chips)} chip(s) across {len(files)} playbook(s), "
        f"{len(users)} distinct key(s), against {len(by_locale)} locale file(s)"
    )

    if keyless:
        print(f"\n{len(keyless)} chip(s) carry no moduleLabelKey and read English in every language:")
        for chip in keyless:
            print(f"    {chip.path}:{chip.line}  moduleLabel={chip.label!r}")

    for key, miss in sorted(gaps.items(), key=lambda item: (-len(item[1]), item[0])):
        chips_for_key = users[key]
        cases = sorted({chip.path.rsplit("/", 1)[-1] for chip in chips_for_key})
        label = chips_for_key[0].label
        print(
            f"\n{key} cannot be answered by {len(miss)} of {len(by_locale)} locale(s), "
            f"so {len(chips_for_key)} chip(s) in {len(cases)} case(s) read {label!r} there:"
        )
        print(f"    missing from: {', '.join(miss)}")
        for name in cases:
            print(f"    {name}")

    if keyless or gaps:
        print(
            f"\ncase module chips FAILED: {len(keyless)} chip(s) with no key, "
            f"{len(gaps)} key(s) no locale set can fully answer."
        )
        return 1

    print(
        f"case module chips OK: every one of {len(users)} chip key(s) is answered by "
        f"all {len(by_locale)} locale file(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
