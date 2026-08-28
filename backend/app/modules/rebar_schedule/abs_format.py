# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Reader and writer for the ABS reinforcement-data interchange format.

ABS ("Allgemeine Bewehrungs-Schnittstelle") is the flat text format German
CAD systems emit alongside a printed bending schedule so that a bending shop
can feed the shapes straight into a bending machine. It is specified by the
BVBS guideline "Datenaustausch von Bewehrungsdaten", version 3.1 of May 2021,
published by Bundesverband Bausoftware e.V. This module is written from that
document; the field identifiers, block identifiers and super-group names below
are the vocabulary the standard defines, which is why they are German.

    Careful with the acronym. BVBS names the publisher, and the same
    organisation also publishes the GAEB conformance files this codebase
    already calls "BVBS Pruefdateien" (see app/modules/boq/importers).
    Those are GAEB DA XML and have nothing to do with this format. This
    module is named for what it carries - a rebar schedule - rather than
    for the publisher, so the two never read as the same subject.

Shape of a record
-----------------
One record is one line, terminated by CRLF, and describes one bending shape::

    BF2D@HjOCE-DEMO@r312@ib@p1@l1000@n10@e0.888@d12@gB500B@s48@v@Gl300@w90@l700@w0@C93@

It opens with a super-group identifier (:data:`SUPER_GROUPS`) and then carries
a sequence of blocks. A block starts with an uppercase block identifier and
runs until an ``@`` is followed by another uppercase letter, or the record
ends. Inside a block every field is a lowercase identifier, its value, and a
closing ``@``. The same letter means different things in different blocks:
``r`` is the drawing number in the header block and a bend radius in the
geometry block, so a field is only ever interpreted together with its block.

Blocks appear in a fixed order: header, then either a geometry block or a
spacer block, then a coupler block, then any number of bar blocks, then a
private block, then the checksum. Only the header and the checksum are
mandatory.

Checksum
--------
Every record ends with a checksum block whose value is chosen so that

    (checksum - 96) + sum(ASCII values from the start of the record up to and
    including the "C" of the checksum block) is a multiple of 32

which the guideline gives as ``IP = 96 - (sum modulo 32)``. Two consequences
are worth knowing before trusting it: the checksum is taken modulo 32 and both
``@`` (64) and the space (32) are exact multiples of 32, so inserting or
dropping a separator or a space does not change it. It catches transcription
damage, not deliberate tampering.

This module keeps the exact source text of every record it parses, so parsing
and re-serialising a file returns the original bytes.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

# ── Vocabulary defined by the standard ──────────────────────────────────────

#: Super-group identifiers, one per family of reinforcement shape.
SUPER_GROUPS: tuple[str, ...] = ("BF2D", "BF3D", "BFWE", "BFMA", "BFGT", "BFAU")

#: What each super-group covers, keyed for the i18n layer rather than shown raw.
SUPER_GROUP_KINDS: dict[str, str] = {
    "BF2D": "planar_bending_shape",
    "BF3D": "spatial_bending_shape",
    "BFWE": "helix_or_spiral",
    "BFMA": "mesh",
    "BFGT": "lattice_girder",
    "BFAU": "spacer_or_support_cage",
}

HEADER = "H"
GEOMETRY = "G"
SPACER = "A"
COUPLER = "M"
PRIVATE = "P"
CHECKSUM = "C"
#: Bar blocks: X for transverse bars, Y for longitudinal bars, E for skewed.
BAR_BLOCKS: tuple[str, ...] = ("X", "Y", "E")

#: Every block identifier the standard defines, in the order a record uses them.
BLOCK_ORDER: tuple[str, ...] = (HEADER, GEOMETRY, SPACER, COUPLER, *BAR_BLOCKS, PRIVATE, CHECKSUM)

#: Header fields in the order the standard fixes. A record must carry every
#: identifier that applies to its super-group, in this order, even when the
#: value is empty.
HEADER_FIELD_ORDER: tuple[str, ...] = (
    "j",  # project number (optional)
    "r",  # drawing number
    "i",  # drawing index
    "p",  # bending / mesh / accessory position
    "l",  # bar length, mesh length or support length, in mm
    "n",  # number of bars, meshes or accessories
    "e",  # weight of one shape, in kg
    "d",  # bar diameter, in mm
    "g",  # steel grade
    "s",  # bending roller diameter, in mm
    "m",  # mesh type, spacer type or support type
    "b",  # mesh width, in mm
    "h",  # lattice-girder or spacer height, in mm
    "v",  # author, reserved by the standard and currently not to be used
    "a",  # layer, counting upwards (optional)
    "t",  # delta length for staggered reinforcement (optional)
    "c",  # stagger group (optional)
)

#: Header fields each super-group must carry, from the applicability table.
HEADER_FIELDS_BY_GROUP: dict[str, tuple[str, ...]] = {
    "BF2D": ("j", "r", "i", "p", "l", "n", "e", "d", "g", "s", "v"),
    "BF3D": ("j", "r", "i", "p", "l", "n", "e", "d", "g", "s", "v"),
    "BFWE": ("j", "r", "i", "p", "l", "n", "e", "d", "g", "s", "v"),
    "BFMA": ("j", "r", "i", "p", "l", "n", "e", "g", "s", "m", "b", "v"),
    "BFGT": ("j", "r", "i", "p", "l", "n", "e", "g", "m", "h", "v"),
    "BFAU": ("j", "r", "i", "p", "l", "n", "e", "m", "h"),
}

#: Optional header fields, valid where the applicability table allows them but
#: never required. Kept apart from the mandatory set so the ordering rule can
#: accept a record that simply omits them.
OPTIONAL_HEADER_FIELDS: frozenset[str] = frozenset({"a", "t", "c"})

#: Geometry fields, per block, again in the order the standard fixes.
GEOMETRY_FIELDS: tuple[str, ...] = (
    "l",  # leg length, in mm
    "r",  # radius of a curved leg, in mm
    "w",  # angle of the bend that follows, in degrees
    "x",  # X coordinate of a 3D bar
    "y",  # Y coordinate of a 3D bar
    "z",  # Z coordinate of a 3D bar
    "n",  # number of turns of a helix
    "g",  # pitch of a helix, in mm
    "c",  # last length of staggered reinforcement
)

#: Record separator. The standard names CRLF explicitly.
RECORD_SEPARATOR = "\r\n"

#: The standard's compactness target: no more than 1000 characters per shape.
MAX_RECORD_LENGTH = 1000

#: The checksum is taken modulo this, and offset from this.
_CHECKSUM_MODULUS = 32
_CHECKSUM_OFFSET = 96

_NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")


class AbsError(Exception):
    """Base class for every failure raised by this codec."""


class AbsSyntaxError(AbsError):
    """A record does not follow the grammar the standard describes.

    Raised only for damage that leaves the record unreadable - an unknown
    super-group, a block that never terminates, a field with no identifier.
    Everything a reader can still make sense of is parsed and left to the
    validation rules to judge, so that an operator sees a full report rather
    than the first problem.
    """

    def __init__(self, message: str, *, line_no: int | None = None, column: int | None = None) -> None:
        self.line_no = line_no
        self.column = column
        where = "" if line_no is None else f" (line {line_no}"
        if where and column is not None:
            where += f", column {column}"
        if where:
            where += ")"
        super().__init__(f"{message}{where}")


# ── Checksum ────────────────────────────────────────────────────────────────


def compute_checksum(prefix: str) -> int:
    """Compute the checksum value for a record prefix.

    Args:
        prefix: Everything from the start of the record up to and including
            the ``C`` that opens the checksum block.

    Returns:
        The checksum value, always between 65 and 96 inclusive.

    Example:
        On the shortest prefix there is, a super-group identifier and the C
        that opens the checksum block, the sum is 66 + 70 + 50 + 68 + 64 + 67,
        which is 385. That is 32 * 12 with 1 left over, so the answer is 95.

        >>> compute_checksum("BF2D@C")
        95
    """
    return _CHECKSUM_OFFSET - (sum(ord(char) for char in prefix) % _CHECKSUM_MODULUS)


def split_checksum(text: str) -> tuple[str, int | None]:
    """Split a record into the part before the checksum block and its value.

    Args:
        text: One record, without its CRLF terminator.

    Returns:
        A pair of the text up to but not including the ``C``, and the declared
        checksum value, or ``None`` when the record carries no checksum block.
    """
    marker = text.rfind("@" + CHECKSUM)
    if marker < 0:
        return text, None
    body = text[: marker + 1]
    declared = text[marker + 2 :].rstrip("@")
    if not declared.isdigit():
        return text, None
    return body, int(declared)


def verify_checksum(text: str) -> bool:
    """Report whether a record's declared checksum matches its content.

    Args:
        text: One record, without its CRLF terminator.

    Returns:
        ``True`` when a checksum block is present and correct, ``False`` when
        it is present and wrong or absent altogether.
    """
    body, declared = split_checksum(text)
    if declared is None:
        return False
    return compute_checksum(body + CHECKSUM) == declared


# ── Parsed shapes ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AbsField:
    """One ``<identifier><value>@`` field inside a block."""

    key: str
    value: str

    def as_decimal(self) -> Decimal | None:
        """Return the value as a number, or ``None`` if it is not numeric."""
        text = self.value.strip()
        if not _NUMERIC.match(text):
            return None
        try:
            return Decimal(text)
        except InvalidOperation:  # pragma: no cover - guarded by the regex
            return None


@dataclass(frozen=True, slots=True)
class AbsBlock:
    """One block of a record.

    Attributes:
        kind: The uppercase block identifier.
        axis: ``"x"`` or ``"y"`` for a mesh geometry block that says which
            bars are bent, otherwise ``None``.
        fields: The block's fields, in source order. Repeats are kept, because
            a 3D geometry block repeats ``x``/``y``/``z`` once per vertex.
        raw: The block's exact source text, including its identifier and its
            closing ``@``.
    """

    kind: str
    axis: str | None
    fields: tuple[AbsField, ...]
    raw: str

    def first(self, key: str) -> str | None:
        """Return the first value carried under ``key``, or ``None``."""
        for item in self.fields:
            if item.key == key:
                return item.value
        return None

    def values(self, key: str) -> tuple[str, ...]:
        """Return every value carried under ``key``, in source order."""
        return tuple(item.value for item in self.fields if item.key == key)


@dataclass(frozen=True, slots=True)
class AbsRecord:
    """One reinforcement shape.

    Attributes:
        group: The super-group identifier, one of :data:`SUPER_GROUPS`.
        blocks: Every block of the record in source order, checksum included.
        raw: The record's exact source text, without its CRLF terminator.
        line_no: 1-based line number in the source file, for error reporting.
    """

    group: str
    blocks: tuple[AbsBlock, ...]
    raw: str
    line_no: int = 0

    @property
    def header(self) -> AbsBlock | None:
        """The header block, or ``None`` when the record has none."""
        return self.block(HEADER)

    @property
    def geometry(self) -> AbsBlock | None:
        """The geometry block, or ``None`` when the record has none."""
        return self.block(GEOMETRY)

    @property
    def bar_blocks(self) -> tuple[AbsBlock, ...]:
        """Every bar block, in source order."""
        return tuple(item for item in self.blocks if item.kind in BAR_BLOCKS)

    def block(self, kind: str) -> AbsBlock | None:
        """Return the first block of ``kind``, or ``None``."""
        for item in self.blocks:
            if item.kind == kind:
                return item
        return None

    def header_value(self, key: str) -> str | None:
        """Return a header field's value, or ``None`` when it is absent."""
        head = self.header
        return None if head is None else head.first(key)

    def header_number(self, key: str) -> Decimal | None:
        """Return a header field's value as a number, or ``None``."""
        head = self.header
        if head is None:
            return None
        for item in head.fields:
            if item.key == key:
                return item.as_decimal()
        return None

    @property
    def declared_checksum(self) -> int | None:
        """The checksum value the record carries, or ``None``."""
        return split_checksum(self.raw)[1]

    @property
    def checksum_ok(self) -> bool:
        """Whether the declared checksum matches the record's content."""
        return verify_checksum(self.raw)


@dataclass(slots=True)
class AbsFile:
    """A parsed ABS file.

    Attributes:
        records: Every record the file carried, in source order.
        encoding: The encoding the bytes were decoded with.
        non_ascii_lines: 1-based line numbers that held bytes outside ASCII.
            The standard names ASCII, so these are reported rather than
            silently accepted.
    """

    records: list[AbsRecord] = field(default_factory=list)
    encoding: str = "ascii"
    non_ascii_lines: list[int] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self) -> Iterator[AbsRecord]:
        return iter(self.records)


# ── Geometry, read out of a geometry block ──────────────────────────────────


@dataclass(frozen=True, slots=True)
class BendSegment:
    """One leg of a planar bending shape, with the bend that follows it.

    Attributes:
        length_mm: Length of a straight leg, or ``None`` for a curved one.
        radius_mm: Inner radius of a curved leg, or ``None`` for a straight one.
        angle_deg: The bend angle that follows. For a curved leg this is the
            arc's opening angle. A shape whose last leg ends straight carries
            an explicit zero here, which the standard requires.
        trailing_angle_deg: The bend angle written after an arc's opening
            angle, when the transition into the next leg is itself bent.
    """

    length_mm: Decimal | None
    radius_mm: Decimal | None
    angle_deg: Decimal | None
    trailing_angle_deg: Decimal | None = None

    @property
    def developed_length_mm(self) -> Decimal:
        """Length this segment contributes to the bar's developed length.

        A straight leg contributes its own length. A curved leg contributes
        the arc it draws, ``radius * angle`` in radians. Straight legs are
        outside dimensions and bends between them add nothing, which is why a
        chain of straight legs sums to the header length exactly.
        """
        if self.length_mm is not None:
            return self.length_mm
        if self.radius_mm is not None and self.angle_deg is not None:
            arc = float(self.radius_mm) * math.radians(abs(float(self.angle_deg)))
            return Decimal(str(round(arc, 3)))
        return Decimal(0)


def read_segments(block: AbsBlock) -> list[BendSegment]:
    """Read the leg/angle pairs out of a planar or mesh geometry block.

    The standard fixes the order: a length or a radius first, then the angle.
    After a curved leg a second angle may follow, giving the bend that leads
    into the next leg.

    Args:
        block: A geometry block, as returned by :func:`parse_record`.

    Returns:
        The segments in source order. Coordinate and helix fields are ignored,
        because they describe a different geometry; use
        :func:`read_coordinates` and :func:`read_turns` for those.
    """
    segments: list[BendSegment] = []
    pending_length: Decimal | None = None
    pending_radius: Decimal | None = None
    open_leg = False
    for item in block.fields:
        if item.key in ("l", "r"):
            if open_leg:
                # A leg with no angle behind it. The standard requires the
                # angle, so this is left for the validation rules to report.
                segments.append(BendSegment(pending_length, pending_radius, None))
            pending_length = item.as_decimal() if item.key == "l" else None
            pending_radius = item.as_decimal() if item.key == "r" else None
            open_leg = True
        elif item.key == "w":
            if open_leg:
                segments.append(BendSegment(pending_length, pending_radius, item.as_decimal()))
                pending_length, pending_radius, open_leg = None, None, False
            elif segments and segments[-1].radius_mm is not None and segments[-1].trailing_angle_deg is None:
                # A second angle after an arc: the bend leading into the next leg.
                last = segments[-1]
                segments[-1] = BendSegment(last.length_mm, last.radius_mm, last.angle_deg, item.as_decimal())
    if open_leg:
        segments.append(BendSegment(pending_length, pending_radius, None))
    return segments


def read_coordinates(block: AbsBlock) -> list[tuple[Decimal, Decimal, Decimal]]:
    """Read the vertex offsets out of a spatial geometry block.

    A 3D bar is written as a run of ``x``/``y``/``z`` triples. The standard
    allows a writer to name only the coordinates that actually change, so a
    missing member of a triple repeats as zero rather than carrying over.

    Args:
        block: A geometry block belonging to a ``BF3D`` record.

    Returns:
        One ``(x, y, z)`` offset per vertex, in source order.
    """
    out: list[tuple[Decimal, Decimal, Decimal]] = []
    current: dict[str, Decimal] = {}
    for item in block.fields:
        if item.key not in ("x", "y", "z"):
            continue
        if item.key in current:
            out.append((current.get("x", Decimal(0)), current.get("y", Decimal(0)), current.get("z", Decimal(0))))
            current = {}
        current[item.key] = item.as_decimal() or Decimal(0)
    if current:
        out.append((current.get("x", Decimal(0)), current.get("y", Decimal(0)), current.get("z", Decimal(0))))
    return out


def read_turns(block: AbsBlock) -> list[tuple[Decimal, Decimal]]:
    """Read the turn count and pitch pairs out of a helix geometry block.

    A helix is written as its planar shape first, then any number of ``n``
    (turns) and ``g`` (pitch, in mm) pairs, so that one column can carry a
    tighter pitch at its foot and head than in the middle.

    Args:
        block: A geometry block belonging to a ``BFWE`` record.

    Returns:
        One ``(turns, pitch_mm)`` pair per run, in source order.
    """
    out: list[tuple[Decimal, Decimal]] = []
    pending: Decimal | None = None
    for item in block.fields:
        if item.key == "n":
            pending = item.as_decimal()
        elif item.key == "g" and pending is not None:
            out.append((pending, item.as_decimal() or Decimal(0)))
            pending = None
    return out


# ── Reading ─────────────────────────────────────────────────────────────────


def _read_block_body(text: str, start: int, line_no: int) -> tuple[str, int]:
    """Return a block body starting at ``start`` and the index just past it.

    A block runs until an ``@`` that is followed by an uppercase letter, or
    until the record ends. The returned body includes its closing ``@``.
    """
    cursor = start
    while True:
        at = text.find("@", cursor)
        if at < 0:
            raise AbsSyntaxError("block is not terminated by '@'", line_no=line_no, column=start + 1)
        nxt = at + 1
        if nxt >= len(text) or text[nxt].isupper():
            return text[start:nxt], nxt
        cursor = nxt


def _read_fields(body: str, *, block_kind: str, line_no: int, offset: int) -> tuple[AbsField, ...]:
    """Split a block body into its fields.

    The checksum block carries a bare number rather than an identified field,
    and the private block carries whatever the writing system chose, so both
    are handled by the caller and never reach here.
    """
    fields: list[AbsField] = []
    for chunk in body.split("@")[:-1]:
        if not chunk:
            raise AbsSyntaxError(
                f"block '{block_kind}' has a field with no identifier",
                line_no=line_no,
                column=offset + 1,
            )
        key = chunk[0]
        if not ("a" <= key <= "z"):
            raise AbsSyntaxError(
                f"block '{block_kind}' has field identifier '{key}', which is not a lowercase letter",
                line_no=line_no,
                column=offset + 1,
            )
        fields.append(AbsField(key=key, value=chunk[1:]))
    return tuple(fields)


def parse_record(text: str, *, line_no: int = 0) -> AbsRecord:
    """Parse one record.

    Args:
        text: One record, with or without its CRLF terminator.
        line_no: 1-based line number, carried into errors and the result.

    Returns:
        The parsed record, holding its blocks and its exact source text.

    Raises:
        AbsSyntaxError: The record is not readable - an unknown super-group,
            a block that never closes, or a field with no identifier.
    """
    raw = text.rstrip("\r\n")
    group = next((name for name in SUPER_GROUPS if raw.startswith(name + "@")), None)
    if group is None:
        opening = raw[:8]
        raise AbsSyntaxError(
            f"record does not open with a known super-group identifier, found '{opening}'",
            line_no=line_no,
            column=1,
        )

    blocks: list[AbsBlock] = []
    cursor = len(group) + 1
    while cursor < len(raw):
        block_start = cursor
        kind = raw[cursor]
        if not kind.isupper():
            raise AbsSyntaxError(
                f"expected an uppercase block identifier, found '{kind}'",
                line_no=line_no,
                column=cursor + 1,
            )
        body_start = cursor + 1
        axis: str | None = None
        # A mesh whose bars are bent marks the geometry block Gx or Gy. A
        # spatial record writes its first coordinate as the field x, so the
        # axis marker is only ever read for a mesh, and only when a field
        # identifier follows it.
        if (
            kind == GEOMETRY
            and group == "BFMA"
            and body_start + 1 < len(raw)
            and raw[body_start] in ("x", "y")
            and raw[body_start + 1].islower()
        ):
            axis = raw[body_start]
            body_start += 1
        body, cursor = _read_block_body(raw, body_start, line_no)
        if kind == CHECKSUM:
            fields: tuple[AbsField, ...] = (AbsField(key="", value=body.rstrip("@")),)
        elif kind == PRIVATE:
            # Free-form, project or company internal. The standard only asks
            # that it be closed with '@' and hold no '@' followed by an
            # uppercase letter, so its content is kept whole rather than
            # forced into identified fields.
            fields = (AbsField(key="", value=body.rstrip("@")),)
        else:
            fields = _read_fields(body, block_kind=kind, line_no=line_no, offset=body_start)
        blocks.append(AbsBlock(kind=kind, axis=axis, fields=fields, raw=raw[block_start:cursor]))
    return AbsRecord(group=group, blocks=tuple(blocks), raw=raw, line_no=line_no)


def decode_bytes(data: bytes) -> tuple[str, str, list[int]]:
    """Decode ABS file bytes, reporting anything outside ASCII.

    The standard names ASCII. Files written by German CAD systems do reach us
    with single-byte accented characters in free-text fields, so rather than
    refuse them the decoder falls back to cp1252 and reports which lines were
    affected, leaving the judgement to the validation rules.

    Args:
        data: The raw file bytes.

    Returns:
        The decoded text, the encoding used, and the 1-based numbers of the
        lines that held bytes outside ASCII.
    """
    try:
        return data.decode("ascii"), "ascii", []
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
        offenders = [n for n, line in enumerate(text.splitlines(), start=1) if not line.isascii()]
        return text, "cp1252", offenders


def parse_file(data: bytes | str) -> AbsFile:
    """Parse a whole ABS file.

    Blank lines are skipped, so a file that ends with a trailing newline - as
    every file written with CRLF record terminators does - reads cleanly.

    Args:
        data: The file's bytes, or already-decoded text.

    Returns:
        The parsed file.

    Raises:
        AbsSyntaxError: Any record is unreadable. The error names the line.
    """
    if isinstance(data, bytes):
        text, encoding, non_ascii = decode_bytes(data)
    else:
        text, encoding, non_ascii = data, "ascii", [n for n, ln in enumerate(data.splitlines(), 1) if not ln.isascii()]
    parsed = AbsFile(encoding=encoding, non_ascii_lines=non_ascii)
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        parsed.records.append(parse_record(line, line_no=line_no))
    return parsed


# ── Writing ─────────────────────────────────────────────────────────────────


def render_block(kind: str, fields: list[tuple[str, str]] | tuple[tuple[str, str], ...], *, axis: str = "") -> str:
    """Render one block from ``(identifier, value)`` pairs.

    Args:
        kind: The uppercase block identifier.
        fields: The block's fields in the order they should be written. A
            field with no value still writes its identifier, which the
            standard requires.
        axis: ``"x"`` or ``"y"`` for a mesh geometry block whose bars are
            bent, otherwise empty.

    Returns:
        The block's text, including its closing ``@``.
    """
    body = "".join(f"{key}{value}@" for key, value in fields)
    return f"{kind}{axis}{body}"


def render_record(group: str, blocks: list[str] | tuple[str, ...]) -> str:
    """Render a record and append its checksum block.

    Args:
        group: The super-group identifier.
        blocks: Rendered blocks, in the order the standard fixes, without a
            checksum block - this function computes and appends it.

    Returns:
        The record's text, without a CRLF terminator.

    Raises:
        ValueError: ``group`` is not a super-group the standard defines.
    """
    if group not in SUPER_GROUPS:
        raise ValueError(f"unknown super-group '{group}'")
    body = f"{group}@" + "".join(blocks)
    return f"{body}{CHECKSUM}{compute_checksum(body + CHECKSUM)}@"


def render_file(records: list[AbsRecord] | tuple[AbsRecord, ...]) -> bytes:
    """Render parsed records back to file bytes.

    Every record is written from the source text it was parsed from, so a file
    that is read and written again is byte-for-byte what came in. That matters
    here: a record's checksum covers its exact characters, and re-deriving the
    text from the parsed fields would silently normalise a file the bending
    shop already holds a signed copy of.

    Args:
        records: Records to write.

    Returns:
        The file's bytes, ASCII where the records allow it and cp1252 where a
        free-text field carries an accented character.
    """
    text = "".join(f"{record.raw}{RECORD_SEPARATOR}" for record in records)
    try:
        return text.encode("ascii")
    except UnicodeEncodeError:
        return text.encode("cp1252", errors="replace")
