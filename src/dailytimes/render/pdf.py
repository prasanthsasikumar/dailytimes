"""PDF renderer: paints a laid-out Document as vector PDF (A4, Letter, A5 or receipt-length)."""

from __future__ import annotations

import io

from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas

from . import fonts
from .layout import NAMEPLATE, Document, LineOp, QROp, RectOp, TextOp


def render_pdf(doc: Document) -> bytes:
    fonts.ensure_registered()
    buf = io.BytesIO()
    first = doc.pages[0]
    c = canvas.Canvas(buf, pagesize=(first.width, first.height), pageCompression=1, invariant=1)
    c.setTitle(f"{NAMEPLATE} — {doc.date.isoformat()}")
    c.setAuthor("Daily Times")
    c.setCreator("dailytimes")
    for page in doc.pages:
        H = page.height
        c.setPageSize((page.width, H))
        for op in page.ops:
            if isinstance(op, TextOp):
                _text(c, op, H)
            elif isinstance(op, LineOp):
                c.setStrokeColor(HexColor(op.color))
                c.setLineWidth(op.width)
                c.setDash(list(op.dash) if op.dash else [])
                c.setLineCap(1 if op.dash and op.dash[0] < 1.5 else 0)
                c.line(op.x1, H - op.y1, op.x2, H - op.y2)
            elif isinstance(op, RectOp):
                c.setFillColor(HexColor(op.color))
                c.rect(op.x, H - op.y - op.h, op.w, op.h, stroke=0, fill=1)
            elif isinstance(op, QROp):
                _qr(c, op, H)
        c.showPage()
    c.save()
    return buf.getvalue()


def _text(c: canvas.Canvas, op: TextOp, H: float) -> None:
    name = fonts.pdf_name(op.font)
    width = fonts.width(op.text, op.font, op.size, op.tracking)
    x = op.x - {"l": 0, "m": width / 2, "r": width}[op.anchor]
    t = c.beginText()
    t.setFont(name, op.size)
    t.setFillColor(HexColor(op.color))
    t.setCharSpace(op.tracking)
    t.setTextOrigin(x, H - op.y)
    t.textOut(op.text)
    c.drawText(t)


def _qr(c: canvas.Canvas, op: QROp, H: float) -> None:
    n = len(op.matrix)
    m = op.size / n
    c.setFillColor(HexColor("#000000"))
    for r, row in enumerate(op.matrix):
        col = 0
        while col < n:  # merge horizontal runs into one rectangle
            if row[col]:
                start = col
                while col < n and row[col]:
                    col += 1
                c.rect(op.x + start * m, H - op.y - (r + 1) * m, (col - start) * m, m, stroke=0, fill=1)
            else:
                col += 1
