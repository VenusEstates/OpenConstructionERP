"""Render the README banner: the eight company types and fifteen professional roles.

The banner is a honeycomb of the two vocabularies the Cases hub is built around,
and it is derived from the product rather than drawn by hand, so adding a role is
a rerun of this script instead of a redraw in an image editor.

Both vocabularies are read from the frontend sources that already own them:

    frontend/src/features/cases/companyTypes.ts   COMPANY_TYPE_META    (8 entries)
    frontend/src/features/cases/roles.ts          ROLE_META            (15 entries)

and the photographs come from the pool the Cases hub itself deals from:

    frontend/public/assets/people/cmt-<stem>.webp   128x128 company scenes
    frontend/public/assets/people/prf-<stem>.webp   340x480 role portraits

Every tile is captioned. An earlier banner showed the same twenty-three cells as
bare glyphs, which said how MANY company types and roles the product knows about
without ever saying WHICH, and a reader cannot check a claim they cannot read.

Two files are written, a light and a dark twin, because the README pairs them in
a picture element and a single opaque banner renders as a slab on whichever theme
it was not drawn for.

Usage:

    python scripts/render_readme_banner.py

Requires Pillow, which is already a backend dependency. No browser, no Node.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
CASES = REPO / "frontend" / "src" / "features" / "cases"
PEOPLE = REPO / "frontend" / "public" / "assets" / "people"
OUTDIR = REPO / "docs" / "screenshots"

# Supersampling factor for the hexagon masks. A hexagon drawn straight into the
# final raster has visibly stepped diagonals; drawn four times over and reduced,
# the edges resolve. Only the masks pay this cost, not the photographs.
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


# The two band headings, and the counts they spell out. These are the reason the
# script cannot simply draw whatever it finds: "FIFTEEN PROFESSIONAL ROLES" over
# fourteen hexagons is a caption that lies, and nothing downstream would catch it.
EXPECTED_COMPANIES = 8
EXPECTED_ROLES = 15
HEAD_COMPANIES = "EIGHT COMPANY TYPES"
HEAD_ROLES = "FIFTEEN PROFESSIONAL ROLES"

# Short captions. The `labelDefault` strings in the sources are written for a
# selector row with a full line to itself ("Project / construction management
# firm"); under a hexagon they have about two short lines, so the banner keeps
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
    # under a heading that reads FIFTEEN. The headings spell their counts out in
    # words, so the counts are part of the drawing and belong under a guard.
    if (len(company_ids), len(role_ids)) != (EXPECTED_COMPANIES, EXPECTED_ROLES):
        raise SystemExit(
            f"the vocabularies moved: {len(company_ids)} company types and {len(role_ids)} roles, "
            f"but this script draws headings for {EXPECTED_COMPANIES} and {EXPECTED_ROLES}. "
            "Update HEAD_COMPANIES, HEAD_ROLES and the expected counts together with the captions."
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
# Drawing
# --------------------------------------------------------------------------- #

HEX_W = 168
HEX_H = round(HEX_W * 2 / (3**0.5))  # pointy-top: height is width * 2/sqrt(3)
CAP_H = 62
BAND_GAP = 46
ROW_GAP = 14
MARGIN = 72


@dataclass(frozen=True)
class Theme:
    """Everything that differs between the light and the dark twin."""

    name: str
    bg: tuple[int, int, int]
    title: tuple[int, int, int]
    title_dim: tuple[int, int, int]
    body: tuple[int, int, int]
    caption: tuple[int, int, int]
    heading: tuple[int, int, int]
    rule: tuple[int, int, int]
    # How much of the accent colour is laid over a photograph, and how far the
    # photograph is lifted towards or pushed away from the background, so a face
    # reads at tile size on either canvas.
    wash: float
    lift: float


LIGHT = Theme(
    name="light",
    bg=(255, 255, 255),
    title=(15, 23, 42),
    title_dim=(148, 163, 184),
    body=(71, 85, 105),
    caption=(51, 65, 85),
    heading=(148, 163, 184),
    rule=(226, 232, 240),
    wash=0.16,
    lift=0.06,
)

DARK = Theme(
    name="dark",
    bg=(13, 17, 27),
    title=(255, 255, 255),
    title_dim=(100, 116, 139),
    body=(148, 163, 184),
    caption=(203, 213, 225),
    heading=(100, 116, 139),
    rule=(30, 41, 59),
    wash=0.20,
    lift=-0.10,
)


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


def _hexagon(w: int, h: int) -> list[tuple[float, float]]:
    """A pointy-top hexagon inscribed in a `w` by `h` box, as six points."""
    return [
        (w / 2, 0),
        (w, h / 4),
        (w, h * 3 / 4),
        (w / 2, h),
        (0, h * 3 / 4),
        (0, h / 4),
    ]


def _hex_mask(w: int, h: int) -> Image.Image:
    """An antialiased alpha mask of a pointy-top hexagon, drawn oversized."""
    big = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(big).polygon([(x * SS, y * SS) for x, y in _hexagon(w, h)], fill=255)
    return big.resize((w, h), Image.Resampling.LANCZOS)


def _tile(cell: Cell, theme: Theme, mask: Image.Image) -> Image.Image:
    """One hexagon: the photograph, cropped to fill, washed in the cell's accent."""
    photo = Image.open(cell.photo).convert("RGB")

    # Cover, then take the top-middle rather than the centre: these are portraits
    # and the head sits high in the frame, so a centred crop of a 340x480 lands on
    # the chest.
    scale = max(HEX_W / photo.width, HEX_H / photo.height)
    grown = (max(1, round(photo.width * scale)), max(1, round(photo.height * scale)))
    photo = photo.resize(grown, Image.Resampling.LANCZOS)
    left = (photo.width - HEX_W) // 2
    top = round((photo.height - HEX_H) * 0.18)
    photo = photo.crop((left, top, left + HEX_W, top + HEX_H))

    photo = Image.blend(photo, Image.new("RGB", photo.size, cell.accent), theme.wash)
    if theme.lift:
        toward = (255, 255, 255) if theme.lift > 0 else (0, 0, 0)
        photo = Image.blend(photo, Image.new("RGB", photo.size, toward), abs(theme.lift))

    tile = Image.new("RGBA", (HEX_W, HEX_H), (0, 0, 0, 0))
    tile.paste(photo, (0, 0), mask)
    return tile


def _draw_band(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    cells: list[Cell],
    theme: Theme,
    mask: Image.Image,
    fonts: dict[str, ImageFont.FreeTypeFont],
    x0: int,
    y0: int,
    per_row: int,
) -> int:
    """Draw `cells` as staggered rows of captioned hexagons. Returns the bottom y."""
    y = y0
    for start in range(0, len(cells), per_row):
        row = cells[start : start + per_row]
        # Odd rows step in half a hexagon, which is what makes the strip read as a
        # comb rather than as a grid of unrelated tiles.
        indent = (HEX_W // 2) if (start // per_row) % 2 else 0
        for i, cell in enumerate(row):
            x = x0 + indent + i * HEX_W
            tile = _tile(cell, theme, mask)
            canvas.paste(tile, (x, y), tile)
            cx = x + HEX_W // 2
            cy = y + HEX_H + 14
            for line in cell.caption:
                draw.text((cx, cy), line, font=fonts["cap"], fill=theme.caption, anchor="ma")
                cy += 22
        y += HEX_H + CAP_H + ROW_GAP
    return y - ROW_GAP


def render(theme: Theme, companies: list[Cell], roles: list[Cell]) -> Image.Image:
    """The whole banner for one theme."""
    fonts = {
        "title": _font(["arialbd.ttf", "segoeuib.ttf"], 54),
        "body": _font(["arial.ttf", "segoeui.ttf"], 25),
        "head": _font(["ARIALNB.TTF", "arialbd.ttf"], 22),
        "cap": _font(["ARIALN.TTF", "arial.ttf"], 21),
    }

    comb_w = 8 * HEX_W
    width = MARGIN * 2 + comb_w
    header_h = 150
    band_a = HEX_H + CAP_H
    band_b = 2 * (HEX_H + CAP_H) + ROW_GAP
    height = MARGIN + header_h + band_a + BAND_GAP + 34 + band_b + MARGIN

    canvas = Image.new("RGB", (width, height), theme.bg)
    draw = ImageDraw.Draw(canvas)
    mask = _hex_mask(HEX_W, HEX_H)

    # Header: the product name, then what the picture below it is showing.
    x, y = MARGIN, MARGIN
    draw.text((x, y), "OpenConstruction", font=fonts["title"], fill=theme.title)
    x += round(draw.textlength("OpenConstruction", font=fonts["title"]))
    draw.text((x, y), "ERP", font=fonts["title"], fill=theme.title_dim)
    draw.text(
        (MARGIN, y + 74),
        "Open-source construction cost management. Estimating, BoQ, BIM and procurement,",
        font=fonts["body"],
        fill=theme.body,
    )
    tagline = "in one place your whole team already understands."
    draw.text((MARGIN, y + 106), tagline, font=fonts["body"], fill=theme.body)

    y = MARGIN + header_h
    draw.text((MARGIN, y), HEAD_COMPANIES, font=fonts["head"], fill=theme.heading)
    draw.line((MARGIN + 250, y + 12, width - MARGIN, y + 12), fill=theme.rule, width=2)
    y = _draw_band(canvas, draw, companies, theme, mask, fonts, MARGIN, y + 34, per_row=8)

    y += BAND_GAP
    draw.text((MARGIN, y), HEAD_ROLES, font=fonts["head"], fill=theme.heading)
    draw.line((MARGIN + 320, y + 12, width - MARGIN, y + 12), fill=theme.rule, width=2)
    _draw_band(canvas, draw, roles, theme, mask, fonts, MARGIN, y + 34, per_row=8)

    return canvas


def main() -> int:
    companies, roles = build_cells()
    OUTDIR.mkdir(parents=True, exist_ok=True)
    for theme in (LIGHT, DARK):
        out = OUTDIR / f"banner-cast-{theme.name}.png"
        # Twenty-three photographs in true colour weigh about 900 KB, which is a
        # lot to put at the top of a README. An adaptive 256-colour palette takes
        # that to roughly a quarter of it, and the pictures survive because they
        # are already washed towards one accent apiece and hold few distinct hues.
        canvas = render(theme, companies, roles)
        canvas.quantize(
            colors=256,
            method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.FLOYDSTEINBERG,
        ).save(out, optimize=True)
        print(f"{out.relative_to(REPO)}  {out.stat().st_size // 1024} KB")
    print(f"{len(companies)} company types, {len(roles)} professional roles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
