#!/usr/bin/env python3
"""Every `from app... import name` in the commit must resolve inside the commit.

On 2026-08-17 `app/modules/rfi/router.py` was committed, clean, importing
`count_activity_for_entity` from `app.core.audit_log` twice. The function
itself never left the working tree. Every local run passed, because every
local run reads the working tree, where the function was present. A fresh
clone could not import the RFI router at all, so the application did not
start, and the first thing to notice was a test failing on collection in CI.

Two neighbouring guards do not cover this. A check for untracked FILES that
the tree references cannot see it, because `audit_log.py` was tracked and only
the symbol inside it was absent. `check_migration_heads.py` answers the same
shape of question for the alembic graph, which is where this class was first
found (a revision naming a parent the repository did not carry), and this is
the import-graph half of it.

So: read blobs out of a git ref rather than off disk, parse them, and ask of
every intra-app `from a.b.c import name` whether this ref's own `a/b/c.py`
binds `name` at module level. Text only, no runtime import, so it needs no
database, no dependencies installed, and it cannot hang on an environment
problem the way importing the real package can.

Deliberately quiet about three shapes that cannot break a boot:

  * an import inside `try:` that is caught, which is how this tree probes for
    optional modules,
  * an import inside `if TYPE_CHECKING:`, which never executes,
  * a name reached through a star import, where the namespace is not knowable
    from the module's own text.

Relative imports are skipped; they resolve by position and a missing one is a
syntax-level error the interpreter reports on its own. Modules that do not
parse are counted and named rather than being reported as missing, because
calling an unreadable file an absent one produced 120 false findings on the
first run of this script and a checker that cries wolf stops being read.

Needs a 3.12 interpreter, the same floor the backend itself declares, because
it parses the backend's own syntax. Anything older reads healthy files as
unparsable and the check refuses rather than blaming them.

Usage: .venv-run/Scripts/python.exe scripts/check_head_imports.py [ref]
       (default ref HEAD)
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys

PKG = "app"
BACKEND = "backend"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=os.getcwd()
    ).stdout


def module_path(mod: str) -> list[str]:
    """Candidate blob paths for a dotted module name, package before module."""
    rel = mod.replace(".", "/")
    return [f"{BACKEND}/{rel}/__init__.py", f"{BACKEND}/{rel}.py"]


def bound_by(target: ast.expr, names: set[str]) -> None:
    """Names bound by one assignment target, including unpacking.

    `ai_limiter, api_limiter, login_limiter = _create_limiters()` binds three
    module-level names through a Tuple target. Reading only ast.Name targets
    missed all three and called four live imports unresolved.
    """
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for el in target.elts:
            bound_by(el, names)
    elif isinstance(target, ast.Starred):
        bound_by(target.value, names)


def optional_line_ranges(tree: ast.Module) -> list[tuple[int, int]]:
    """Line spans where an import may fail or never runs at all."""
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        guarded = isinstance(node, ast.Try)
        if isinstance(node, ast.If):
            test = node.test
            guarded = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            )
        lineno = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if guarded and lineno and end:
            spans.append((lineno, end))
    return spans


def toplevel_names(tree: ast.Module) -> tuple[set[str], bool]:
    """Every name bound at module level, and whether the module star-imports."""
    names: set[str] = set()
    star = False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                bound_by(t, names)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "*":
                    star = True
                else:
                    names.add(a.asname or a.name)
        elif isinstance(node, ast.If):
            # A TYPE_CHECKING block or a version guard still binds names.
            for sub in list(node.body) + list(node.orelse):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(sub.name)
                elif isinstance(sub, ast.ImportFrom):
                    for a in sub.names:
                        if a.name != "*":
                            names.add(a.asname or a.name)
                elif isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        bound_by(t, names)
    return names, star


def main() -> int:
    ref = sys.argv[1] if len(sys.argv) > 1 else "HEAD"

    # The backend needs 3.12 (PEP 695 `type` aliases, PEP 701 f-strings), so an
    # older interpreter cannot parse it and the files that use either read as
    # unparsable. The check then names those files and returns 2, which is
    # indistinguishable from "this commit is broken". On 2026-08-18 that cost a
    # real investigation before a release: a 3.11 on PATH accused four healthy
    # files, one of them on the single line `type JobHandler = ...`. "I cannot
    # read this" is not "this is wrong", so refuse rather than accuse.
    # Not a compatibility branch ruff can fold away: this check parses 3.12 syntax
    # and has to refuse rather than report a false clean when run on an older one.
    if sys.version_info < (3, 12):  # noqa: UP036
        running = ".".join(str(p) for p in sys.version_info[:3])
        print(f"ERROR: this check parses 3.12 syntax and is running on {running}, so it proved nothing")
        print("Re-run with the project interpreter, e.g. .venv-run/Scripts/python.exe scripts/check_head_imports.py")
        return 2

    files = [p for p in git("ls-tree", "--name-only", "-r", ref, f"{BACKEND}/{PKG}").splitlines() if p.endswith(".py")]
    if not files:
        # "found nothing" and "did not look" must not print the same thing.
        print(f"ERROR: {ref} carries no {BACKEND}/{PKG} python files, this check proved nothing")
        return 2

    blobs = {path: git("show", f"{ref}:{path}") for path in files}

    defined: dict[str, tuple[set[str], bool]] = {}
    unparsable: list[str] = []
    for path, text in blobs.items():
        try:
            defined[path] = toplevel_names(ast.parse(text))
        except SyntaxError as exc:
            unparsable.append(f"{path}: {exc}")

    broken: list[str] = []
    checked = 0
    skipped: set[str] = set()

    for path, text in blobs.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        optional = optional_line_ranges(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level:
                continue
            if any(lo <= node.lineno <= hi for lo, hi in optional):
                continue
            mod = node.module or ""
            if not (mod == PKG or mod.startswith(PKG + ".")):
                continue
            target = next((c for c in module_path(mod) if c in defined), None)
            if target is None:
                present = next((c for c in module_path(mod) if c in blobs), None)
                if present is not None:
                    skipped.add(present)
                    continue
                for a in node.names:
                    if a.name != "*":
                        broken.append(f"MISSING MODULE {mod} (wanted {a.name}), imported by {path}")
                continue
            names, star = defined[target]
            for a in node.names:
                if a.name == "*":
                    continue
                checked += 1
                if a.name in names or star:
                    continue
                if any(c in defined for c in module_path(f"{mod}.{a.name}")):
                    continue  # importing a submodule, not a name
                broken.append(f"UNRESOLVED {mod}.{a.name}, imported by {path}, not defined in {target}")

    if unparsable:
        print(f"ERROR: {len(unparsable)} file(s) under {BACKEND}/{PKG} do not parse in {ref}:")
        for u in unparsable:
            print(f"    {u}")
        return 2

    if broken:
        print(f"ERROR: {len(broken)} import(s) in {ref} name something {ref} does not carry.")
        print("A clean clone of this commit cannot import these modules, so the application does not start.")
        print("The fix is almost always that the definition is still sitting in a working tree uncommitted.\n")
        for b in broken:
            print(f"    {b}")
        return 1

    note = f", {len(skipped)} unparsable module(s) skipped" if skipped else ""
    print(f"head imports OK: {checked} imported name(s) in {ref} all resolve within {ref}{note}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
