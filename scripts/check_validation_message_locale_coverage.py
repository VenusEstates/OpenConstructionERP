#!/usr/bin/env python3
"""Ratchet: the validation-message bundle may not answer fewer locales than it does today.

``backend/app/core/validation/messages/`` (``en.json``, ``de.json``, ``es.json``,
``ru.json``) is a *different* catalogue from the two other i18n systems this repo
already gates, and counting it against either of their numbers answers the wrong
question:

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
    with the result. Every production call site that feeds the validation
    engine a locale (``app/modules/cases/validators.py``,
    ``app/modules/variations/service.py``, ``app/modules/validation/service.py``)
    reads it back through ``get_locale()`` and nothing else. So SUPPORTED_LOCALES,
    not the frontend's 41 or 43, is the actual population this bundle is ever
    asked to answer in production — the middleware has already done the
    regional-to-base collapse the frontend orphan guard has to do itself, and
    this bundle receives none of the 41 frontend codes it doesn't also cover.

  * This bundle answers however many of those 28 codes have a ``<code>.json``
    file. As of the measurement this ratchet was written from: 4 (``de``,
    ``en``, ``es``, ``ru``), i.e. it is short by 24, not by "43 minus 4" or
    "41 minus 4". The count below is read from disk, not written down here.

None of that reconciles the two systems, and it should not: the frontend
picker and the backend request resolver serve different questions (what a user
sees in a dropdown vs. what a backend string ever renders as), and this bundle
inherits its ceiling from the second one, not the first.

WHAT A READER SEES TODAY, measured, not assumed: ``translate(key, locale="fr")``
returns the English string (never a raw key — ``en`` is the unconditional last
resort), and logs one ``WARNING`` the first time each key is requested in that
locale, deduped forever after per ``(locale, key)`` pair. That warning is real,
but it is not a signal anyone or anything currently reads: no test asserts it,
no gate greps for it, no dashboard counts it, and it fires lazily (only for a
key that has already rendered in English at least once), so a language that
never happens to trip a given rule leaves zero trace that the rule speaks
English there. A whole missing locale and a locale mid-translation are
indistinguishable at every level except that log line. This is the gap that is
invisible to a green build the module docstring above hints at, made concrete.

There is a second, sharper finding this ratchet does NOT fix (fixing it means
editing ``messages/__init__.py``, which is out of scope here): the bundle's
own ``MessageBundle.translate()`` does an exact-string match against its
locale dict and then falls straight to English — it has no equivalent of
``app.core.i18n.locale_candidates()``'s regional-to-base chaining. This is
currently harmless only because ``SUPPORTED_LOCALES`` is base-language-only, so
nothing regional ever reaches ``get_locale()``. But it means a caller that
*does* pass a regional code straight to ``translate()`` — a future consumer,
a test, a document renderer with its own locale plumbing — gets 100% English
even where the base translation is complete: verified directly by calling
``translate("boq_markup.contingency_not_on_profit.fail", locale="es-MX", ...)``,
which returns the English sentence while ``locale="es"`` on the same key
returns the complete Spanish one already sitting in ``es.json``.

RATCHET, not a hard requirement. ``SUPPORTED_LOCALES`` names 28 codes and the
bundle answers 4 of them today; a gate demanding 28 of 28 could not pass on
the commit that adds it and would be disabled within a week. So this compares
the SET of locales the bundle answers today against
``validation_i18n_locale_coverage_baseline.json`` and only fails when that set
shrinks — a bundle file deleted or emptied. Growing the set (a new locale
file added) passes silently and prints a reminder to regenerate the baseline
with ``--write-baseline``, the same shape as
``scripts/gen_i18n_backend_coverage_baseline.py``'s sibling ratchet for
``backend/locales/``.

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
MESSAGES_DIR = REPO_ROOT / "backend" / "app" / "core" / "validation" / "messages"
FRONTEND_I18N_TS = REPO_ROOT / "frontend" / "src" / "app" / "i18n.ts"
FRONTEND_LOCALES_DIR = REPO_ROOT / "frontend" / "src" / "app" / "locales"
BASELINE_PATH = REPO_ROOT / "scripts" / "validation_i18n_locale_coverage_baseline.json"


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
    """Locale codes the validation-message bundle answers today (one file each).

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


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, list[str]]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_baseline(answered: set[str], path: Path = BASELINE_PATH) -> None:
    payload = {"answered_locales": sorted(answered)}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def check(
    *,
    i18n_module: Path = I18N_MODULE,
    messages_dir: Path = MESSAGES_DIR,
    frontend_i18n_ts: Path = FRONTEND_I18N_TS,
    frontend_locales_dir: Path = FRONTEND_LOCALES_DIR,
    baseline_path: Path = BASELINE_PATH,
) -> tuple[int, list[str]]:
    """Run the measurement and the ratchet. Returns ``(exit_code, report_lines)``."""
    lines: list[str] = []

    frontend_files = sorted(p.stem for p in frontend_locales_dir.glob("*.ts"))
    frontend_offered = read_frontend_languages(frontend_i18n_ts)
    frontend_base = sorted({base_language(code) for code in frontend_offered})
    supported_locales = read_supported_locales(i18n_module)
    answered = read_bundle_locales(messages_dir)

    lines.append(f"frontend locale files on disk: {len(frontend_files)}")
    lines.append(f"frontend SUPPORTED_LANGUAGES reachable (uncommented) entries: {len(frontend_offered)}")
    lines.append(f"  of which distinct base UI languages (regional variants collapsed): {len(frontend_base)}")
    lines.append(
        f"backend app.core.i18n.SUPPORTED_LOCALES (what a request resolves to "
        f"before reaching this bundle): {len(supported_locales)}"
    )
    lines.append(f"validation-message bundle answers: {len(answered)} {sorted(answered)}")

    missing = sorted(set(supported_locales) - answered)
    lines.append(f"missing from the bundle (declared debt, not failed by this ratchet): {len(missing)} {missing}")

    extra = sorted(answered - set(supported_locales))
    if extra:
        lines.append(f"bundle also carries locale(s) app.core.i18n.SUPPORTED_LOCALES does not list: {extra}")

    baseline = load_baseline(baseline_path)
    baseline_answered = set(baseline["answered_locales"])

    regressed = sorted(baseline_answered - answered)
    if regressed:
        lines.append(f"REGRESSION: bundle used to answer {regressed} and no longer does")
        return 1, lines

    stale = sorted(baseline_answered - set(supported_locales) - answered)
    if stale:
        # Unreachable given the regression check above (answered is a superset
        # of baseline_answered at this point), kept as an explicit statement
        # rather than an assumption.
        lines.append(f"baseline names locale(s) with no file and no longer valid: {stale}")
        return 1, lines

    grown = sorted(answered - baseline_answered)
    if grown:
        lines.append(
            f"bundle answers {grown} beyond the recorded baseline — pass, but regenerate the baseline "
            f"with --write-baseline so this improvement is locked in"
        )

    lines.append("OK: validation-message bundle has not lost ground")
    return 0, lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Regenerate the baseline from the bundle's current answered-locale set and exit 0.",
    )
    args = parser.parse_args()

    if args.write_baseline:
        answered = read_bundle_locales(MESSAGES_DIR)
        write_baseline(answered)
        print(f"{BASELINE_PATH.relative_to(REPO_ROOT)}: recorded {len(answered)} locale(s): {sorted(answered)}")
        return 0

    exit_code, lines = check()
    print("\n".join(lines))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
