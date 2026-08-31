#!/usr/bin/env python3
"""Locale diacritic ratchet: stop a new sentence shipping with its marks stripped.

Ten locale files ship strings that are the right words in the right order with
every diacritic deleted. The Swedish reads "En driftsattningskontroll utan ett
overenskommet godkannandevarde ar ett grael i vantan"; the French, "Arreter le
decompte final et liberer la retenue de garantie"; the Romanian, "Aceasta
inregistreaza plata ca o intrare imuabila in registru si inchide factura". They
were born that way - `git log -S` on the accented spelling returns nothing - so
they arrived in bulk translation commits and no gate has ever objected. Nothing
renders wrong, nothing fails to parse, the key is present and every coverage
check counts a string that is there. Only a reader of that language sees that
the text is not the language.

Repairing them is translation work and cannot be mechanical: a find/replace is
exactly what turned the German locale into `Qulle`, `Steur` and `zurst` (each
with an umlaut), which is what `check_locale_umlaut_folding.py` now guards. So
this gate does not try to fix anything. It holds the line: the strings already
in the baseline are declared debt, and no new one may join them.

How a string is judged
----------------------
Per file, and with no dictionary. A word counts as evidence when its unaccented
skeleton appears in this same file *only* ever spelled with the accent - if
`fran` and `från` both occur, `fran` proves nothing, because the file itself
uses the bare spelling somewhere. A value is reported when it carries no
diacritic at all, runs to at least six words, and at least three of them are
that kind of evidence. Both thresholds are conservative on purpose: this is a
heuristic and it is aimed at prose, not at "OK" or a units label.

The honest limit
----------------
That evidence rule is what makes the detector precise, and it is also what
makes it under-report, worst in the files that are worst damaged. A file's own
stripped strings supply the bare twins that disqualify words in its other
stripped strings. Measured instance: the Swedish
`cases.run_a_soft_landings_performance_handover.step.targets.why` is 32 words
of fully stripped Swedish that this detector does not report, because only
`overlamnandet` and `gor` qualified - `mal`, `ar` and `fran` each have a bare
twin elsewhere in `sv.ts`. Relaxing the rule is not the fix; without the
every-other-spelling condition it returns 2886 hits for `de.ts` and 3713 for
`es.ts`, both of which are correct files.

So a green run means "no new string crossed this detector's bar". It never
means "no new string was stripped".

When a repair makes this gate fail
----------------------------------
Repairing strings in a file removes bare twins from it, which promotes more
skeletons to evidence, which can make the detector see damage in that file it
could not see before. The newly listed strings are not false positives and the
gate is not misfiring: those strings were always broken and the repair is what
uncovered them. Fix them in the same pass if you can, or record them with
--update-baseline and read the diff. A locale that both lost and gained keys in
one run is this case, and the output says so.

Usage::

    python scripts/check_locale_stripped_diacritics.py
    python scripts/check_locale_stripped_diacritics.py --update-baseline

Exit code 0 means no key entered the set. Exit code 1 means at least one did,
and the output names every locale, key and string.
"""

from __future__ import annotations

import collections
import json
import re
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCALES_DIR = REPO_ROOT / "frontend" / "src" / "app" / "locales"
BASELINE = Path(__file__).resolve().parent / "locale_stripped_diacritics_baseline.json"

#: A value must be at least this many words before it is judged. Short values
#: are labels and units, where an absent accent is usually correct.
MIN_WORDS = 6

#: How many words of evidence a value needs. Three keeps single coincidences
#: out; the German locale, which is correct, produces none at this bar.
MIN_EVIDENCE = 3

_LINE = re.compile(r'^\s*"((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\]|\\.)*)"')
_WORD = re.compile(r"[0-9A-Za-zÀ-ɏ]+")


def _skeleton(word: str) -> str:
    """``word`` lowercased with every combining mark removed."""
    return "".join(
        c
        for c in unicodedata.normalize("NFD", word.lower())
        if not unicodedata.combining(c)
    )


def _has_diacritic(text: str) -> bool:
    return any(unicodedata.combining(c) for c in unicodedata.normalize("NFD", text))


def _entries(path: Path) -> list[tuple[str, str]]:
    out = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            match = _LINE.match(line)
            if match:
                out.append((match.group(1), match.group(2)))
    return out


def stripped_keys(path: Path) -> dict[str, str]:
    """Keys in ``path`` whose value looks like prose with its diacritics removed."""
    entries = _entries(path)

    spellings: dict[str, set[str]] = collections.defaultdict(set)
    for _, value in entries:
        if _has_diacritic(value):
            for word in _WORD.findall(value):
                spellings[_skeleton(word)].add(word)
    # Evidence: this file only ever spells the word with its accent.
    evidence = {
        skel
        for skel, forms in spellings.items()
        if all(_has_diacritic(f) for f in forms)
    }

    found = {}
    for key, value in entries:
        if _has_diacritic(value):
            continue
        words = _WORD.findall(value)
        if len(words) < MIN_WORDS:
            continue
        if sum(1 for w in words if _skeleton(w) in evidence) >= MIN_EVIDENCE:
            found[key] = value
    return found


def observe() -> dict[str, dict[str, str]]:
    out = {}
    for path in sorted(LOCALES_DIR.glob("*.ts")):
        found = stripped_keys(path)
        if found:
            out[path.name] = dict(sorted(found.items()))
    return out


def main() -> int:
    if not LOCALES_DIR.is_dir():
        print(
            f"no locale directory at {LOCALES_DIR} - has the layout changed?",
            file=sys.stderr,
        )
        return 1

    observed = observe()

    if "--update-baseline" in sys.argv:
        payload = {name: sorted(found) for name, found in observed.items()}
        BASELINE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        total = sum(len(v) for v in payload.values())
        print(f"baseline rewritten: {total} strings across {len(payload)} locales")
        for name, keys in payload.items():
            print(f"  {name}: {len(keys)}")
        print(
            "\nRead the diff before committing. A number going UP is the gate telling you something."
        )
        return 0

    if not BASELINE.exists():
        print(
            f"no baseline at {BASELINE}; create it with --update-baseline",
            file=sys.stderr,
        )
        return 1
    baseline = {
        name: set(keys)
        for name, keys in json.loads(BASELINE.read_text(encoding="utf-8")).items()
    }

    observed_total = sum(len(v) for v in observed.values())
    baseline_total = sum(len(v) for v in baseline.values())

    added: list[tuple[str, str, str]] = []
    unmasking: set[str] = set()
    for name, found in observed.items():
        new = set(found) - baseline.get(name, set())
        if new:
            added.extend((name, key, found[key]) for key in sorted(new))
            if baseline.get(name, set()) - set(found):
                unmasking.add(name)

    if added:
        print(
            f"{len(added)} locale string(s) newly detected as stripped of every diacritic "
            f"(baseline {baseline_total}, observed {observed_total} across {len(observed)} locales):",
            file=sys.stderr,
        )
        for name, key, value in added:
            shown = value if len(value) <= 110 else value[:107] + "..."
            print(f"  {name}: {key}\n      {shown}", file=sys.stderr)
        if unmasking:
            print(
                "\n"
                + ", ".join(sorted(unmasking))
                + " also LOST keys in this run, so this is very likely a repair uncovering damage the\n"
                "detector could not see before: fixing strings removes the bare spellings that were\n"
                "hiding others. Those strings were always broken. Fix them too if you can, or accept\n"
                "them with --update-baseline and read the diff.",
                file=sys.stderr,
            )
        print(
            "\nWrite the accented text; do NOT run a find/replace to restore marks. That is the exact\n"
            "pass that turned the German locale into non-words (see check_locale_umlaut_folding.py).\n"
            "If a listed string is genuinely correct without diacritics, record it with\n"
            "--update-baseline and say why in the commit message.",
            file=sys.stderr,
        )
        return 1

    removed = baseline_total - observed_total
    print(
        f"locale diacritic ratchet OK: {observed_total} declared strings across {len(observed)} locales, "
        f"nothing new (baseline {baseline_total})"
    )
    if removed > 0:
        print(
            f"  {removed} fewer than the baseline - run --update-baseline to bank the repair"
        )
    print(
        "  a green run means no new string crossed this detector's bar, not that none was stripped"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
