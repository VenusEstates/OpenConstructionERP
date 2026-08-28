# Bundled CAD/BIM converters (desktop installer)

This directory is bundled into the desktop app as a read-only Tauri resource.
The Windows release workflow downloads the DDC IFC converter (`IfcExporter.exe`
plus the libraries it loads) from the public repo
`datadrivenconstruction/cad2data-Revit-IFC-DWG-DGN` and extracts it into
`ifc_windows/` here, before the Tauri build packages resources. That lets a
fresh Windows install convert `.ifc` files offline with no first-use download.

At runtime the Tauri shell sets `OE_BUNDLED_CONVERTERS_DIR` to this directory and
the backend resolver (`backend/app/modules/boq/cad_import.py:find_converter`)
prefers `OE_BUNDLED_CONVERTERS_DIR/<format>_windows/<Exporter>.exe` over a
download.

Only the IFC converter is bundled. The RVT converter is about 600 MB and stays
on demand. On non-Windows builds, and on Windows builds where the download step
was skipped, this directory ships empty and the backend keeps its normal
auto-download behaviour unchanged.

## We bundle the terminal build, not the graphical one

The upstream folder holds two programs. `IfcExporter.exe` is the command line
converter, and it is the only one this codebase ever runs: every conversion goes
through `convert_cad_to_excel` in `backend/app/modules/boq/cad_import.py`, which
is a `subprocess` call with arguments and no window.
`DDC_Community_IFC_converter.exe` is a desktop window for a person to click, and
we neither open it nor ship it.

So the workflow deletes the graphical shell, `Qt6Gui.dll`, `Qt6Widgets.dll`,
`platforms/` and `styles/` after extraction. `Qt6Core.dll` stays: the command
line exporter imports it directly and cannot start without it. The full
reasoning, measured from the PE import tables of every binary in the pinned
converter tree, is in `backend/app/core/converter_source.py` next to
`is_graphical_only`, and the same decision applies to the converters the app
downloads at runtime.

A converter folder without `Qt6Gui.dll` is not a broken install. Do not "repair"
it by copying the whole upstream folder in.

Layout the workflow produces here:

    ifc_windows/
      IfcExporter.exe
      Qt6Core.dll
      LICENSE, THIRD-PARTY-NOTICES
      datadrivenlibs/ ...

Size, from the workflow's own output rather than from memory: the upstream IFC
folder is about 242 MB, and about 19 MB of that is the graphical half we now
drop. The step prints both numbers on every release build, so read them there.

The converter binaries themselves are intentionally not committed to this repo.
