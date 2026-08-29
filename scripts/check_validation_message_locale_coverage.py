#!/usr/bin/env python3
"""Ratchet: no validation-message catalogue may answer fewer locales than it does today.

SEVEN CATALOGUES, NOT ONE. This gate started life watching only
``backend/app/core/validation/messages/``. That was never the whole
population: six modules carry their own ``messages/`` directory with the same
``<locale>.json`` shape, loaded by the same :class:`MessageBundle` class, and
until this file was widened all six could have lost a locale without anything
turning red.

HOW THE SEVEN ARE FOUND, and how that discovery was checked. ``discover_catalogues``
globs for directories named ``messages`` holding ``*.json`` under
``backend/app``. Two other rules were rejected by measurement rather than by
reasoning, and both fail in a way that would have been silent:

  * Globbing for ``en.json`` anywhere under ``backend/app`` returns nine
    directories, not seven. ``modules/costs/translations`` and
    ``modules/property_dev/data/document_locales`` also hold per-locale JSON,
    but no ``MessageBundle`` reads either of them, so a ratchet counting them
    would be gating a population it had misidentified.
  * Globbing for ``messages/__init__.py`` — "find the catalogue packages" —
    returns six and silently drops ``modules/rebar_schedule/messages``, which
    has no ``__init__.py`` at all because its bundle is constructed one
    directory up in ``validators.py``. A package-shaped search cannot see a
    directory that is not a package.

Because a directory name is a weak thing to hang a gate on, the discovery is
cross-checked against behaviour: ``bundle_construction_dirs`` finds every
``MessageBundle(`` construction site in the tree and maps each to the directory
it must be asking for — a site in ``D/__init__.py`` claims ``D`` (this covers
``MessageBundle()`` with no argument, which defaults to its own package
directory), a site in ``X/anything.py`` claims ``X/messages``. The two sets are
required to agree. A construction site whose directory is not in the discovered
set fails this gate loudly, because that is the instrument going blind rather
than the tree losing ground: some eighth catalogue exists in a shape this
search does not anticipate, and the ratchet would otherwise never mention it.

THREE POPULATIONS, and why these catalogues inherit their ceiling from exactly
one of them. Counting a bundle against the wrong one answers the wrong question:

  * ``frontend/src/app/i18n.ts`` decides what a USER can pick in the language
    switcher. As of this writing that file holds 43 ``locales/*.ts`` files, but
    two of them answer no one: ``mn`` has no ``SUPPORTED_LANGUAGES`` entry at all
    and ``uz``'s entry is commented out, both by design (see the comments beside
    them). So the picker actually offers 41 codes, five of which are regional
    overlays that resolve through a base language already on the list
    (``en-US``, ``es-MX``, ``es-CL``, ``es-CO`` -> ``es``, ``pt-BR`` -> ``pt``),
    which is 36 distinct base UI languages. None of this is hardcoded below —
    ``read_frontend_languages`` parses the same array a human reads.

  * ``app/core/i18n.py`` decides what a BACKEND REQUEST resolves to before any
    backend code, including this validation engine, ever sees a locale string.
    ``AcceptLanguageMiddleware`` clamps every incoming ``Accept-Language`` tag
    and every ``?locale=`` override to ``SUPPORTED_LOCALES`` (28 codes, all
    base-language — no regional variants) or ``"en"``, and calls ``set_locale()``
    with the result. Production call sites feeding the core engine
    (``app/modules/cases/validators.py``, ``app/modules/variations/service.py``,
    ``app/modules/validation/service.py``) read it back through ``get_locale()``
    and nothing else. The module catalogues sit one hop further out — a rule
    reads ``ValidationContext.metadata["locale"]`` — but that metadata is
    populated from the same ``get_locale()``, so the ceiling is the same 28.
    Neither the frontend's 41 nor its 43 is the population any of these
    catalogues is ever asked to answer in production: the middleware has already
    done the regional-to-base collapse, and no catalogue here receives a
    frontend code it does not also cover.

  * Each catalogue answers however many of those 28 codes have a ``<code>.json``
    file. The per-catalogue counts are read from disk and printed, never
    written down here — see the report this script prints, which lists them
    separately rather than summed, so one catalogue collapsing cannot be
    hidden by another growing.

WHAT A READER SEES TODAY, measured, not assumed: ``translate(key, locale="fr")``
returns the English string (never a raw key — ``en`` is the unconditional last
resort), and logs one ``WARNING`` the first time each key is requested in that
locale, deduped forever after per ``(locale, key)`` pair. That warning is real,
but it is not a signal anyone or anything currently reads: no test asserts it,
no gate greps for it, no dashboard counts it, and it fires lazily (only for a
key that has already rendered in English at least once), so a language that
never happens to trip a given rule leaves zero trace that the rule speaks
English there. A whole missing locale and a locale mid-translation are
indistinguishable at every level except that log line.

``MessageBundle.translate()`` resolves through
``app.core.i18n.locale_candidates()``, so a regional code chains to its base
language (``es-MX`` -> ``es``) before bottoming out at English, and a hit on the
base language is not logged as a fallback because it is not one. Read the body
of ``translate`` rather than trusting this sentence: an earlier version of this
docstring said the opposite, correctly at the time, and went stale the morning
``341d37ca7`` added the chaining underneath it.

WHAT THIS GATE CANNOT SEE, which is more than it can. It measures BREADTH and
only breadth: how many locale files a catalogue has, per catalogue. Three
things are outside it by construction, and the third is the one that bites.

  * DEPTH. A locale file present but half-translated counts exactly the same as
    a complete one. ``test_validation_i18n.py`` is the per-key guard; this is not.
  * QUALITY. A file full of English strings under ``de.json`` counts as German.
  * CATALOGUES THAT ARE NOT FILES AT ALL. A translation table written as a
    Python dict literal has no per-locale file to count, so it is invisible
    here no matter how many locales it is missing. That is not a hypothetical
    shape in this repo, it is the dominant one: 45 files under ``backend/app``
    hold a dict literal keyed by ``"en"``, ``"de"`` and ``"ru"`` together, 34 of
    them named ``intl.py``, and NOT ONE of them is among the seven directories
    this gate watches — the two populations are disjoint. Measured with::

        grep -rln '"ru":' backend/app --include=*.py \
          | xargs grep -ln '"de":' | xargs grep -ln '"en":'

    That 45 is a floor, not a census: the probe demands all three of those keys,
    so a table carrying only English and German does not appear in it. Widening
    this gate to cover them is not a matter of adding a directory — there is no
    directory. It needs a different instrument, because the thing being counted
    stopped being a file.

  * HOW A CATALOGUE RESOLVES, as opposed to what it holds. This is the limit
    that has actually bitten, and the case is worth reading before trusting a
    green run here. ``341d37ca7`` fixed three catalogues that answered a
    regional locale in English while holding a complete translation for its base
    language. Two were ``modules/requirements/intl.py`` and
    ``modules/risk/intl.py``, in-memory tables invisible here for the reason
    above. The third was ``modules/bcf/messages/``, which IS one of the seven
    directories this gate watches — and this gate saw nothing, because there was
    nothing of its kind to see. That commit changed only ``bcf/messages/__init__.py``;
    every locale file was present before it and present after it, none added and
    none lost, so a breadth ratchet had nothing to compare. Three catalogues
    served English to a translated language for as long as it took someone to
    notice by hand, and a green run of this script was true the whole time. The
    defect was in the lookup, not in the inventory.

RATCHET, not a hard requirement. ``SUPPORTED_LOCALES`` names 28 codes and no
catalogue here answers more than 4 of them; a gate demanding 28 of 28 could not
pass on the commit that adds it and would be switched off within a week by
whoever it first inconvenienced. So this compares the SET of locales each
catalogue answers today against ``validation_i18n_locale_coverage_baseline.json``
and fails only when a set shrinks — a file deleted or emptied — naming the
catalogue that lost it. Growing a set passes and prints a reminder to regenerate
the baseline with ``--write-baseline``, the same shape as
``scripts/gen_i18n_backend_coverage_baseline.py``'s sibling ratchet for
``backend/locales/``. A catalogue appearing that the baseline has never heard of
also passes with a reminder: a new module bringing its own messages is new
ground, not lost ground.

Run it from the repo root:

    python scripts/check_validation_message_locale_coverage.py
    python scripts/check_validation_message_locale_coverage.py --write-baseline
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
I18N_MODULE = REPO_ROOT / "backend" / "app" / "core" / "i18n.py"
APP_ROOT = REPO_ROOT / "backend" / "app"
MESSAGES_DIR = APP_ROOT / "core" / "validation" / "messages"
FRONTEND_I18N_TS = REPO_ROOT / "frontend" / "src" / "app" / "i18n.ts"
FRONTEND_LOCALES_DIR = REPO_ROOT / "frontend" / "src" / "app" / "locales"
BASELINE_PATH = REPO_ROOT / "scripts" / "validation_i18n_locale_coverage_baseline.json"


def catalogue_name(messages_dir: Path, repo_root: Path = REPO_ROOT) -> str:
    """Stable, greppable name for a catalogue: its path relative to the repo root.

    A path rather than a short label because the failure message has to say
    which catalogue lost a locale to somebody who has never opened this file,
    and ``backend/app/modules/bcf/messages`` needs no glossary.
    """
    try:
        return messages_dir.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return messages_dir.as_posix()


def discover_catalogues(app_root: Path = APP_ROOT) -> dict[str, Path]:
    """Every ``messages/`` directory under ``app_root`` that holds at least one JSON file.

    Deliberately keyed on the directory NAME plus JSON contents rather than on
    the presence of an ``__init__.py``: ``modules/rebar_schedule/messages`` is
    not a package (its bundle is built in the ``validators.py`` beside it) and a
    package-shaped search drops it without saying so. See the module docstring
    for the two discovery rules this one replaced and why.
    """
    found: dict[str, Path] = {}
    for path in sorted(app_root.rglob("messages/*.json")):
        found[catalogue_name(path.parent)] = path.parent
    return found


def bundle_construction_dirs(app_root: Path = APP_ROOT) -> dict[str, list[str]]:
    """Map each ``MessageBundle(`` construction site to the directory it must be asking for.

    Structural, not evaluative: resolving ``messages_dir=_MESSAGES_DIR`` or
    ``Path(__file__).parent / "messages"`` for real would mean executing module
    code, and the position of the file already answers the question. A site in
    ``D/__init__.py`` where ``D`` is named ``messages`` claims ``D`` — this is
    also the no-argument ``MessageBundle()`` case, which defaults to its own
    package directory. Any other site claims ``messages`` beside itself.

    Returns ``{catalogue name: ["path:line", ...]}``. Only the mapping matters
    to the caller; the site strings exist so a mismatch report can point at the
    line that has to change.
    """
    claimed: dict[str, list[str]] = {}
    for path in sorted(app_root.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "MessageBundle(" not in text:
            continue
        if path.name == "__init__.py" and path.parent.name == "messages":
            target = path.parent
        else:
            target = path.parent / "messages"
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "MessageBundle(" in line and not line.lstrip().startswith(("#", "*")):
                site = f"{catalogue_name(path)}:{lineno}"
                claimed.setdefault(catalogue_name(target), []).append(site)
    return claimed


def read_supported_locales(i18n_module: Path = I18N_MODULE) -> list[str]:
    """Read ``SUPPORTED_LOCALES`` out of ``app/core/i18n.py`` by parsing, not importing.

    Importing ``app.core.i18n`` pulls in the ``app`` package, and this repo has
    an open issue where importing the backend on this machine can hang (see
    the WMI/sqlalchemy note in project memory) — unnecessary risk for reading
    a list-of-string-literals constant. AST gives the same source of truth
    without executing anything.
    """
    tree = ast.parse(i18n_module.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "SUPPORTED_LOCALES" for target in node.targets):
            continue
        if isinstance(node.value, ast.List):
            return [
                elt.value for elt in node.value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ]
    raise RuntimeError(f"SUPPORTED_LOCALES not found in {i18n_module}")


def read_bundle_locales(messages_dir: Path = MESSAGES_DIR) -> set[str]:
    """Locale codes one catalogue answers today (one ``<code>.json`` file each).

    A file only counts if it parses and yields a non-empty mapping — an empty
    or unparsable file answers nothing, whatever its name claims.
    """
    answered: set[str] = set()
    for path in sorted(messages_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data:
            answered.add(path.stem)
    return answered


def read_frontend_languages(i18n_ts: Path = FRONTEND_I18N_TS) -> list[str]:
    """Reachable ``SUPPORTED_LANGUAGES`` codes from ``frontend/src/app/i18n.ts``.

    Line-based, not a TS parser: skips any line whose trimmed text starts with
    ``//`` (a fully commented-out entry, e.g. ``uz`` today) so a language taken
    off the picker without deleting its file is not counted as offered. An
    entry with no ``code:`` field, such as the ``uk`` region-vs-language
    comment block, contributes nothing because there is nothing to match.
    """
    text = i18n_ts.read_text(encoding="utf-8")
    start = text.find("export const SUPPORTED_LANGUAGES")
    if start == -1:
        raise RuntimeError(f"SUPPORTED_LANGUAGES not found in {i18n_ts}")
    end = text.find("\n];", start)
    block = text[start:end]
    codes: list[str] = []
    for line in block.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("//"):
            continue
        marker = "code: '"
        idx = trimmed.find(marker)
        if idx == -1:
            continue
        rest = trimmed[idx + len(marker) :]
        end_quote = rest.find("'")
        if end_quote != -1:
            codes.append(rest[:end_quote])
    return codes


def base_language(code: str) -> str:
    """The base language a regional code resolves through (``es-MX`` -> ``es``)."""
    return code.split("-", 1)[0]


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, set[str]]:
    """Read the per-catalogue baseline as ``{catalogue name: {locale, ...}}``.

    Raises on the single-catalogue shape this file used before it was widened,
    rather than reading it as an empty mapping: a baseline silently understood
    as "no catalogue has any recorded ground" is a ratchet that cannot fail.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "catalogues" not in payload:
        raise RuntimeError(
            f"{path} is in the old single-catalogue format (a flat 'answered_locales' list). "
            f"Regenerate it with --write-baseline; a baseline this gate cannot read is a gate that "
            f"passes everything."
        )
    return {name: set(locales) for name, locales in payload["catalogues"].items()}


def write_baseline(answered: dict[str, set[str]], path: Path = BASELINE_PATH) -> None:
    payload = {"catalogues": {name: sorted(locales) for name, locales in sorted(answered.items())}}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def measure(catalogues: dict[str, Path]) -> dict[str, set[str]]:
    """Answered locales per catalogue, in the same key space as the baseline."""
    return {name: read_bundle_locales(path) for name, path in catalogues.items()}


def check(
    *,
    i18n_module: Path = I18N_MODULE,
    catalogues: dict[str, Path] | None = None,
    app_root: Path = APP_ROOT,
    frontend_i18n_ts: Path = FRONTEND_I18N_TS,
    frontend_locales_dir: Path = FRONTEND_LOCALES_DIR,
    baseline_path: Path = BASELINE_PATH,
    cross_check: bool = True,
) -> tuple[int, list[str]]:
    """Run the measurement and the ratchet. Returns ``(exit_code, report_lines)``.

    ``catalogues`` defaults to discovery, so an ordinary run exercises the same
    path a test does. Passing an explicit mapping lets a test swap one
    catalogue for a scratch copy without rebuilding the other six, and
    ``cross_check`` is off in that case because a scratch directory outside the
    tree has no construction site to agree with.
    """
    lines: list[str] = []
    failures: list[str] = []

    if catalogues is None:
        catalogues = discover_catalogues(app_root)

    frontend_files = sorted(p.stem for p in frontend_locales_dir.glob("*.ts"))
    frontend_offered = read_frontend_languages(frontend_i18n_ts)
    frontend_base = sorted({base_language(code) for code in frontend_offered})
    supported_locales = read_supported_locales(i18n_module)
    supported = set(supported_locales)

    lines.append(f"frontend locale files on disk: {len(frontend_files)}")
    lines.append(f"frontend SUPPORTED_LANGUAGES reachable (uncommented) entries: {len(frontend_offered)}")
    lines.append(f"  of which distinct base UI languages (regional variants collapsed): {len(frontend_base)}")
    lines.append(
        f"backend app.core.i18n.SUPPORTED_LOCALES (what a request resolves to "
        f"before reaching these catalogues): {len(supported_locales)}"
    )

    if cross_check:
        claimed = bundle_construction_dirs(app_root)
        unmatched = sorted(set(claimed) - set(catalogues))
        for name in unmatched:
            failures.append(
                f"DISCOVERY: a MessageBundle is built for {name} ({', '.join(claimed[name])}) but no such "
                f"catalogue was discovered - this gate is not watching it, and would never say so"
            )
        unread = sorted(set(catalogues) - set(claimed))
        for name in unread:
            lines.append(f"note: {name} holds locale files but no MessageBundle construction site claims it")

    answered = measure(catalogues)
    baseline = load_baseline(baseline_path)

    lines.append(f"catalogues watched: {len(catalogues)}")
    for name in sorted(catalogues):
        have = answered[name]
        missing = sorted(supported - have)
        lines.append(f"  {name}: answers {len(have)} {sorted(have)}; missing {len(missing)} of {len(supported)}")
        extra = sorted(have - supported)
        if extra:
            lines.append(f"    also carries {extra}, which app.core.i18n.SUPPORTED_LOCALES does not list")

    for name in sorted(baseline):
        if name not in answered:
            failures.append(
                f"REGRESSION: catalogue {name} is in the baseline answering "
                f"{sorted(baseline[name])} and is no longer on disk at all"
            )
            continue
        regressed = sorted(baseline[name] - answered[name])
        if regressed:
            failures.append(f"REGRESSION: catalogue {name} used to answer {regressed} and no longer does")

    if failures:
        lines.extend(failures)
        return 1, lines

    for name in sorted(answered):
        if name not in baseline:
            lines.append(
                f"catalogue {name} is new since the baseline was written (answers {sorted(answered[name])}) "
                f"- pass, but regenerate the baseline with --write-baseline"
            )
            continue
        grown = sorted(answered[name] - baseline[name])
        if grown:
            lines.append(
                f"catalogue {name} answers {grown} beyond the recorded baseline - pass, but regenerate "
                f"the baseline with --write-baseline so this improvement is locked in"
            )

    lines.append(f"OK: none of the {len(catalogues)} validation-message catalogues has lost ground")
    return 0, lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Regenerate the baseline from every catalogue's current answered-locale set and exit 0.",
    )
    args = parser.parse_args()

    if args.write_baseline:
        answered = measure(discover_catalogues())
        write_baseline(answered)
        print(f"{BASELINE_PATH.relative_to(REPO_ROOT)}: recorded {len(answered)} catalogue(s)")
        for name in sorted(answered):
            print(f"  {name}: {len(answered[name])} locale(s) {sorted(answered[name])}")
        return 0

    exit_code, lines = check()
    print("\n".join(lines))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
