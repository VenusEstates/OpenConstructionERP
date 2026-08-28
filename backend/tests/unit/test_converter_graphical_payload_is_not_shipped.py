"""We install the terminal converter and leave the graphical one behind.

Upstream ships two programs in every converter folder: the command line
``*Exporter.exe`` the backend runs as a subprocess, and a
``DDC_Community_*_converter.exe`` window for a person to click. We only ever
run the first one, so the second one and the Qt libraries that exist only for
it have no business being in our Windows installer or on a user's disk. They
were there anyway - the release workflow copied the whole upstream folder and
the runtime installer downloaded every file the GitHub listing returned - which
put about 19 MB of LGPL-3.0 Qt GUI libraries into every artefact for a window
nothing in this codebase opens.

What is safe to leave out was measured, not guessed. The PE import tables of
all 140 executables and libraries in the pinned ``DDC_CONVERTER_IFC`` tree say:

* ``IfcExporter.exe`` imports ``Qt6Core.dll`` and nothing else from Qt, and the
  RVT, DWG and DGN exporters are the same;
* ``Qt6Gui.dll``, ``Qt6Widgets.dll``, ``platforms/qwindows.dll`` and
  ``styles/qmodernwindowsstyle.dll`` are reachable only from
  ``DDC_Community_IFC_converter.exe``;
* none of the 133 files in ``datadrivenlibs/`` import Qt at all.

Converting a 4 MB IFC model with those five entries removed produced a
byte-identical XLSX - all 13 archive members - and a byte-identical JSON.

So ``Qt6Core.dll`` stays and the graphical half goes. This file keeps that
true in three places at once: the predicate, the release workflow's own list,
and what an install actually writes to disk. No network here; the listing and
the transport are stubbed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from app.core import converter_source
from app.core.converter_source import is_graphical_only
from app.modules.takeoff import router

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "desktop-release.yml"

# The PowerShell array the bundling step prunes with:
#   $graphicalOnly = @(
#     'DDC_Community_IFC_converter.exe',
#     ...
#   )
_WORKFLOW_ARRAY = re.compile(r"\$graphicalOnly\s*=\s*@\(([^)]*)\)", re.DOTALL)
_QUOTED = re.compile(r"'([^']+)'")

# What the pinned IFC folder holds at its top level, from the GitHub tree API
# at ref 45498426fd225c36a2a2a3a67993fd39c5d9d0ff. Written down so the
# predicate is exercised against a real listing rather than against examples
# chosen to suit it.
_PINNED_IFC_TOP_LEVEL: tuple[str, ...] = (
    "DDC_Community_IFC_converter.exe",
    "IfcExporter.exe",
    "LICENSE",
    "Qt6Core.dll",
    "Qt6Gui.dll",
    "Qt6Widgets.dll",
    "ReadMe_IFC_DDC_Converter.pdf",
    "THIRD-PARTY-NOTICES",
    "datadrivenlibs/ddcifc.exe",
    "platforms/qwindows.dll",
    "styles/qmodernwindowsstyle.dll",
)


def _pe_bytes(*, machine: int = 0x8664, pe_off: int = 0x80, size: int = 512) -> bytes:
    """Smallest buffer that parses as a 64-bit PE image.

    The installer verifies the architecture of everything it downloaded, so a
    stub install needs files that survive that gate.
    """
    buf = bytearray(b"MZ" + b"\x00" * (0x40 - 2))
    buf[0x3C:0x40] = pe_off.to_bytes(4, "little")
    stub = b"This program cannot be run in DOS mode.\x0d\x0a\x0a$"
    buf += stub + b"\x00" * (pe_off - len(buf) - len(stub))
    buf += b"PE\x00\x00" + machine.to_bytes(2, "little")
    buf += b"\x00" * max(0, size - len(buf))
    return bytes(buf)


# ── The predicate ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    ["IfcExporter.exe", "RvtExporter.exe", "DwgExporter.exe", "DgnExporter.exe"],
)
def test_the_command_line_exporter_is_never_left_out(name: str) -> None:
    """The one file the whole install exists to deliver."""
    assert not is_graphical_only(name)


def test_qt6core_is_never_left_out() -> None:
    """The terminal build is itself a Qt program.

    ``IfcExporter.exe`` imports ``Qt6Core.dll`` in its import table, so an
    install without it is a converter that cannot start. This is the assertion
    that stops "remove Qt" from being read as "remove all of Qt".
    """
    assert not is_graphical_only("Qt6Core.dll")


@pytest.mark.parametrize(
    "name",
    [
        "DDC_Community_IFC_converter.exe",
        "DDC_Community_DWG_converter.exe",
        "DDC_Community_DGN_converter.exe",
        "DDC_Community_Revit(2015-2026)_converter.exe",
        "DDC_Community_RVT2IFC_converter.exe",
    ],
)
def test_every_format_s_graphical_shell_is_recognised(name: str) -> None:
    """The shell is named per format, so it is matched by prefix.

    All five names are the real ones from the pinned tree, brackets and all.
    """
    assert is_graphical_only(name)


@pytest.mark.parametrize("name", ["Qt6Gui.dll", "Qt6Widgets.dll"])
def test_the_gui_only_qt_libraries_are_recognised(name: str) -> None:
    assert is_graphical_only(name)


@pytest.mark.parametrize(
    "rel",
    [
        "platforms/qwindows.dll",
        "styles/qmodernwindowsstyle.dll",
        "platforms\\qwindows.dll",
        "styles\\qmodernwindowsstyle.dll",
    ],
)
def test_the_qt_plugin_folders_are_recognised_with_either_separator(rel: str) -> None:
    """Callers pass repo paths with ``/`` and on-disk paths with ``\\``."""
    assert is_graphical_only(rel)


@pytest.mark.parametrize(
    "rel",
    [
        "LICENSE",
        "THIRD-PARTY-NOTICES",
        "ReadMe_IFC_DDC_Converter.pdf",
        "datadrivenlibs/IfcExporter.exe",
        "datadrivenlibs/ddcifc.exe",
        "datadrivenlibs/ddcalert.exe",
        "datadrivenlibs/IFC4X3_27.2_17.txexp",
        "datadrivenlibs/libxl.dll",
    ],
)
def test_the_conversion_payload_and_the_notices_are_kept(rel: str) -> None:
    """Everything the exporter loads at conversion time, plus the licences.

    ``THIRD-PARTY-NOTICES`` matters twice: dropping it would take the upstream
    attribution out of an artefact that still ships Qt6Core.
    """
    assert not is_graphical_only(rel)


def test_a_community_named_file_inside_datadrivenlibs_is_kept() -> None:
    """The shell match is top level only.

    ``datadrivenlibs/`` is the conversion payload. A prefix match that reached
    into it would delete a library the exporter loads, and the install would
    fail at the first conversion rather than at install time.
    """
    assert not is_graphical_only("datadrivenlibs/DDC_Community_helper.exe")


def test_an_empty_path_is_not_graphical() -> None:
    """A listing entry with no path must not select the folder itself."""
    assert not is_graphical_only("")


def test_the_predicate_splits_the_pinned_listing_the_way_we_measured() -> None:
    """Both directions on one real listing, so neither half can drift alone."""
    graphical = {rel for rel in _PINNED_IFC_TOP_LEVEL if is_graphical_only(rel)}
    assert graphical == {
        "DDC_Community_IFC_converter.exe",
        "Qt6Gui.dll",
        "Qt6Widgets.dll",
        "platforms/qwindows.dll",
        "styles/qmodernwindowsstyle.dll",
    }


# ── The release workflow's copy of the list ──────────────────────────────
#
# The workflow is PowerShell and cannot import the predicate, so its list is a
# second copy by necessity. Equality is the assertion: a list that drifts is
# how the graphical build gets back into the installer while the backend still
# believes it was left out.


def _workflow_graphical_names() -> list[str]:
    if not WORKFLOW_PATH.is_file():
        pytest.fail(
            f"{WORKFLOW_PATH} does not exist. It carries the step that prunes the "
            f"graphical converter payload out of the Windows installer; if the "
            f"workflow moved, point this test at its new path rather than deleting it."
        )
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    match = _WORKFLOW_ARRAY.search(text)
    if match is None:
        pytest.fail(
            "No '$graphicalOnly = @( ... )' array found in desktop-release.yml. The "
            "bundling step must strip the graphical shell and its Qt libraries before "
            "the Tauri build packages resources - if that step was rewritten, teach "
            "this gate the new shape rather than dropping it."
        )
    return _QUOTED.findall(match.group(1))


def test_the_workflow_prunes_exactly_what_the_predicate_calls_graphical() -> None:
    names = _workflow_graphical_names()
    assert sorted(names) == sorted(
        [
            "DDC_Community_IFC_converter.exe",
            "Qt6Gui.dll",
            "Qt6Widgets.dll",
            "platforms",
            "styles",
        ]
    ), f"desktop-release.yml prunes {sorted(names)}"


def test_every_name_the_workflow_prunes_is_graphical_only() -> None:
    """The two copies checked against each other rather than each on its own."""
    for name in _workflow_graphical_names():
        assert is_graphical_only(name), (
            f"desktop-release.yml deletes {name!r} from the bundled converter, but "
            f"app/core/converter_source.py does not consider it graphical-only. One "
            f"of the two is wrong and the installer is the one users get."
        )


def test_the_workflow_does_not_prune_the_library_the_exporter_needs() -> None:
    """A red this gate can actually go: Qt6Core.dll in that array ships a dead exe."""
    assert "Qt6Core.dll" not in _workflow_graphical_names()


def test_the_workflow_still_asserts_qt6core_survived_the_prune() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "Qt6Core.dll missing after the graphical-only prune" in text, (
        "The bundling step no longer fails when Qt6Core.dll is absent after pruning. "
        "Without that check the installer can ship an IfcExporter.exe that Windows "
        "refuses to start, and nothing in the build says so."
    )


# ── What an install actually writes ──────────────────────────────────────

_PUBLISHED: dict[str, bytes] = {
    "DgnExporter.exe": _pe_bytes(),
    "DDC_Community_DGN_converter.exe": _pe_bytes(),
    "Qt6Core.dll": _pe_bytes(),
    "Qt6Gui.dll": _pe_bytes(),
    "Qt6Widgets.dll": _pe_bytes(),
    "LICENSE": b"placeholder text, not a binary and not a licence\n",
    "platforms/qwindows.dll": _pe_bytes(),
    "styles/qmodernwindowsstyle.dll": _pe_bytes(),
    "datadrivenlibs/ddcdgn.exe": _pe_bytes(),
}


def _run_install(
    monkeypatch: pytest.MonkeyPatch,
    install_dir: Path,
    published: dict[str, bytes],
) -> Path:
    """Run the real Windows installer with the listing and transport stubbed."""
    src = router._WINDOWS_CONVERTER_DIRS["dgn"]
    base = "https://raw.githubusercontent.com/org/repo/main"
    entries: list[dict[str, Any]] = [
        {"path": f"{src}/{rel}", "download_url": f"{base}/{rel}", "size": len(data)} for rel, data in published.items()
    ]
    by_url = {f"{base}/{rel}": data for rel, data in published.items()}

    def _fake_download(url: str, target: Path) -> int:
        data = by_url[url]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return len(data)

    monkeypatch.setattr(router, "_CONVERTER_INSTALL_DIR", install_dir)
    monkeypatch.setattr(router, "_github_list_directory", lambda _path: entries)
    monkeypatch.setattr(router, "_download_one_file", _fake_download)
    return router._download_converter_files_windows("dgn", clean=False)


def test_an_install_writes_the_exporter_and_the_library_it_needs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _run_install(monkeypatch, tmp_path, _PUBLISHED)
    dest = tmp_path / "dgn_windows"
    assert (dest / "DgnExporter.exe").is_file()
    assert (dest / "Qt6Core.dll").is_file()
    assert (dest / "datadrivenlibs" / "ddcdgn.exe").is_file()
    assert (dest / "LICENSE").is_file()


@pytest.mark.parametrize(
    "rel",
    [
        "DDC_Community_DGN_converter.exe",
        "Qt6Gui.dll",
        "Qt6Widgets.dll",
        "platforms/qwindows.dll",
        "styles/qmodernwindowsstyle.dll",
    ],
)
def test_an_install_does_not_write_the_graphical_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rel: str
) -> None:
    """Measured on the folder afterwards, not on the job list beforehand."""
    _run_install(monkeypatch, tmp_path, _PUBLISHED)
    assert not (tmp_path / "dgn_windows" / Path(rel)).exists()


def test_a_reinstall_clears_a_graphical_payload_an_older_install_left(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Users who installed before this change have the GUI half on disk.

    The installer prunes whatever is not in the download set, so skipping the
    graphical files also takes them out of an existing folder. That is the
    only route by which an already-installed converter loses them.
    """
    dest = tmp_path / "dgn_windows"
    (dest / "styles").mkdir(parents=True)
    stale_lib = dest / "Qt6Gui.dll"
    stale_lib.write_bytes(_pe_bytes())
    stale_plugin = dest / "styles" / "qmodernwindowsstyle.dll"
    stale_plugin.write_bytes(_pe_bytes())

    _run_install(monkeypatch, tmp_path, _PUBLISHED)

    assert not stale_lib.exists()
    assert not stale_plugin.exists()
    assert (dest / "Qt6Core.dll").is_file(), "the prune must not take the library we need with it"


def test_a_folder_without_the_gui_libraries_is_a_complete_install(tmp_path: Path) -> None:
    """The resolver's own definition of "complete" had to move with this.

    ``missing_companion_files`` used to require Qt6Gui.dll and Qt6Widgets.dll
    beside the exe. Left as it was, every install this change produces would be
    reported as a partial one, demoted in ``find_converter`` and called
    unhealthy by the health check.
    """
    from app.modules.boq import cad_import

    exe = tmp_path / "DgnExporter.exe"
    exe.write_bytes(_pe_bytes())
    (tmp_path / "Qt6Core.dll").write_bytes(_pe_bytes())

    assert cad_import.missing_companion_files(exe) == []


def test_a_folder_without_qt6core_is_still_reported_as_partial(tmp_path: Path) -> None:
    """The check kept its point: it is narrower now, not gone."""
    from app.modules.boq import cad_import

    exe = tmp_path / "DgnExporter.exe"
    exe.write_bytes(_pe_bytes())

    assert cad_import.missing_companion_files(exe) == ["Qt6Core.dll"]


def test_the_installer_reads_the_shared_predicate() -> None:
    """One declaration, not a second copy that agrees today."""
    assert router.is_graphical_only is converter_source.is_graphical_only
