#!/usr/bin/env python3
# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Keep the property_dev role lists in the UI equal to what the backend admits.

The UI hides a button when the current role could not perform the action
anyway. That is a UX decision, not a security boundary, and the backend
stays the only authority. But the hidden button is a lie the moment the
two sides disagree, and the disagreement is silent in both directions:

  * the UI shows a control the backend refuses, which reads to the user as
    a broken product, and
  * the UI hides a control the backend now allows, which reads to us as a
    fix that did not work. That direction is worse, because it hides a
    successful change instead of a failed one.

The second direction is not hypothetical. ``property_dev.owner_scoped_delete``
was introduced at EDITOR level precisely so that ownership, and not role,
would be the wall on those routes. The role list in the UI still named the
four roles from before and would have gone on hiding the button from the
one role the change was made for.

Two properties matter more than the comparison itself.

First, the backend side is a CLOSURE, not a literal. A permission mapped to
EDITOR admits MANAGER and ADMIN through the rank hierarchy, and admits every
alias in ``ROLE_ALIASES`` that resolves into one of those. Nine role strings
pass ``property_dev.owner_scoped_delete``; only three of them appear in the
mapping itself. A check that compared literals to literals would be green in
exactly the situation it exists to catch, so this asks the same function the
request path asks, ``permission_registry.role_has_permission``.

Second, the assertion is set EQUALITY. Asserting only that every role in the
UI list passes the backend leaves the missing-role direction unguarded, and
the missing-role direction is the defect that prompted this file.

A correct constant that nothing reads is still the old defect, so the last
check counts the delete gates in the page against the number of them that
resolve through one of these constants. Without it a call site could go back
to an inline array and every other assertion here would stay green over it.

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
FEATURE = REPO_ROOT / "frontend" / "src" / "features" / "property-dev"
TS_SOURCE = FEATURE / "permissions.ts"
TS_CONSUMER = FEATURE / "PropertyDevPage.tsx"

# Each row pairs an exported TypeScript constant with the backend permission
# it mirrors. Adding a third UI role list is a row here, not a rewrite.
GATES: list[tuple[str, str]] = [
    ("ROLES_WITH_OWNER_SCOPED_DELETE", "property_dev.owner_scoped_delete"),
    ("ROLES_WITH_LEAD_DELETE", "property_dev.lead.delete"),
]

# Where the permission registry was actually imported from, printed with the
# verdict so a passing run says which tree it was passing about.
REGISTRY_SOURCE: list[Path] = []


def _backend_roles(permission: str) -> set[str]:
    """Every role string the request path would admit for ``permission``.

    Resolved by asking the registry, not by reading the mapping, so aliases
    and the rank hierarchy are both accounted for.
    """
    # Inserted FIRST, and derived from __file__ rather than from the working
    # directory, so the answer does not depend on where this was launched.
    if sys.path[:1] != [str(BACKEND)]:
        sys.path.insert(0, str(BACKEND))

    import app.core.permissions as core_permissions
    from app.core.permissions import ROLE_ALIASES, Role, permission_registry
    from app.modules.property_dev.permissions import register_property_dev_permissions

    # The environment this runs in can also hold an INSTALLED copy of the
    # backend, and an installed copy is a different and usually older tree.
    # Answering from it would compare the UI against a version of the
    # permission map nobody is editing, and the answer would look normal.
    # So the source of the answer is asserted, not assumed.
    loaded_from = Path(core_permissions.__file__).resolve()
    REGISTRY_SOURCE.append(loaded_from)
    if not loaded_from.is_relative_to(BACKEND):
        raise LookupError(
            f"resolved the permission registry from {loaded_from}, which is outside {BACKEND}. "
            f"That is an installed copy of the backend, not the tree being edited, so any answer "
            f"below would describe a different version. Put the repository's backend directory "
            f"first on PYTHONPATH."
        )

    register_property_dev_permissions()

    # Registration is checked separately and first. A permission name that is
    # not registered denies everyone except ADMIN, which short-circuits ahead
    # of the lookup, and the resulting three-element set looks like a
    # frontend problem rather than the typo it is.
    if permission not in permission_registry.list_all():
        raise LookupError(
            f"{permission!r} is not registered. Either the name is misspelled in this gate "
            f"or the module stopped registering it. Nothing below this line is meaningful "
            f"until that is resolved."
        )

    candidates = {r.value for r in Role} | set(ROLE_ALIASES)
    return {role for role in candidates if permission_registry.role_has_permission(role, permission)}


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
        print("property_dev role gate parity: FAIL")
        for problem in problems:
            print(f"  {problem}")
        return 1

    source = REGISTRY_SOURCE[0] if REGISTRY_SOURCE else "unknown"
    print(
        f"property_dev role gate parity: OK ({len(GATES)} role lists equal to the permission registry, "
        f"and every delete gate in {TS_CONSUMER.name} reads one of them)\n"
        f"  registry read from: {source}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
