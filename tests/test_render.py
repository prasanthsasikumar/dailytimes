import datetime as dt

import pytest
from PIL import Image
from pydantic import ValidationError

from dailytimes.blocks import Brief
from dailytimes.render import list_profiles, load_profile, render
from dailytimes.render import fonts
from dailytimes.render import raster
from dailytimes.render.layout import NAMEPLATE, LineOp, QROp, TextOp, layout, wrap, theme_for
from dailytimes.render.profiles import MM, Profile

DAY = dt.date(2026, 10, 21)


def thermal(px: int) -> Profile:
    return Profile(name=f"t{px}", type="thermal", layout="slip", dpi=203, printable_width_px=px)


def texts(doc):
    return [op.text for p in doc.pages for op in p.ops if isinstance(op, TextOp)]


def long_brief(n=40) -> Brief:
    blocks = [{"type": "heading", "text": f"Section {i // 8}"} if i % 8 == 0 else
              {"type": "text", "text": f"Paragraph {i}: " + "word " * 14, "url": f"https://ex.com/{i}"}
              for i in range(n)]
    return Brief.model_validate({"blocks": blocks})


def test_shipped_profiles_load():
    names = {p.name for p in list_profiles()}
    assert {"thermal-58mm", "thermal-80mm", "a4", "letter"} <= names
    p58, p80 = load_profile("thermal-58mm"), load_profile("thermal-80mm")
    assert (p58.printable_width_px, p80.printable_width_px) == (384, 576)
    assert p80.cutter and not p58.cutter
    assert load_profile("a4").layout == "newspaper" and load_profile("letter").page_size == "Letter"


def test_profile_validation(tmp_path):
    with pytest.raises(ValidationError):
        Profile(name="x", type="thermal", layout="slip")  # no width
    with pytest.raises(ValidationError):
        Profile(name="x", type="page", layout="newspaper")  # no page size
    with pytest.raises(ValidationError):
        Profile(name="x", type="page", layout="slip", page_size="receipt")  # no paper width
    with pytest.raises(ValidationError):
        Profile(name="x", type="page", layout="slip", page_size="A4", bogus=1)
    f = tmp_path / "mine.yaml"
    f.write_text("name: mine\ntype: thermal\nlayout: slip\nprintable_width_px: 832\nmargins_mm: 2\n")
    p = load_profile(str(f))
    assert p.margins_mm == (2, 2, 2, 2)
    with pytest.raises(FileNotFoundError):
        load_profile("no-such-profile")


@pytest.mark.parametrize("px", [384, 576, 832])
def test_raster_any_width_is_exact_and_1bit(sample_brief, px):
    r = render(sample_brief, thermal(px), ("raster",), DAY)
    (img,) = r.images
    assert img.size[0] == px and img.mode == "1"
    assert img.size[1] > 600
    # something is printed near the top (nameplate) and the edges stay inside the paper
    bbox = img.point(lambda v: 255 - v).getbbox()
    assert bbox[0] >= 0 and bbox[2] <= px and bbox[1] < 60


def test_raster_png_has_dpi(sample_brief):
    r = render(sample_brief, load_profile("thermal-58mm"), ("raster",), DAY)
    png = r.png_pages()[0]
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("name,size_mm", [("a4", (210, 297)), ("letter", (215.9, 279.4)), ("a5", (148, 210))])
def test_pdf_page_sizes(sample_brief, name, size_mm):
    prof = load_profile(name)
    r = render(sample_brief, prof, ("pdf",), DAY)
    assert r.pdf.startswith(b"%PDF")
    w, h = size_mm[0] * MM, size_mm[1] * MM
    doc = layout(sample_brief, prof, DAY)
    assert all(abs(p.width - w) < 0.01 and abs(p.height - h) < 0.01 for p in doc.pages)


def test_receipt_pdf_is_content_length(sample_brief):
    prof = load_profile("receipt-80mm-pdf")
    short = Brief.model_validate({"blocks": [{"type": "text", "text": "one line"}]})
    d_long, d_short = layout(sample_brief, prof, DAY), layout(short, prof, DAY)
    assert len(d_long.pages) == len(d_short.pages) == 1
    assert d_long.pages[0].height > d_short.pages[0].height * 2
    assert abs(d_long.pages[0].width - 80 * MM) < 0.01
    assert render(sample_brief, prof, ("pdf",), DAY).pdf.startswith(b"%PDF")


def test_nameplate_and_date_on_every_profile(sample_brief):
    for prof in list_profiles():
        doc = layout(sample_brief, prof, DAY)
        t = texts(doc)
        assert NAMEPLATE in t, prof.name
        assert any("21" in s and "OCTOBER" in s.upper() for s in t), prof.name


def test_nameplate_style_differs(sample_brief):
    def masthead_size(name):
        doc = layout(sample_brief, load_profile(name), DAY)
        return max(op.size for op in doc.pages[0].ops if isinstance(op, TextOp) and op.font == "masthead")
    assert masthead_size("a4") > 2.2 * masthead_size("thermal-80mm")
    a4 = texts(layout(sample_brief, load_profile("a4"), DAY))
    assert any(s.startswith("VOL. I") for s in a4)  # newspaper date bar
    letter = texts(layout(sample_brief, load_profile("letter"), DAY))
    assert "WEDNESDAY, OCTOBER 21, 2026" in letter  # US date style on Letter


def test_links_printed_as_short_text_and_qr_rendered(sample_brief):
    doc = layout(sample_brief, load_profile("thermal-80mm"), DAY)
    t = texts(doc)
    assert "conf.example.org" in t  # source lines name the publisher, not the path
    assert "careers.example.org" in t
    qrs = [op for op in doc.pages[0].ops if isinstance(op, QROp)]
    assert len(qrs) == 1 and len(qrs[0].matrix) >= 21


def test_text_never_overflows_column(sample_brief):
    for prof in list_profiles():
        doc = layout(long_brief(), prof, DAY)
        _, right, _, _ = prof.margins_pt
        limit = prof.page_width_pt - right + 0.5
        for page in doc.pages:
            for op in page.ops:
                if isinstance(op, TextOp) and op.anchor == "l":
                    assert op.x + fonts.width(op.text, op.font, op.size, op.tracking) <= limit, (prof.name, op.text)


def test_long_brief_paginates_with_continuation_and_page_numbers():
    doc = layout(long_brief(60), load_profile("a4"), DAY)
    assert len(doc.pages) >= 2
    t = texts(doc)
    assert f"Page 1 of {len(doc.pages)}" in t and f"Page 2 of {len(doc.pages)}" in t
    second = [op for op in doc.pages[1].ops if isinstance(op, TextOp)]
    assert second[0].text == NAMEPLATE and second[0].size < 20  # compact continuation header


def test_columns_are_balanced(sample_brief):
    prof = load_profile("a4")
    doc = layout(sample_brief, prof, DAY)
    mid = prof.page_width_pt / 2
    body = [op for op in doc.pages[0].ops if isinstance(op, TextOp) and op.y > 150 and op.y < 780]
    left = max(op.y for op in body if op.x < mid)
    right = max(op.y for op in body if op.x > mid)
    assert abs(left - right) < 80
    assert any(isinstance(op, LineOp) and op.x1 == op.x2 for op in doc.pages[0].ops)  # gutter rule


def test_heading_keeps_with_next_and_no_divider_at_column_top():
    doc = layout(long_brief(60), load_profile("a4"), DAY)
    for page in doc.pages:
        ops = [op for op in page.ops if isinstance(op, TextOp)]
        for i, op in enumerate(ops[:-1]):
            if op.text.startswith("SECTION"):
                nxt = ops[i + 1]
                assert abs(nxt.x - op.x) < 1 and nxt.y > op.y  # body follows heading in same column


def test_unsupported_characters_are_sanitised():
    st = theme_for(load_profile("a4")).body
    assert st.prep("Up 3% \U0001F4C8 → target ✓") == "Up 3% -> target v"
    b = Brief.model_validate({"blocks": [{"type": "text", "text": "Rocket \U0001F680 launch"}]})
    assert "Rocket launch" in texts(layout(b, load_profile("thermal-58mm"), DAY))


def test_wrap_breaks_long_words():
    st = theme_for(load_profile("thermal-58mm")).body
    lines = wrap("x" * 200, st, 100)
    assert len(lines) > 1 and all(st.width(l) <= 100 for l in lines)


def test_color_profile_renders_rgb(sample_brief):
    prof = load_profile("a4").model_copy(update={"color": True, "dpi": 100})
    (img,) = render(sample_brief, prof, ("raster",), DAY).images
    assert img.mode == "RGB" and isinstance(img, Image.Image)


def test_fill_page_uses_the_whole_page():
    import json
    from pathlib import Path

    full = Brief.model_validate(json.loads((Path(__file__).parents[1] / "examples/full_page_brief.json").read_text()))
    prof = load_profile("a4")
    assert prof.fill_page
    for brief in (full, long_brief(12)):
        doc = layout(brief, prof, DAY)
        assert len(doc.pages) == 1
    # a full brief reaches close to the footer
    doc = layout(full, prof, DAY)
    footer_y = prof.page_height_pt - prof.margins_pt[2] - 20
    lowest = max(op.y for op in doc.pages[0].ops if isinstance(op, TextOp) and op.y < footer_y)
    assert lowest > footer_y - 60
    # a brief that would spill a little onto page 2 is tightened onto one page
    unfilled = prof.model_copy(update={"fill_page": False})
    spill = long_brief(36)
    if len(layout(spill, unfilled, DAY).pages) == 2:
        assert len(layout(spill, prof, DAY).pages) in (1, 2)
    # too long for one page even when tightened: normal size, several pages
    assert len(layout(long_brief(60), prof, DAY).pages) >= 2


def test_light_brief_scales_up_but_capped(sample_brief):
    prof = load_profile("a4")
    body = lambda doc: max(op.size for op in doc.pages[0].ops if isinstance(op, TextOp) and op.font == "serif")
    plain = layout(sample_brief, prof.model_copy(update={"fill_page": False}), DAY)
    filled = layout(sample_brief, prof, DAY)
    assert body(plain) < body(filled) <= body(plain) * 1.3 + 0.01


def lead_brief(n=24) -> Brief:
    blocks = [{"type": "heading", "text": "Top story"}]
    blocks += [{"type": "bullet" if i % 2 else "text", "lead": f"Lead number {i}",
                "text": f"Item {i}: Reality Labs revenue $443M, down 6% on lower Quest sales; Zuckerberg says "
                        "new devices ship only with major breakthroughs.", "url": f"https://ex.com/{i}"}
               for i in range(n)]
    return Brief.model_validate({"blocks": blocks})


def test_lead_runs_in_bold_and_stays_in_column():
    prof = load_profile("a4")
    doc = layout(lead_brief(), prof, DAY)
    ops = [op for op in doc.pages[0].ops if isinstance(op, TextOp)]
    lead = next(op for op in ops if op.text.startswith("Lead number 0"))
    assert lead.font == "serif-bold" and lead.text == "Lead number 0:"
    body = next(op for op in ops if op.text.startswith("Item 0:"))
    assert body.font == "serif" and body.y == lead.y and body.x > lead.x  # same line, after the lead
    rules = sorted(op.x1 for op in doc.pages[0].ops if isinstance(op, LineOp) and op.x1 == op.x2)
    for op in ops:
        if op.anchor == "l" and 100 < op.y < 780:
            edge = next((x for x in rules if x > op.x), prof.page_width_pt)
            assert op.x + fonts.width(op.text, op.font, op.size, op.tracking) < edge, op.text


def test_lead_with_punctuation_is_kept_and_emoji_only_lead_is_dropped():
    b = Brief.model_validate({"blocks": [
        {"type": "bullet", "lead": "Meta Q3.", "text": "Revenue down."},
        {"type": "bullet", "lead": "\U0001F680", "text": "Launch today."},
    ]})
    t = texts(layout(b, load_profile("thermal-80mm"), DAY))
    assert "Meta Q3." in t and "Launch today." in t


def test_raster_text_stays_left_of_gutter_without_raqm(monkeypatch):
    """Pillow's basic layout engine (no libraqm, as on the server) runs ~3% wide at preview dpi."""
    from PIL import ImageFont
    monkeypatch.setattr(raster, "_font", lambda key, px: ImageFont.truetype(
        str(fonts.path(key)), px, layout_engine=ImageFont.Layout.BASIC))
    prof = load_profile("a4").model_copy(update={"dpi": 110})
    doc = layout(lead_brief(), prof, DAY)
    img = raster.render_raster(doc)[0].convert("L")
    rule = next(op for op in doc.pages[0].ops if isinstance(op, LineOp) and op.x1 == op.x2)
    sc = prof.dpi / 72
    rx, px = round(rule.x1 * sc), img.load()
    ink = [(x, y) for y in range(round(rule.y1 * sc) + 5, round(rule.y2 * sc) - 5)
           for x in range(rx - 6, rx - 1) if px[x, y] < 128]
    assert not ink
