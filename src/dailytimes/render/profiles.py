"""Printer profiles: paper, resolution and layout style, loaded from YAML.

A profile describes the *paper*, not the transport. The same profile can be
used with any adapter that accepts its output (e.g. `a4` via CUPS, email or file).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MM = 72 / 25.4  # points per millimetre

PACKAGE_PROFILES = Path(__file__).resolve().parent.parent / "profiles"
PAGE_SIZES_MM = {"A4": (210, 297), "Letter": (215.9, 279.4), "A5": (148, 210)}


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    type: Literal["thermal", "page"]
    layout: Literal["slip", "newspaper"]
    dpi: int = Field(default=203, ge=72, le=1200)
    # thermal: physical roll width and the printable dot width (384/576/832...)
    paper_width_mm: float | None = Field(default=None, gt=20, le=300)
    printable_width_px: int | None = Field(default=None, ge=128, le=4096)
    # page: A4 | Letter | A5 | receipt (continuous page as wide as paper_width_mm)
    page_size: Literal["A4", "Letter", "A5", "receipt"] | None = None
    margins_mm: tuple[float, float, float, float] = (3, 2, 6, 2)  # top right bottom left
    cutter: bool = False
    color: bool = False
    columns: int = Field(default=1, ge=1, le=4)
    font_scale: float = Field(default=1.0, gt=0.5, lt=2.0)
    # page profiles: scale type (within 0.85x-1.3x) so a brief fills exactly one page
    fill_page: bool = False

    @field_validator("margins_mm", mode="before")
    @classmethod
    def _margins(cls, v):
        if isinstance(v, (int, float)):
            return (v, v, v, v)
        if isinstance(v, (list, tuple)) and len(v) == 2:
            return (v[0], v[1], v[0], v[1])
        return v

    @model_validator(mode="after")
    def _check(self) -> "Profile":
        if self.type == "thermal":
            if not self.printable_width_px:
                raise ValueError("thermal profiles need printable_width_px")
        else:
            if not self.page_size:
                raise ValueError("page profiles need page_size")
            if self.page_size == "receipt" and not self.paper_width_mm:
                raise ValueError("receipt page_size needs paper_width_mm")
        return self

    # ---------------------------------------------------------- geometry (points)

    @property
    def continuous(self) -> bool:
        """True for roll media: one page as tall as the content."""
        return self.type == "thermal" or self.page_size == "receipt"

    @property
    def page_width_pt(self) -> float:
        if self.type == "thermal":
            return self.printable_width_px * 72 / self.dpi
        if self.page_size == "receipt":
            return self.paper_width_mm * MM
        return PAGE_SIZES_MM[self.page_size][0] * MM

    @property
    def page_height_pt(self) -> float | None:
        if self.continuous:
            return None
        return PAGE_SIZES_MM[self.page_size][1] * MM

    @property
    def margins_pt(self) -> tuple[float, float, float, float]:
        return tuple(m * MM for m in self.margins_mm)  # type: ignore[return-value]

    @property
    def content_width_pt(self) -> float:
        _, r, _, l = self.margins_pt
        return self.page_width_pt - l - r

    @property
    def preferred_output(self) -> Literal["raster", "pdf"]:
        return "raster" if self.type == "thermal" else "pdf"


def profile_dirs() -> list[Path]:
    dirs = []
    if env := os.environ.get("DAILYTIMES_PROFILES"):
        dirs += [Path(p) for p in env.split(os.pathsep)]
    dirs.append(Path.home() / ".config" / "dailytimes" / "profiles")
    dirs.append(PACKAGE_PROFILES)
    return dirs


def load_profile(name_or_path: str | os.PathLike) -> Profile:
    p = Path(name_or_path)
    if p.suffix in (".yaml", ".yml") and p.exists():
        return Profile.model_validate(yaml.safe_load(p.read_text()))
    for d in profile_dirs():
        for ext in (".yaml", ".yml"):
            cand = d / f"{name_or_path}{ext}"
            if cand.exists():
                return Profile.model_validate(yaml.safe_load(cand.read_text()))
    raise FileNotFoundError(f"no printer profile named {name_or_path!r} (looked in {', '.join(map(str, profile_dirs()))})")


def list_profiles() -> list[Profile]:
    seen: dict[str, Profile] = {}
    for d in reversed(profile_dirs()):  # user dirs override package ones
        if d.is_dir():
            for f in sorted(d.glob("*.y*ml")):
                prof = Profile.model_validate(yaml.safe_load(f.read_text()))
                seen[prof.name] = prof
    return sorted(seen.values(), key=lambda p: p.name)
