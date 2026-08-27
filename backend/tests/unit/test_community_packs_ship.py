# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""The community packs have to be packaged, and then they have to be findable.

Nineteen packs sat in the repository and no pip or desktop install had ever
listed one. Two independent defects, either of which alone was enough:

1. ``backend/pyproject.toml`` packaged ``app`` and ``openconstructionerp`` and
   nothing else, so the ``packs/`` tree never entered the wheel.
2. ``app/core/partner_pack/discovery.py`` located the tree by counting five
   parent directories up from its own file. That reaches the repo root in a
   source checkout and the virtualenv's ``Lib`` directory in an install, where
   nothing has ever existed.

Each defect hid the other. Fixing the packaging alone ships bytes nothing
looks at; fixing the resolution alone points at a directory that was never
shipped. So this file checks both halves, and checks them the way the bug
demanded: the resolution half is exercised against a synthetic *installed*
layout, because a check that only ever runs in the source tree is precisely
the blindness that let this survive.

What is deliberately not here: proof that a built wheel contains the files.
Configuration that looks right can still produce an archive that does not, so
that assertion belongs on the artefact and lives in the wheel-inspection step
of ``.github/workflows/pypi-publish.yml``.

Pure filesystem and AST work, no database and no application import beyond the
discovery module itself, so nothing here can be skipped by a database marker.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

from app.core.partner_pack.discovery import _packs_dir_for

# backend/tests/unit/<this file> -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PACKS_DIR = _REPO_ROOT / "packs"
_PYPROJECT = _REPO_ROOT / "backend" / "pyproject.toml"

# The dotted name discovery.py is imported under. The resolver reads its depth
# from this string, so the synthetic layouts below have to use the real one.
_DISCOVERY_MODULE = "app.core.partner_pack.discovery"


def _pack_dirs() -> list[Path]:
    """Every directory under ``packs/`` that holds a loadable pack package."""
    return sorted(d for d in _PACKS_DIR.iterdir() if d.is_dir() and any(d.glob("src/openconstructionerp_*")))


def _manifest_path(pack_dir: Path) -> Path | None:
    for pkg_dir in sorted((pack_dir / "src").glob("openconstructionerp_*")):
        candidate = pkg_dir / "manifest.py"
        if candidate.is_file():
            return candidate
    return None


def _declared_partner_url(manifest_path: Path) -> str | None:
    """Return the pack's ``partner_url``, read without executing the manifest.

    ``None`` means the pack names no outside rights holder. A string means it
    does. The manifest is parsed rather than imported: this file must stay
    runnable with no database and no import side effects, and nineteen
    ``exec_module`` calls to read one keyword would be neither.
    """
    tree = ast.parse(manifest_path.read_text(encoding="utf-8"), str(manifest_path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "partner_url":
                continue
            value = keyword.value
            if isinstance(value, ast.Constant) and (value.value is None or isinstance(value.value, str)):
                return value.value
            raise AssertionError(
                f"{manifest_path} declares partner_url as {ast.dump(value)}, which this reader "
                f"cannot evaluate. It is the signal that decides whether the pack goes out in a "
                f"community artefact, so extend the reader rather than letting it guess."
            )
    raise AssertionError(
        f"{manifest_path} declares no partner_url at all. Every pack manifest states it, and the "
        f"absence would silently read as 'no outside rights holder' here."
    )


def _is_deprecated(pack_dir: Path) -> bool:
    """Mirror the skip discovery.py already applies to a deprecated pack."""
    return any(pack_dir.rglob("DEPRECATED.txt"))


def _shippable_slugs() -> set[str]:
    """The packs a community artefact may carry, computed from the tree itself.

    Two disqualifying signals, both properties of the pack rather than of a
    list someone maintains:

    * a ``DEPRECATED.txt`` anywhere under the pack, which discovery.py already
      refuses to load, so shipping it would ship bytes nothing can use;
    * a ``partner_url``, which is a pack naming an outside rights holder. Those
      carry a third party's name, logo and colours under a partnership
      agreement, and an AGPL community wheel is not the artefact that
      redistributes them.

    Computed rather than written down so that adding a pack does not silently
    change what ships in either direction.
    """
    slugs = set()
    for pack_dir in _pack_dirs():
        if _is_deprecated(pack_dir):
            continue
        manifest_path = _manifest_path(pack_dir)
        assert manifest_path is not None, f"{pack_dir.name} has a package dir but no manifest.py"
        if _declared_partner_url(manifest_path) is not None:
            continue
        slugs.add(pack_dir.name)
    return slugs


def _force_included_slugs() -> set[str]:
    """Pack slugs the wheel force-include map ships, read from pyproject.toml."""
    with open(_PYPROJECT, "rb") as handle:
        data = tomllib.load(handle)
    force_include = data["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    slugs = set()
    for source in force_include:
        parts = Path(source).as_posix().split("/")
        if len(parts) >= 3 and parts[0] == ".." and parts[1] == "packs":
            slugs.add(parts[2])
    return slugs


def test_the_pack_tree_is_readable_at_all() -> None:
    """Guard the instrument before the comparisons that lean on it.

    Every assertion below is a set difference, and two empty sets agree. If the
    tree moved or the layout changed, this says so instead of reporting a clean
    sweep over nothing.
    """
    packs = _pack_dirs()
    print(f"\n{len(packs)} pack packages under {_PACKS_DIR}: {[p.name for p in packs]}")
    assert len(packs) >= 10, (
        f"only {len(packs)} pack directories were found under {_PACKS_DIR}. The repository carries "
        f"far more than that, so this reader is no longer looking at the tree it thinks it is."
    )


def test_every_shippable_pack_is_force_included() -> None:
    """The direction that reproduces the original defect: packs that never ship."""
    missing = _shippable_slugs() - _force_included_slugs()
    assert not missing, (
        f"{len(missing)} pack(s) carry no disqualifying signal and are not force-included into the "
        f"wheel, so no pip or desktop install can list them: {sorted(missing)}. Add "
        f'\'"../packs/<slug>/src" = "packs/<slug>/src"\' to '
        f"[tool.hatch.build.targets.wheel.force-include] in backend/pyproject.toml, and the matching "
        f"entry to _COMMUNITY_PACKS in desktop/pyinstaller.spec."
    )


def test_no_withheld_pack_is_force_included() -> None:
    """The other direction, which matters more: a release cannot be taken back."""
    surplus = _force_included_slugs() - _shippable_slugs()
    assert not surplus, (
        f"{len(surplus)} pack(s) are force-included into the community wheel while carrying a "
        f"signal that says they should not be: {sorted(surplus)}. Either the pack is deprecated, or "
        f"it declares a partner_url and so names an outside rights holder. Remove the force-include "
        f"line, or change the signal in the pack if the decision has genuinely changed."
    )


@pytest.mark.parametrize("slug", sorted(_force_included_slugs()))
def test_every_shipped_pack_declares_an_open_licence(slug: str) -> None:
    """A pack in the community wheel has to say it is AGPL, in its own metadata."""
    pyproject = _PACKS_DIR / slug / "pyproject.toml"
    assert pyproject.is_file(), f"{slug} is force-included into the wheel and has no pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert "AGPL-3.0-or-later" in text, (
        f"packs/{slug}/pyproject.toml does not declare AGPL-3.0-or-later, and the pack is shipped "
        f"inside the AGPL community wheel. Sort the licence out before the next release."
    )


def _plant_pack(packs_dir: Path) -> None:
    """Create the smallest tree that counts as a real pack tree."""
    pkg = packs_dir / "demo-pack" / "src" / "openconstructionerp_demo_pack"
    pkg.mkdir(parents=True)
    (pkg / "manifest.py").write_text("MANIFEST = {}\n", encoding="utf-8")


def _discovery_at(root: Path) -> Path:
    """Path discovery.py would occupy under an install root holding ``app``."""
    return root / "app" / "core" / "partner_pack" / "discovery.py"


def test_the_packs_dir_resolves_in_an_installed_layout(tmp_path: Path) -> None:
    """The case that was broken for every user and that nothing measured.

    In a wheel install discovery.py sits at
    ``site-packages/app/core/partner_pack/discovery.py`` and the packs are
    force-included beside the package at ``site-packages/packs``. The old code
    counted five parents from the file, walked out of site-packages entirely
    and landed on the virtualenv's ``Lib``, where no packs directory has ever
    existed, so the UI reported none. No source-tree test could see it.
    """
    site_packages = tmp_path / "Lib" / "site-packages"
    _plant_pack(site_packages / "packs")

    resolved = _packs_dir_for(_discovery_at(site_packages), _DISCOVERY_MODULE)

    assert resolved == (site_packages / "packs").resolve(), (
        f"an installed layout resolved to {resolved}, not the packs directory shipped beside the "
        f"app package. This is the exact shape of the defect this test exists for."
    )


def test_the_packs_dir_resolves_in_a_source_checkout(tmp_path: Path) -> None:
    """The layout that worked before must keep working. Both, or neither."""
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    _plant_pack(repo / "packs")

    resolved = _packs_dir_for(_discovery_at(repo / "backend"), _DISCOVERY_MODULE)

    assert resolved == (repo / "packs").resolve(), (
        f"a source checkout resolved to {resolved}, not the repo's packs tree. The fix for the "
        f"install layout must not cost the checkout the behaviour it already had."
    )


def test_a_directory_that_only_has_the_name_is_not_the_tree(tmp_path: Path) -> None:
    """Shape, not existence. Existence alone is how the original arithmetic lied.

    An install root can hold an unrelated ``packs`` directory, and accepting it
    would leave discovery scanning the wrong place while reporting that it
    found somewhere to scan. The candidate has to contain a pack.
    """
    repo = tmp_path / "repo"
    (repo / "backend" / "packs" / "not-a-pack").mkdir(parents=True)
    _plant_pack(repo / "packs")

    resolved = _packs_dir_for(_discovery_at(repo / "backend"), _DISCOVERY_MODULE)

    assert resolved == (repo / "packs").resolve(), (
        f"resolved to {resolved}. A directory named 'packs' holding no pack was accepted over the "
        f"real tree one level up, so the shape check is not doing its job."
    )


def test_no_pack_tree_anywhere_resolves_to_none(tmp_path: Path) -> None:
    """An install carrying no packs must say so, not point somewhere arbitrary."""
    resolved = _packs_dir_for(_discovery_at(tmp_path / "site-packages"), _DISCOVERY_MODULE)
    assert resolved is None, f"expected None for a layout with no packs anywhere, got {resolved}"


def test_the_resolver_reads_its_depth_from_the_module_name(tmp_path: Path) -> None:
    """Pin the derivation itself, which is what stops the arithmetic drifting again.

    The depth comes from the dotted name, so a module at a different nesting
    resolves differently with no constant edited anywhere.

    Stated exactly, because the loose version of this claim is false and was
    measured to be: the resolver tries two candidates, the install root and one
    directory above it, so a depth constant that is wrong by a single level is
    absorbed by that window and this test cannot see it. What it does catch is a
    written-down depth that no longer matches where the module actually sits,
    which is the shape of the original defect. Hence a module nested two levels
    deeper than the real one rather than one.
    """
    site_packages = tmp_path / "site-packages"
    _plant_pack(site_packages / "packs")

    deeper = site_packages / "app" / "core" / "partner_pack" / "one" / "two" / "discovery.py"
    resolved = _packs_dir_for(deeper, "app.core.partner_pack.one.two.discovery")

    assert resolved == (site_packages / "packs").resolve(), (
        f"a six-component module name resolved to {resolved}. The depth is supposed to come from "
        f"the module's own name rather than a written-down number of directory levels."
    )
