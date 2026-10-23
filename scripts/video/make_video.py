"""30-second promo video (1080x1080, 30 fps) with the original track from music.py.

    .venv/bin/python scripts/video/make_video.py        # -> out/video/dailytimes-30s.mp4
Every scene starts on a bar line (128 BPM, 1 bar = 1.875 s); text pops on the beat.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import music  # noqa: E402
from dailytimes.blocks import Brief  # noqa: E402
from dailytimes.render import load_profile, render  # noqa: E402
from dailytimes.render.fonts import path as font_path  # noqa: E402

W = H = 1080
FPS = 30
BEAT, BAR = music.BEAT, music.BAR
DURATION = music.LENGTH
OUT = ROOT / "out" / "video"
PAPER = (241, 236, 226)
INK = (20, 19, 18)
ACCENT = (139, 26, 26)
DARK = (17, 18, 22)


def F(key: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(font_path(key)), size)


MAST = {s: F("masthead", s) for s in (64, 90, 150)}
SANS_B = {s: F("sans-bold", s) for s in (26, 30, 36, 44, 56, 64, 76)}
SERIF = {s: F("serif", s) for s in (30, 36, 42)}
SERIF_B = {s: F("serif-bold", s) for s in (48, 60, 72)}


# ------------------------------------------------------------------ helpers

def ease_out(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def ease_back(x: float) -> float:  # overshoot pop
    x = max(0.0, min(1.0, x))
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def beat_pulse(t: float, strength: float = 0.035) -> float:
    """Scale factor that kicks on every beat after the drop."""
    if t < music.DROP_BAR * BAR:
        return 1.0
    ph = (t % BEAT) / BEAT
    return 1 + strength * math.exp(-ph * 7)


def text_center(d: ImageDraw.ImageDraw, xy, text: str, font, fill=INK, anchor="mm") -> None:
    d.text(xy, text, font=font, fill=fill, anchor=anchor)


def paste_scaled(bg: Image.Image, img: Image.Image, center: tuple[float, float], scale: float,
                 angle: float = 0.0, alpha: float = 1.0) -> None:
    if scale <= 0.01 or alpha <= 0.01:
        return
    im = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    if angle:
        im = im.rotate(angle, resample=Image.BICUBIC, expand=True)
    if alpha < 1:
        a = im.getchannel("A").point(lambda v: int(v * alpha))
        im.putalpha(a)
    bg.alpha_composite(im, (int(center[0] - im.width / 2), int(center[1] - im.height / 2)))


def with_shadow(img: Image.Image, blur: int = 16, alpha: int = 110) -> Image.Image:
    pad = blur * 3
    out = Image.new("RGBA", (img.width + pad * 2, img.height + pad * 2), (0, 0, 0, 0))
    sh = Image.new("RGBA", out.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rectangle([pad, pad + 10, pad + img.width, pad + img.height + 10], fill=(30, 20, 10, alpha))
    out.alpha_composite(sh.filter(ImageFilter.GaussianBlur(blur)))
    out.paste(img.convert("RGB"), (pad, pad))
    return out


def text_image(text: str, font, fill=INK) -> Image.Image:
    l, t, r, b = font.getbbox(text)
    im = Image.new("RGBA", (r - l + 8, b - t + 8), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((4 - l, 4 - t), text, font=font, fill=fill)
    return im


# ------------------------------------------------------------------ assets

def load_assets() -> dict:
    def brief(name):
        return Brief.model_validate(json.loads((ROOT / "examples" / name).read_text()))

    import datetime as dt
    day = dt.date(2026, 10, 23)
    a4 = render(brief("full_page_brief.json"), load_profile("a4").model_copy(update={"dpi": 120}), ("raster",),
                day).images[0].convert("RGB")
    letter = render(brief("full_page_brief.json"), load_profile("letter").model_copy(update={"dpi": 120}),
                    ("raster",), day).images[0].convert("RGB")
    r80 = render(brief("sample_brief.json"), load_profile("thermal-80mm"), ("raster",), day).images[0].convert("RGB")
    r58 = render(brief("sample_brief.json"), load_profile("thermal-58mm"), ("raster",), day).images[0].convert("RGB")
    return {
        "a4": with_shadow(a4), "letter": with_shadow(letter), "r80": r80, "r58": with_shadow(r58),
        "r80s": with_shadow(r80), "a4_raw": a4,
        "mast_big": text_image("The Daily Times", MAST[150]),
        "mast_mid": text_image("The Daily Times", MAST[90]),
    }


# ------------------------------------------------------------------ scenes (start times in bars)

S = {"screen": 0, "paper?": 2, "drop": 3, "printer": 5, "ai": 7, "auto": 9, "any": 11, "private": 13, "end": 15}


def at(name: str) -> float:
    return S[name] * BAR


NOTIFS = ["37 unread emails", "Breaking news", "12 new posts", "Your meeting starts in 10 min",
          "Stock alert: AAPL", "3 new messages", "Trending now", "Reminder: reply to Dana",
          "48 notifications", "Someone mentioned you"]


def scene_screen(img, d, t):
    img.paste(DARK, (0, 0, W, H))
    local = t - at("screen")
    n = int(local / (BEAT / 2)) + 1  # a new notification every half beat
    for k in range(min(n, len(NOTIFS))):
        appear = k * BEAT / 2
        p = ease_back((local - appear) / 0.25)
        y = 170 + k * 78 - (max(0, n - 7) * 78) * ease_out((local - appear) / 0.3)
        x = 540 + (1 - p) * 300 * (1 if k % 2 else -1)
        if y < 60:
            continue
        d.rounded_rectangle([x - 330, y - 30, x + 330, y + 30], radius=22, fill=(44, 46, 54))
        d.ellipse([x - 312, y - 14, x - 284, y + 14], fill=(90, 160, 255) if k % 3 else (255, 92, 92))
        d.text((x - 266, y), NOTIFS[k], font=SANS_B[30], fill=(230, 232, 240), anchor="lm")
    # caption
    band = 1 - ease_out((0.6 - local) / 0.6) if local < 0.6 else 1
    d.rectangle([0, H - 230, W, H], fill=(10, 10, 12))
    text_center(d, (540, H - 140), "Your morning starts", SANS_B[56], fill=(255, 255, 255))
    text_center(d, (540, H - 72), "on a screen.", SANS_B[56], fill=(255, 92, 92))
    _ = band


def scene_paper_question(img, d, t):
    local = t - at("paper?")
    # flash from dark to paper
    k = ease_out(local / 0.35)
    bgc = tuple(int(DARK[i] + (PAPER[i] - DARK[i]) * k) for i in range(3))
    img.paste(bgc, (0, 0, W, H))
    p = ease_back(local / 0.4)
    paste_scaled(img, text_image("What if it started", SERIF_B[72]), (540, 470), 0.6 + 0.4 * p, alpha=min(1, local * 4))
    paste_scaled(img, text_image("on paper?", SERIF_B[72], fill=ACCENT), (540, 570), 0.6 + 0.4 * ease_back((local - BEAT) / 0.4),
                 alpha=min(1, max(0, (local - BEAT) * 4)))


def scene_drop(img, d, t, A):
    local = t - at("drop")
    img.paste(PAPER, (0, 0, W, H))
    # masthead slams in from big to normal
    s = 1.0 + 1.6 * (1 - ease_out(local / 0.28))
    paste_scaled(img, A["mast_big"], (540, 400), 0.92 * s * beat_pulse(t, 0.02))
    if local > 0.25:
        w = ease_out((local - 0.25) / 0.4)
        d.line([(540 - 420 * w, 500), (540 + 420 * w, 500)], fill=INK, width=6)
        d.line([(540 - 420 * w, 512), (540 + 420 * w, 512)], fill=INK, width=2)
    if local > BEAT:
        a = min(1, (local - BEAT) * 3)
        paste_scaled(img, text_image("A one-page newspaper about your day,", SERIF[42]), (540, 600), 1, alpha=a)
        paste_scaled(img, text_image("written by your AI.", SERIF[42]), (540, 660), 1, alpha=a)
    if local > 2 * BEAT:
        p = ease_back((local - 2 * BEAT) / 0.35)
        paste_scaled(img, text_image("Printed on any printer you own.", SANS_B[44], fill=ACCENT), (540, 780), p)


def scene_printer(img, d, t, A):
    local = t - at("printer")
    img.paste(PAPER, (0, 0, W, H))
    rec = A["r80"]
    scale = 560 / rec.width
    rec_s = rec.resize((560, int(rec.height * scale)), Image.LANCZOS)
    slot_y = 900
    # paper rises out of the slot over ~1.6 bars
    shown = int(rec_s.height * ease_out(local / (BAR * 1.6)) * 0.62)
    if shown > 0:
        piece = rec_s.crop((0, 0, rec_s.width, shown))
        img.paste(piece, (260, slot_y - shown))
        d.rectangle([260, slot_y - shown, 820, slot_y], outline=(200, 195, 185), width=2)
    # printer body
    d.rounded_rectangle([200, slot_y - 6, 880, slot_y + 160], radius=26, fill=(38, 38, 42))
    d.rectangle([250, slot_y - 6, 830, slot_y + 8], fill=(15, 15, 17))
    blink = (int(local / (BEAT / 2)) % 2 == 0)
    d.ellipse([820, slot_y + 60, 840, slot_y + 80], fill=(90, 220, 120) if blink else (40, 90, 50))
    d.rectangle([0, 0, W, 270], fill=PAPER)  # the paper slides under the headline
    d.line([(0, 270), (W, 270)], fill=(215, 208, 195), width=2)
    for k in range(3):
        r = ((local * 1.4 + k * 0.33) % 1.0)
        d.ellipse([110 - 70 * r, 170 - 70 * r, 110 + 70 * r, 170 + 70 * r], outline=ACCENT, width=3)
    d.ellipse([96, 156, 124, 184], fill=ACCENT)
    cap = ease_back(local / 0.35)
    paste_scaled(img, text_image("Prints when you", SANS_B[64]), (560, 120), cap)
    paste_scaled(img, text_image("walk up to it.", SANS_B[64], fill=ACCENT), (560, 200), ease_back((local - BEAT) / 0.35))


def scene_ai(img, d, t):
    local = t - at("ai")
    img.paste(DARK, (0, 0, W, H))
    names = ["Claude", "ChatGPT", "Codex", "Claude Code", "n8n", "any agent"]
    cx, cy = 540, 590
    for k, name in enumerate(names):
        start = k * BEAT / 2
        if local < start:
            continue
        p = ease_back((local - start) / 0.45)
        ang = math.radians(-90 + 30 + k * 60)
        radius = 640 - 370 * p  # fly in from off-screen, settle on the ring
        x, y = cx + math.cos(ang) * radius * 1.25, cy + math.sin(ang) * radius
        d.line([(x, y), (cx, cy)], fill=(80, 84, 98), width=2)
        chip = text_image(name, SANS_B[36], fill=(255, 255, 255))
        cw, ch = chip.width + 40, chip.height + 26
        d.rounded_rectangle([x - cw / 2, y - ch / 2, x + cw / 2, y + ch / 2], radius=ch / 2, fill=(55, 58, 70))
        img.alpha_composite(chip, (int(x - chip.width / 2), int(y - chip.height / 2)))
    # the tool, center
    pop = ease_back((local - 3 * BEAT / 2) / 0.4)
    box = text_image("print_brief", F("narrow-bold", 64), fill=(20, 19, 18))
    if pop > 0:
        bw, bh = box.width + 70, box.height + 46
        sc = pop * beat_pulse(t, 0.03)
        d.rounded_rectangle([540 - bw / 2 * sc, 590 - bh / 2 * sc, 540 + bw / 2 * sc, 590 + bh / 2 * sc], radius=18,
                            fill=PAPER)
        paste_scaled(img, box, (540, 590), sc)
    text_center(d, (540, 120), "Your AI writes it.", SANS_B[64], fill=(255, 255, 255))
    if local > BEAT * 2:
        a = min(1, (local - 2 * BEAT) * 3)
        paste_scaled(img, text_image("One private MCP endpoint. Any agent.", SERIF[36], fill=(200, 205, 215)),
                     (540, 950), 1, alpha=a)
        paste_scaled(img, text_image("It reads your calendar + inbox. We only get the page.", SERIF[30],
                                     fill=(150, 156, 170)), (540, 1005), 1, alpha=a)


def scene_auto(img, d, t, A):
    local = t - at("auto")
    img.paste(PAPER, (0, 0, W, H))
    text_center(d, (540, 110), "No AI? It still works.", SANS_B[64])
    sentence = "“I'm a UX researcher in Wellington. I follow AI, climate tech and rugby.”"
    n = int(len(sentence) * ease_out(local / (BAR * 0.9)))
    lines, cur = [], ""
    for word in sentence[:n].split(" "):
        test = (cur + " " + word).strip()
        if SERIF[36].getlength(test) > 860:
            lines.append(cur)
            cur = word
        else:
            cur = test
    lines.append(cur)
    d.rounded_rectangle([90, 180, 990, 320], radius=14, outline=INK, width=3, fill=(255, 255, 255))
    for i, line in enumerate(lines[:2]):
        d.text((120, 210 + i * 46), line, font=SERIF[36], fill=INK)
    if local > BAR * 0.9:
        p = ease_back((local - BAR * 0.9) / 0.45)
        paste_scaled(img, A["a4"], (540, 680), 0.41 * p * beat_pulse(t, 0.012), angle=-2 * (1 - p))
        paste_scaled(img, text_image("Headlines, papers, prices, weather: quoted as published.", SANS_B[26],
                                     fill=ACCENT), (540, 1045), 1, alpha=min(1, (local - BAR) * 3))


def scene_any(img, d, t, A):
    local = t - at("any")
    img.paste(PAPER, (0, 0, W, H))
    text_center(d, (540, 100), "Any printer.", SANS_B[76])
    # (image, label, centre x, target width): receipts tall on the left, pages on the right
    items = [(A["r58"], "58 mm", 95, 150), (A["r80s"], "80 mm", 315, 210), (A["a4"], "A4", 605, 290),
             (A["letter"], "Letter", 925, 270)]
    for k, (im, label, x, tw) in enumerate(items):
        start = k * BEAT
        if local < start:
            continue
        p = ease_back((local - start) / 0.35)
        sc = tw / im.width * 1.15  # shadow padding
        h = im.height * sc
        y = 960 - h / 2 + (1 - p) * 600
        paste_scaled(img, im, (x, y), sc * beat_pulse(t, 0.012))
        d.text((x, 1030), label, font=SANS_B[36], fill=INK, anchor="mm")


def scene_private(img, d, t):
    local = t - at("private")
    img.paste(DARK, (0, 0, W, H))
    text_center(d, (540, 190), "Private by design.", SANS_B[76], fill=(255, 255, 255))
    lines = ["We never see your calendar or mail.", "Editions deleted after 30 days.",
             "No ads, no trackers. Delete anytime.", "Open source. MIT."]
    for k, line in enumerate(lines):
        start = BEAT * (0.5 + k * 0.75)
        if local < start:
            continue
        p = ease_out((local - start) / 0.3)
        y = 380 + k * 120
        x = 140 - (1 - p) * 60
        d.line([(x, y + 2), (x + 14, y + 18), (x + 40, y - 18)], fill=(110, 220, 140), width=7, joint="curve")
        d.text((x + 70, y), line, font=SERIF[42], fill=(235, 236, 240), anchor="lm")


def scene_end(img, d, t, A):
    local = t - at("end")
    img.paste(PAPER, (0, 0, W, H))
    p = ease_back(local / 0.4)
    paste_scaled(img, A["mast_big"], (540, 330), 0.92 * p * beat_pulse(t, 0.02))
    d.line([(120, 430), (960, 430)], fill=INK, width=6)
    d.line([(120, 442), (960, 442)], fill=INK, width=2)
    if local > 0.3:
        a = min(1, (local - 0.3) * 4)
        paste_scaled(img, text_image("Your morning, in print.", SERIF[42]), (540, 520), 1, alpha=a)
    if local > BEAT:
        pop = ease_back((local - BEAT) / 0.35)
        paste_scaled(img, text_image("times.flowsxr.com", SANS_B[64], fill=ACCENT), (540, 680), pop)
    if local > 2 * BEAT:
        a = min(1, (local - 2 * BEAT) * 4)
        paste_scaled(img, text_image("github.com/prasanthsasikumar/dailytimes", SANS_B[36]), (540, 790), 1, alpha=a)
        paste_scaled(img, text_image("Free · open source · works with any AI and any printer", SERIF[30],
                                     fill=(90, 84, 76)), (540, 860), 1, alpha=a)


def frame(t: float, A: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), PAPER + (255,))
    d = ImageDraw.Draw(img)
    if t < at("paper?"):
        scene_screen(img, d, t)
    elif t < at("drop"):
        scene_paper_question(img, d, t)
    elif t < at("printer"):
        scene_drop(img, d, t, A)
    elif t < at("ai"):
        scene_printer(img, d, t, A)
    elif t < at("auto"):
        scene_ai(img, d, t)
    elif t < at("any"):
        scene_auto(img, d, t, A)
    elif t < at("private"):
        scene_any(img, d, t, A)
    elif t < at("end"):
        scene_private(img, d, t)
    else:
        scene_end(img, d, t, A)
    # white flash on the drop
    drop = at("drop")
    if drop <= t < drop + 0.18:
        flash = Image.new("RGBA", (W, H), (255, 255, 255, int(255 * (1 - (t - drop) / 0.18))))
        img.alpha_composite(flash)
    return img.convert("RGB")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    wav = music.render(OUT / "music.wav")
    A = load_assets()
    total = DURATION + 1.2  # let the last hit ring
    mp4 = OUT / "dailytimes-30s.mp4"
    ff = subprocess.Popen([
        "ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
        "-i", "-", "-i", str(wav), "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(mp4)], stdin=subprocess.PIPE)
    n = int(total * FPS)
    for i in range(n):
        ff.stdin.write(frame(i / FPS, A).tobytes())
    ff.stdin.close()
    ff.wait()
    print(mp4)


if __name__ == "__main__":
    main()
