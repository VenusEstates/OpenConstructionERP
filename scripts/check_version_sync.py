#!/usr/bin/env python3
"""‌⁠‍Verify backend and frontend​‌‍⁠​‌‍⁠​‌‍⁠​‌‍⁠ version literals are in sync.

The OpenConstructionERP frontend (``frontend/package.json``) and the
Python package (``backend/pyproject.toml``) MUST report the same version
because the running app reads its version from the installed Python
package via ``importlib.metadata.version("openconstructionerp")``.
A drift between the two files means ``/api/health`` lies about which
version users are actually running.

This script is wired into both the local pre-commit hook and the
GitHub Actions CI workflow so the drift can never make it past a
commit again.  History note: v1.3.32 → v1.4.2 silently shipped with
``backend/pyproject.toml`` stuck at ``1.3.31`` because nothing was
guarding it; this script was added in v1.4.4 to make the same gap
impossible.

Exit codes:
    0  - versions match (and matched ``CHANGELOG.md`` and the visible
         in-app changelog if those files were updated in the same diff)
    1  - versions drift, missing version literals, or unparseable files

Usage::

    python scripts/check_version_sync.py

Run from anywhere. The script resolves paths relative to the repo
root (one level up from this file).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PYPROJECT = REPO_ROOT / "backend" / "pyproject.toml"
PACKAGE_JSON = REPO_ROOT / "frontend" / "package.json"
PACKAGE_LOCK = REPO_ROOT / "frontend" / "package-lock.json"
CHANGELOG_MD = REPO_ROOT / "CHANGELOG.md"
CHANGELOG_TSX = REPO_ROOT / "frontend" / "src" / "features" / "about" / "Changelog.tsx"
TAURI_CONF = REPO_ROOT / "desktop" / "src-tauri" / "tauri.conf.json"
CARGO_TOML = REPO_ROOT / "desktop" / "src-tauri" / "Cargo.toml"
CARGO_LOCK = REPO_ROOT / "desktop" / "src-tauri" / "Cargo.lock"
INDEX_HTML = REPO_ROOT / "frontend" / "index.html"

# Match `version = "1.4.4"` in pyproject.toml, first occurrence only,
# under the [project] table.  We deliberately stop at the first hit
# instead of using a real TOML parser to keep the script dependency-free.
_PYPROJECT_RE = re.compile(r'^\s*version\s*=\s*"([^"]+)"', re.MULTILINE)

# Slice the `[package]` table out of a Cargo manifest, up to the next
# `[section]` header or end of file.  Anchoring on the table is the whole
# point: a manifest carries a `version` for every dependency as well, so a
# first-match search would happily read `tauri = { version = "2" }` and then
# pass forever while validating a literal nobody bumps.  Today those
# dependency versions live in inline tables and would not match the pattern
# below anyway, but one `[dependencies.tauri]` section written the long way
# is enough to move the first match, and nothing would say so.
_CARGO_PACKAGE_RE = re.compile(r"^\[package\]\s*$(.*?)(?=^\[|\Z)", re.MULTILINE | re.DOTALL)
_CARGO_VERSION_RE = re.compile(r'^\s*version\s*=\s*"([^"]+)"', re.MULTILINE)
_CARGO_NAME_RE = re.compile(r'^\s*name\s*=\s*"([^"]+)"', re.MULTILINE)

# The same slicing for Cargo.lock, which records one `[[package]]` entry per
# resolved crate.  The stakes are higher here than in the manifest: the lock
# carries 538 of these blocks and 538 version literals, so a reader that is not
# anchored has a whole field of decoys to land on and would report some
# alphabetically unlucky dependency as the desktop version, forever, in green.
_CARGO_LOCK_ENTRY_RE = re.compile(r"^\[\[package\]\]\s*$(.*?)(?=^\[|\Z)", re.MULTILINE | re.DOTALL)


def _read_pyproject_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = _PYPROJECT_RE.search(text)
    if match is None:
        raise SystemExit(f'[FAIL] {path}: no `version = "..."` literal found')
    return match.group(1)


def _read_package_json_version(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise SystemExit(f"[FAIL] {path}: missing or non-string `version` field")
    return version


def _read_package_lock_versions(path: Path) -> tuple[str, str]:
    """Return the root version recorded in the lockfile, from both places.

    npm writes the project's own version twice: once at the top level and
    once as the empty-string entry in ``packages``. A plain ``npm version``
    bump updates both, but editing ``package.json`` by hand updates neither,
    and nothing else in the repository reads the lockfile's copy. It sat at
    12.6.1 through two releases without anything noticing.

    That matters beyond tidiness because ``npm ci`` is the one command that
    treats the lockfile as authoritative, and it is the first step of the
    Docker build. Any npm that decides to validate this field turns a stale
    literal into a build that fails for everyone who clones the tag, while
    the developer who bumped by hand never sees it locally.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    root = data.get("version")
    packages = data.get("packages")
    own = packages.get("", {}).get("version") if isinstance(packages, dict) else None
    if not isinstance(root, str) or not isinstance(own, str):
        raise SystemExit(f"[FAIL] {path}: missing `version` or `packages[''].version`")
    return root, own


def _read_tauri_conf_version(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise SystemExit(f"[FAIL] {path}: missing or non-string `version` field")
    return version


def _read_cargo_toml_version(path: Path) -> str:
    """Return the crate version from the `[package]` table of a Cargo manifest.

    The Tauri shell is a Rust crate and its manifest carries the version the
    compiled binary reports, independently of ``tauri.conf.json``.  It went
    unchecked until v14.3.0 and drifted eleven minor releases behind while
    every other literal stayed in step, which is exactly the shape of gap the
    rest of this script exists to close.
    """
    text = path.read_text(encoding="utf-8")
    package = _CARGO_PACKAGE_RE.search(text)
    if package is None:
        raise SystemExit(f"[FAIL] {path}: no `[package]` table found")
    match = _CARGO_VERSION_RE.search(package.group(1))
    if match is None:
        raise SystemExit(f'[FAIL] {path}: no `version = "..."` literal under `[package]`')
    return match.group(1)


def _read_cargo_toml_name(path: Path) -> str:
    """Return the crate name from the `[package]` table of a Cargo manifest."""
    package = _CARGO_PACKAGE_RE.search(path.read_text(encoding="utf-8"))
    if package is None:
        raise SystemExit(f"[FAIL] {path}: no `[package]` table found")
    match = _CARGO_NAME_RE.search(package.group(1))
    if match is None:
        raise SystemExit(f'[FAIL] {path}: no `name = "..."` literal under `[package]`')
    return match.group(1)


def _read_cargo_lock_version(path: Path, crate: str) -> str:
    """Return the version Cargo.lock records for the crate's own entry.

    Cargo rewrites this entry on the next build if it disagrees with the
    manifest, so a stale one is a lockfile that lies until someone compiles.
    The crate name is read out of Cargo.toml rather than written down here, so
    a rename cannot leave this quietly reading a name that no longer exists.
    """
    for entry in _CARGO_LOCK_ENTRY_RE.finditer(path.read_text(encoding="utf-8")):
        block = entry.group(1)
        name = _CARGO_NAME_RE.search(block)
        if name is None or name.group(1) != crate:
            continue
        match = _CARGO_VERSION_RE.search(block)
        if match is None:
            raise SystemExit(f"[FAIL] {path}: `{crate}` entry carries no version")
        return match.group(1)
    raise SystemExit(f"[FAIL] {path}: no `[[package]]` entry named `{crate}`")


def _read_index_html_software_version(path: Path) -> str:
    """Return the version the served page claims in its structured data.

    `frontend/index.html` carries a schema.org SoftwareApplication block, and
    `softwareVersion` in it is a public statement about which release a visitor
    is looking at. Nothing was reading it, so it was bumped by hand twice in the
    product's whole history and then sat at 7.6.0 while the product shipped
    14.8.1, eight majors later. Search engines and anyone reading the page
    source were told the wrong release for months.

    Unlike every other literal here this one is not consumed by a build, so a
    stale value fails nothing and shows up nowhere except in what the page
    tells the world about itself. That is precisely why it needs a gate rather
    than a convention.
    """
    text = path.read_text(encoding="utf-8")
    match = re.search(r'"softwareVersion"\s*:\s*"([^"]+)"', text)
    if match is None:
        raise SystemExit(f'[FAIL] {path}: no `"softwareVersion": "..."` literal found')
    return match.group(1)


def _changelog_md_top_version(path: Path) -> str | None:
    """‌⁠‍Return the topmost version listed in CHANGELOG.md, or None.

    The CHANGELOG follows the Keep a Changelog format with entries
    like ``## [1.4.4] - 2026-04-11``.  We grab the first ``## [N.N.N]``
    we encounter as the "current" version.
    """
    if not path.exists():
        return None
    pattern = re.compile(r"^##\s*\[(\d+\.\d+\.\d+)\]", re.MULTILINE)
    match = pattern.search(path.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def _changelog_tsx_top_version(path: Path) -> str | None:
    """‌⁠‍Return the topmost ``version: '1.2.3'`` in the in-app Changelog.

    Looks for the first ``version: '...'`` literal in the file. The
    React component lists newest first, so the top one is "current".
    """
    if not path.exists():
        return None
    pattern = re.compile(r"version:\s*['\"](\d+\.\d+\.\d+)['\"]")
    match = pattern.search(path.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def main() -> int:
    backend_version = _read_pyproject_version(PYPROJECT)
    frontend_version = _read_package_json_version(PACKAGE_JSON)
    lock_root, lock_own = _read_package_lock_versions(PACKAGE_LOCK)
    changelog_md_version = _changelog_md_top_version(CHANGELOG_MD)
    changelog_tsx_version = _changelog_tsx_top_version(CHANGELOG_TSX)
    tauri_version = _read_tauri_conf_version(TAURI_CONF)
    cargo_version = _read_cargo_toml_version(CARGO_TOML)
    crate_name = _read_cargo_toml_name(CARGO_TOML)
    lock_version = _read_cargo_lock_version(CARGO_LOCK, crate_name)
    index_html_version = _read_index_html_software_version(INDEX_HTML)

    print(f"backend  ({PYPROJECT.name})       = {backend_version}")
    print(f"frontend ({PACKAGE_JSON.name})    = {frontend_version}")
    print(f"frontend ({PACKAGE_LOCK.name}) = {lock_root}")
    print(f"changelog ({CHANGELOG_MD.name})    = {changelog_md_version or '?'}")
    print(f"changelog ({CHANGELOG_TSX.name})   = {changelog_tsx_version or '?'}")
    print(f"desktop  ({TAURI_CONF.name})    = {tauri_version}")
    print(f"desktop  ({CARGO_TOML.name})         = {cargo_version}")
    print(f"desktop  ({CARGO_LOCK.name})         = {lock_version}")
    print(f"frontend ({INDEX_HTML.name})       = {index_html_version}")

    failures: list[str] = []

    if backend_version != frontend_version:
        failures.append(
            f"[FAIL] backend/pyproject.toml ({backend_version}) does not match "
            f"frontend/package.json ({frontend_version})"
        )

    for label, found in (("version", lock_root), ("packages[''].version", lock_own)):
        if found != frontend_version:
            failures.append(
                f"[FAIL] frontend/package-lock.json {label} ({found}) does not "
                f"match frontend/package.json ({frontend_version}) - run "
                f"`npm install --package-lock-only` in frontend/ and commit the "
                f"lockfile alongside the bump"
            )

    if tauri_version != backend_version:
        failures.append(
            f"[FAIL] desktop/src-tauri/tauri.conf.json ({tauri_version}) does not "
            f"match backend version ({backend_version}) - bump the desktop app "
            f"version so the installers report the right release"
        )

    if cargo_version != backend_version:
        failures.append(
            f"[FAIL] desktop/src-tauri/Cargo.toml ({cargo_version}) does not "
            f"match backend version ({backend_version}) - bump the desktop crate "
            f"version so the compiled binary reports the right release, and set "
            f"the crate's own entry in desktop/src-tauri/Cargo.lock to match"
        )

    if lock_version != backend_version:
        failures.append(
            f"[FAIL] desktop/src-tauri/Cargo.lock `{crate_name}` ({lock_version}) "
            f"does not match backend version ({backend_version}) - cargo rewrites "
            f"this entry on the next build anyway, so a stale one only means the "
            f"lockfile disagrees with the manifest until someone compiles"
        )

    if index_html_version != backend_version:
        failures.append(
            f"[FAIL] frontend/index.html softwareVersion ({index_html_version}) "
            f"does not match backend version ({backend_version}) - the page tells "
            f"search engines and anyone reading its source which release this is, "
            f"and nothing else in the build corrects it"
        )

    # CHANGELOG drift is a softer warning, only flag if BOTH changelog
    # files have a top entry but they don't match the source-of-truth
    # version.  A missing entry just means the bump is in progress.
    if changelog_md_version and changelog_md_version != backend_version:
        failures.append(
            f"[FAIL] CHANGELOG.md top entry [{changelog_md_version}] does not "
            f"match backend version ({backend_version}), add a new entry"
        )
    if changelog_tsx_version and changelog_tsx_version != backend_version:
        failures.append(
            f"[FAIL] Changelog.tsx top entry version='{changelog_tsx_version}' "
            f"does not match backend version ({backend_version}), add a "
            f"new entry to the visible in-app changelog"
        )

    if failures:
        print()
        for failure in failures:
            print(failure)
        print()
        print(
            "Fix: bump backend/pyproject.toml + frontend/package.json + "
            "frontend/package-lock.json + CHANGELOG.md + "
            "frontend/src/features/about/Changelog.tsx + "
            "desktop/src-tauri/tauri.conf.json + desktop/src-tauri/Cargo.toml + "
            "desktop/src-tauri/Cargo.lock + frontend/index.html in a single "
            "commit so the running app, "
            "the desktop installers and the docs stay honest about which version "
            "users are actually getting. That is ten literals across nine "
            "files: frontend/package-lock.json carries the version twice."
        )
        return 1

    print()
    print(f"[OK] All version literals consistent at {backend_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
