# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""PDF / CSV / XLSX report builder for the BI Dashboards module.

A single :class:`ReportBuilder` covers all output formats so the service
layer treats them uniformly. Files are written to a per-tenant tmpdir
(falling back to ``tempfile.gettempdir()``) and the absolute path is
returned to the caller for download streaming.

Why server-local files (not S3): some installs run without object
storage. The ``/reports/{report_id}/download`` endpoint streams from
disk; tenants on S3 patch the storage layer through a hook (out of
scope for v1).
"""

from __future__ import annotations

import csv
import logging
import os
import tempfile
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.pdf_fonts import (
    BODY_FONT,
    BOLD_FONT,
    pdf_table_available_width,
    pdf_table_column_widths,
    pdf_table_paragraph_rows,
    register_pdf_fonts,
)

logger = logging.getLogger(__name__)


# Words that are names rather than words, and look wrong in sentence case.
# A report is read by estimators and finance people who write AC and EAC, so
# "Ac by currency" reads as a typo where "AC by currency" reads as the column
# they asked for.
_HEADER_ACRONYMS: frozenset[str] = frozenset(
    {
        "ac",
        "bac",
        "boq",
        "co2",
        "copq",
        "cpi",
        "csv",
        "cv",
        "din",
        "dso",
        "eac",
        "etc",
        "ev",
        "fx",
        "gaeb",
        "hse",
        "id",
        "ids",
        "kpi",
        "nrm",
        "pdf",
        "po",
        "pv",
        "qa",
        "qc",
        "rfi",
        "rfq",
        "roi",
        "spi",
        "sv",
        "tcpi",
        "trir",
        "url",
        "vac",
        "vat",
        "wbs",
    },
)

#: Row keys that are structural rather than data. ``run_report`` flattens a
#: KPI's breakdown into ``breakdown__<key>`` columns; the prefix is a join
#: marker, not part of the reader's question.
_BREAKDOWN_PREFIX = "breakdown__"


def humanize_column(name: str) -> str:
    """Turn a row key into a column heading a person can read.

    The row keys are an API shape - ``breakdown__ac_by_currency``,
    ``source_record_count`` - and printing them into a document is showing
    the reader the plumbing. They are also unreadable in a narrow column
    for a mechanical reason: a run with no spaces in it has nowhere to
    wrap, so ``breakdown__ac_by_currency`` gets chopped mid-word into
    six-character fragments, while ``Breakdown: AC by currency`` breaks
    between words.

    Only the heading changes. The row keys themselves are the JSON the
    report API returns and are left exactly as they are.

    Args:
        name: The row key.

    Returns:
        The heading to print.
    """
    label = name
    prefix = ""
    if label.startswith(_BREAKDOWN_PREFIX):
        prefix = "Breakdown: "
        label = label[len(_BREAKDOWN_PREFIX) :]
    label = label.lstrip("_")
    if not label:
        return name
    words = [w for w in label.replace("_", " ").split(" ") if w]
    rendered: list[str] = []
    for index, word in enumerate(words):
        if word.lower() in _HEADER_ACRONYMS:
            rendered.append(word.upper())
        elif index == 0:
            rendered.append(word[:1].upper() + word[1:])
        else:
            rendered.append(word)
    return prefix + " ".join(rendered)


def _safe_filename(stem: str, ext: str) -> str:
    """Make a filesystem-safe report filename."""
    keep = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
    safe = "".join(c if c in keep else "_" for c in stem)
    return f"{safe[:64]}_{uuid.uuid4().hex[:8]}.{ext}"


def _reports_dir() -> str:
    """Return the directory where reports are persisted.

    Honours ``BI_REPORTS_DIR`` env var; falls back to a subdir of the
    OS temp dir.
    """
    base = os.environ.get("BI_REPORTS_DIR")
    if base:
        os.makedirs(base, exist_ok=True)
        return base
    base = os.path.join(tempfile.gettempdir(), "openconstructionerp_reports")
    os.makedirs(base, exist_ok=True)
    return base


def build_csv_report(
    *,
    report_name: str,
    rows: list[dict[str, Any]],
) -> tuple[str, int]:
    """Write CSV → return ``(path, byte_size)``."""
    if not rows:
        # Empty CSV with single placeholder column for valid downloads
        path = os.path.join(
            _reports_dir(),
            _safe_filename(report_name, "csv"),
        )
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("(no rows)\n")
        return path, os.path.getsize(path)

    columns: list[str] = []
    for row in rows:
        for k in row:
            if k not in columns:
                columns.append(k)
    path = os.path.join(
        _reports_dir(),
        _safe_filename(report_name, "csv"),
    )
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in columns})
    return path, os.path.getsize(path)


def build_pdf_report(
    *,
    report_name: str,
    rows: list[dict[str, Any]],
    description: str | None = None,
) -> tuple[str, int]:
    """Render rows to a PDF table using reportlab. Returns ``(path, bytes)``.

    Falls back to a CSV → text-only PDF if reportlab is unavailable
    (every supported install ships it, but defensive).
    """
    path = os.path.join(_reports_dir(), _safe_filename(report_name, "pdf"))
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError:  # pragma: no cover - reportlab is a hard dep
        logger.warning("reportlab unavailable - emitting plain-text PDF")
        with open(path, "wb") as fh:
            fh.write(b"%PDF-1.4\n% reportlab missing - see logs\n")
        return path, os.path.getsize(path)

    # Register the bundled Unicode (DejaVu) faces so Cyrillic / Greek / accented
    # Latin text renders instead of tofu boxes. Idempotent; safe to call here.
    register_pdf_fonts()

    doc = SimpleDocTemplate(
        path,
        pagesize=landscape(A4),
        leftMargin=1 * cm,
        rightMargin=1 * cm,
        topMargin=1 * cm,
        bottomMargin=1 * cm,
        title=report_name,
    )
    styles = getSampleStyleSheet()
    # The base styles default to Helvetica (Latin-1 only); point them at the
    # registered Unicode faces so all rendered text uses them.
    styles["Title"].fontName = BOLD_FONT
    styles["BodyText"].fontName = BODY_FONT
    story: list[Any] = []
    story.append(Paragraph(report_name, styles["Title"]))
    if description:
        story.append(Paragraph(description, styles["BodyText"]))
    story.append(
        Paragraph(
            f"Generated {datetime.utcnow().isoformat(timespec='seconds')} UTC",
            styles["BodyText"],
        ),
    )
    story.append(Spacer(1, 0.5 * cm))

    if not rows:
        story.append(Paragraph("(no data)", styles["BodyText"]))
        doc.build(story)
        return path, os.path.getsize(path)

    # Build column list preserving insertion order across rows
    columns: list[str] = []
    for row in rows:
        for k in row:
            if k not in columns:
                columns.append(k)
    # The heading is the reader's, the key stays the API's. Humanised before
    # the widths are computed, because the widths are computed from this
    # very table and a heading that wraps at word boundaries needs a
    # different amount of room than one that has to be chopped mid-word.
    table_data: list[list[str]] = [[humanize_column(col) for col in columns]]
    for row in rows:
        table_data.append(
            [_format_cell(row.get(col)) for col in columns],
        )
    # A dashboard export is whatever columns somebody selected, so its width is
    # not known when this code is written. Left to size itself the table grew
    # past the sheet and the right hand columns were drawn onto no paper at
    # all, which extracts perfectly and prints incomplete. Cells become
    # Paragraphs so a long value wraps, and the widths are computed because
    # reportlab spreads flowable columns across the whole frame when it is left
    # to choose, which would stretch the narrow exports that are the common
    # case. Both styles are built here rather than taken from the sample sheet
    # because a Paragraph carries its own face and colour: the FONTNAME and
    # TEXTCOLOR table commands that used to dress the header row do not reach
    # one. Nothing pre-shapes these rows; a Paragraph shapes its own text, and
    # shaping the same string twice destroys it.
    header_style = ParagraphStyle(
        "dashboard-header",
        parent=styles["BodyText"],
        fontName=BOLD_FONT,
        fontSize=8,
        leading=9.6,
        textColor=colors.whitesmoke,
        spaceBefore=0,
        spaceAfter=0,
    )
    cell_style = ParagraphStyle(
        "dashboard-cell",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8,
        leading=9.6,
        spaceBefore=0,
        spaceAfter=0,
    )
    col_widths = pdf_table_column_widths(
        table_data,
        pdf_table_available_width(doc),
        cell_style,
        header_style=header_style,
        header_rows=1,
        report=report_name,
    )
    table_data = pdf_table_paragraph_rows(table_data, cell_style, header_style=header_style, header_rows=1)
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                # Backgrounds, rules and padding only. Face, size and colour
                # travel with each cell's own paragraph style now, and a table
                # command naming any of the three would be read as authoritative
                # while changing nothing on the page.
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f3f4f6")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ],
        ),
    )
    story.append(table)
    doc.build(story)
    return path, os.path.getsize(path)


def build_xlsx_report(
    *,
    report_name: str,
    rows: list[dict[str, Any]],
) -> tuple[str, int]:
    """Render rows to an XLSX using openpyxl. Returns ``(path, bytes)``.

    Returns CSV-shaped output if openpyxl is unavailable.
    """
    try:
        from openpyxl import Workbook  # type: ignore
    except ImportError:
        # Gracefully fall back to CSV with .xlsx renamed
        return build_csv_report(report_name=report_name, rows=rows)

    path = os.path.join(
        _reports_dir(),
        _safe_filename(report_name, "xlsx"),
    )
    wb = Workbook()
    ws = wb.active
    ws.title = report_name[:31]
    if rows:
        columns: list[str] = []
        for row in rows:
            for k in row:
                if k not in columns:
                    columns.append(k)
        ws.append(columns)
        for row in rows:
            ws.append([_format_cell(row.get(col)) for col in columns])
    else:
        ws.append(["(no rows)"])
    wb.save(path)
    return path, os.path.getsize(path)


def _format_cell(v: Any, _depth: int = 0) -> str:
    """Render one value for a table cell.

    A KPI breakdown is a dict, and several carry a list. Passed to ``str``
    those print as Python source - ``{'EUR': Decimal('100000.00')}`` - which
    is not a number, not a currency, and not something a reader can parse
    even by squinting. The Decimal wrappers are the giveaway that this was
    never meant to be read: nothing consumes that shape, in any of the three
    output formats.

    So a mapping becomes ``EUR: 100,000; USD: 23,456.79`` and a sequence
    becomes ``GBP, CHF``, with each value formatted the same way a
    top-level value would be. Recursion is capped at one nested level and
    falls back to ``str`` below it, because a cell that needs two levels of
    nesting is a table design problem rather than a formatting one.

    Args:
        v: The value to render.
        _depth: Internal recursion guard.

    Returns:
        The text to print in the cell.
    """
    if v is None:
        return ""
    if isinstance(v, bool):
        # Before the Decimal branch: bool is an int, and "1.0000" for True
        # would be worse than the word.
        return str(v)
    if isinstance(v, Decimal):
        return f"{v:,.4f}".rstrip("0").rstrip(".") or "0"
    if _depth < 1:
        if isinstance(v, dict):
            return "; ".join(f"{k}: {_format_cell(value, _depth + 1)}" for k, value in v.items())
        if isinstance(v, (list, tuple)):
            return ", ".join(_format_cell(item, _depth + 1) for item in v)
        if isinstance(v, (set, frozenset)):
            # Sorted, so the same data does not print in two different
            # orders in two runs of the same report.
            return ", ".join(sorted(_format_cell(item, _depth + 1) for item in v))
    return str(v)


def build_report(
    *,
    output_format: str,
    report_name: str,
    rows: list[dict[str, Any]],
    description: str | None = None,
) -> tuple[str, int]:
    """Dispatch on ``output_format`` (``pdf`` / ``xlsx`` / ``csv``)."""
    fmt = (output_format or "pdf").lower()
    if fmt == "pdf":
        return build_pdf_report(
            report_name=report_name,
            rows=rows,
            description=description,
        )
    if fmt == "xlsx":
        return build_xlsx_report(report_name=report_name, rows=rows)
    if fmt == "csv":
        return build_csv_report(report_name=report_name, rows=rows)
    # Unknown - default to CSV (safe + machine-readable)
    return build_csv_report(report_name=report_name, rows=rows)


# ── Chart export (CSV + SVG) ────────────────────────────────────────────


def export_widget_csv(
    *,
    widget_label: str,
    breakdown: dict[str, Any],
    history: list[dict[str, Any]] | None = None,
) -> tuple[str, int]:
    """Export a widget's value + breakdown + history as CSV.

    Used by ``GET /widgets/{id}/export?format=csv``.
    """
    path = os.path.join(
        _reports_dir(),
        _safe_filename(f"widget_{widget_label}", "csv"),
    )
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["key", "value"])
        for k, v in (breakdown or {}).items():
            writer.writerow([k, _format_cell(v)])
        if history:
            writer.writerow([])
            writer.writerow(["period_start", "period_end", "value"])
            for h in history:
                writer.writerow(
                    [
                        h.get("period_start", ""),
                        h.get("period_end", ""),
                        _format_cell(h.get("value")),
                    ],
                )
    return path, os.path.getsize(path)


def export_widget_svg(
    *,
    widget_label: str,
    history: list[dict[str, Any]],
    unit: str = "",
) -> tuple[str, int]:
    """Render a minimal line-chart of widget history as inline SVG.

    No external libs - handwritten SVG. Used by chart-export endpoint.
    """
    path = os.path.join(
        _reports_dir(),
        _safe_filename(f"widget_{widget_label}", "svg"),
    )
    points: list[tuple[float, float]] = []
    for idx, row in enumerate(history):
        try:
            v = float(row.get("value") or 0)
        except (ValueError, TypeError):
            v = 0.0
        points.append((float(idx), v))
    if not points:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="120">'
            '<text x="200" y="60" text-anchor="middle" font-family="sans-serif" '
            'font-size="14">(no history)</text></svg>'
        )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)
        return path, os.path.getsize(path)

    w, h = 400.0, 120.0
    pad = 20.0
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x_min, x_max = min(xs), max(xs) or 1.0
    y_min, y_max = min(ys), max(ys)
    y_range = (y_max - y_min) or 1.0
    x_range = (x_max - x_min) or 1.0

    def _to_svg(x: float, y: float) -> tuple[float, float]:
        sx = pad + (x - x_min) / x_range * (w - 2 * pad)
        sy = h - pad - (y - y_min) / y_range * (h - 2 * pad)
        return sx, sy

    path_d = "M " + " L ".join(f"{sx:.1f} {sy:.1f}" for sx, sy in (_to_svg(x, y) for x, y in points))
    title = f"{widget_label} ({unit})" if unit else widget_label
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{w:.0f}" height="{h:.0f}">'
        f'<rect width="{w:.0f}" height="{h:.0f}" fill="#ffffff"/>'
        f'<path d="{path_d}" stroke="#2563eb" stroke-width="2" fill="none"/>'
        f'<text x="10" y="15" font-family="sans-serif" font-size="11" '
        f'fill="#111827">{title}</text>'
        "</svg>"
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(svg)
    return path, os.path.getsize(path)


__all__ = [
    "build_csv_report",
    "build_pdf_report",
    "build_report",
    "build_xlsx_report",
    "export_widget_csv",
    "export_widget_svg",
    "humanize_column",
]
