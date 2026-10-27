"""Raster renderer: paints a laid-out Document into images at the profile's dpi.

Thermal (mono) profiles produce crisp 1-bit images exactly `printable_width_px` wide.
"""

from __future__ import annotations

import functools
import math

from PIL import Image, ImageDraw, ImageFont

from . import fonts
from .layout import Document, LineOp, QROp, RectOp, TextOp


@functools.cache
def _font(key: str, px: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(fonts.path(key)), px)


def _fill(color: str, mono: bool):
    if mono:
        return 255 if color.lower() in ("#ffffff", "#fff", "white") else 0
    return color


def render_raster(doc: Document) -> list[Image.Image]:
    prof = doc.profile
    mono = not prof.color
    hard = mono and prof.type == "thermal"  # thermal heads print dots: crisp 1-bit; page previews stay smooth
    scale = prof.dpi / 72
    images = []
    for page in doc.pages:
        width = prof.printable_width_px if prof.type == "thermal" else round(page.width * scale)
        height = math.ceil(page.height * scale)
        img = Image.new("L" if mono else "RGB", (width, height), 255 if mono else "white")
        d = ImageDraw.Draw(img)
        if hard:
            d.fontmode = "1"  # no anti-aliasing: thermal heads print dots, not greys
        for op in page.ops:
            if isinstance(op, TextOp):
                _text(d, op, scale, mono)
            elif isinstance(op, LineOp):
                _line(d, op, scale, mono)
            elif isinstance(op, RectOp):
                x0, y0 = round(op.x * scale), round(op.y * scale)
                x1, y1 = round((op.x + op.w) * scale), round((op.y + op.h) * scale)
                d.rectangle([x0, y0, x1 - 1, y1 - 1], fill=_fill(op.color, mono))
            elif isinstance(op, QROp):
                _qr(d, op, scale)
        images.append(img.convert("1", dither=Image.Dither.NONE) if hard else img)
    return images


def _text(d: ImageDraw.ImageDraw, op: TextOp, scale: float, mono: bool) -> None:
    font = _font(op.font, op.size * scale)
    fill = _fill(op.color, mono)
    x, y = op.x * scale, op.y * scale
    total = fonts.width(op.text, op.font, op.size, op.tracking) * scale
    x -= {"l": 0, "m": total / 2, "r": total}[op.anchor]
    if op.tracking:
        for ch in op.text:
            d.text((x, y), ch, font=font, fill=fill, anchor="ls")
            x += (fonts.width(ch, op.font, op.size) + op.tracking) * scale
        return
    # Place each word at its layout position: without libraqm, Pillow's hinted advances run
    # ~3% wide at preview dpi, which would push long lines past the column edge.
    space = fonts.width(" ", op.font, op.size) * scale
    for word in op.text.split(" "):
        if word:
            d.text((x, y), word, font=font, fill=fill, anchor="ls")
        x += fonts.width(word, op.font, op.size) * scale + space


def _line(d: ImageDraw.ImageDraw, op: LineOp, scale: float, mono: bool) -> None:
    w = max(1, round(op.width * scale))
    fill = _fill(op.color, mono)
    x1, y1, x2, y2 = (v * scale for v in (op.x1, op.y1, op.x2, op.y2))
    if op.dash is None:
        d.line([(x1, y1), (x2, y2)], fill=fill, width=w)
        return
    on, off = (max(1.0, v * scale) for v in op.dash)
    length = math.hypot(x2 - x1, y2 - y1)
    if length == 0:
        return
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    pos = 0.0
    while pos < length:
        end = min(pos + on, length)
        if w <= 2 and on <= 2.5:  # dots: draw squares so they survive thresholding
            cx, cy = round(x1 + ux * pos), round(y1 + uy * pos - w / 2)
            d.rectangle([cx, cy, cx + w - 1, cy + w - 1], fill=fill)
        else:
            d.line([(x1 + ux * pos, y1 + uy * pos), (x1 + ux * end, y1 + uy * end)], fill=fill, width=w)
        pos += on + off


def _qr(d: ImageDraw.ImageDraw, op: QROp, scale: float) -> None:
    n = len(op.matrix)
    target = op.size * scale
    module = max(1, int(target // n))  # whole pixels per module keeps the code scannable
    total = module * n
    ox = round(op.x * scale + (target - total) / 2)
    oy = round(op.y * scale + (target - total) / 2)
    for r, row in enumerate(op.matrix):
        for c, dark in enumerate(row):
            if dark:
                x, y = ox + c * module, oy + r * module
                d.rectangle([x, y, x + module - 1, y + module - 1], fill=0)
