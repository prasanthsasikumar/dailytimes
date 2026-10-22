"""The calibration slip/page printed by `dailytimes test-print`."""

from __future__ import annotations

from .blocks import Brief
from .render.profiles import Profile


def calibration_brief(profile: Profile, adapter: str) -> Brief:
    if profile.type == "thermal":
        width = f"{profile.printable_width_px} px / {profile.printable_width_px / profile.dpi * 25.4:.0f} mm"
    elif profile.page_size == "receipt":
        width = f"{profile.paper_width_mm:g} mm roll"
    else:
        width = profile.page_size
    blocks = [
        {"type": "heading", "text": "Printer test"},
        {"type": "row", "left": "Profile", "right": profile.name},
        {"type": "row", "left": "Adapter", "right": adapter},
        {"type": "row", "left": "Paper", "right": width},
        {"type": "row", "left": "Resolution", "right": f"{profile.dpi} dpi"},
        {"type": "row", "left": "Cutter", "right": "yes" if profile.cutter else "no"},
        {"type": "divider"},
        {"type": "heading", "text": "Check", "level": 2},
        {"type": "bullet", "text": "The black bar and the rules reach both edges of the printable area, nothing is cut off."},
        {"type": "bullet", "text": "Small print below is readable: AaBbCcDdEe 0123456789."},
        {"type": "bullet", "text": "Your phone camera opens the QR code."},
        {"type": "text", "text": "ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghijklmnopqrstuvwxyz 0123456789 $%&@ ÄÖÜ é ñ ß"},
        {"type": "qr", "url": "https://example.com/daily-times/calibration", "caption": "Scan test"},
        {"type": "small", "text": "If the right edge is clipped, pick a narrower profile or lower printable_width_px. "
                                  "If text is faint, raise print density on the printer."},
    ]
    return Brief.model_validate({"blocks": blocks, "title": "Calibration"})
