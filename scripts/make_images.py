"""Render the README / social images from the fictional sample editions.

    .venv/bin/python scripts/make_images.py      # -> docs/images/*.png, src/dailytimes/relay/static/*.png
"""

import datetime as dt
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from dailytimes.blocks import Brief
from dailytimes.render import load_profile, render
from dailytimes.render.fonts import path as font_path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"
STATIC = ROOT / "src" / "dailytimes" / "relay" / "static"
DAY = dt.date(2026, 10, 23)
PAPER_BG = (236, 231, 220)
INK = (22, 21, 20)


def brief(name: str) -> Brief:
    return Brief.model_validate(json.loads((ROOT / "examples" / name).read_text()))


def page(name: str, profile: str, dpi: int | None = None) -> Image.Image:
    prof = load_profile(profile)
    if dpi:
        prof = prof.model_copy(update={"dpi": dpi})
    img = render(brief(name), prof, ("raster",), DAY).images[0]
    return img.convert("RGB")


def shadowed(img: Image.Image, radius: int = 18, offset: tuple[int, int] = (0, 10), alpha: int = 90) -> Image.Image:
    """Paper with a soft drop shadow, on a transparent canvas."""
    pad = radius * 3
    canvas = Image.new("RGBA", (img.width + pad * 2, img.height + pad * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle(
        [pad + offset[0], pad + offset[1], pad + offset[0] + img.width, pad + offset[1] + img.height],
        fill=(40, 30, 20, alpha))
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(radius)))
    canvas.paste(img, (pad, pad))
    return canvas


def fit_height(img: Image.Image, h: int) -> Image.Image:
    return img.resize((round(img.width * h / img.height), h), Image.LANCZOS)


def hero() -> Image.Image:
    """A4 front page with a receipt slip lying across its corner."""
    a4 = fit_height(page("full_page_brief.json", "a4", 170), 1500)
    slip = page("sample_brief.json", "thermal-80mm")
    slip = fit_height(slip, 1150)
    W, H = 2000, 1700
    bg = Image.new("RGBA", (W, H), PAPER_BG + (255,))
    a4s = shadowed(a4)
    bg.alpha_composite(a4s, (120, (H - a4s.height) // 2))
    tilted = shadowed(slip, radius=14, alpha=110).rotate(-6, resample=Image.BICUBIC, expand=True)
    bg.alpha_composite(tilted, (W - tilted.width - 90, H - tilted.height - 40))
    return bg.convert("RGB")


def social(hero_img: Image.Image) -> Image.Image:
    """1280x640 GitHub/OG card: nameplate + tagline on the left, the paper on the right."""
    W, H = 1280, 640
    card = Image.new("RGB", (W, H), PAPER_BG)
    d = ImageDraw.Draw(card)
    mast = ImageFont.truetype(str(font_path("masthead")), 86)
    serif = ImageFont.truetype(str(font_path("serif")), 31)
    sans = ImageFont.truetype(str(font_path("sans-bold")), 19)
    d.text((70, 120), "The Daily", font=mast, fill=INK)
    d.text((70, 215), "Times", font=mast, fill=INK)
    d.line([(72, 330), (600, 330)], fill=INK, width=4)
    d.line([(72, 338), (600, 338)], fill=INK, width=1)
    y = 362
    for line in ("Your AI writes a one-page", "newspaper about your day.", "It prints on any printer",
                 "when you walk up to it."):
        d.text((72, y), line, font=serif, fill=INK)
        y += 42
    d.text((72, 560), "OPEN SOURCE  ·  MCP  ·  ANY PRINTER  ·  PRIVATE BY DESIGN", font=sans, fill=(90, 84, 76))
    crop = hero_img.crop((80, 60, 2000, 1700))
    crop = fit_height(crop, 600)
    card.paste(crop, (W - crop.width + 40, 20))
    return card


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STATIC.mkdir(parents=True, exist_ok=True)
    h = hero()
    h.resize((1400, round(1400 * h.height / h.width)), Image.LANCZOS).save(OUT / "hero.png", optimize=True)
    soc = social(h)
    soc.save(OUT / "social-preview.png", optimize=True)
    soc.save(STATIC / "og.png", optimize=True)
    icon = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(icon).text((32, 34), "T", font=ImageFont.truetype(str(font_path("masthead")), 58), fill=INK,
                              anchor="mm")
    icon.save(STATIC / "favicon.png")

    # gallery: the same edition on four papers
    a4 = page("full_page_brief.json", "a4", 110)
    a4.save(OUT / "edition-a4.png", optimize=True)
    page("full_page_brief.json", "letter", 110).save(OUT / "edition-letter.png", optimize=True)
    page("sample_brief.json", "thermal-80mm").save(OUT / "edition-80mm.png", optimize=True)
    page("sample_brief.json", "thermal-58mm").save(OUT / "edition-58mm.png", optimize=True)
    fit_height(a4, 900).save(STATIC / "sample-a4.png", optimize=True)

    # one strip for the README: 58 mm, 80 mm and A4 at the same physical scale feel
    strips = [fit_height(page("sample_brief.json", "thermal-58mm"), 1100),
              fit_height(page("sample_brief.json", "thermal-80mm"), 1100),
              fit_height(page("full_page_brief.json", "a4", 110), 1100)]
    W = sum(s.width for s in strips) + 80 * (len(strips) + 1)
    row = Image.new("RGBA", (W, 1260), PAPER_BG + (255,))
    x = 40
    for s in strips:
        sh = shadowed(s, radius=12)
        row.alpha_composite(sh, (x, (1260 - sh.height) // 2))
        x += s.width + 80
    row.convert("RGB").resize((1400, round(1400 * 1260 / W)), Image.LANCZOS).save(OUT / "papers.png", optimize=True)
    for f in sorted(OUT.glob("*.png")):
        print(f.relative_to(ROOT), Image.open(f).size, f"{f.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
