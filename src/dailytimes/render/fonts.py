"""Bundled fonts (OFL) shared by the layout engine and both renderers.

Text is measured with ReportLab's unhinted advance widths so layout is
identical regardless of output resolution.
"""

from __future__ import annotations

import functools
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_DIR = Path(__file__).parent / "fonts"

FILES = {
    "masthead": "UnifrakturMaguntia-Book.ttf",
    "serif": "PT_Serif-Web-Regular.ttf",
    "serif-bold": "PT_Serif-Web-Bold.ttf",
    "serif-italic": "PT_Serif-Web-Italic.ttf",
    "sans": "PT_Sans-Web-Regular.ttf",
    "sans-bold": "PT_Sans-Web-Bold.ttf",
    "narrow": "PT_Sans-Narrow-Web-Regular.ttf",
    "narrow-bold": "PT_Sans-Narrow-Web-Bold.ttf",
}

# Characters assistants like to use that the bundled fonts lack.
SUBSTITUTES = {
    "→": "->", "←": "<-", "↗": "->", "↘": "->", "⇒": "=>", "■": "-", "▪": "-", "●": "•",
    "✓": "v", "✔": "v", "✗": "x", "✘": "x", "★": "*", "☆": "*", "▲": "+", "▼": "-",
    " ": " ", " ": " ", "​": "",
}


def pdf_name(key: str) -> str:
    return "DT-" + key


def path(key: str) -> Path:
    return FONT_DIR / FILES[key]


@functools.cache
def _face(key: str):
    font = TTFont(pdf_name(key), str(path(key)))
    pdfmetrics.registerFont(font)
    return font.face


def ensure_registered() -> None:
    for key in FILES:
        _face(key)


def ascent(key: str) -> float:
    """Ascent as a fraction of the font size."""
    return _face(key).ascent / 1000


def descent(key: str) -> float:
    """Descent (positive) as a fraction of the font size."""
    return -_face(key).descent / 1000


def width(text: str, key: str, size: float, tracking: float = 0.0) -> float:
    _face(key)
    w = pdfmetrics.stringWidth(text, pdf_name(key), size)
    return w + tracking * max(len(text) - 1, 0)


@functools.cache
def _coverage(key: str) -> frozenset[int]:
    return frozenset(_face(key).charToGlyph)


def sanitize(text: str, key: str) -> str:
    """Replace or drop characters the font can't draw (emoji, arrows) instead of printing boxes."""
    cov = _coverage(key)
    out = []
    for ch in text:
        if ord(ch) in cov:
            out.append(ch)
        elif ch in SUBSTITUTES:
            out.append(SUBSTITUTES[ch])
        elif ch.isspace():
            out.append(" ")
        # anything else (emoji, unsupported scripts) is dropped
    return " ".join("".join(out).split()) if out else ""
