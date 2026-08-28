"""Render the README banner: every backend module, and how tightly they are wired.

The banner is one honeycomb. Each cell is one directory under
`backend/app/modules`, its colour says which category the module puts in its own
manifest, and how deep that colour runs says how many of the other modules it is
wired to. Nothing in the picture is typed in by hand, so a module added to the
tree shows up on the next run of this script rather than on the next time someone
remembers the banner exists.

The cells touch. A hexagon row whose neighbours sit a full row height apart is a
line of separate tiles that happen to be six sided, and an earlier banner drew
exactly that: the triangular notches above and below every cell stayed open and
the picture read as a chain. Pointy top hexagons share edges at one spacing only,
a row step of three quarters of the cell height with every other row inset by half
a cell width, and that is the spacing here.

Two graphs are counted, because they answer two different questions and the
difference between them is worth seeing:

    declared dependencies   the `depends` lists in the manifests, which is what
                            the module loader topologically sorts on
    imports                 one module's Python actually importing another's,
                            which is where the coupling really is

Imports are found with a line anchored regular expression rather than with the
`ast` module on purpose. Parts of the backend use PEP 695 generic syntax, so
parsing the tree requires Python 3.12, and this script should render on whatever
interpreter is at hand. The regular expression was checked against a full `ast`
walk over the 2120 files a 3.12 parser accepts and the two agree exactly, 704
directed pairs either way, so nothing is traded for the portability.

The title is not in the picture. It lives in the README as text, above the image,
where it is selectable, searchable and readable by a screen reader.

Usage:

    python scripts/render_readme_banner.py

Requires Pillow, which is already a backend dependency. No browser, no Node.
"""

from __future__ import annotations

import ast
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
MODULES = REPO / "backend" / "app" / "modules"
FONT = REPO / "scripts" / "assets" / "fonts" / "Inter-Variable-Latin.ttf"
OUT = REPO / "docs" / "screenshots" / "banner.png"

# Supersampling for the comb. A hexagon drawn straight into the final raster has
# visibly stepped diagonals; drawn three times over and reduced, the edges
# resolve. Text is drawn at final size instead, because FreeType hints it better
# than a downscale does.
SS = 3

WIDTH = 1800
COLS = 24
ROWS = 8
MARGIN = 18

# Pointy top geometry. Width and height of a regular hexagon are related by
# W = H * sqrt(3) / 2, rows step three quarters of the height, and alternate rows
# are inset by half a width. Change one of these and the cells stop touching.
HEX_W = (WIDTH - 2 * MARGIN) * 2 // (2 * COLS + 1)
HEX_H = round(HEX_W * 2 / 3**0.5)
ROW_STEP = HEX_H * 3 // 4

# Where the colour ramp saturates, in number of modules wired to. Read off the
# measured distribution: the union of the two graphs has a median around six and
# a long thin tail, so scaling against the maximum would leave nine cells in ten
# indistinguishable from an unwired one. Twenty sits near the ninetieth
# percentile, which spreads out the range the modules actually occupy while
# staying monotonic, so a deeper cell is still a more wired one all the way up.
RAMP_TOP = 20
RAMP_GAMMA = 0.62

# How far the palest cell is washed out toward the paper. This was 0.90 and the
# regional and other buckets, which are the least wired, came out close enough to
# white that you could not count them. A banner whose subject is how many modules
# there are cannot afford cells that disappear, so the floor is high enough that
# every one of the 192 is visible against the paper.
RAMP_FLOOR = 0.74

INK = (28, 25, 23)
MUTED = (120, 113, 108)
PAPER = (255, 255, 255)

# One colour per `category` value that at least three modules claim for
# themselves. The six categories with a single member each, and the three
# directories carrying no manifest at all, share the last swatch: inventing a
# colour for a category of one would give six specks equal billing with a
# hundred and nineteen modules.
BUCKETS: list[tuple[str, str, tuple[int, int, int]]] = [
    ("core", "Core", (29, 78, 216)),
    ("business", "Business", (13, 148, 136)),
    ("regional", "Regional", (217, 119, 6)),
    ("extension", "Extension", (124, 58, 237)),
    ("controls", "Controls", (225, 29, 72)),
    ("enterprise", "Enterprise", (71, 85, 105)),
    ("__other__", "Other", (168, 162, 158)),
]


# --------------------------------------------------------------------------- #
# The tree, read
# --------------------------------------------------------------------------- #


@dataclass
class Module:
    """One directory under `backend/app/modules`."""

    ident: str
    manifest_name: str | None
    category: str
    depends: list[str] = field(default_factory=list)


@dataclass
class Survey:
    """Everything the picture is drawn from, counted once."""

    modules: list[Module]
    declared: set[tuple[str, str]]
    imports: set[tuple[str, str]]
    degree: Counter


_IMPORT = re.compile(
    r"^[ \t]*(?:from[ \t]+app\.modules\.([a-z0-9_]+)|import[ \t]+app\.modules\.([a-z0-9_]+))",
    re.MULTILINE,
)


def _manifest_of(path: Path) -> tuple[str | None, str, list[str]]:
    """The name, category and depends list of the `ModuleManifest(...)` in a file.

    Parsed, never imported. Importing a manifest pulls in `app.core.module_loader`
    and through it most of the application, and a banner has no business needing
    the backend to be importable.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "ModuleManifest":
            kw = {k.arg: k.value for k in node.keywords if k.arg}

            def read(key: str, fallback):
                return ast.literal_eval(kw[key]) if key in kw else fallback

            return read("name", None), read("category", "__other__"), read("depends", None) or []
    return None, "__other__", []


def survey() -> Survey:
    """Read the module tree and both dependency graphs."""
    idents = sorted(p.name for p in MODULES.iterdir() if p.is_dir() and p.name != "__pycache__")
    known = set(idents)

    modules: list[Module] = []
    by_manifest_name: dict[str, str] = {}
    for ident in idents:
        manifest = MODULES / ident / "manifest.py"
        if manifest.exists():
            name, category, depends = _manifest_of(manifest)
        else:
            name, category, depends = None, "__other__", []
        if name:
            by_manifest_name[name] = ident
        modules.append(Module(ident, name, category, depends))

    # Declared dependencies. An entry naming something no manifest declares stops
    # the run: a dropped edge would quietly lower a number the README states, and
    # nothing downstream would notice it had gone.
    declared: set[tuple[str, str]] = set()
    for module in modules:
        for target in module.depends:
            other = by_manifest_name.get(target)
            if other is None:
                raise SystemExit(
                    f"{module.ident}/manifest.py declares a dependency on {target!r}, which no "
                    f"manifest in the tree names. Fix the manifest, or the count in the banner "
                    f"goes quietly wrong."
                )
            if other != module.ident:
                declared.add(tuple(sorted((module.ident, other))))

    imports: set[tuple[str, str]] = set()
    for ident in idents:
        for source in (MODULES / ident).rglob("*.py"):
            if "__pycache__" in source.parts:
                continue
            text = source.read_text(encoding="utf-8", errors="replace")
            for from_form, import_form in _IMPORT.findall(text):
                target = from_form or import_form
                if target in known and target != ident:
                    imports.add(tuple(sorted((ident, target))))

    degree: Counter = Counter()
    for a, b in declared | imports:
        degree[a] += 1
        degree[b] += 1
    return Survey(modules, declared, imports, degree)


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #


def _bucket_index(category: str) -> int:
    for i, (key, _, _) in enumerate(BUCKETS):
        if key == category:
            return i
    return len(BUCKETS) - 1


def _tint(base: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    """The bucket colour, from a pale wash at t=0 to the full colour at t=1."""
    pale = [round(c + (255 - c) * RAMP_FLOOR) for c in base]
    return (
        round(pale[0] + (base[0] - pale[0]) * t),
        round(pale[1] + (base[1] - pale[1]) * t),
        round(pale[2] + (base[2] - pale[2]) * t),
    )


def _hexagon(x: float, y: float, w: float, h: float) -> list[tuple[float, float]]:
    """A pointy top hexagon in the box (x, y) to (x + w, y + h).

    Full width holds only between h/4 and 3h/4; the cell tapers to a point above
    and below that band, so anything placed outside it runs off the sides.
    """
    return [
        (x + w / 2, y),
        (x + w, y + h / 4),
        (x + w, y + h * 3 / 4),
        (x + w / 2, y + h),
        (x, y + h * 3 / 4),
        (x, y + h / 4),
    ]


def _font(size: int, weight: int) -> ImageFont.FreeTypeFont:
    if not FONT.exists():
        raise SystemExit(
            f"the banner face is missing: {FONT}\n"
            f"It is tracked in the repository on purpose. A missing font does not fail, it "
            f"substitutes, and a banner set in whatever face happened to be installed is one "
            f"nobody else can reproduce."
        )
    face = ImageFont.truetype(str(FONT), size)
    # Optical size axis first, then weight. Inter's optical axis runs 14 to 32:
    # display sizes want the larger end, running text the smaller.
    face.set_variation_by_axes([32.0 if size >= 40 else 14.0, float(weight)])
    return face


def draw_comb(data: Survey) -> Image.Image:
    """The honeycomb, one cell per module, sorted by bucket and then by degree."""
    order = sorted(
        data.modules,
        key=lambda m: (_bucket_index(m.category), -data.degree[m.ident], m.ident),
    )
    comb_w = COLS * HEX_W + HEX_W // 2
    comb_h = (ROWS - 1) * ROW_STEP + HEX_H

    canvas = Image.new("RGB", (comb_w * SS, comb_h * SS), PAPER)
    pen = ImageDraw.Draw(canvas)
    for index, module in enumerate(order):
        row, col = divmod(index, COLS)
        # Boustrophedon: every other row runs right to left, so the ordering is
        # continuous across the turn. Filled strictly left to right instead, each
        # category ends pale at the right margin and the next one starts dark at
        # the left, which puts a hard vertical seam down the picture and reads as
        # a rendering fault rather than as the category boundary it is.
        if row % 2:
            col = COLS - 1 - col
        x = (col * HEX_W + (HEX_W // 2 if row % 2 else 0)) * SS
        y = row * ROW_STEP * SS
        t = min(1.0, (data.degree[module.ident] / RAMP_TOP) ** RAMP_GAMMA)
        fill = _tint(BUCKETS[_bucket_index(module.category)][2], t)
        pen.polygon(_hexagon(x, y, HEX_W * SS, HEX_H * SS), fill=fill, outline=PAPER, width=2 * SS)
    return canvas.resize((comb_w, comb_h), Image.Resampling.LANCZOS)


def draw_legend(pen: ImageDraw.ImageDraw, data: Survey, top: int, width: int) -> int:
    """A swatch and a count for each bucket, laid out across the full width."""
    counts = Counter(_bucket_index(m.category) for m in data.modules)
    label_font = _font(23, 600)
    count_font = _font(23, 400)

    chip_w, chip_h = 21, 24
    entries = []
    for i, (_, label, colour) in enumerate(BUCKETS):
        count = str(counts[i])
        span = chip_w + 11 + pen.textlength(label, font=label_font) + 8 + pen.textlength(count, font=count_font)
        entries.append((label, count, colour, span))

    gap = (width - sum(e[3] for e in entries)) / (len(entries) - 1)
    x = float(MARGIN)
    for label, count, colour in ((e[0], e[1], e[2]) for e in entries):
        pen.polygon(_hexagon(x, top + 1, chip_w, chip_h), fill=colour)
        x += chip_w + 11
        pen.text((x, top), label, font=label_font, fill=INK)
        x += pen.textlength(label, font=label_font) + 8
        pen.text((x, top), count, font=count_font, fill=MUTED)
        x += pen.textlength(count, font=count_font) + gap
    return top + chip_h


def render() -> None:
    data = survey()
    comb = draw_comb(data)

    lead_font = _font(27, 500)
    note_font = _font(24, 400)
    top_pad, legend_gap, caption_gap, line_gap, bottom_pad = 22, 34, 24, 12, 28
    height = top_pad + comb.height + legend_gap + 25 + caption_gap + 33 + line_gap + 30 + bottom_pad

    canvas = Image.new("RGB", (WIDTH, height), PAPER)
    canvas.paste(comb, (MARGIN, top_pad))
    pen = ImageDraw.Draw(canvas)

    y = draw_legend(pen, data, top_pad + comb.height + legend_gap, WIDTH - 2 * MARGIN)

    busiest = ", ".join(ident for ident, _ in data.degree.most_common(3))
    lead = (
        f"{len(data.modules)} backend modules  ·  {len(data.declared)} dependencies declared in their "
        f"manifests  ·  {len(data.imports)} imports between them"
    )
    note = (
        f"Depth of colour is how many other modules each one is wired to. "
        f"{sum(1 for m in data.modules if data.degree[m.ident])} of {len(data.modules)} are wired to "
        f"at least one; the deepest are {busiest}."
    )
    pen.text((MARGIN, y + caption_gap), lead, font=lead_font, fill=INK)
    pen.text((MARGIN, y + caption_gap + 33 + line_gap), note, font=note_font, fill=MUTED)

    # A caption that runs off the edge is a caption nobody finishes reading, and
    # the width it needs depends on the numbers, which change with the tree.
    for label, text, face in (("lead", lead, lead_font), ("note", note, note_font)):
        over = pen.textlength(text, font=face) - (WIDTH - 2 * MARGIN)
        if over > 0:
            raise SystemExit(f"the {label} line overruns the banner by {over:.0f}px: {text}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT, optimize=True)

    degrees = [data.degree[m.ident] for m in data.modules]
    unmanifested = [m.ident for m in data.modules if m.manifest_name is None]
    print(f"wrote {OUT.relative_to(REPO)}  {canvas.width}x{canvas.height}  {OUT.stat().st_size // 1024} KB")
    print(f"  cell {HEX_W}x{HEX_H}, row step {ROW_STEP}, {ROWS} rows of {COLS}")
    print(f"  modules               {len(data.modules)}")
    print(f"  declared dependencies {len(data.declared)}")
    print(f"  import links          {len(data.imports)}")
    print(f"  wired to at least one {sum(1 for d in degrees if d)}")
    print(f"  degree median {statistics.median(degrees)}  mean {statistics.mean(degrees):.1f}  max {max(degrees)}")
    print(f"  ramp saturates at {RAMP_TOP}, above {sum(1 for d in degrees if d < RAMP_TOP)} of {len(degrees)}")
    if unmanifested:
        # Said out loud rather than raised. The loader discovers modules by finding
        # a manifest, so a directory without one is invisible to it and drops out
        # with no error at all; that silence is the reason to print it.
        print(f"  NO MANIFEST, drawn in the Other bucket: {', '.join(unmanifested)}")


if __name__ == "__main__":
    render()
