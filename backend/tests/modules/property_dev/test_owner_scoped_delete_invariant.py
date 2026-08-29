# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""``property_dev.owner_scoped_delete`` is only safe next to an owner check.

The module has two delete permissions on purpose. ``property_dev.delete``
maps to MANAGER and is the only wall on the routes that carry it.
``property_dev.owner_scoped_delete`` maps to EDITOR, which is deliberately
low, because on those routes the real wall is the strict ownership check in
the handler body: only ``project.owner_id`` passes it, the global admin
role included.

That makes the low level safe today and dangerous tomorrow. The first route
that picks up ``owner_scoped_delete`` without also calling a
``_verify_owner_via_*`` helper is a hole any editor in the system can walk
through, and nothing else in the tree would notice. It would not look like
a bug: the request would arrive, pass its permission check, and be served.
No 500, no traceback, no failing test, just a delete that should not have
been allowed. Review would have to spot an ABSENT line, which is the thing
review is worst at.

So the pairing is enforced here rather than trusted. This is a source-level
invariant, not a behavioural test, because the defect it guards against is
a missing call - and a missing call has no behaviour to observe until
somebody exercises that one route with the wrong account.
"""

import ast
from pathlib import Path

ROUTER = Path(__file__).resolve().parents[3] / "app" / "modules" / "property_dev" / "router.py"

OWNER_SCOPED = "property_dev.owner_scoped_delete"
OWNER_HELPER_PREFIX = "_verify_owner_via"


def _permission_of(fn: ast.AST) -> str | None:
    """The literal inside ``Depends(RequirePermission("..."))``, if any."""
    for default in list(fn.args.defaults) + list(fn.args.kw_defaults):
        if not (isinstance(default, ast.Call) and isinstance(default.func, ast.Name)):
            continue
        if default.func.id != "Depends":
            continue
        for arg in default.args:
            if (
                isinstance(arg, ast.Call)
                and isinstance(arg.func, ast.Name)
                and arg.func.id == "RequirePermission"
                and arg.args
                and isinstance(arg.args[0], ast.Constant)
            ):
                return arg.args[0].value
    return None


def _calls_an_owner_helper(fn: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id.startswith(OWNER_HELPER_PREFIX)
        for node in ast.walk(fn)
    )


def _owner_scoped_routes() -> list[tuple[int, str, bool]]:
    tree = ast.parse(ROUTER.read_text(encoding="utf-8"))
    out = []
    for fn in ast.walk(tree):
        if isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef) and _permission_of(fn) == OWNER_SCOPED:
            out.append((fn.lineno, fn.name, _calls_an_owner_helper(fn)))
    return sorted(out)


def test_owner_scoped_delete_always_sits_behind_an_owner_check() -> None:
    routes = _owner_scoped_routes()

    # Floor. Without it this passes triumphantly over an empty set the day
    # the permission is renamed or the routes are restructured, which is
    # exactly when the invariant stops being checked and nobody is told.
    assert routes, (
        f"no route is gated by {OWNER_SCOPED!r} any more. Either the permission was renamed "
        f"and this gate was left pointing at the old name, or the routes were restructured. "
        f"Either way the pairing is no longer being enforced - fix the gate, do not delete it."
    )

    unguarded = [(line, name) for line, name, guarded in routes if not guarded]
    assert not unguarded, (
        f"{len(unguarded)} of {len(routes)} routes carry {OWNER_SCOPED!r} without calling a "
        f"{OWNER_HELPER_PREFIX}* helper: "
        + ", ".join(f"{name} (router.py:{line})" for line, name in unguarded)
        + ". That permission maps to EDITOR and is only safe because ownership is checked in "
        "the handler body. Without the helper any editor can delete another tenant's record. "
        "Add the owner check, or move the route back to 'property_dev.delete' (MANAGER)."
    )
