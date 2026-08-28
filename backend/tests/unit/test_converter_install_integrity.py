"""What the converter installer checks, and what it removes.

Issue #416. A user reported that Windows refused to start the DGN converter
with error 0xC000007B, and our own message told them a library beside it was
built for the other word size and that they should download the converter
again. Both halves were measured and both were wrong: all 248 PE images we
publish for DGN are x64, as is every historical version of every format, and
downloading again cannot remove a file - the installer overwrote and added
and never deleted anything.

Three gaps behind that, all covered here:

* The install gate read four bytes of one file out of 251 and never looked at
  the machine field, so a 32-bit build would have passed it cleanly.
* The installer mirrored in one direction only. A real install on a
  maintainer's machine still held a binary upstream had deleted three days
  before that install last ran.
* ``force`` was accepted by the endpoint and thrown away, so the Update button
  and the Install button did the same thing.

Everything here is pure filesystem work against synthetic PE headers - no
network, no database, no converter binary.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from app.core.converter_source import is_graphical_only
from app.modules.boq import cad_import
from app.modules.takeoff import router

# Machine values from IMAGE_FILE_HEADER. The first is what we publish; the
# second is the build the report accused us of shipping.
_X64 = 0x8664
_X86 = 0x014C

# Stand-in bytes for any file in a converter folder that is not a PE image.
#
# This is DELIBERATELY not the name of a real licence, and it must stay that
# way. Three sites used to spell out a real permissive licence name, chosen
# only because a licence file is the obvious example of a text file the
# architecture sweep has to skip. A later licence audit read those three
# literals as a description of what upstream actually ships, reported that the
# converters carry an Apache 2.0 LICENSE next to LGPL Qt DLLs, and filed the
# mismatch as a compliance finding. There is no such mismatch: the real
# ``LICENSE`` in every converter folder is a DataDrivenConstruction proprietary
# notice that defers third-party terms to ``THIRD-PARTY-NOTICES``.
#
# The sweep only ever asks "does this parse as a PE image", so the content is
# free. Keep it self-describing, so the next reader has nothing to mistake for
# a claim about upstream.
_NOT_A_PE_IMAGE = b"placeholder text, not a binary and not a licence\n"


def _pe_bytes(machine: int = _X64, *, pe_off: int = 0x80, size: int = 512) -> bytes:
    """Smallest buffer that parses as a PE image for the given machine.

    The DOS stub carries real ``0x0A`` bytes so that a CRLF mangle of this
    buffer actually shifts the header, which is what the corrupt-download
    case needs. The machine value is a parameter and never a default that
    happens to match: a sweep compares files against each other, so a helper
    that always emitted zero would make every comparison pass.
    """
    buf = bytearray(b"MZ" + b"\x00" * (0x40 - 2))
    buf[0x3C:0x40] = pe_off.to_bytes(4, "little")
    stub = b"This program cannot be run in DOS mode.\x0d\x0a\x0a$"
    buf += stub + b"\x00" * (pe_off - len(buf) - len(stub))
    buf += b"PE\x00\x00" + machine.to_bytes(2, "little")
    buf += b"\x00" * max(0, size - len(buf))
    return bytes(buf)


# ── Reading the machine field ────────────────────────────────────────────


def test_the_machine_field_of_a_64_bit_image_is_read(tmp_path: Path) -> None:
    path = tmp_path / "DgnExporter.exe"
    path.write_bytes(_pe_bytes(_X64))
    assert router._read_pe_machine(path) == (_X64, None)


def test_the_machine_field_of_a_32_bit_image_is_read(tmp_path: Path) -> None:
    """The value the gate exists to notice, and never used to look at."""
    path = tmp_path / "Qt6Core.dll"
    path.write_bytes(_pe_bytes(_X86))
    assert router._read_pe_machine(path) == (_X86, None)


def test_a_file_that_is_not_a_binary_is_reported_as_neither(tmp_path: Path) -> None:
    """Every converter folder ships a licence, a readme PDF and a notices file.

    Those must come back as "not a PE" rather than as a problem, or the sweep
    fails an install over the licence text.
    """
    path = tmp_path / "LICENSE"
    path.write_bytes(_NOT_A_PE_IMAGE)
    assert router._read_pe_machine(path) == (None, None)


def test_a_mangled_image_reports_a_reason_instead_of_a_machine(tmp_path: Path) -> None:
    """The WinError 216 corruption: every 0x0A rewritten as 0x0D 0x0A."""
    path = tmp_path / "Qt6Core.dll"
    path.write_bytes(_pe_bytes(_X64).replace(b"\x0a", b"\x0d\x0a"))
    machine, problem = router._read_pe_machine(path)
    assert machine is None
    assert problem is not None and "PE signature" in problem


def test_the_structural_gate_still_answers_what_it_used_to(tmp_path: Path) -> None:
    """``_verify_pe_executable`` now delegates, so pin its contract here too.

    It reports structure and says nothing about architecture - a 32-bit image
    is structurally perfect, which is exactly why one file and four bytes was
    never going to catch this.
    """
    good = tmp_path / "DgnExporter.exe"
    good.write_bytes(_pe_bytes(_X64))
    assert router._verify_pe_executable(good) is None

    thirty_two_bit = tmp_path / "Other.exe"
    thirty_two_bit.write_bytes(_pe_bytes(_X86))
    assert router._verify_pe_executable(thirty_two_bit) is None

    not_a_binary = tmp_path / "NotABinary.exe"
    not_a_binary.write_bytes(b"PK\x03\x04" + b"\x00" * 200)
    reason = router._verify_pe_executable(not_a_binary)
    assert reason is not None and "MZ" in reason


# ── The whole-tree architecture sweep ────────────────────────────────────


def _install_tree(root: Path, files: dict[str, bytes]) -> Path:
    """Materialise a converter folder and return the launched exe."""
    for rel, data in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return root / "DgnExporter.exe"


def test_a_tree_that_is_one_architecture_passes(tmp_path: Path) -> None:
    exe = _install_tree(
        tmp_path,
        {
            "DgnExporter.exe": _pe_bytes(_X64),
            "Qt6Core.dll": _pe_bytes(_X64),
            "platforms/qwindows.dll": _pe_bytes(_X64),
            "datadrivenlibs/ddcdgn.x64": _pe_bytes(_X64),
        },
    )
    assert router._verify_downloaded_architecture(tmp_path, exe) is None


def test_a_32_bit_library_beside_a_64_bit_exe_fails_the_sweep(tmp_path: Path) -> None:
    """The case the gate exists for, and the one nobody had ever seen it catch.

    A 32-bit library next to a 64-bit program is what Windows answers
    0xC000007B to. The wrong byte goes in the library and not in the exe,
    because the sweep compares against the exe: putting it in the exe would
    test the comparison in the direction that cannot happen at install time.
    """
    exe = _install_tree(
        tmp_path,
        {
            "DgnExporter.exe": _pe_bytes(_X64),
            "Qt6Core.dll": _pe_bytes(_X86),
            "Qt6Gui.dll": _pe_bytes(_X64),
        },
    )
    problem = router._verify_downloaded_architecture(tmp_path, exe)
    assert problem is not None
    assert "Qt6Core.dll" in problem
    assert "x86" in problem
    assert "Qt6Gui.dll" not in problem, "only the offender should be named"


def test_a_renamed_library_is_classified_by_signature_not_extension(tmp_path: Path) -> None:
    """185 of the 251 files in a DGN install are DLLs with the extension changed.

    ``.tx``, ``.txv``, ``.txr`` and ``.x64`` are the converter's format readers,
    loaded at runtime like any other library. An extension filter would walk
    past every one of them, so the sweep has to key on the MZ signature.
    """
    exe = _install_tree(
        tmp_path,
        {
            "DgnExporter.exe": _pe_bytes(_X64),
            "datadrivenlibs/format_reader.tx": _pe_bytes(_X86),
        },
    )
    problem = router._verify_downloaded_architecture(tmp_path, exe)
    assert problem is not None
    assert "format_reader.tx" in problem


def test_the_licence_and_the_readme_do_not_fail_an_install(tmp_path: Path) -> None:
    exe = _install_tree(
        tmp_path,
        {
            "DgnExporter.exe": _pe_bytes(_X64),
            "Qt6Core.dll": _pe_bytes(_X64),
            "LICENSE": _NOT_A_PE_IMAGE,
            "ReadMe_DGN_DDC_Converter.pdf": b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n",
            "THIRD-PARTY-NOTICES": b"Qt is licensed under the LGPL.\n",
        },
    )
    assert router._verify_downloaded_architecture(tmp_path, exe) is None


def test_a_corrupt_library_fails_the_sweep_too(tmp_path: Path) -> None:
    """0xC000007B covers a malformed image, not only a wrong word size."""
    exe = _install_tree(
        tmp_path,
        {
            "DgnExporter.exe": _pe_bytes(_X64),
            "Qt6Core.dll": _pe_bytes(_X64).replace(b"\x0a", b"\x0d\x0a"),
        },
    )
    problem = router._verify_downloaded_architecture(tmp_path, exe)
    assert problem is not None
    assert "Qt6Core.dll" in problem


def test_the_sweep_reports_the_exe_when_the_exe_is_the_broken_one(tmp_path: Path) -> None:
    exe = _install_tree(tmp_path, {"DgnExporter.exe": b"PK\x03\x04" + b"\x00" * 200})
    problem = router._verify_downloaded_architecture(tmp_path, exe)
    assert problem is not None and "DgnExporter.exe" in problem


# ── A whole install, with the network replaced ───────────────────────────


_PUBLISHED: dict[str, bytes] = {
    "DgnExporter.exe": _pe_bytes(_X64),
    "Qt6Core.dll": _pe_bytes(_X64),
    "Qt6Gui.dll": _pe_bytes(_X64),
    "Qt6Widgets.dll": _pe_bytes(_X64),
    "LICENSE": _NOT_A_PE_IMAGE,
    "platforms/qwindows.dll": _pe_bytes(_X64),
    "datadrivenlibs/ddcdgn.exe": _pe_bytes(_X64),
}


def _run_install(
    monkeypatch: pytest.MonkeyPatch,
    install_dir: Path,
    published: dict[str, bytes],
    *,
    clean: bool = False,
    on_download: Any = None,
) -> Path:
    """Run the real Windows installer with the listing and transport stubbed.

    Everything between them - target resolution, the thread pool, the
    verification gate, the prune - is the code under test.
    """
    src = router._WINDOWS_CONVERTER_DIRS["dgn"]
    base = "https://raw.githubusercontent.com/org/repo/main"
    entries = [
        {"path": f"{src}/{rel}", "download_url": f"{base}/{rel}", "size": len(data)} for rel, data in published.items()
    ]
    by_url = {f"{base}/{rel}": data for rel, data in published.items()}

    def _fake_download(url: str, target: Path) -> int:
        if on_download is not None:
            on_download()
        data = by_url[url]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return len(data)

    monkeypatch.setattr(router, "_CONVERTER_INSTALL_DIR", install_dir)
    monkeypatch.setattr(router, "_github_list_directory", lambda _path: entries)
    monkeypatch.setattr(router, "_download_one_file", _fake_download)
    return router._download_converter_files_windows("dgn", clean=clean)


def test_a_file_that_disappeared_upstream_is_gone_after_an_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The defect, reproduced from the state a real install was found in.

    Upstream deleted ``datadrivenlibs/ddcalert.exe`` from one converter, a
    reinstall ran three days later, and the file was still there afterwards
    with its old timestamp. The starting tree here is that folder.
    """
    dest = tmp_path / "dgn_windows"
    stale = dest / "datadrivenlibs" / "ddcalert.exe"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(_pe_bytes(_X64))

    _run_install(monkeypatch, tmp_path, _PUBLISHED)

    assert not stale.exists(), "a reinstall has to remove what we no longer publish"


def test_the_install_keeps_every_file_the_listing_carries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The other half. A prune that deleted the install would also pass above.

    "Every file" now means every file we install. The listing still carries the
    graphical shell and the Qt libraries only it uses, and the installer skips
    them on purpose - see
    tests/unit/test_converter_graphical_payload_is_not_shipped.py, which owns
    that decision and asserts they are absent afterwards.
    """
    exe = _run_install(monkeypatch, tmp_path, _PUBLISHED)
    dest = tmp_path / "dgn_windows"

    assert exe == dest / "DgnExporter.exe"
    installed = {rel: data for rel, data in _PUBLISHED.items() if not is_graphical_only(rel)}
    assert installed, "the fixture must still publish something we install"
    for rel, data in installed.items():
        assert (dest / rel).read_bytes() == data


def test_a_directory_the_prune_empties_is_removed_too(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A converter version that drops a whole subfolder should not leave a shell."""
    dest = tmp_path / "dgn_windows"
    orphan = dest / "styles"
    orphan.mkdir(parents=True)
    (orphan / "qmodernwindowsstyle.dll").write_bytes(_pe_bytes(_X64))

    _run_install(monkeypatch, tmp_path, _PUBLISHED)

    assert not orphan.exists()
    # datadrivenlibs/ rather than platforms/: the listing carries both, but
    # platforms/ holds Qt plugins that only the graphical shell loads and the
    # installer no longer downloads it, so it would be missing here for a
    # second reason and the assertion would stop meaning what it says.
    assert (dest / "datadrivenlibs").is_dir(), "a folder the listing still carries stays"


def test_a_forced_install_empties_the_folder_before_downloading(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``clean`` has to happen first, or it is just the prune under another name.

    Observed at download time rather than afterwards, because the prune leaves
    the same end state and would make this test pass without the wipe. The
    file watched for is one the listing does not carry, so no download can
    recreate it under a parallel worker and confuse the reading.
    """
    dest = tmp_path / "dgn_windows"
    dest.mkdir(parents=True)
    older_version = dest / "ddcalert.exe"
    older_version.write_bytes(_pe_bytes(_X64))
    seen: list[bool] = []

    _run_install(
        monkeypatch,
        tmp_path,
        _PUBLISHED,
        clean=True,
        on_download=lambda: seen.append(older_version.exists()),
    )

    assert seen and not any(seen), "the old folder must be gone before the first byte arrives"


def test_an_ordinary_install_writes_over_the_folder_in_place(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The pair for the test above - without ``clean`` the old files are still there.

    That is deliberate: an automatic download triggered by a CAD upload must
    not empty a working converter folder on a connection that then fails.
    """
    dest = tmp_path / "dgn_windows"
    dest.mkdir(parents=True)
    older_version = dest / "ddcalert.exe"
    older_version.write_bytes(_pe_bytes(_X64))
    seen: list[bool] = []

    _run_install(
        monkeypatch,
        tmp_path,
        _PUBLISHED,
        on_download=lambda: seen.append(older_version.exists()),
    )

    assert seen and all(seen), "without force the folder is written over, not emptied"
    assert not older_version.exists(), "and the prune still clears it once the download lands"


def test_a_32_bit_library_in_the_download_fails_the_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """End to end: the gate has to stop the install, not just have an opinion."""
    poisoned = dict(_PUBLISHED)
    poisoned["Qt6Core.dll"] = _pe_bytes(_X86)

    with pytest.raises(RuntimeError, match="Qt6Core.dll"):
        _run_install(monkeypatch, tmp_path, poisoned)

    assert not (tmp_path / "dgn_windows").exists(), "a tree that fails verification must not be left behind"


def test_a_stale_file_does_not_take_a_good_download_down_with_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Order matters: prune first, then sweep.

    A leftover from an older version is not ours to verify. With the sweep
    first, a stale 32-bit file would roll back a perfectly good fresh
    download - to punish a file the prune was about to delete anyway.
    """
    dest = tmp_path / "dgn_windows"
    stale = dest / "datadrivenlibs" / "ddcalert.exe"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(_pe_bytes(_X86))

    exe = _run_install(monkeypatch, tmp_path, _PUBLISHED)

    assert exe.exists()
    assert not stale.exists()


def test_a_leftover_that_cannot_be_deleted_fails_the_install_and_says_which(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The state this whole prune exists to end must not be reported as success.

    On Windows a file another process holds open cannot be deleted, and that is
    the one case where the folder keeps a binary we no longer publish. Reporting
    the install as done there would reproduce the defect with extra steps. The
    deletion is refused through a stub because a real lock is a Windows
    behaviour and this suite runs everywhere.
    """
    dest = tmp_path / "dgn_windows"
    stale = dest / "held_open_by_something.dll"
    dest.mkdir(parents=True)
    stale.write_bytes(_pe_bytes(_X64))

    real_unlink = Path.unlink

    def _refuse_one(self: Path, **kwargs: Any) -> None:
        if self.name == stale.name:
            raise OSError(13, "Permission denied")
        real_unlink(self, **kwargs)

    monkeypatch.setattr(Path, "unlink", _refuse_one)

    with pytest.raises(RuntimeError, match="held_open_by_something.dll"):
        _run_install(monkeypatch, tmp_path, _PUBLISHED)

    # The fresh download stays put: it is good, and the leftover is the problem.
    assert (dest / "DgnExporter.exe").exists()


# ── force, from the endpoint down ────────────────────────────────────────


async def test_the_update_button_asks_for_a_clean_download(monkeypatch: pytest.MonkeyPatch) -> None:
    """``force`` used to be assigned to ``_`` with a comment and dropped there.

    The download is stubbed to record the flag and then fail, which returns
    the impl straight away - the wiring is the whole subject of this test.
    """
    seen: dict[str, bool] = {}

    def _recorder(_converter_id: str, *, clean: bool = False) -> Path:
        seen["clean"] = clean
        raise RuntimeError("stubbed - the flag is what this test reads")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(router, "_download_converter_files_windows", _recorder)

    result = await router._install_converter_impl("dgn", True, None)

    assert seen["clean"] is True
    assert result["installed"] is False


async def test_an_ordinary_install_does_not_ask_for_a_clean_download(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, bool] = {}

    def _recorder(_converter_id: str, *, clean: bool = False) -> Path:
        seen["clean"] = clean
        raise RuntimeError("stubbed - the flag is what this test reads")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(router, "_download_converter_files_windows", _recorder)

    await router._install_converter_impl("dgn", False, None)

    assert seen["clean"] is False


# ── Resolution, once a partial folder is possible ────────────────────────


def _isolate_search(monkeypatch: pytest.MonkeyPatch, home: Path, *paths: Path) -> None:
    """Point resolution at the given folders and nothing else.

    ``CONVERTERS`` is forced to the Windows names so this runs the Windows
    branch on a Linux runner - the check keys on the exe suffix, not on the
    host platform.
    """
    monkeypatch.setattr(cad_import, "CONVERTERS", cad_import._WINDOWS_CONVERTERS)
    monkeypatch.setattr(cad_import, "CONVERTER_SEARCH_PATHS", list(paths))
    monkeypatch.setattr(cad_import, "_find_ddc_toolkit_bin", lambda: None)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    monkeypatch.delenv("OPENESTIMATOR_CONVERTERS_DIR", raising=False)
    monkeypatch.delenv("OE_BUNDLED_CONVERTERS_DIR", raising=False)


def _folder(root: Path, name: str, *, complete: bool) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "DgnExporter.exe").write_bytes(_pe_bytes(_X64, size=4096))
    if complete:
        for companion in cad_import._WINDOWS_COMPANION_FILES:
            (folder / companion).write_bytes(_pe_bytes(_X64, size=4096))
    return folder


def test_a_complete_folder_wins_over_a_partial_one_earlier_in_the_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A partial folder used to win on position alone.

    Which is how a rollback that could not delete everything ended up
    shadowing a working install, with Windows resolving the missing libraries
    from wherever else on the machine it found them.
    """
    partial = _folder(tmp_path, "partial", complete=False)
    complete = _folder(tmp_path, "complete", complete=True)
    _isolate_search(monkeypatch, tmp_path / "home", partial, complete)

    assert cad_import.find_converter("dgn") == complete / "DgnExporter.exe"


def test_a_partial_folder_is_still_returned_when_it_is_all_there_is(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Resolution must never answer None where it used to answer a path.

    Two other modules resolve converters through this helper. Reporting the
    partial folder belongs to the health check, which has the room to explain
    it; this function's job is only to stop preferring it.
    """
    partial = _folder(tmp_path, "partial", complete=False)
    _isolate_search(monkeypatch, tmp_path / "home", partial)

    assert cad_import.find_converter("dgn") == partial / "DgnExporter.exe"


# ── The install endpoint, over the folder the health check calls broken ───


async def _ask_to_install(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Call the endpoint far enough to learn whether it short-circuited.

    The platform is forced to one with no native build, so the answer comes
    back synchronously and nothing is downloaded or spawned. That branch sits
    directly below the short-circuit under test and touches neither the
    request nor the current user.
    """
    monkeypatch.setattr(sys, "platform", "darwin")
    return await router.install_converter("dgn", None, "test-user", force=False)  # type: ignore[arg-type]


async def test_a_partial_folder_is_not_reported_as_already_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two endpoints over one folder used to disagree about it.

    The health check reported the same directory as failed and named the
    missing libraries, while this endpoint answered "already installed" and
    downloaded nothing - so the Reinstall button beside that message was a no
    -op on precisely the folder the message was about. It is a plain install
    and not a forced one: the frontend calls this without ``force`` from the
    error overlay.

    Letting it through repairs the folder rather than looping, because the
    download rewrites the tree and the prune clears what the listing does not
    carry, after which the same folder resolves as complete.
    """
    partial = _folder(tmp_path, "dgn_windows", complete=False)
    _isolate_search(monkeypatch, tmp_path / "home", partial)

    result = await _ask_to_install(monkeypatch)

    assert not result.get("already_installed")
    assert result.get("installed") is False


async def test_a_complete_folder_is_still_reported_as_already_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The pair. Deleting the short-circuit outright would pass the test above."""
    complete = _folder(tmp_path, "dgn_windows", complete=True)
    _isolate_search(monkeypatch, tmp_path / "home", complete)

    result = await _ask_to_install(monkeypatch)

    assert result["already_installed"] is True
    assert result["path"] == str(complete / "DgnExporter.exe")
