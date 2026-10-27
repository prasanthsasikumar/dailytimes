"""The single layout engine.

Blocks -> stacked boxes -> flowed into frames (one endless frame for roll paper,
N columns per page for sheets) -> pages of draw ops in points, top-left origin.
Renderers only paint ops; every typographic decision lives here.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Callable

import qrcode
from qrcode.constants import ERROR_CORRECT_M

from ..blocks import Brief, short_link, source_name
from . import fonts
from .profiles import Profile

NAMEPLATE = "The Daily Times"
BLACK = "#000000"


# ------------------------------------------------------------------ draw ops

@dataclass
class TextOp:
    x: float
    y: float  # baseline
    text: str
    font: str
    size: float
    anchor: str = "l"  # l | m | r : x is the left edge, centre or right edge
    tracking: float = 0.0
    color: str = BLACK


@dataclass
class LineOp:
    x1: float
    y1: float
    x2: float
    y2: float
    width: float = 0.5
    dash: tuple[float, float] | None = None
    color: str = BLACK


@dataclass
class RectOp:
    x: float
    y: float
    w: float
    h: float
    color: str = BLACK


@dataclass
class QROp:
    x: float
    y: float
    size: float
    matrix: list[list[bool]]


Op = TextOp | LineOp | RectOp | QROp


def _moved(op: Op, dx: float, dy: float) -> Op:
    if isinstance(op, LineOp):
        return dataclasses.replace(op, x1=op.x1 + dx, x2=op.x2 + dx, y1=op.y1 + dy, y2=op.y2 + dy)
    return dataclasses.replace(op, x=op.x + dx, y=op.y + dy)


@dataclass
class Page:
    width: float
    height: float
    ops: list[Op] = field(default_factory=list)


@dataclass
class Document:
    profile: Profile
    date: dt.date
    pages: list[Page]


# ------------------------------------------------------------------ styles

@dataclass(frozen=True)
class TextStyle:
    font: str
    size: float
    leading: float
    tracking: float = 0.0
    upper: bool = False
    color: str = BLACK

    def prep(self, text: str) -> str:
        text = fonts.sanitize(text, self.font)
        return text.upper() if self.upper else text

    def width(self, text: str) -> float:
        return fonts.width(text, self.font, self.size, self.tracking)

    def baseline(self) -> float:
        """Baseline offset from the top of a line box."""
        a, d = fonts.ascent(self.font), fonts.descent(self.font)
        return (self.leading - (a + d) * self.size) / 2 + a * self.size


@dataclass(frozen=True)
class Theme:
    body: TextStyle
    bold: TextStyle
    h1: TextStyle
    h2: TextStyle
    small: TextStyle
    link: TextStyle
    caption: TextStyle
    date: TextStyle
    masthead_max: float
    gap: float  # between blocks
    tight: float  # between consecutive bullets/rows
    section_gap: float
    bullet_indent: float
    qr_size: float
    rule: float  # hairline width
    accent: str


def theme_for(profile: Profile) -> Theme:
    s = profile.font_scale
    accent = "#8b1a1a" if profile.color else BLACK
    if profile.layout == "slip":
        return Theme(
            body=TextStyle("sans", 9 * s, 11.6 * s),
            bold=TextStyle("sans-bold", 9 * s, 11.6 * s),
            h1=TextStyle("sans-bold", 8.6 * s, 11 * s, tracking=0.9 * s, upper=True, color="#ffffff"),
            h2=TextStyle("sans-bold", 10 * s, 12.4 * s),
            small=TextStyle("sans", 7 * s, 8.8 * s),
            link=TextStyle("narrow", 7.4 * s, 9 * s),
            caption=TextStyle("sans-bold", 8 * s, 10 * s),
            date=TextStyle("sans-bold", 7 * s, 10 * s, tracking=0.7 * s, upper=True),
            masthead_max=26 * s,
            gap=4.5 * s, tight=1.6 * s, section_gap=9 * s,
            bullet_indent=8 * s, qr_size=74 * s, rule=0.6, accent=BLACK,
        )
    grey = "#555555" if profile.color else "#333333"
    return Theme(
        body=TextStyle("serif", 9.6 * s, 12.8 * s),
        bold=TextStyle("serif-bold", 9.6 * s, 12.8 * s),
        h1=TextStyle("sans-bold", 8.4 * s, 11 * s, tracking=1.3 * s, upper=True, color=accent),
        h2=TextStyle("serif-bold", 13 * s, 15.4 * s),
        small=TextStyle("serif-italic", 7.6 * s, 9.6 * s),
        link=TextStyle("sans", 7 * s, 9.4 * s, color=grey),
        caption=TextStyle("serif-bold", 9.4 * s, 11.8 * s),
        date=TextStyle("serif", 7.6 * s, 10 * s, tracking=0.9 * s, upper=True),
        masthead_max=64 * s,
        gap=6 * s, tight=2.4 * s, section_gap=13 * s,
        bullet_indent=9 * s, qr_size=60 * s, rule=0.5, accent=accent,
    )


# ------------------------------------------------------------------ text helpers

def wrap(text: str, st: TextStyle, width: float) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in text.split(" "):
        cand = f"{cur} {word}" if cur else word
        if st.width(cand) <= width:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        while st.width(word) > width and len(word) > 1:  # hard-break very long words (urls)
            n = len(word) - 1
            while n > 1 and st.width(word[:n]) > width:
                n -= 1
            lines.append(word[:n])
            word = word[n:]
        cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def fit(text: str, st: TextStyle, width: float) -> str:
    if st.width(text) <= width:
        return text
    while len(text) > 1 and st.width(text + "…") > width:
        text = text[:-1]
    return text.rstrip() + "…"


def lead_text(lead: str) -> str:
    """A run-in lead reads as a label: give it a colon unless it already ends in punctuation."""
    return lead if lead[-1] in ".:;!?)-–—" else lead + ":"


def wrap_runs(words: list[tuple[str, TextStyle]], width: float) -> list[list[tuple[str, TextStyle]]]:
    """Greedy wrap of words that each carry their own style (a bold lead followed by body text)."""
    lines: list[list[tuple[str, TextStyle]]] = []
    cur: list[tuple[str, TextStyle]] = []
    cur_w = 0.0
    for word, st in words:
        ww = st.width(word)
        add = ww + (cur[-1][1].width(" ") if cur else 0)
        if cur and cur_w + add > width:
            lines.append(cur)
            cur, cur_w, add = [], 0.0, ww
        if not cur and ww > width:  # hard-break very long words (urls)
            *full, word = wrap(word, st, width)
            lines += [[(piece, st)] for piece in full]
            add = st.width(word)
        cur.append((word, st))
        cur_w += add
    if cur:
        lines.append(cur)
    return lines


# ------------------------------------------------------------------ boxes

@dataclass
class Box:
    height: float
    ops: list[Op] = field(default_factory=list)
    space_before: float = 0.0
    keep_with_next: bool = False
    droppable: bool = False  # skipped when it would land at the top of a frame (dividers)


def line_box(text: str, st: TextStyle, x: float = 0.0, anchor: str = "l") -> Box:
    return Box(st.leading, [TextOp(x, st.baseline(), text, st.font, st.size, anchor, st.tracking, st.color)])


def para(text: str, st: TextStyle, width: float, x: float = 0.0, align: str = "l",
         lead: str | None = None, lead_st: TextStyle | None = None) -> list[Box]:
    text = st.prep(text)
    lead = lead_st.prep(lead) if lead and lead_st else ""  # may sanitize down to nothing (emoji only)
    if lead:
        boxes = _lead_lines(lead_text(lead), lead_st, text, st, width, x)
    else:
        ax = {"l": x, "m": x + width / 2, "r": x + width}[align]
        boxes = [line_box(line, st, ax, align) for line in wrap(text, st, width)]
    if len(boxes) > 1:  # no lone first line at a column foot, no lone last line at a column head
        boxes[0].keep_with_next = True
        boxes[-2].keep_with_next = True
    return boxes


def _lead_lines(lead: str, lead_st: TextStyle, text: str, st: TextStyle, width: float, x: float) -> list[Box]:
    words = [(w, lead_st) for w in lead.split(" ")] + [(w, st) for w in text.split(" ") if w]
    boxes = []
    for line in wrap_runs(words, width):
        ops: list[Op] = []
        cx, run, run_st = x, [], line[0][1]
        for word, wst in line + [("", None)]:  # sentinel flushes the last run
            if wst is not run_st and run:
                chunk = " ".join(run)
                ops.append(TextOp(cx, st.baseline(), chunk, run_st.font, run_st.size, "l", run_st.tracking,
                                  run_st.color))
                cx += run_st.width(chunk) + run_st.width(" ")
                run = []
            if wst is not None:
                run.append(word)
                run_st = wst
        boxes.append(Box(st.leading, ops))
    return boxes


class Composer:
    """Turns blocks into boxes for one column width."""

    def __init__(self, profile: Profile, theme: Theme, width: float):
        self.p, self.t, self.w = profile, theme, width
        self.slip = profile.layout == "slip"

    # -------------------------------------------------- per block

    def blocks(self, blocks) -> list[Box]:
        out: list[Box] = []
        prev = None
        blocks = list(blocks)
        for i, b in enumerate(blocks):
            nxt = blocks[i + 1] if i + 1 < len(blocks) else None
            if b.type == "divider" and (nxt is None or (nxt.type == "heading" and nxt.level == 1)):
                continue  # a section heading already carries its own rule
            boxes = getattr(self, "_" + b.type)(b)
            if boxes:
                boxes[0].space_before = self._space(prev, b)
                out += boxes
            prev = b
        return out

    def _space(self, prev, cur) -> float:
        t = self.t
        if prev is None:
            return 0.0
        if cur.type == "heading" and cur.level == 1:
            return t.section_gap
        if prev.type == "divider" or cur.type == "divider":
            return 0.0
        if prev.type == "heading":
            return t.gap * (0.9 if prev.level == 1 else 0.35)
        if cur.type == "heading":
            return t.gap * 1.6
        if cur.type == prev.type and cur.type in ("bullet", "row"):
            return t.tight
        return t.gap

    def _heading(self, b) -> list[Box]:
        t, w = self.t, self.w
        if b.level == 2:
            boxes = para(b.text, t.h2, w)
            for bx in boxes:
                bx.keep_with_next = True
            return boxes
        st = t.h1
        if self.slip:  # inverted bar, the classic receipt section header
            pad = 2.2 * self.p.font_scale
            lines = wrap(st.prep(b.text), st, w - 2 * pad)
            h = len(lines) * st.leading + 2 * pad
            ops: list[Op] = [RectOp(0, 0, w, h)]
            for i, line in enumerate(lines):
                ops.append(TextOp(w / 2, pad + i * st.leading + st.baseline(), line, st.font, st.size, "m",
                                  st.tracking, st.color))
            return [Box(h, ops, keep_with_next=True)]
        rule = 1.4
        lines = wrap(st.prep(b.text), st, w)
        ops = [LineOp(0, rule / 2, w, rule / 2, rule, color=t.accent)]
        top = rule + 2.6
        for i, line in enumerate(lines):
            ops.append(TextOp(0, top + i * st.leading + st.baseline(), line, st.font, st.size, "l",
                              st.tracking, st.color))
        return [Box(top + len(lines) * st.leading, ops, keep_with_next=True)]

    def _with_link(self, boxes: list[Box], url: str | None, x: float) -> list[Box]:
        if url:
            boxes[-1].keep_with_next = True  # a source link never leaves its item
            st = self.t.link
            text = fit(st.prep(source_name(url)), st, self.w - x)
            boxes.append(line_box(text, st, x))
        return boxes

    def _text(self, b) -> list[Box]:
        return self._with_link(para(b.text, self.t.body, self.w, lead=b.lead, lead_st=self.t.bold), b.url, 0)

    def _bullet(self, b) -> list[Box]:
        t = self.t
        ind = t.bullet_indent
        boxes = para(b.text, t.body, self.w - ind, x=ind, lead=b.lead, lead_st=t.bold)
        sq = t.body.size * 0.3
        cy = t.body.baseline() - t.body.size * 0.33
        boxes[0].ops.append(RectOp(ind * 0.22, cy - sq / 2, sq, sq))
        return self._with_link(boxes, b.url, ind)

    def _row(self, b) -> list[Box]:
        t, w = self.t, self.w
        right = t.bold.prep(b.right)
        rw = t.bold.width(right)
        gap = 5 * self.p.font_scale
        if rw > w * 0.6:
            right = fit(right, t.bold, w * 0.6)
            rw = t.bold.width(right)
        lines = wrap(t.body.prep(b.left), t.body, w - rw - gap)
        boxes = [line_box(line, t.body) for line in lines]
        first = boxes[0]
        first.ops.append(TextOp(w, t.bold.baseline(), right, t.bold.font, t.bold.size, "r", 0, t.bold.color))
        lead_from = t.body.width(lines[0]) + gap * 0.6
        lead_to = w - rw - gap * 0.6
        if lead_to - lead_from > 6:
            y = t.body.baseline() - 0.6
            dot = 0.9 if self.slip else 0.6
            first.ops.append(LineOp(lead_from, y, lead_to, y, dot, dash=(dot, 2.2 if self.slip else 1.8)))
        return boxes

    def _divider(self, b) -> list[Box]:
        t, w = self.t, self.w
        h = t.gap * 2.4
        if self.slip:
            return [Box(h, [LineOp(0, h / 2, w, h / 2, 0.8, dash=(3, 2))], droppable=True)]
        return [Box(h, [LineOp(0, h / 2, w, h / 2, t.rule)], droppable=True)]

    def _small(self, b) -> list[Box]:
        return para(b.text, self.t.small, self.w)

    def _qr(self, b) -> list[Box]:
        t, w = self.t, self.w
        matrix = qr_matrix(b.url)
        size = min(t.qr_size, w * 0.6)
        link = t.link.prep(short_link(b.url, 60))
        if self.slip:
            boxes = [Box(size, [QROp((w - size) / 2, 0, size, matrix)])]
            if b.caption:
                cap = para(b.caption, t.caption, w, align="m")
                cap[0].space_before = 3
                boxes += cap
            boxes.append(line_box(fit(link, t.link, w), t.link, w / 2, "m"))
            boxes[0].keep_with_next = True
            return boxes
        # newspaper: code on the left, caption and link beside it
        tx = size + 7
        ops: list[Op] = [QROp(0, 0, size, matrix)]
        y = 1.0
        if b.caption:
            for line in wrap(t.caption.prep(b.caption), t.caption, w - tx):
                ops.append(TextOp(tx, y + t.caption.baseline(), line, t.caption.font, t.caption.size, "l", 0,
                                  t.caption.color))
                y += t.caption.leading
        ops.append(TextOp(tx, y + t.link.baseline(), fit(link, t.link, w - tx), t.link.font, t.link.size, "l", 0,
                          t.link.color))
        y += t.link.leading
        ops.append(TextOp(tx, y + t.small.baseline(), "Scan to open on your phone", t.small.font, t.small.size,
                          "l", 0, t.small.color))
        return [Box(max(size, y + t.small.leading), ops)]


def qr_matrix(url: str) -> list[list[bool]]:
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=0)
    qr.add_data(url)
    qr.make(fit=True)
    return qr.get_matrix()


# ------------------------------------------------------------------ nameplates

def format_date(d: dt.date, profile: Profile) -> str:
    if profile.page_size == "Letter":
        return f"{d:%A}, {d:%B} {d.day}, {d.year}"
    return f"{d:%A} {d.day} {d:%B} {d.year}"


def _roman(n: int) -> str:
    vals = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for v, s in vals:
        while n >= v:
            out, n = out + s, n - v
    return out or "I"


def _masthead_size(width: float, cap: float) -> float:
    return min(cap, width / fonts.width(NAMEPLATE, "masthead", 1))


def slip_nameplate(profile: Profile, t: Theme, w: float, d: dt.date, title: str | None) -> list[Box]:
    """Compact nameplate for roll paper."""
    size = _masthead_size(w * 0.98, t.masthead_max)
    a = fonts.ascent("masthead") * size
    title_box = Box(size * 1.12, [TextOp(w / 2, a + size * 0.04, NAMEPLATE, "masthead", size, "m")])
    st = t.date
    y = 2.2
    ops: list[Op] = [LineOp(0, 0.7, w, 0.7, 1.4), LineOp(0, 2.6, w, 2.6, 0.5)]
    y = 3.4
    date_text = fit(st.prep(format_date(d, profile)), st, w)
    ops.append(TextOp(w / 2, y + st.baseline(), date_text, st.font, st.size, "m", st.tracking))
    y += st.leading
    ops.append(LineOp(0, y + 0.4, w, y + 0.4, 0.5))
    boxes = [title_box, Box(y + 1, ops)]
    if title:
        cap = para(title, t.link, w, align="m")
        cap[0].space_before = 1.5
        boxes += cap
    boxes[-1].keep_with_next = True
    return boxes


def newspaper_masthead(profile: Profile, t: Theme, w: float, d: dt.date, title: str | None) -> Box:
    """Full-width broadsheet masthead for page 1."""
    size = _masthead_size(w * 0.86, t.masthead_max)
    a = fonts.ascent("masthead") * size
    ops: list[Op] = [TextOp(w / 2, a, NAMEPLATE, "masthead", size, "m", 0, BLACK)]
    y = a + fonts.descent("masthead") * size * 0.6 + 4
    ops += [LineOp(0, y, w, y, 1.8, color=t.accent), LineOp(0, y + 2.6, w, y + 2.6, 0.6, color=t.accent)]
    y += 3.4
    st = t.date
    center = st.prep(format_date(d, profile))
    left = st.prep(f"Vol. {_roman(max(d.year - 2025, 1))} · No. {d.timetuple().tm_yday}")
    right = st.prep(title or "Personal edition")
    base = y + st.baseline() + 1
    cw, room = st.width(center), w
    ops.append(TextOp(w / 2, base, center, st.font, st.size, "m", st.tracking))
    side = (room - cw) / 2 - 10
    if st.width(left) <= side:
        ops.append(TextOp(0, base, left, st.font, st.size, "l", st.tracking))
    if side > 30:
        ops.append(TextOp(w, base, fit(right, st, side), st.font, st.size, "r", st.tracking))
    y += st.leading + 2
    ops.append(LineOp(0, y, w, y, 0.6, color=t.accent))
    return Box(y + 0.6, ops)


def continuation_header(t: Theme, w: float, d: dt.date, profile: Profile) -> Box:
    size = 15 * profile.font_scale
    a = fonts.ascent("masthead") * size
    st = t.date
    ops: list[Op] = [
        TextOp(0, a, NAMEPLATE, "masthead", size, "l"),
        TextOp(w, a, st.prep(format_date(d, profile)), st.font, st.size, "r", st.tracking),
    ]
    y = a + 5
    ops.append(LineOp(0, y, w, y, 0.6, color=t.accent))
    return Box(y + 1, ops)


# ------------------------------------------------------------------ flow

@dataclass
class Frame:
    page: int
    x: float
    top: float
    bottom: float
    width: float


Placement = tuple[Frame, float, Box]


def flow(boxes: list[Box], get_frame: Callable[[int], Frame | None]) -> tuple[list[Placement], int]:
    """Stack boxes into successive frames, honouring keep_with_next. Returns placements and #boxes placed."""
    out: list[Placement] = []
    fi = 0
    fr = get_frame(0)
    y, first, i = fr.top, True, 0
    while i < len(boxes):
        b = boxes[i]
        if first and b.droppable and fi > 0:
            i += 1
            continue
        need = (0 if first else b.space_before) + b.height
        j = i
        while boxes[j].keep_with_next and j + 1 < len(boxes):
            j += 1
            need += boxes[j].space_before + boxes[j].height
        if y + need > fr.bottom + 0.01 and not first:
            fi += 1
            nxt = get_frame(fi)
            if nxt is None:
                return out, i
            fr, y, first = nxt, nxt.top, True
            continue
        y += 0 if first else b.space_before
        out.append((fr, y, b))
        y += b.height
        first = False
        i += 1
    return out, len(boxes)


def _bottom(placements: list[Placement]) -> float:
    return max((y + b.height for _, y, b in placements), default=0.0)


# ------------------------------------------------------------------ entry point

def layout(brief: Brief, profile: Profile, date: dt.date | None = None) -> Document:
    date = date or brief.date or dt.date.today()
    t = theme_for(profile)
    if profile.continuous:
        return _layout_roll(brief, profile, t, date)
    if profile.fill_page:
        return _layout_filled(brief, profile, date)
    return _layout_pages(brief, profile, t, date)


FILL_MIN, FILL_MAX = 0.85, 1.3


def _layout_filled(brief: Brief, profile: Profile, date: dt.date) -> Document:
    """Pick the largest type scale at which the brief still fits on one page.

    A light brief grows to use the page; one that spills a few lines onto page 2 is
    tightened back onto one. Briefs too long for one page even at FILL_MIN keep normal size.
    """
    def at(scale: float) -> Document:
        p = profile.model_copy(update={"font_scale": profile.font_scale * scale})
        doc = _layout_pages(brief, p, theme_for(p), date)
        doc.profile = profile
        return doc

    if len(at(FILL_MIN).pages) > 1:
        return at(1.0)
    best = at(FILL_MAX)
    if len(best.pages) == 1:
        return best
    lo, hi = FILL_MIN, FILL_MAX
    for _ in range(10):
        mid = (lo + hi) / 2
        if len(at(mid).pages) == 1:
            lo = mid
        else:
            hi = mid
    return at(lo)


def _layout_roll(brief: Brief, profile: Profile, t: Theme, date: dt.date) -> Document:
    top, _, bottom, left = profile.margins_pt
    w = profile.content_width_pt
    boxes = slip_nameplate(profile, t, w, date, brief.title)
    content = Composer(profile, t, w).blocks(brief.blocks)
    if content:
        content[0].space_before = t.gap * 1.6
    boxes += content
    end_w = w * 0.3
    boxes.append(Box(t.gap * 3, [LineOp((w - end_w) / 2, t.gap * 2.2, (w + end_w) / 2, t.gap * 2.2, 1.2)],
                     space_before=t.gap))
    frame = Frame(0, left, top, math.inf, w)
    placements, _ = flow(boxes, lambda i: frame if i == 0 else None)
    page = Page(profile.page_width_pt, math.ceil(_bottom(placements) + bottom))
    for fr, y, b in placements:
        page.ops += [_moved(op, fr.x, y) for op in b.ops]
    return Document(profile, date, [page])


def _layout_pages(brief: Brief, profile: Profile, t: Theme, date: dt.date) -> Document:
    top, right, bottom, left = profile.margins_pt
    pw, ph = profile.page_width_pt, profile.page_height_pt
    w = profile.content_width_pt
    ncol = profile.columns
    gutter = 14 * profile.font_scale if ncol > 1 else 0
    col_w = (w - gutter * (ncol - 1)) / ncol
    mast = newspaper_masthead(profile, t, w, date, brief.title)
    cont = continuation_header(t, w, date, profile)
    footer_h = t.small.leading + 4
    body_bottom = ph - bottom - footer_h - 4

    def col_top(page: int) -> float:
        return top + (mast.height if page == 0 else cont.height) + t.section_gap * 0.8

    def frames(limit_pages: int | None = None, first_page: int = 0, height: float | None = None):
        def get(i: int) -> Frame | None:
            page, col = first_page + i // ncol, i % ncol
            if limit_pages is not None and page >= first_page + limit_pages:
                return None
            ft = col_top(page)
            fb = body_bottom if height is None else min(body_bottom, ft + height)
            return Frame(page, left + col * (col_w + gutter), ft, fb, col_w)
        return get

    boxes = Composer(profile, t, col_w).blocks(brief.blocks)
    placements, _ = flow(boxes, frames())
    last_page = placements[-1][0].page if placements else 0

    # balance the columns on the last page
    if ncol > 1 and placements:
        start = next(i for i, (fr, _, _) in enumerate(placements) if fr.page == last_page)
        rest = boxes[start:]
        lo = max(b.height for b in rest)
        hi = body_bottom - col_top(last_page)
        for _ in range(24):
            mid = (lo + hi) / 2
            _, n = flow(rest, frames(1, last_page, mid))
            if n == len(rest):
                hi = mid
            else:
                lo = mid
        balanced, n = flow(rest, frames(1, last_page, hi + 0.5))
        if n == len(rest):
            placements = placements[:start] + balanced

    pages = [Page(pw, ph) for _ in range(last_page + 1)]
    for fr, y, b in placements:
        pages[fr.page].ops += [_moved(op, fr.x, y) for op in b.ops]
    for i, page in enumerate(pages):
        head = mast if i == 0 else cont
        page.ops = [_moved(op, left, top) for op in head.ops] + page.ops
        on_page = [p for p in placements if p[0].page == i]
        if ncol > 1 and on_page:
            y0, y1 = col_top(i) - 2, _bottom(on_page) + 2
            for c in range(1, ncol):
                x = left + c * (col_w + gutter) - gutter / 2
                page.ops.append(LineOp(x, y0, x, y1, t.rule))
        fy = ph - bottom - footer_h
        st = t.small
        page.ops.append(LineOp(left, fy, pw - right, fy, t.rule))
        base = fy + 3 + st.baseline()
        page.ops.append(TextOp(left, base, f"{NAMEPLATE} · {format_date(date, profile)}", st.font, st.size, "l"))
        if len(pages) > 1:
            page.ops.append(TextOp(pw - right, base, f"Page {i + 1} of {len(pages)}", st.font, st.size, "r"))
    return Document(profile, date, pages)
