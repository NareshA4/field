"""Audit evidence exports (CSV / XLSX / PDF). They read the SAME queryset the viewer shows (organization + site
scope + filters), cap at ``EXPORT_LIMIT`` rows (newest first; the file says when it is truncated) and are themselves
audited (``audit.exported``). Spreadsheet cells that could be interpreted as formulas are neutralised."""
from __future__ import annotations

import csv
import datetime
import io
import json
from dataclasses import dataclass

from django.utils import timezone

EXPORT_LIMIT = 10000
FORMATS = {"csv": ("text/csv; charset=utf-8", "csv"),
           "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
           "pdf": ("application/pdf", "pdf")}
COLUMNS = ["When (UTC)", "Actor", "Action", "Entity type", "Entity id", "Entity", "Site", "Before", "After",
           "Details", "IP address", "Request id"]


@dataclass
class Export:
    body: bytes
    content_type: str
    filename: str
    rows: int
    truncated: bool


def _safe(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _json(value) -> str:
    return "" if value in (None, {}, []) else json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)


def rows_for(qs, org):
    """[(columns...)] for the newest ``EXPORT_LIMIT`` rows, plus the truncated flag."""
    from apps.sites.models import Site

    sites = {s.pk: s.code for s in Site.objects.for_organization(org)}
    items = list(qs.order_by("-occurred_at", "-id")[:EXPORT_LIMIT + 1])
    truncated = len(items) > EXPORT_LIMIT
    out = []
    for e in items[:EXPORT_LIMIT]:
        out.append([e.occurred_at.astimezone(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S"), e.actor_email or "system",
                    e.action, e.target_type, e.target_id, e.target_repr, sites.get(e.site_id, "") if e.site_id else "",
                    _json(e.before), _json(e.after), _json(e.metadata), e.ip_address or "", e.request_id])
    return out, truncated


def build(fmt: str, qs, org, *, actor, filters_text: str = "") -> Export:
    if fmt not in FORMATS:
        raise ValueError("Unknown export format.")
    rows, truncated = rows_for(qs, org)
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    ctype, ext = FORMATS[fmt]
    name = f"audit-{org.slug}-{stamp}.{ext}"
    note = f"Showing the newest {EXPORT_LIMIT} rows only; narrow the filters to export the rest." if truncated else ""
    meta = [f"Organization: {org.name}", f"Generated: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')} UTC by "
            f"{getattr(actor, 'email', '')}", f"Rows: {len(rows)}", f"Filters: {filters_text or 'none'}"]
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([_safe(c) for c in r])
        body = ("﻿" + buf.getvalue()).encode("utf-8")
    elif fmt == "xlsx":
        body = _xlsx(rows, meta, note)
    else:
        body = _pdf(rows, meta, note)
    return Export(body, ctype, name, len(rows), truncated)


def _xlsx(rows, meta, note) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Audit"
    ws.append(COLUMNS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in rows:
        ws.append([_safe(c) for c in r])
    ws.freeze_panes = "A2"
    for i, width in enumerate((20, 28, 28, 24, 38, 40, 8, 40, 40, 40, 16, 34), start=1):
        ws.column_dimensions[chr(64 + i)].width = width
    info = wb.create_sheet("About")
    for line in meta + ([note] if note else []):
        info.append([line])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _pdf(rows, meta, note) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small", fontSize=7, leading=8.5)
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24,
                            title="Audit evidence")
    story = [Paragraph("Audit evidence", styles["Title"])]
    story += [Paragraph(line.replace("&", "&amp;").replace("<", "&lt;"), styles["Normal"]) for line in meta]
    if note:
        story.append(Paragraph(f"<b>{note}</b>", styles["Normal"]))
    story.append(Spacer(1, 8))

    def cell(text, n=90):
        text = str(text or "")
        text = text if len(text) <= n else text[: n - 1] + "…"
        return Paragraph(text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), small)

    data = [[Paragraph(f"<b>{h}</b>", small) for h in ("When (UTC)", "Actor", "Action", "Entity", "Site", "Change")]]
    for r in rows:
        change = r[8] or r[9]
        data.append([cell(r[0], 20), cell(r[1], 40), cell(r[2], 40), cell(f"{r[3]} {r[5]}", 70), cell(r[6], 10),
                     cell(change, 110)])
    table = Table(data, repeatRows=1, colWidths=[78, 120, 110, 190, 36, 280])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("GRID", (0, 0), (-1, -1), 0.25, colors.grey)]))
    story.append(table)
    doc.build(story)
    return out.getvalue()
