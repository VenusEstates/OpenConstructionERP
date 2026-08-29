# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""The hook config and the lanes may diverge, but only on purpose.

``.pre-commit-config.yaml`` declares 38 hooks. Thirty-four of them sit at the
default pre-commit stage and none of those run in this checkout, because there
is no ``.git/hooks/pre-commit``: the only installed hook is a hand-written
``commit-msg`` shim that calls ``check_commit_subject.py`` and
``check_commit_trailers.py`` directly. The shim says so itself, and it exists
because twelve commits once shipped with a shell fragment as their subject.

That is survivable, and measurably so. Every script named by a local hook is
also invoked by at least one workflow, 26 of 26, so nothing is guarded by the
config alone. The traffic runs the other way. Twenty ``check_*.py`` scripts are
invoked by a workflow and declared by no hook, and they arrived one at a time,
by nobody adding the second half rather than by anyone deciding the second half
was wrong.

The clearest case is a pair. ``check_locale_escapes.py`` guards a locale escape
corrupted by a doubled backslash, and it is a declared hook.
``check_locale_lost_escapes.py`` opens by naming itself the second direction of
that same defect, the backslash stripped instead of doubled, over the same files
at the same cost. It is not declared. Nobody chose that.

So this file does not try to make the two lists equal. That would mean two
hundred lines of YAML enforcing nothing, on a checkout with no pre-commit hook
to run it. It makes the difference explicit instead: a script that runs in a
lane and not on a commit has to appear in one of the two maps below, and the
count of the ones carrying no reason may only go down.

Installing pre-commit is not the fix and is closer to the opposite of one. It
would switch on 34 hooks for every commit in a tree that takes one every few
minutes, several of which walk the whole source tree.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPTS = ROOT / "scripts"
PRE_COMMIT = ROOT / ".pre-commit-config.yaml"

SCRIPT_REF = re.compile(r"scripts/([A-Za-z0-9_]+\.py)")

# Invoked by a lane, deliberately not on a commit, with the evidence for each.
# The quoted text is the name of the workflow step that runs it.
CI_ONLY_BY_DESIGN = {
    "check_a11y_attribute_ratchet.py": (
        "Self-test rather than a gate on the tree. Step: 'Check the accessibility "
        "ratchet can still see its own defects'."
    ),
    "check_docker_force_include_context.py": ("Self-test. Step: 'Prove the Docker force-include gate can fail'."),
    "check_prompt_provenance.py": ("Self-test. Step: 'Prove the prompt provenance guard can fail'."),
    "check_workflow_guard_count.py": (
        "Guards repo-hygiene.yml itself rather than the source tree. Step: 'Check this "
        "file's header still counts its own guards correctly'. A hook scoped by files: "
        "would never fire on the thing this watches."
    ),
    "check_head_imports.py": (
        "Reads the committed tree. Step: 'Check every intra-app import against the "
        "committed tree'. At pre-commit time the commit it needs does not exist yet."
    ),
    "check_module_inference_declarations.py": (
        "Reports rather than gates. Step: 'Report which modules reach an inference primitive'."
    ),
    "check_desktop_sidecar_bundle.py": (
        "Needs a built artifact. Step: 'Check the sidecar carries a usable embedded "
        "database', in desktop-release.yml, against a bundle no commit produces."
    ),
}

# Invoked by a lane, declared by no hook, and carrying no reason on record. This
# is the set the module docstring is about: it grew by default, not by decision.
# A name here is a question for whoever knows the script, not a settled
# exemption.
#
# RATCHET: this set may only shrink. Resolve a name by declaring the hook in
# .pre-commit-config.yaml, or by moving it into CI_ONLY_BY_DESIGN with its
# reason. Do not add to it. A newly added lane script should be decided on when
# it is added, which is what the gate below is for.
CI_ONLY_UNEXPLAINED = frozenset(
    {
        "check_case_module_chip_locales.py",
        "check_case_routes.py",
        "check_demo_firm_names.py",
        "check_i18n_computed_keys.py",
        "check_locale_lost_escapes.py",
        "check_locale_mixed_script.py",
        "check_migration_heads.py",
        "check_module_display_names.py",
        "check_module_manifests.py",
        "check_no_proprietary_classification.py",
        "check_nsis_template_drift.py",
        "check_permission_registration.py",
        "check_zero_width.py",
    }
)
UNEXPLAINED_CEILING = 13

# No caller in any channel: no hook, no lane, no test, no Makefile, no sibling
# script. Recorded rather than deleted, because removing code is a decision for
# a person to make. RATCHET: may only shrink.
UNREFERENCED = frozenset({"check_mn_coverage.py"})


def _declared_hook_scripts() -> dict[str, str]:
    """Script filename to hook id, for local hooks that run one of our scripts."""
    doc = yaml.safe_load(PRE_COMMIT.read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for repo in doc["repos"]:
        for hook in repo["hooks"]:
            match = SCRIPT_REF.search(hook.get("entry", ""))
            if match:
                found[match.group(1)] = hook["id"]
    return found


def _lane_invoked_scripts() -> dict[str, set[str]]:
    """Script filename to the workflows that run it, ignoring comment mentions.

    A name inside a YAML comment is documentation, not an invocation. Counting
    those reports a script as gated when nothing calls it, which is how
    check_workflow_guard_count.py first read as running from line 8 of a header.
    """
    found: dict[str, set[str]] = {}
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for line in workflow.read_text(encoding="utf-8", errors="replace").splitlines():
            hash_at = line.find("#")
            for match in SCRIPT_REF.finditer(line):
                if line.lstrip().startswith("#") or 0 <= hash_at < match.start():
                    continue
                found.setdefault(match.group(1), set()).add(workflow.name)
    return found


def test_no_hook_names_a_script_that_no_lane_runs() -> None:
    """The config may not become fiction while nothing installs it.

    With no pre-commit hook on disk, an entry pointing at a renamed or deleted
    script would fail nowhere and read as coverage forever.
    """
    declared = _declared_hook_scripts()
    invoked = _lane_invoked_scripts()
    on_disk = {path.name for path in SCRIPTS.glob("*.py")}

    missing = sorted(name for name in declared if name not in on_disk)
    assert not missing, f"{len(missing)} of {len(declared)} declared hook scripts do not exist: {missing}"

    unrun = sorted(name for name in declared if name not in invoked)
    assert not unrun, (
        f"{len(unrun)} of {len(declared)} declared hook scripts are run by no workflow. On a "
        f"checkout without .git/hooks/pre-commit that means they run nowhere at all: {unrun}"
    )


def test_every_lane_check_script_is_declared_or_explained() -> None:
    """A gate that runs in a lane and not on a commit has to say why."""
    declared = _declared_hook_scripts()
    invoked = _lane_invoked_scripts()
    lane_checks = {name for name in invoked if name.startswith("check_")}
    listed = set(CI_ONLY_BY_DESIGN) | CI_ONLY_UNEXPLAINED

    undecided = sorted(lane_checks - set(declared) - listed)
    assert not undecided, (
        f"{len(undecided)} of {len(lane_checks)} check scripts run in a lane, are declared by no "
        f"hook, and carry no entry explaining the difference: {undecided}. Declare the hook in "
        ".pre-commit-config.yaml, or add it to CI_ONLY_BY_DESIGN with the reason."
    )

    stale = sorted(listed & set(declared))
    assert not stale, f"{len(stale)} scripts are listed as lane-only but are now declared hooks: {stale}"

    gone = sorted(listed - lane_checks)
    assert not gone, f"{len(gone)} scripts are listed as lane-only but no lane runs them any more: {gone}"

    for name, reason in CI_ONLY_BY_DESIGN.items():
        assert len(reason.strip()) > 30, f"{name} carries a reason too short to weigh: {reason!r}"


def test_the_unexplained_divergence_only_shrinks() -> None:
    """The count of how much of this happened by accident rather than by choice."""
    assert len(CI_ONLY_UNEXPLAINED) <= UNEXPLAINED_CEILING, (
        f"CI_ONLY_UNEXPLAINED grew to {len(CI_ONLY_UNEXPLAINED)}, and the ceiling is "
        f"{UNEXPLAINED_CEILING}. A newly added lane script should be declared as a hook or "
        "explained, not filed away as unexplained."
    )
    assert len(UNREFERENCED) <= 1, f"UNREFERENCED grew to {len(UNREFERENCED)}, and it may only shrink."


def test_no_check_script_is_unreachable() -> None:
    """Every check_*.py has a caller somewhere, or is recorded as having none.

    Reachability is searched across every channel that can invoke one, because a
    search over lanes alone reports the test-gated scripts as dead. That is not
    hypothetical: the first census of this reported check_locale_resolution.py,
    check_backup_freshness.py and check_validation_message_locale_coverage.py as
    orphans, and all three are exercised by a unit test.

    This file is excluded from its own search. UNREFERENCED names the scripts
    that nothing calls, so leaving it in would make the record of a dead script
    into a caller for it, and the list could never report anything again. The
    first run of this test failed exactly that way.
    """
    self_path = Path(__file__).resolve()
    on_disk = {path.name for path in SCRIPTS.glob("check_*.py")}
    referenced = set(_declared_hook_scripts()) | set(_lane_invoked_scripts())

    haystacks = [PRE_COMMIT, ROOT / "Makefile"]
    haystacks += sorted(WORKFLOWS.glob("*.yml"))
    haystacks += sorted((BACKEND / "tests").rglob("*.py"))
    haystacks += sorted(SCRIPTS.glob("*.py"))

    stems = {name[: -len(".py")]: name for name in on_disk}
    for path in haystacks:
        if not path.exists() or path.resolve() == self_path:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for stem, name in stems.items():
            if path.name == name:
                continue  # a script naming itself is not a caller
            if stem in text:
                referenced.add(name)

    unreachable = sorted(on_disk - referenced - UNREFERENCED)
    assert not unreachable, (
        f"{len(unreachable)} of {len(on_disk)} check scripts have no caller in any hook, workflow, "
        f"test, Makefile or sibling script: {unreachable}. Either wire it up, or record it in "
        "UNREFERENCED so that the decision is visible."
    )

    revived = sorted(UNREFERENCED & referenced)
    assert not revived, f"{sorted(revived)} are recorded as unreferenced but now have a caller; remove them."
