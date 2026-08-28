#!/usr/bin/env python3
"""Fail if the desktop application window cannot reach the commands it invokes.

The launcher serves the application from the backend it starts, and navigates
the webview to ``http://127.0.0.1:<port>/``. Tauri calls that a REMOTE origin:
``Webview::is_local_url`` counts only the Tauri custom protocol and a
``frontendDist`` that is a URL, and this build's ``frontendDist`` is a
directory. A remote origin then hits the branch in ``webview/mod.rs`` that
reads ``if (plugin_command.is_some() || has_app_acl_manifest || !is_local) &&
invoke.acl.is_none()`` and rejects the call.

That is not a hypothetical. Before the ``permissions`` directory and the
``app-window`` capability existed, the application declared no ACL of its own,
no capability named the loopback origin, and so every ``#[tauri::command]``
invoked from the application window was refused. The user-visible shape of it
was that every outbound link in the product - the docs, the repository, the
marketing site, contact mail - did nothing at all when clicked. No error, no
browser, nothing, because the frontend swallowed the rejection.

Two things have to stay true for that to stay fixed, and this gate checks both.

The link opener has to be reachable from the window that invokes it
-------------------------------------------------------------------
A capability with a ``remote`` block has to exist, its URL patterns have to
actually cover the address the launcher navigates to, and the permissions it
names have to resolve to the link-opening command. Checking that the file
merely contains the string ``http://127.0.0.1:*`` would pass a file where
somebody renamed the permission and severed the grant, so the check resolves
the permission set for real and matches the pattern against sample addresses.

Nothing else may go dead the same way
-------------------------------------
Declaring an application ACL at all flips ``has_app_acl_manifest`` true, which
turns on the same check for the LOCAL origin, where app commands used to be
unrestricted. So every command in ``generate_handler!`` now needs a grant from
some capability or it is dead on both origins with nothing on screen to say so.
Adding a command and forgetting its permission is a silent regression of
exactly the shape this file exists to close, so it fails here instead.

The pattern matcher below is a deliberately narrow subset of the URLPattern
standard that Tauri actually uses, so it self-tests before every scan against
cases measured against the real ``tauri_utils::acl::RemoteUrlPattern``. A
matcher that quietly stopped matching would make this gate vacuously green.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
TAURI = ROOT / "desktop" / "src-tauri"
PERMISSIONS_DIR = TAURI / "permissions"
CAPABILITIES_DIR = TAURI / "capabilities"
LAUNCHER = TAURI / "src" / "main.rs"

# The command the outbound links in the user interface go through. The frontend
# reaches it from `openExternalUrl` in frontend/src/shared/lib/desktop.ts.
LINK_COMMAND = "open_external_url"

DEFAULT_PORTS = {"http": 80, "https": 443}

# Ports the launcher can pick at runtime: the stable default when it is free,
# and anything else when it is not. A grant that only covers one of these is a
# grant that works on some machines.
SAMPLE_PORTS = (8732, 1024, 49512, 65535)

# Measured against tauri_utils::acl::RemoteUrlPattern (tauri-utils 2.9.3), which
# is what decides this at runtime. Each row is pattern, address, expected match.
MATCHER_SELF_TEST = (
    ("http://127.0.0.1:*", "http://127.0.0.1:8732/", True),
    ("http://127.0.0.1:*", "http://127.0.0.1:49512/", True),
    ("http://127.0.0.1:*", "http://127.0.0.1:49512/boq/123?a=1#b", True),
    ("http://127.0.0.1:*", "http://127.0.0.1.evil.invalid/", False),
    ("http://127.0.0.1:*", "http://evil.invalid/", False),
    ("http://127.0.0.1:*", "https://127.0.0.1:8732/", False),
    ("http://127.0.0.1:*", "http://192.168.1.5:8732/", False),
    ("http://127.0.0.1:*", "http://localhost:8732/", False),
    ("http://localhost:*", "http://localhost:8732/boq?a=1", True),
    ("http://localhost:*", "http://127.0.0.1:8732/", False),
    ("http://127.0.0.1:*/*", "http://127.0.0.1:8732/", True),
    ("http://127.0.0.1:8732", "http://127.0.0.1:8732/", True),
    ("http://127.0.0.1:8732", "http://127.0.0.1:49512/", False),
)

PATTERN_RE = re.compile(r"(?P<scheme>[a-z][a-z0-9+.\-]*)://(?P<host>[^/:]+)(?::(?P<port>[^/]+))?(?P<path>/.*)?\Z")

# The address the launcher hands the webview, and only that. The API calls in
# the same file are written as `http://127.0.0.1:{port}/api/health` and friends,
# so requiring the format string to end right after the root slash separates the
# page origin from the requests the launcher makes on its own behalf.
APP_ORIGIN_RE = re.compile(r'format!\("http://(?P<host>[^"/:]+):\{[A-Za-z_][A-Za-z0-9_]*\}/"\)')

HANDLER_RE = re.compile(r"generate_handler!\[(?P<body>.*?)\]", re.DOTALL)

COMMENT_RE = re.compile(r"//.*$", re.MULTILINE)


def pattern_matches(pattern: str, url: str) -> bool:
    """Does `pattern` cover `url`, for the subset of URLPattern Tauri needs here.

    Supports `*` in the host and the port, and a `/*` or absent path meaning any
    path. Anything richer than that is refused rather than guessed at, because a
    pattern this cannot reason about is one nobody reading the capability file
    can reason about either.
    """
    parsed = PATTERN_RE.fullmatch(pattern)
    if parsed is None:
        return False
    target = urlsplit(url)

    if parsed["scheme"] != target.scheme:
        return False

    host = parsed["host"]
    if host != "*" and host.lower() != (target.hostname or "").lower():
        return False

    port = parsed["port"]
    target_port = target.port if target.port is not None else DEFAULT_PORTS.get(target.scheme)
    if port is None:
        if target_port != DEFAULT_PORTS.get(parsed["scheme"]):
            return False
    elif port != "*" and (not port.isdigit() or int(port) != target_port):
        return False

    path = parsed["path"]
    if path in (None, "", "/", "/*"):
        return True
    if path.endswith("/*"):
        return target.path.startswith(path[:-1])
    return target.path == path


def self_test_matcher() -> list[str]:
    """Prove the matcher still answers the way the real one does."""
    return [
        f"matcher disagrees with tauri_utils: {pattern!r} vs {url} said "
        f"{pattern_matches(pattern, url)}, RemoteUrlPattern says {expected}"
        for pattern, url, expected in MATCHER_SELF_TEST
        if pattern_matches(pattern, url) is not expected
    ]


def load_permission_files() -> tuple[dict[str, set[str]], dict[str, list[str]], int]:
    """Read the application's own permission definitions off disk.

    Returns the command set each permission allows, the members of each
    permission set, and how many files were read.
    """
    commands: dict[str, set[str]] = {}
    sets: dict[str, list[str]] = {}
    files = sorted(p for p in PERMISSIONS_DIR.rglob("*") if p.suffix in {".json", ".toml"})

    for path in files:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if path.suffix == ".json" else tomllib.loads(raw)
        for permission in data.get("permission", []):
            allowed = permission.get("commands", {}).get("allow", [])
            commands[permission["identifier"]] = set(allowed)
        for group in data.get("set", []):
            sets[group["identifier"]] = list(group.get("permissions", []))
        default = data.get("default")
        if isinstance(default, dict):
            sets["default"] = list(default.get("permissions", []))

    return commands, sets, len(files)


def resolve(identifier: str, commands: dict[str, set[str]], sets: dict[str, list[str]]) -> set[str]:
    """Every command an identifier grants, following permission sets."""
    seen: set[str] = set()
    pending = [identifier]
    granted: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        granted |= commands.get(current, set())
        pending.extend(sets.get(current, []))
    return granted


def load_capabilities() -> list[tuple[Path, dict]]:
    """Every capability the desktop build ships, with the file it came from."""
    found: list[tuple[Path, dict]] = []
    for path in sorted(CAPABILITIES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data if isinstance(data, list) else [data]
        found.extend((path, entry) for entry in entries)
    return found


def handler_commands() -> set[str]:
    """The commands the launcher registers, as written in generate_handler!."""
    source = COMMENT_RE.sub("", LAUNCHER.read_text(encoding="utf-8"))
    names: set[str] = set()
    for block in HANDLER_RE.finditer(source):
        for raw in block["body"].split(","):
            name = raw.strip().split("::")[-1]
            if name:
                names.add(name)
    return names


def app_origin_addresses() -> tuple[set[str], list[str]]:
    """Sample addresses the launcher can navigate the webview to."""
    source = LAUNCHER.read_text(encoding="utf-8")
    hosts = {match["host"] for match in APP_ORIGIN_RE.finditer(source)}
    addresses = [f"http://{host}:{port}/" for host in sorted(hosts) for port in SAMPLE_PORTS]
    addresses += [f"http://{host}:{SAMPLE_PORTS[0]}/boq/123?tab=items#row" for host in sorted(hosts)]
    return hosts, addresses


def main() -> int:
    problems = self_test_matcher()
    if problems:
        print("The URL pattern matcher in this gate no longer agrees with the one Tauri uses.")
        for problem in problems:
            print(f"  {problem}")
        print(
            "\nUntil that is fixed this check cannot tell a working grant from a broken one,\n"
            "so it refuses rather than reporting a pass it did not earn."
        )
        return 1

    if not PERMISSIONS_DIR.is_dir():
        print(f"{PERMISSIONS_DIR.relative_to(ROOT)} does not exist.")
        print(
            "\nThe desktop application defines no permissions of its own, so no capability can\n"
            "name one, so the application window - which Tauri treats as a remote origin - is\n"
            "refused every command it invokes. Every outbound link in the product is dead and\n"
            "silent in that state."
        )
        return 1

    commands, sets, permission_file_count = load_permission_files()
    capabilities = load_capabilities()
    handlers = handler_commands()
    hosts, addresses = app_origin_addresses()

    if not hosts:
        print("Could not find the address the launcher navigates the webview to.")
        print(
            f"\nThis gate reads it out of {LAUNCHER.relative_to(ROOT)}, looking for a format string\n"
            'written exactly as format!("http://<host>:{<port>}/"). If the launcher now builds\n'
            "that address some other way, teach this check the new shape; it cannot check a\n"
            "grant against an address it cannot find."
        )
        return 1

    granted_anywhere: set[str] = set()
    remote_grants: list[tuple[Path, dict, set[str]]] = []
    prefixed_remote: list[str] = []

    for path, capability in capabilities:
        allowed: set[str] = set()
        for entry in capability.get("permissions", []):
            identifier = entry if isinstance(entry, str) else entry.get("identifier", "")
            if ":" in identifier:
                if capability.get("remote"):
                    prefixed_remote.append(f"{path.name}: {capability.get('identifier')} -> {identifier}")
                continue
            allowed |= resolve(identifier, commands, sets)
        granted_anywhere |= allowed
        if capability.get("remote"):
            remote_grants.append((path, capability, allowed))

    ungranted = sorted(handlers - granted_anywhere)
    if ungranted:
        print("Commands the launcher registers that no capability grants: " + ", ".join(ungranted))
        print(
            "\nThe desktop application declares its own ACL, which means Tauri checks app\n"
            "commands on the local origin too, not only on the loopback origin the application\n"
            "window runs on. A registered command with no permission behind it is refused\n"
            f"wherever it is called from. Add a permission under {PERMISSIONS_DIR.relative_to(ROOT)}\n"
            "and name it from the capability whose origin needs it."
        )
        return 1

    reaching = [
        (path, capability)
        for path, capability, allowed in remote_grants
        if LINK_COMMAND in allowed
        and any(
            pattern_matches(pattern, address)
            for pattern in capability.get("remote", {}).get("urls", [])
            for address in addresses
        )
    ]

    if not reaching:
        print(f"No capability lets the application window call {LINK_COMMAND}.")
        print(
            f"\nThe launcher navigates the webview to one of {sorted(hosts)} on a port it picks at\n"
            "runtime, and Tauri classifies that as a remote origin. A capability therefore needs\n"
            'a "remote" block whose URL patterns cover that address, and permissions that resolve\n'
            f"to {LINK_COMMAND}. Without both, clicking any outbound link in the product does\n"
            "nothing at all: the command is refused before it runs.\n"
            f"\nCapabilities carrying a remote block: {len(remote_grants)}.\n"
            f"Addresses checked: {', '.join(addresses[:4])} and {len(addresses) - 4} more."
        )
        return 1

    if prefixed_remote:
        print("A remote origin is being granted a plugin permission: " + "; ".join(prefixed_remote))
        print(
            "\nA capability with a remote block describes what content served over the network may\n"
            "do. Application commands are written by us and validate their own arguments; plugin\n"
            "permissions are broader by design, and shell or process access handed to a remote\n"
            "origin is a different decision from letting a page open a link. Grant an application\n"
            "permission instead, or make the case for this one deliberately."
        )
        return 1

    names = ", ".join(sorted(capability.get("identifier", path.name) for path, capability in reaching))
    print(
        f"desktop link ACL: {permission_file_count} permission file(s) define "
        f"{len(commands)} permission(s) and {len(sets)} set(s); "
        f"all {len(handlers)} registered command(s) are granted; "
        f"{LINK_COMMAND} reaches the application window through {names}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
