"""Render the README banner: who the platform is for, and how much of it there is.

The banner is one honeycomb of the two vocabularies the Cases hub is built around
and a second honeycomb of every backend module, and all of it is derived from the
product rather than drawn by hand, so adding a role or a module is a rerun of this
script instead of a redraw in an image editor.

Three sources are read, and none of the numbers in the picture are typed in here:

    frontend/src/features/cases/companyTypes.ts   COMPANY_TYPE_META    (8 entries)
    frontend/src/features/cases/roles.ts          ROLE_META            (15 entries)
    backend/app/modules/*/manifest.py             ModuleManifest       (one per module)

and the photographs come from the pool the Cases hub itself deals from:

    frontend/public/assets/people/cmt-<stem>.webp   128x128 company scenes
    frontend/public/assets/people/prf-<stem>.webp   340x480 role portraits

The cells touch. A hexagon row whose neighbours are a full row-height apart is a
line of separate tiles that happen to be six-sided, and an earlier version of this
banner drew exactly that: the triangular gaps above and below every cell stayed
open, so the picture read as a chain rather than as a comb. Rows step three
quarters of a hexagon here and every other row is inset by half a cell, which is
the geometry that makes hexagons share edges. That costs the space under each
cell where the captions used to sit, so the captions moved inside, into the band
between half height and three quarter height where a pointy-top hexagon is still
at its full width.

The module comb is the same shape at a smaller scale, one cell per module, and
each cell is tinted by how many other modules its manifest is wired to. A module
that nothing depends on and that depends on nothing sits pale; the ones the rest
of the platform is built on top of are solid. The count in the heading and the
count of links under it are both computed from the manifests on every run, so
neither can drift away from the tree the way a typed-in number does.

One file is written, on white. The banner used to ship as a light and a dark twin
paired in a picture element; it is a single white image now.

Usage:

    python scripts/render_readme_banner.py

Requires Pillow, which is already a backend dependency. No browser, no Node.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
CASES = REPO / "frontend" / "src" / "features" / "cases"
PEOPLE = REPO / "frontend" / "public" / "assets" / "people"
MODULES = REPO / "backend" / "app" / "modules"
OUTDIR = REPO / "docs" / "screenshots"
OUT = OUTDIR / "banner.png"

# Supersampling factor for the hexagon masks and for the module comb. A hexagon
# drawn straight into the final raster has visibly stepped diagonals; drawn four
# times over and reduced, the edges resolve. Only the shapes pay this cost, not
# the photographs.
SS = 4


# --------------------------------------------------------------------------- #
# Vocabularies, read from the TypeScript sources that own them
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Cell:
    """One captioned hexagon: a photograph, a caption and an accent colour."""

    ident: str
    caption: tuple[str, ...]
    photo: Path
    accent: tuple[int, int, int]


def _read_ids(source: Path, array_name: str) -> list[str]:
    """The `id:` values of every entry in a `const <array_name>: X[] = [...]` literal.

    Parsing the source rather than hardcoding the ids is the whole point of the
    script: a role added to `roles.ts` has to show up here on the next run without
    anyone remembering that this file exists.
    """
    text = source.read_text(encoding="utf-8")
    start = text.index(f"export const {array_name}")
    end = text.index("\n];", start)
    # Digits are in the character class so that an id like `tier-2-contractor` is
    # READ and then rejected by `_require`, rather than being skipped as if it had
    # never been written. An id this regex cannot match is invisible twice over:
    # its tile goes missing, and because `_accents` pairs ids with tints by
    # position it also shifts the accent of every entry after it.
    return re.findall(r"^\s{4}id: '([a-z0-9-]+)',$", text[start:end], flags=re.MULTILINE)


# The two cast headings, and the counts they spell out. These are the reason the
# script cannot simply draw whatever it finds: "FIFTEEN PROFESSIONAL ROLES" over
# fourteen hexagons is a caption that lies, and nothing downstream would catch it.
# The module heading needs no such guard because its number is not written here at
# all, it is counted on the way past.
EXPECTED_COMPANIES = 8
EXPECTED_ROLES = 15
HEAD_CAST = "EIGHT COMPANY TYPES, FIFTEEN PROFESSIONAL ROLES"

# Short captions. The `labelDefault` strings in the sources are written for a
# selector row with a full line to itself ("Project / construction management
# firm"); inside a hexagon they have about two short lines, so the banner keeps
# its own shortened forms and asserts below that it has one for every id.
COMPANY_CAPTIONS: dict[str, tuple[str, ...]] = {
    "general-contractor": ("General", "contractor"),
    "subcontractor": ("Specialist", "subcontractor"),
    "cost-consultant": ("Cost consultancy", "/ QS"),
    "designer": ("Design /", "engineering"),
    "developer-client": ("Developer", "/ client"),
    "project-manager": ("Project", "management"),
    "bim-consultant": ("BIM / digital", "consultancy"),
    "owner-operator": ("Owner /", "operator (FM)"),
}

ROLE_CAPTIONS: dict[str, tuple[str, ...]] = {
    "estimator": ("Estimator",),
    "quantity-surveyor": ("Quantity", "surveyor"),
    "site-manager": ("Site", "manager"),
    "project-manager": ("Project", "manager"),
    "bim-coordinator": ("BIM", "coordinator"),
    "procurement-buyer": ("Procurement", "/ buyer"),
    "planner": ("Planner /", "scheduler"),
    "hse-officer": ("Health &", "safety"),
    "design-lead": ("Design", "lead"),
    "document-controller": ("Document", "controller"),
    "commercial-manager": ("Commercial", "manager"),
    "accountant": ("Accountant",),
    "contract-administrator": ("Contract", "administrator"),
    "finance-manager": ("Finance", "manager"),
    "foreman": ("Foreman /", "supervisor"),
}

# Company type -> photo stem. Copied from COMPANY_THUMB_ALIASES in caseFaces.ts,
# which points each company type at its archetype's picture; ids that already
# name a stem need no entry there and get none here.
COMPANY_STEM: dict[str, str] = {
    "general-contractor": "general-contractor",
    "subcontractor": "subcontractor",
    "cost-consultant": "estimator",
    "designer": "architecture-engineering",
    "developer-client": "real-estate-developer",
    "project-manager": "construction-manager",
    "bim-consultant": "bim-vdc",
    "owner-operator": "facility-manager",
}

# Role -> portrait stem. This mapping is the banner's own and deliberately does
# NOT live in caseFaces.ts: that module casts by company type on purpose, and its
# header says in as many words not to make it read `Playbook.roles`. Here the two
# axes are both being drawn at once, so each role needs a face of its own, and
# the constraint is only that the fifteen are distinct - a reader comparing two
# neighbouring tiles must not see the same person under two job titles.
ROLE_STEM: dict[str, str] = {
    "estimator": "estimator",
    "quantity-surveyor": "quality-manager",
    "site-manager": "site-supervisor",
    "project-manager": "construction-manager",
    "bim-coordinator": "bim-vdc",
    "procurement-buyer": "procurement-manager",
    "planner": "scheduler-planner",
    "hse-officer": "hse-manager",
    "design-lead": "architecture-engineering",
    "document-controller": "government-agency",
    "commercial-manager": "commercial-manager",
    "accountant": "owner-client",
    "contract-administrator": "design-build",
    "finance-manager": "real-estate-developer",
    "foreman": "subcontractor",
}

# Accent colours, the 600 weight of the Tailwind hue each source file names in its
# tint, so the banner and the app tint the same concept the same way.
TAILWIND_600: dict[str, tuple[int, int, int]] = {
    "blue": (37, 99, 235),
    "orange": (234, 88, 12),
    "green": (22, 163, 74),
    "purple": (147, 51, 234),
    "pink": (219, 39, 119),
    "yellow": (202, 138, 4),
    "indigo": (79, 70, 229),
    "cyan": (8, 145, 178),
    "amber": (217, 119, 6),
    "teal": (13, 148, 136),
    "violet": (124, 58, 237),
    "emerald": (5, 150, 105),
    "sky": (2, 132, 199),
    "red": (220, 38, 38),
    "slate": (71, 85, 105),
    "fuchsia": (192, 38, 211),
    "rose": (225, 29, 72),
}


def _accents(source: Path, array_name: str, ids: list[str]) -> dict[str, tuple[int, int, int]]:
    """The accent colour per entry, taken from the Tailwind hue in its `tint.text`.

    `tint.text` is a literal like `text-amber-600 dark:text-amber-400`; the hue is
    what the banner needs and the weights are the app's business.
    """
    text = source.read_text(encoding="utf-8")
    start = text.index(f"export const {array_name}")
    # Bounded at the end of the literal, like `_read_ids`: an unbounded scan would
    # happily read a tint belonging to some later constant in the same file. Ids
    # and tints are paired by POSITION, so one stray or missing match silently
    # recolours every entry after it - hence `!=` rather than `<`.
    end = text.index("\n];", start)
    hues = re.findall(r"^\s+text: 'text-([a-z]+)-\d00 ", text[start:end], flags=re.MULTILINE)
    if len(hues) != len(ids):
        raise SystemExit(f"{source.name}: found {len(hues)} tints for {len(ids)} ids")
    unknown = sorted({h for h in hues if h not in TAILWIND_600})
    if unknown:
        raise SystemExit(f"{source.name}: no RGB for Tailwind hue(s) {', '.join(unknown)}")
    return {i: TAILWIND_600[h] for i, h in zip(ids, hues, strict=True)}


def build_cells() -> tuple[list[Cell], list[Cell]]:
    """The eight company types and the fifteen roles, in source order."""
    company_ids = _read_ids(CASES / "companyTypes.ts", "COMPANY_TYPE_META")
    role_ids = _read_ids(CASES / "roles.ts", "ROLE_META")
    if not company_ids or not role_ids:
        raise SystemExit("could not parse the vocabularies out of the cases sources")

    # `_require` only fires on an id it has never seen, so it catches an ADDITION
    # and nothing else: delete a role and the banner quietly draws fourteen tiles
    # under a heading that reads FIFTEEN. The heading spells its counts out in
    # words, so the counts are part of the drawing and belong under a guard.
    if (len(company_ids), len(role_ids)) != (EXPECTED_COMPANIES, EXPECTED_ROLES):
        raise SystemExit(
            f"the vocabularies moved: {len(company_ids)} company types and {len(role_ids)} roles, "
            f"but this script draws a heading for {EXPECTED_COMPANIES} and {EXPECTED_ROLES}. "
            "Update HEAD_CAST, the expected counts and the captions together."
        )

    company_accents = _accents(CASES / "companyTypes.ts", "COMPANY_TYPE_META", company_ids)
    role_accents = _accents(CASES / "roles.ts", "ROLE_META", role_ids)

    companies, roles = [], []
    for ident in company_ids:
        _require(ident, COMPANY_CAPTIONS, COMPANY_STEM, "company type")
        companies.append(
            Cell(
                ident,
                COMPANY_CAPTIONS[ident],
                PEOPLE / f"cmt-{COMPANY_STEM[ident]}.webp",
                company_accents[ident],
            )
        )
    for ident in role_ids:
        _require(ident, ROLE_CAPTIONS, ROLE_STEM, "role")
        roles.append(
            Cell(
                ident,
                ROLE_CAPTIONS[ident],
                PEOPLE / f"prf-{ROLE_STEM[ident]}.webp",
                role_accents[ident],
            )
        )

    if len({c.photo for c in roles}) != len(roles):
        raise SystemExit("two roles share a portrait; give each its own face in ROLE_STEM")
    for cell in companies + roles:
        if not cell.photo.exists():
            raise SystemExit(f"{cell.ident}: no photograph at {cell.photo}")
    return companies, roles


def _require(ident: str, captions: dict, stems: dict, kind: str) -> None:
    """Fail loudly when a vocabulary grew and this file did not follow it."""
    if ident not in captions:
        raise SystemExit(f"new {kind} '{ident}' has no caption in this script")
    if ident not in stems:
        raise SystemExit(f"new {kind} '{ident}' has no photo stem in this script")


# --------------------------------------------------------------------------- #
# The modules, read from the manifests that own them
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Modules:
    """What the manifests say, counted rather than typed in."""

    #: One entry per directory under `backend/app/modules`, in name order.
    idents: tuple[str, ...]
    #: Links between modules: how many other modules each one is wired to,
    #: counted in both directions, so a module everything depends on scores as
    #: highly as one that depends on everything.
    degree: dict[str, int]
    #: Distinct undirected pairs, which is the number the banner claims.
    links: int
    #: Directories with no parseable manifest. Drawn, but at degree zero, and
    #: named on stdout so that a module which quietly lost its manifest is not
    #: quietly drawn as an unconnected cell.
    without_manifest: tuple[str, ...]


def read_modules() -> Modules:
    """Every backend module and the dependency graph its manifests declare.

    The manifests are parsed, not imported. Importing them pulls in
    `app.core.module_loader` and through it most of the application, which is a
    lot of machinery to stand up to read four keyword arguments, and it would
    make a banner depend on the backend being importable at all.
    """
    idents = sorted(p.name for p in MODULES.iterdir() if p.is_dir() and p.name != "__pycache__")
    if not idents:
        raise SystemExit(f"no modules found under {MODULES}")

    # `depends` names a manifest's `name` ("oe_boq"), not its directory ("boq"),
    # so the graph is resolved through this map rather than by string surgery on
    # the prefix. A module whose name does not follow the convention still lands
    # in here correctly, and a depends entry naming nothing at all is reported
    # instead of being silently dropped, because a dropped edge lowers the link
    # count in the heading and nothing else would notice.
    declared: dict[str, list[str]] = {}
    by_name: dict[str, str] = {}
    without: list[str] = []

    for ident in idents:
        manifest = MODULES / ident / "manifest.py"
        if not manifest.exists():
            without.append(ident)
            continue
        call = _manifest_call(manifest)
        if call is None:
            without.append(ident)
            continue
        kwargs = {kw.arg: kw.value for kw in call.keywords if kw.arg}
        name = _literal(kwargs.get("name"))
        if not isinstance(name, str):
            without.append(ident)
            continue
        by_name[name] = ident
        depends = _literal(kwargs.get("depends")) or []
        declared[ident] = [d for d in depends if isinstance(d, str)]

    pairs: set[tuple[str, str]] = set()
    unresolved: list[str] = []
    for ident, depends in declared.items():
        for target in depends:
            other = by_name.get(target)
            if other is None:
                unresolved.append(f"{ident} -> {target}")
                continue
            if other != ident:
                pairs.add(tuple(sorted((ident, other))))  # type: ignore[arg-type]
    if unresolved:
        raise SystemExit(
            "these manifests depend on a name no manifest declares, so the link count "
            "would be short by that many: " + ", ".join(sorted(unresolved))
        )

    degree = dict.fromkeys(idents, 0)
    for left, right in pairs:
        degree[left] += 1
        degree[right] += 1
    return Modules(tuple(idents), degree, len(pairs), tuple(without))


def _manifest_call(path: Path) -> ast.Call | None:
    """The `ModuleManifest(...)` call in a manifest file, or None if there is none."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "ModuleManifest":
            return node
    return None


def _literal(node: ast.AST | None):
    """A literal keyword argument, or None when it is computed rather than written."""
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #

# Pointy-top geometry. The height is chosen first and made divisible by four so
# that three quarters of it, which is the distance between rows in a comb, is a
# whole number of pixels: a fractional row step accumulates down the picture and
# opens hairline seams between cells that are supposed to be touching.
HEX_H = 196
HEX_W = round(HEX_H * (3**0.5) / 2)  # 170
ROW_STEP = HEX_H * 3 // 4  # 147
PER_ROW = 8

# The module comb, same shape, small enough that all of them fit in six rows.
MOD_COLS = 32
MOD_W = (PER_ROW * HEX_W) / MOD_COLS  # kept fractional; the comb is drawn, not pasted
MOD_H = MOD_W * 2 / (3**0.5)
MOD_STEP = MOD_H * 3 / 4

MARGIN = 72
BAND_GAP = 54
HEAD_GAP = 36

BG = (255, 255, 255)
TITLE = (15, 23, 42)
TITLE_DIM = (148, 163, 184)
BODY = (71, 85, 105)
HEADING = (100, 116, 139)
RULE = (226, 232, 240)
NOTE = (100, 116, 139)

# How much of the accent colour is laid over a photograph, and how far the
# photograph is lifted, so a face reads at tile size on white.
WASH = 0.16
LIFT = 0.06

# The module comb runs between these two, palest for a module nothing is wired to
# and solid for the ones the rest of the platform is built on top of.
MOD_PALE = (219, 234, 254)
MOD_DEEP = (30, 64, 175)


def _font(names: list[str], size: int) -> ImageFont.FreeTypeFont:
    """The first of `names` that this machine actually has, at `size`.

    Rasterising with a system face is fine: what ships is the PNG, and no font
    file leaves the machine that drew it.
    """
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    raise SystemExit(f"none of these fonts are installed: {', '.join(names)}")


def _hexagon(w: float, h: float) -> list[tuple[float, float]]:
    """A pointy-top hexagon inscribed in a `w` by `h` box, as six points.

    Full width holds between h/4 and 3h/4 and the shape tapers to a point above
    and below that, which is what bounds where a caption can sit.
    """
    return [
        (w / 2, 0),
        (w, h / 4),
        (w, h * 3 / 4),
        (w / 2, h),
        (0, h * 3 / 4),
        (0, h / 4),
    ]


def _hex_mask(w: int, h: int, inset: float) -> Image.Image:
    """An antialiased alpha mask of a pointy-top hexagon, drawn oversized.

    `inset` shrinks the shape about its centre. Cells that share an edge and are
    filled to that edge merge into one another, and a comb of photographs with no
    wall between them is a collage; a hairline of background is what makes the
    cells read as cells while they still touch.
    """
    big = Image.new("L", (w * SS, h * SS), 0)
    points = [((x - w / 2) * (1 - inset) + w / 2, (y - h / 2) * (1 - inset) + h / 2) for x, y in _hexagon(w, h)]
    ImageDraw.Draw(big).polygon([(x * SS, y * SS) for x, y in points], fill=255)
    return big.resize((w, h), Image.Resampling.LANCZOS)


def _tile(cell: Cell, mask: Image.Image) -> Image.Image:
    """One hexagon: the photograph, cropped to fill, washed in the cell's accent."""
    photo = Image.open(cell.photo).convert("RGB")

    # Cover, then take the top-middle rather than the centre: these are portraits
    # and the head sits high in the frame, so a centred crop of a 340x480 lands on
    # the chest.
    scale = max(HEX_W / photo.width, HEX_H / photo.height)
    grown = (max(1, round(photo.width * scale)), max(1, round(photo.height * scale)))
    photo = photo.resize(grown, Image.Resampling.LANCZOS)
    left = (photo.width - HEX_W) // 2
    top = round((photo.height - HEX_H) * 0.10)
    photo = photo.crop((left, top, left + HEX_W, top + HEX_H))

    photo = Image.blend(photo, Image.new("RGB", photo.size, cell.accent), WASH)
    photo = Image.blend(photo, Image.new("RGB", photo.size, (255, 255, 255)), LIFT)

    # The caption sits on the picture now that the rows have closed up, so the
    # bottom of the cell is darkened to carry white text. The gradient starts
    # above the caption band rather than at its edge, because a hard line across
    # a face reads as damage to the photograph.
    scrim = Image.new("L", (HEX_W, HEX_H), 0)
    pen = ImageDraw.Draw(scrim)
    # The ramp has to reach most of its weight BEFORE the caption starts, not at
    # the bottom of the cell: text set on the first few percent of a gradient is
    # text on the bare photograph, and half of these photographs are pale.
    fade_from, fade_to = round(HEX_H * 0.30), round(HEX_H * 0.56)
    for y in range(fade_from, HEX_H):
        ramp = min(1.0, (y - fade_from) / max(1, fade_to - fade_from))
        pen.line((0, y, HEX_W, y), fill=round(225 * ramp**0.85))
    photo = Image.composite(Image.new("RGB", photo.size, (9, 14, 26)), photo, scrim)

    tile = Image.new("RGBA", (HEX_W, HEX_H), (0, 0, 0, 0))
    tile.paste(photo, (0, 0), mask)
    return tile


def _draw_comb(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    rows: list[list[Cell]],
    mask: Image.Image,
    font: ImageFont.FreeTypeFont,
    x0: int,
    y0: int,
) -> int:
    """Draw `rows` as a connected honeycomb. Returns the bottom y.

    Every other row is inset half a cell and the rows step three quarters of a
    hexagon, which is the only spacing at which pointy-top hexagons share edges.
    """
    for index, row in enumerate(rows):
        indent = (HEX_W // 2) if index % 2 else 0
        y = y0 + index * ROW_STEP
        for column, cell in enumerate(row):
            x = x0 + indent + column * HEX_W
            tile = _tile(cell, mask)
            canvas.paste(tile, (x, y), tile)

            # The caption sits in the band where the hexagon is at full width.
            # Below three quarter height the shape tapers to a point and text put
            # there runs off both sides of it, which is the failure the captions
            # were moved inside to avoid in the first place.
            centre = x + HEX_W // 2
            baseline = y + round(HEX_H * 0.745) - 23 * len(cell.caption)
            for line in cell.caption:
                draw.text(
                    (centre, baseline),
                    line,
                    font=font,
                    fill=(255, 255, 255),
                    anchor="ma",
                )
                baseline += 23
    return y0 + (len(rows) - 1) * ROW_STEP + HEX_H


def _draw_module_comb(modules: Modules, width: int) -> Image.Image:
    """The module honeycomb: one cell per module, tinted by how connected it is.

    Drawn as polygons on a supersampled layer rather than pasted from a mask,
    because the cell width does not come out whole and rounding each cell to a
    pixel would leave the seams a comb is supposed not to have.
    """
    rows = [modules.idents[i : i + MOD_COLS] for i in range(0, len(modules.idents), MOD_COLS)]
    height = round((len(rows) - 1) * MOD_STEP + MOD_H)
    layer = Image.new("RGB", (width * SS, height * SS), BG)
    pen = ImageDraw.Draw(layer)

    # Measured, not guessed: the median module is wired to two others and the
    # busiest to a hundred and forty seven, so scaling against the maximum leaves
    # six cells out of seven indistinguishable from an unconnected one. The ramp
    # is scaled against the twelfth-busiest instead and saturates above it, which
    # spreads the range the modules actually occupy. It stays monotonic, so the
    # note under the comb is still true, it just stops being unreadable.
    top = 12
    for index, row in enumerate(rows):
        indent = (MOD_W / 2) if index % 2 else 0
        y = index * MOD_STEP
        for column, ident in enumerate(row):
            x = indent + column * MOD_W
            share = min(1.0, (modules.degree[ident] / top) ** 0.6)
            fill = tuple(round(pale + (deep - pale) * share) for pale, deep in zip(MOD_PALE, MOD_DEEP, strict=True))
            points = _hexagon(MOD_W - 1.6, MOD_H - 1.6)
            pen.polygon([((x + px) * SS, (y + py) * SS) for px, py in points], fill=fill)
    return layer.resize((width, height), Image.Resampling.LANCZOS)


def render(companies: list[Cell], roles: list[Cell], modules: Modules) -> Image.Image:
    """The whole banner."""
    fonts = {
        "title": _font(["arialbd.ttf", "segoeuib.ttf"], 54),
        "body": _font(["arial.ttf", "segoeui.ttf"], 25),
        "head": _font(["ARIALNB.TTF", "arialbd.ttf"], 22),
        "cap": _font(["ARIALN.TTF", "arial.ttf"], 20),
        "note": _font(["arial.ttf", "segoeui.ttf"], 20),
    }

    # Eight, then seven, then eight. The short row in the middle is what makes the
    # block square off: inset by half a cell it starts and ends half a cell inside
    # the rows above and below it, so the comb has a straight left and right edge
    # instead of a sawtooth one.
    cast_rows = [companies, roles[:7], roles[7:]]
    comb_w = PER_ROW * HEX_W
    width = MARGIN * 2 + comb_w

    header_h = 152
    cast_h = (len(cast_rows) - 1) * ROW_STEP + HEX_H
    module_rows = -(-len(modules.idents) // MOD_COLS)
    modules_h = round((module_rows - 1) * MOD_STEP + MOD_H)
    height = MARGIN + header_h + HEAD_GAP + cast_h + BAND_GAP + HEAD_GAP + modules_h + 44 + MARGIN

    canvas = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(canvas)
    mask = _hex_mask(HEX_W, HEX_H, inset=0.018)

    # Header: the product name, then what the picture below it is showing.
    x, y = MARGIN, MARGIN
    draw.text((x, y), "OpenConstruction", font=fonts["title"], fill=TITLE)
    x += round(draw.textlength("OpenConstruction", font=fonts["title"]))
    draw.text((x, y), "ERP", font=fonts["title"], fill=TITLE_DIM)
    draw.text(
        (MARGIN, y + 74),
        "Open-source construction cost management. Estimating, BoQ, BIM and procurement,",
        font=fonts["body"],
        fill=BODY,
    )
    draw.text(
        (MARGIN, y + 106),
        "in one place your whole team already understands.",
        font=fonts["body"],
        fill=BODY,
    )

    y = MARGIN + header_h
    _draw_heading(draw, fonts["head"], HEAD_CAST, y, width)
    y = _draw_comb(canvas, draw, cast_rows, mask, fonts["cap"], MARGIN, y + HEAD_GAP)

    # The two numbers in this heading are counted on the way past rather than
    # written down, so the heading cannot end up describing a tree that has moved.
    y += BAND_GAP
    heading = f"{len(modules.idents)} MODULES, {modules.links} DEPENDENCIES DECLARED BETWEEN THEM"
    _draw_heading(draw, fonts["head"], heading, y, width)
    y += HEAD_GAP
    comb = _draw_module_comb(modules, comb_w)
    canvas.paste(comb, (MARGIN, y))
    y += comb.height + 16

    draw.text(
        (MARGIN, y),
        "One cell is one module. The deeper it sits, the more of the others it is wired to.",
        font=fonts["note"],
        fill=NOTE,
    )
    return canvas


def _draw_heading(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    text: str,
    y: int,
    width: int,
) -> None:
    """A small caps heading with a rule running from its end to the right margin."""
    draw.text((MARGIN, y), text, font=font, fill=HEADING)
    end = MARGIN + round(draw.textlength(text, font=font)) + 18
    draw.line((end, y + 12, width - MARGIN, y + 12), fill=RULE, width=2)


def main() -> int:
    companies, roles = build_cells()
    modules = read_modules()
    OUTDIR.mkdir(parents=True, exist_ok=True)

    canvas = render(companies, roles, modules)
    # Twenty-three photographs in true colour weigh about 900 KB, which is a lot
    # to put at the top of a README. An adaptive 256-colour palette takes that to
    # roughly a quarter of it, and the pictures survive because they are already
    # washed towards one accent apiece and hold few distinct hues.
    canvas.quantize(
        colors=256,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.FLOYDSTEINBERG,
    ).save(OUT, optimize=True)

    print(f"{OUT.relative_to(REPO)}  {OUT.stat().st_size // 1024} KB  {canvas.width}x{canvas.height}")
    print(f"{len(companies)} company types, {len(roles)} professional roles")
    wired = sum(1 for d in modules.degree.values() if d)
    print(f"{len(modules.idents)} modules, {modules.links} declared links, {wired} of them wired to at least one other")
    if modules.without_manifest:
        # Not fatal, because a banner that refuses to draw over a missing manifest
        # would be a strange place to enforce that. Named, because these modules
        # are drawn at degree zero and would otherwise look like a measurement
        # rather than a gap.
        print(
            f"note: {len(modules.without_manifest)} module(s) have no readable manifest and are "
            f"drawn unconnected: {', '.join(modules.without_manifest)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
