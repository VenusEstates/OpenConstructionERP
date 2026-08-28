#!/usr/bin/env python3
"""Case-route guard: every step of every shipped case must land on a real screen.

A case is a guided walkthrough. Each step names the screen it happens on in its
``to`` field, and the reader clicks straight through to it. If that path has no
matching ``<Route>`` in ``App.tsx``, the click lands on the not-found page and
the walkthrough dead-ends, which is worse than not shipping the case at all.

This is not hypothetical. ``automate-a-recurring-check-with-a-pipeline`` had
three steps pointing at ``/pipelines`` and the route was never declared, even
though the sidebar linked to it and the feature behind it was complete: page,
canvas, store, templates and a typed client all present. The menu entry went
nowhere for as long as the module had existed. Nothing caught it, because
nothing compared the two lists. Measured 2026-08-05: with that route declared,
all 518 steps across the 144 shipped cases resolve, so this guard starts clean
and any failure it reports is new.

Two lists, one comparison:

  - Every ``path="..."`` in ``frontend/src/app/App.tsx``, plus every route a
    bundled module declares in ``frontend/src/modules/*/manifest.ts(x)``.
    Modules mount their routes into the same ``<Routes>`` through
    ``useModuleRouteElements``, so App.tsx alone is not the route table. The
    catch-all ``*`` is deliberately excluded: it matches everything, so
    counting it as a match would make this file always pass and mean nothing.
  - Every ``to`` in ``frontend/src/features/cases/data/*.playbook.ts``.

Matching is segment-wise rather than by string equality, because a route
segment written ``:boqId`` stands for any value and a step is allowed to name a
concrete one. Query strings are stripped first: React Router does not match on
them.

A step naming ``/projects/:projectId/...`` has to satisfy the check twice.
``resolveStepRoute`` (``frontend/src/features/cases/progress.ts``) fills the
slot when a sample project is picked and strips the whole
``/projects/:projectId`` prefix when one is not, so both the scoped and the
unscoped form are reachable at run time and both have to exist. Checking only
the scoped one would pass a case that dead-ends for every reader who has not
picked a project, which is the default.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_TSX = REPO_ROOT / "frontend" / "src" / "app" / "App.tsx"
MODULES_DIR = REPO_ROOT / "frontend" / "src" / "modules"
MANIFEST_NAMES = ("manifest.ts", "manifest.tsx")
PLAYBOOK_DIR = REPO_ROOT / "frontend" / "src" / "features" / "cases" / "data"
PLAYBOOK_GLOB = "*.playbook.ts"

ROUTE_PATH_RE = re.compile(r'path="([^"]*)"')
# A module manifest writes its routes as object literals, so the path is a
# `path:` property rather than a JSX attribute. Both quote styles appear.
MANIFEST_PATH_RE = re.compile(r"""path:\s*(["'])(/[^"'\n]*)\1""")
DEFAULT_ENABLED_FALSE_RE = re.compile(r"defaultEnabled:\s*false")
# `to` inside a playbook step. Single and double quotes both appear in the tree.
STEP_TO_RE = re.compile(r"""^\s*to:\s*(["'])([^"']*)\1""", re.MULTILINE)
PLAYBOOK_ID_RE = re.compile(r"""^\s*id:\s*(["'])([^"']*)\1""", re.MULTILINE)


def read_routes(path: Path) -> list[str]:
    """Every route path declared in App.tsx, catch-all excluded."""
    source = path.read_text(encoding="utf-8", errors="replace")
    return [p for p in ROUTE_PATH_RE.findall(source) if p != "*"]


def read_module_routes(directory: Path) -> list[str]:
    """Every route path a bundled module declares in its manifest.

    App.tsx is not the whole route table. ``useModuleRouteElements`` mounts
    ``manifest.routes`` for every enabled module inside the same ``<Routes>``,
    so a module screen such as ``/gaeb-exchange`` is exactly as reachable as
    anything written in App.tsx. Reading only App.tsx calls it dead, and that
    is what happened: the first case to link a module screen turned this lane
    red for a route that works. ``cases.test.ts`` already read both sources.

    Modules that ship disabled are skipped on purpose. Their routes are not
    mounted until the reader turns the module on, so a step pointing at one
    really does dead-end on a default install. regional-exchange is the live
    example: it declares twenty country screens and ships off, so a case that
    links ``/us-masterformat-exchange`` has to say "enable the module first"
    or point somewhere else, and this guard is right to call it dead.

    Only literal paths are read, and manifests are read as text rather than
    imported, the same trade-off ``cases.test.ts`` makes. A module that builds
    its paths from a table (again regional-exchange, ``/${tpl.routeSlug}``) is
    invisible here; it is also disabled, so nothing is missed today, and if one
    is ever both enabled and table-built the result is a loud false failure
    rather than a silent pass. Both manifest extensions are read because
    regional-exchange keeps its manifest in a ``.tsx``.
    """
    routes: list[str] = []
    for module_dir in sorted(p for p in directory.iterdir() if p.is_dir()):
        manifests = [module_dir / name for name in MANIFEST_NAMES]
        manifest = next((m for m in manifests if m.is_file()), None)
        if manifest is None:
            continue
        source = manifest.read_text(encoding="utf-8", errors="replace")
        if DEFAULT_ENABLED_FALSE_RE.search(source):
            continue
        routes.extend(path for _, path in MANIFEST_PATH_RE.findall(source))
    return routes


def segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def matches(target: str, route: str) -> bool:
    """Whether a step target is served by one route declaration.

    A route segment beginning with ``:`` is a parameter and stands for any
    single segment, including one that is itself written ``:projectId``.
    """
    t_parts, r_parts = segments(target), segments(route)
    if len(t_parts) != len(r_parts):
        return False
    return all(r.startswith(":") or r == t for t, r in zip(t_parts, r_parts, strict=False))


def resolve_unscoped(target: str) -> str | None:
    """The form a step resolves to when no sample project is picked.

    Mirrors ``resolveStepRoute``. Returns None when the step has no
    ``:projectId`` slot and therefore only has the one form.
    """
    if ":projectId" not in target:
        return None
    stripped = re.sub(r"^/projects/:projectId", "", target)
    if stripped == "":
        return "/"
    return stripped if stripped.startswith("/") else f"/{stripped}"


def read_steps(directory: Path) -> list[tuple[str, str]]:
    """Every (playbook file stem, step target) pair, query strings stripped."""
    pairs: list[tuple[str, str]] = []
    for path in sorted(directory.glob(PLAYBOOK_GLOB)):
        source = path.read_text(encoding="utf-8", errors="replace")
        first_id = PLAYBOOK_ID_RE.search(source)
        case_id = first_id.group(2) if first_id else path.stem
        for _, target in STEP_TO_RE.findall(source):
            pairs.append((case_id, target.split("?", 1)[0]))
    return pairs


def main() -> int:
    if not APP_TSX.is_file():
        print(f"ERROR: {APP_TSX} not found", file=sys.stderr)
        return 1
    if not PLAYBOOK_DIR.is_dir():
        print(f"ERROR: {PLAYBOOK_DIR} not found", file=sys.stderr)
        return 1

    app_routes = read_routes(APP_TSX)
    module_routes = read_module_routes(MODULES_DIR) if MODULES_DIR.is_dir() else []
    routes = app_routes + module_routes
    steps = read_steps(PLAYBOOK_DIR)

    # An empty read is a broken scan, not a clean tree. Both of these have moved
    # before, and a guard that silently measures nothing is worse than none.
    if not app_routes:
        print(f"ERROR: no route declarations found in {APP_TSX}", file=sys.stderr)
        return 1
    if not module_routes:
        print(
            f"ERROR: no module routes found under {MODULES_DIR}; modules mount "
            "their own routes, so an empty read here would call every module "
            "screen dead",
            file=sys.stderr,
        )
        return 1
    if not steps:
        print(
            f"ERROR: no case steps found under {PLAYBOOK_DIR}/{PLAYBOOK_GLOB}",
            file=sys.stderr,
        )
        return 1

    dead: list[tuple[str, str, str]] = []
    for case_id, target in steps:
        if not any(matches(target, route) for route in routes):
            dead.append((case_id, target, "no route declares this path"))
            continue
        unscoped = resolve_unscoped(target)
        if unscoped is not None and not any(matches(unscoped, r) for r in routes):
            dead.append(
                (
                    case_id,
                    target,
                    f"resolves to {unscoped} with no project picked, and that has no route",
                )
            )

    if dead:
        print(
            f"Case steps pointing at screens that do not exist: {len(dead)}",
            file=sys.stderr,
        )
        for case_id, target, why in sorted(set(dead)):
            print(f"  {case_id}: {target} - {why}", file=sys.stderr)
        print(
            "\nA case step is a link the reader clicks. Either declare the route "
            "in frontend/src/app/App.tsx or in the module manifest that owns the "
            "screen, or point the step at a screen that exists. Do not add the "
            "case to a list of exceptions: a walkthrough that dead-ends is not a "
            "walkthrough.",
            file=sys.stderr,
        )
        return 1

    cases = len({case_id for case_id, _ in steps})
    print(
        f"case routes OK: {len(steps)} steps across {cases} cases, "
        f"all resolve against {len(routes)} declared routes "
        f"({len(app_routes)} in App.tsx, {len(module_routes)} from module manifests)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
