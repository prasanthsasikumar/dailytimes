"""Rendering: one layout engine (`layout`), two renderers (`raster`, `pdf`)."""

from __future__ import annotations

import datetime as dt
import io
from dataclasses import dataclass, field

from PIL import Image

from ..blocks import Brief
from .layout import layout
from .pdf import render_pdf
from .profiles import Profile, list_profiles, load_profile
from .raster import render_raster

__all__ = ["Rendered", "render", "layout", "render_pdf", "render_raster", "Profile", "load_profile",
           "list_profiles", "brief_from_relay"]


@dataclass
class Rendered:
    """Output of a render, in whichever formats were asked for."""

    profile: Profile
    date: dt.date
    pdf: bytes | None = None
    images: list[Image.Image] = field(default_factory=list)

    def png_pages(self) -> list[bytes]:
        out = []
        for img in self.images:
            buf = io.BytesIO()
            img.save(buf, format="PNG", dpi=(self.profile.dpi, self.profile.dpi))
            out.append(buf.getvalue())
        return out


def render(brief: Brief, profile: Profile, formats: tuple[str, ...] | None = None,
           date: dt.date | None = None) -> Rendered:
    formats = formats or (profile.preferred_output,)
    doc = layout(brief, profile, date)
    out = Rendered(profile, doc.date)
    if "pdf" in formats:
        out.pdf = render_pdf(doc)
    if "raster" in formats:
        out.images = render_raster(doc)
    return out


def brief_from_relay(data: dict) -> Brief:
    """Rebuild a validated Brief from the relay's device API payload."""
    return Brief.model_validate({"blocks": data["blocks"], "title": data.get("title"), "date": data.get("date")})
