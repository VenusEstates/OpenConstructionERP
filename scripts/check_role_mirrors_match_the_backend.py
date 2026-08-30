#!/usr/bin/env python3
# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Keep every copy of the backend role vocabulary in the UI equal to the backend.

The UI hides a control when the current role could not use it anyway. That is
a UX decision, not a security boundary, and the backend stays the only
authority. But the hidden control is a lie the moment the two sides disagree,
and the disagreement is silent in both directions:

  * the UI shows a control the backend refuses, which reads to the user as a
    broken product, and
  * the UI hides a control the backend now allows, which reads to us as a fix
    that did not work. That direction is worse, because it hides a successful
    change instead of a failed one.

The second direction is not hypothetical. ``property_dev.owner_scoped_delete``
was introduced at EDITOR level precisely so that ownership, and not role,
would be the wall on those routes. The role list in the UI still named the
four roles from before and would have gone on hiding the button from the one
role the change was made for.

This file checks two different kinds of copy, and the difference between them
decides what can be checked at all.

A TABLE is a copy whose correct value follows from the backend without
knowing anyone's intent: the alias map and the rank hierarchy. Those are
compared entry by entry, and there is exactly one copy of each in the UI.

A CLOSURE is a flattened answer to "who may do X", such as the property_dev
role lists below. Its correct value depends on WHICH permission it mirrors,
and that name appears nowhere in the literal itself, so a closure can only be
checked where something states the permission it belongs to. That is why the
GATES table exists and why the several other flattened role lists elsewhere in
the app are outside this file's reach rather than quietly assumed correct.

Two properties matter more than the comparisons themselves.

First, the backend side of a closure IS a closure, not a literal. A permission
mapped to EDITOR admits MANAGER and ADMIN through the rank hierarchy, and
admits every alias in ``ROLE_ALIASES`` that resolves into one of those. Nine
role strings pass ``property_dev.owner_scoped_delete``; only three of them
appear in the mapping itself. A check that compared literals to literals would
be green in exactly the situation it exists to catch, so this asks the same
function the request path asks, ``permission_registry.role_has_permission``.

Second, the assertion is set EQUALITY. Asserting only that every role in the
UI list passes the backend leaves the missing-role direction unguarded, and
the missing-role direction is the defect that prompted this file.

A correct constant that nothing reads is still the old defect, so the wiring
checks count the delete gates in the page against the number of them that
resolve through one of these constants, and the duplicate scan refuses a
second copy of either table anywhere under ``frontend/src``. Without the first
a call site could go back to an inline array; without the second the tables
could simply be typed out again next to the code that wants them, which is
how there came to be three copies of the alias map.

The duplicate scan's good outcome is an empty result, and an empty result is
also what a broken scanner produces, so it first has to find the canonical
copy it knows is there. If it cannot, it says so instead of reporting zero.

Runs from any working directory: every path here is derived from __file__,
and the repository's backend directory is put first on sys.path rather than
being expected there. That last part is not tidiness. This environment also
carries an INSTALLED copy of the backend in site-packages, several minor
versions behind the tree, and importing it instead would produce a confident
answer about code nobody is editing. The import is therefore checked against
the tree it claims to describe, and the path it resolved is printed next to
the verdict so a green run says which backend it was green about.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"
FEATURE = REPO_ROOT / "frontend" / "src" / "features" / "property-dev"
TS_SOURCE = FEATURE / "permissions.ts"
TS_CONSUMER = FEATURE / "PropertyDevPage.tsx"

# The one place the UI is allowed to hold the backend's role vocabulary.
SHARED_ROLES_TS = FRONTEND_SRC / "shared" / "lib" / "roles.ts"

# Each row pairs an exported TypeScript constant with the backend permission
# it mirrors. Adding a fourth UI role list is a row here, not a rewrite.
GATES: list[tuple[str, str]] = [
    ("ROLES_WITH_OWNER_SCOPED_DELETE", "property_dev.owner_scoped_delete"),
    ("ROLES_WITH_LEAD_DELETE", "property_dev.lead.delete"),
]

# Each shape is a PAIR of keys, not a single token. A single token would match
# a locale file, a catalogue of construction professions, or a type describing
# a payload the server returns, all of which legitimately name these roles
# without mirroring anything. Requiring both keys of a pair identifies an
# object literal that is actually reproducing the backend table.
TABLE_SHAPES: list[tuple[str, tuple[str, str]]] = [
    ("alias map", ("quantity_surveyor:", "superuser:")),
    ("rank table", ("site_foreman:", "field_worker:")),
]

# Where the permission registry was actually imported from, printed with the
# verdict so a passing run says which tree it was passing about.
REGISTRY_SOURCE: list[Path] = []

# How many files the duplicate scan looked at, printed with the verdict so a
# clean result carries the population it was clean over.
SCANNED: list[int] = []


def _backend_permissions():
    """Import the permission registry from the TREE and prove it came from there."""
    # Inserted FIRST, and derived from __file__ rather than from the working
    # directory, so the answer does not depend on where this was launched.
    if sys.path[:1] != [str(BACKEND)]:
        sys.path.insert(0, str(BACKEND))

    import app.core.permissions as core_permissions

    # The environment this runs in can also hold an INSTALLED copy of the
    # backend, and an installed copy is a different and usually older tree.
    # Answering from it would compare the UI against a version of the
    # permission map nobody is editing, and the answer would look normal.
    # So the source of the answer is asserted, not assumed.
    loaded_from = Path(core_permissions.__file__).resolve()
    if not REGISTRY_SOURCE:
        REGISTRY_SOURCE.append(loaded_from)
    if not loaded_from.is_relative_to(BACKEND):
        raise LookupError(
            f"resolved the permission registry from {loaded_from}, which is outside {BACKEND}. "
            f"That is an installed copy of the backend, not the tree being edited, so any answer "
            f"below would describe a different version. Put the repository's backend directory "
            f"first on PYTHONPATH."
        )
    return core_permissions


def _backend_roles(permission: str) -> set[str]:
    """Every role string the request path would admit for ``permission``.

    Resolved by asking the registry, not by reading the mapping, so aliases
    and the rank hierarchy are both accounted for.
    """
    core_permissions = _backend_permissions()
    from app.modules.property_dev.permissions import register_property_dev_permissions

    register_property_dev_permissions()

    # Registration is checked separately and first. A permission name that is
    # not registered denies everyone except ADMIN, which short-circuits ahead
    # of the lookup, and the resulting three-element set looks like a
    # frontend problem rather than the typo it is.
    registry = core_permissions.permission_registry
    if permission not in registry.list_all():
        raise LookupError(
            f"{permission!r} is not registered. Either the name is misspelled in this gate "
            f"or the module stopped registering it. Nothing below this line is meaningful "
            f"until that is resolved."
        )

    candidates = {r.value for r in core_permissions.Role} | set(core_permissions.ROLE_ALIASES)
    return {role for role in candidates if registry.role_has_permission(role, permission)}


def _ts_object(name: str, source: str, value: str) -> dict[str, str]:
    """One exported object literal in the TS source, as a plain dict.

    ``value`` is the pattern for the right-hand side, since one table holds
    quoted role names and the other holds numbers.
    """
    pattern = re.compile(
        r"export\s+const\s+" + re.escape(name) + r"\b[^=]*=\s*\{(?P<body>[^}]*)\}",
        re.S,
    )
    found = pattern.findall(source)
    if len(found) != 1:
        raise LookupError(
            f"expected exactly one 'export const {name}' object in {SHARED_ROLES_TS.name}, "
            f"found {len(found)}. A rename leaves this gate reading an empty table, which is "
            f"not a pass."
        )
    return dict(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(" + value + r")", found[0]))


def _ts_roles(name: str, source: str) -> set[str]:
    """The role strings inside one exported array literal in the TS source."""
    pattern = re.compile(
        r"export\s+const\s+" + re.escape(name) + r"\b[^=]*=\s*\[(?P<body>[^\]]*)\]",
        re.S,
    )
    found = pattern.findall(source)
    # Exactly one. Zero means the constant was renamed and this gate is now
    # comparing against nothing, which passes as an empty set on one side.
    # More than one means the pattern is loose enough to have caught a
    # neighbouring array, and the union of the two would hide a difference.
    if len(found) != 1:
        raise LookupError(
            f"expected exactly one 'export const {name}' array in {TS_SOURCE.name}, found {len(found)}. "
            f"A rename leaves this gate reading an empty set, which is not a pass."
        )
    return set(re.findall(r"['\"]([^'\"]+)['\"]", found[0]))


def _table_problems() -> list[str]:
    """Compare the shared alias map and rank table against the backend."""
    problems: list[str] = []
    try:
        core_permissions = _backend_permissions()
    except LookupError as exc:
        return [f"shared role tables: {exc}"]

    source = SHARED_ROLES_TS.read_text(encoding="utf-8")

    backend_aliases = {alias: role.value for alias, role in core_permissions.ROLE_ALIASES.items()}
    backend_ranks = {role.value: rank for role, rank in core_permissions.ROLE_HIERARCHY.items()}

    for name, backend_table, value_pattern, cast in (
        ("ROLE_ALIASES", backend_aliases, r"'[^']*'", lambda v: v.strip("'")),
        ("ROLE_RANK", backend_ranks, r"-?\d+", int),
    ):
        try:
            raw = _ts_object(name, source, value_pattern)
        except LookupError as exc:
            problems.append(f"{name}: {exc}")
            continue

        ui_table = {key: cast(value) for key, value in raw.items()}
        if ui_table == backend_table:
            continue

        missing = {k: v for k, v in backend_table.items() if ui_table.get(k) != v}
        extra = {k: v for k, v in ui_table.items() if backend_table.get(k) != v}
        problems.append(
            f"{name} in {SHARED_ROLES_TS.name} does not match the backend.\n"
            f"    backend holds ({len(backend_table)}): {backend_table}\n"
            f"    the UI holds  ({len(ui_table)}): {ui_table}\n"
            f"    in the backend but wrong or absent in the UI: {missing or 'none'}\n"
            f"    in the UI but wrong or absent in the backend: {extra or 'none'}"
        )

    return problems


def _duplicate_problems() -> list[str]:
    """Refuse a second copy of either table anywhere under frontend/src.

    Consolidating the three hand-written copies is worth nothing if a fourth
    can be typed out tomorrow, and a fourth is easy to type: the tables are
    eight and seven short lines. So the shapes are searched for, and only the
    canonical file is allowed to carry them.
    """
    problems: list[str] = []
    hits: dict[str, list[Path]] = {label: [] for label, _ in TABLE_SHAPES}
    scanned = 0

    for path in sorted(FRONTEND_SRC.rglob("*.ts*")):
        # Locale files name every role as translated UI text. They are data,
        # not a mirror of the mapping, and they hold thousands of keys.
        if "locales" in path.parts:
            continue
        # Several agents write under this tree while this runs, so a file
        # listed by the walk can be mid-rename or briefly locked by the time
        # it is opened. Retry once, and if it still cannot be read, say which
        # file rather than reporting a clean scan over a population that was
        # never fully read.
        text = None
        last_error: OSError | None = None
        for _ in range(2):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
                break
            except OSError as exc:
                last_error = exc
        if text is None:
            problems.append(
                f"could not read {path.relative_to(REPO_ROOT).as_posix()} ({last_error}). "
                f"The scan below would be reporting a clean result over a file it never opened. "
                f"If another process was writing the tree, run this again."
            )
            continue

        scanned += 1
        for label, shape in TABLE_SHAPES:
            if all(token in text for token in shape):
                hits[label].append(path)

    SCANNED.append(scanned)

    for label, _ in TABLE_SHAPES:
        found = hits[label]
        # The scan's good outcome is "nothing else carries this", and a
        # scanner that matches nothing at all produces the same output. So it
        # must first find the copy that is known to exist.
        if SHARED_ROLES_TS not in found:
            problems.append(
                f"the {label} scan did not find the {label} in {SHARED_ROLES_TS.name}, so it cannot "
                f"be trusted to have found copies elsewhere either. Either the canonical table was "
                f"renamed or reformatted past the shape this looks for, or the shape is wrong. "
                f"Until this line passes, an empty result below means nothing."
            )
            continue

        duplicates = [p for p in found if p != SHARED_ROLES_TS]
        if duplicates:
            listed = "\n".join(f"      {p.relative_to(REPO_ROOT).as_posix()}" for p in duplicates)
            problems.append(
                f"the {label} is written out again in {len(duplicates)} file(s) outside "
                f"{SHARED_ROLES_TS.relative_to(REPO_ROOT).as_posix()}:\n{listed}\n"
                f"    A second copy drifts silently: nothing fails when the backend changes and "
                f"only one copy is updated. Import it from the shared module instead."
            )

    return problems


def _wiring_problems(page: str) -> list[str]:
    """Check that the drawers actually decide by the constants.

    Keeping the constants correct is worth nothing if a call site goes back
    to an inline array. That regression would leave every other check here
    green, because the constant it compares would still be right and simply
    would not be read by anybody. So this counts the delete gates in the page
    against the number of them that resolve through a named constant, and
    refuses any gap between the two.
    """
    problems: list[str] = []

    if "from './permissions'" not in page:
        problems.append(
            f"{TS_CONSUMER.name} no longer imports the role constants. Whatever its delete "
            f"gates decide by now, it is not the thing this gate keeps correct."
        )

    gates = len(re.findall(r"const canDelete = useMemo\(", page))
    resolved = sum(len(re.findall(re.escape(name) + r"\.includes\(", page)) for name, _ in GATES)

    # Floor. Zero gates means the page was restructured and this stopped
    # measuring anything, which must not read as agreement.
    if gates == 0:
        problems.append(
            f"no delete gate found in {TS_CONSUMER.name}. Either the drawers were restructured "
            f"or the pattern this looks for changed. Fix the gate, do not delete it."
        )
    elif gates != resolved:
        problems.append(
            f"{TS_CONSUMER.name} has {gates} delete gates but only {resolved} of them decide by a "
            f"named permission constant. The difference is a gate that went back to an inline role "
            f"list, which drifts silently and is invisible to every other check in this file."
        )

    return problems


def check() -> list[str]:
    """Return one message per divergence. An empty list means the two agree."""
    problems: list[str] = []
    problems.extend(_table_problems())
    problems.extend(_duplicate_problems())

    source = TS_SOURCE.read_text(encoding="utf-8")
    problems.extend(_wiring_problems(TS_CONSUMER.read_text(encoding="utf-8")))

    for const_name, permission in GATES:
        try:
            backend = _backend_roles(permission)
            frontend = _ts_roles(const_name, source)
        except LookupError as exc:
            problems.append(f"{const_name} / {permission}: {exc}")
            continue

        if backend == frontend:
            continue

        missing = sorted(backend - frontend)
        extra = sorted(frontend - backend)
        problems.append(
            f"{const_name} does not match {permission}.\n"
            f"    backend admits ({len(backend)}): {sorted(backend)}\n"
            f"    the UI lists   ({len(frontend)}): {sorted(frontend)}\n"
            f"    admitted by the backend but hidden by the UI: {missing or 'none'}\n"
            f"    offered by the UI but refused by the backend:  {extra or 'none'}"
        )

    return problems


def main() -> int:
    problems = check()
    if problems:
        print("role mirrors: FAIL")
        for problem in problems:
            print(f"  {problem}")
        return 1

    source = REGISTRY_SOURCE[0] if REGISTRY_SOURCE else "unknown"
    scanned = SCANNED[0] if SCANNED else 0
    print(
        f"role mirrors: OK ({len(TABLE_SHAPES)} shared tables equal to the backend, "
        f"{len(GATES)} role lists equal to the permission registry, every delete gate in "
        f"{TS_CONSUMER.name} reads one of them, and no second copy of either table "
        f"among {scanned} files under frontend/src)\n"
        f"  registry read from: {source}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
