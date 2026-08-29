# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Wires scripts/check_validation_message_locale_coverage.py into the pytest run.

That script is the actual gate (it can be run by hand and prints its own
report); this file is what makes it fire on every ordinary backend test run
without a CI workflow edit, the same relationship
test_backend_locale_catalogue.py has to backend/locales - except that guard
predates this one and covers a different catalogue (app.core.i18n's 28
backend locales), not backend/app/core/validation/messages (4 locales today).
Nothing checked the validation-message bundle's locale coverage before this
file: not test_validation_i18n.py (per-key coverage inside locales that
already exist, never file count), not test_backend_locale_catalogue.py (wrong
directory), not any scripts/check_i18n_*.py (all scoped to frontend/).

A gate that cannot go red is not a gate, so this exercises the regression
path directly rather than trusting that the script's own logic is correct by
inspection.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "check_validation_message_locale_coverage.py"
_BASELINE = _REPO_ROOT / "scripts" / "validation_i18n_locale_coverage_baseline.json"
_MESSAGES_DIR = _REPO_ROOT / "backend" / "app" / "core" / "validation" / "messages"


def _load_gate():
    spec = importlib.util.spec_from_file_location("check_validation_message_locale_coverage", _SCRIPT)
    assert spec and spec.loader, f"cannot load {_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


def test_gate_is_green_on_the_current_tree() -> None:
    """The gate must pass on HEAD, or nobody will run it (mirrors the commit-subject guard test)."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        cwd=_REPO_ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, f"gate is red on the current tree:\n{result.stdout}\n{result.stderr}"


def test_baseline_matches_what_is_actually_on_disk() -> None:
    """A stale baseline (recording more than the bundle answers) would mask a real regression."""
    answered = gate.read_bundle_locales(_MESSAGES_DIR)
    baseline = gate.load_baseline(_BASELINE)
    baseline_answered = set(baseline["answered_locales"])
    assert baseline_answered <= answered, (
        f"baseline claims {sorted(baseline_answered - answered)} which the bundle no longer answers - "
        f"the gate would report this tree as passing while it has already regressed"
    )


def test_bundle_answers_a_strict_subset_of_supported_locales_today() -> None:
    """Sanity check on the measurement itself, not just the ratchet outcome.

    Every locale this bundle answers must be one app.core.i18n.SUPPORTED_LOCALES
    actually names, since that is the only value app.core.i18n.get_locale() -
    the sole source every production caller (cases/validators.py,
    variations/service.py, validation/service.py) uses - can ever hand this
    bundle. A bundle locale outside that set would be unreachable in
    production regardless of how complete its translations are.
    """
    supported = set(gate.read_supported_locales(gate.I18N_MODULE))
    answered = gate.read_bundle_locales(_MESSAGES_DIR)
    assert answered <= supported, (
        f"bundle answers {sorted(answered - supported)}, which app.core.i18n.SUPPORTED_LOCALES does not "
        f"list - get_locale() can never produce that code, so no production caller can ever request it"
    )
    assert answered, "bundle answers no locale at all - even 'en' is missing"


def test_frontend_measurement_is_internally_consistent() -> None:
    """The printed frontend numbers must relate the way the docstring claims."""
    files = {p.stem for p in (_REPO_ROOT / "frontend" / "src" / "app" / "locales").glob("*.ts")}
    offered = gate.read_frontend_languages(gate.FRONTEND_I18N_TS)
    offered_set = set(offered)

    assert len(offered) == len(offered_set), "a duplicate code in SUPPORTED_LANGUAGES would hide behind a set"
    # Every reachable code must correspond to a real .ts file - an entry with
    # no file behind it would mean the language picker offers something that
    # silently serves raw keys.
    missing_files = sorted(offered_set - files)
    assert not missing_files, f"SUPPORTED_LANGUAGES lists {missing_files} with no locales/*.ts file"

    base_languages = {gate.base_language(code) for code in offered}
    # Collapsing regional variants can only ever reduce the count, never grow it.
    assert len(base_languages) <= len(offered)


def test_gate_goes_red_when_a_bundle_locale_is_removed(tmp_path: Path) -> None:
    """Delete one file the bundle currently answers - the ratchet must fail."""
    scratch = tmp_path / "messages"
    scratch.mkdir()
    for path in _MESSAGES_DIR.glob("*.json"):
        (scratch / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    baseline = gate.load_baseline(_BASELINE)
    assert baseline["answered_locales"], "baseline is empty, this test cannot prove a regression"
    victim = baseline["answered_locales"][0]
    (scratch / f"{victim}.json").unlink()

    exit_code, lines = gate.check(messages_dir=scratch)
    assert exit_code == 1, f"removing {victim}.json did not turn the gate red:\n" + "\n".join(lines)
    assert any("REGRESSION" in line and victim in line for line in lines)


def test_gate_goes_red_when_a_bundle_locale_is_emptied(tmp_path: Path) -> None:
    """Emptying a file must count the same as deleting it - a size-zero bundle answers nothing."""
    scratch = tmp_path / "messages"
    scratch.mkdir()
    for path in _MESSAGES_DIR.glob("*.json"):
        (scratch / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    baseline = gate.load_baseline(_BASELINE)
    victim = baseline["answered_locales"][0]
    (scratch / f"{victim}.json").write_text("{}", encoding="utf-8")

    exit_code, lines = gate.check(messages_dir=scratch)
    assert exit_code == 1, f"emptying {victim}.json did not turn the gate red:\n" + "\n".join(lines)


def test_gate_stays_green_when_a_bundle_locale_is_added(tmp_path: Path) -> None:
    """Growing the answered set must pass (a hard 28-of-28 requirement would block today's tree)."""
    scratch = tmp_path / "messages"
    scratch.mkdir()
    for path in _MESSAGES_DIR.glob("*.json"):
        (scratch / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    (scratch / "fr.json").write_text(json.dumps({"common": {"ok": "OK"}}), encoding="utf-8")

    exit_code, lines = gate.check(messages_dir=scratch)
    assert exit_code == 0, "\n".join(lines)
    assert any("fr" in line and "regenerate the baseline" in line for line in lines)
