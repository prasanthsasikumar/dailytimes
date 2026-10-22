"""Render a brief JSON with every shipped profile into a folder (PNG + PDF) for visual review."""
import datetime as dt
import json
import sys
from pathlib import Path

from dailytimes.blocks import Brief
from dailytimes.render import list_profiles, render

src = Path(sys.argv[1] if len(sys.argv) > 1 else "examples/sample_brief.json")
out = Path(sys.argv[2] if len(sys.argv) > 2 else "out/preview")
out.mkdir(parents=True, exist_ok=True)
brief = Brief.model_validate(json.loads(src.read_text()))
for p in list_profiles():
    r = render(brief, p, ("raster", "pdf"), dt.date(2026, 10, 21))
    for i, png in enumerate(r.png_pages()):
        (out / f"{p.name}-{i + 1}.png").write_bytes(png)
    (out / f"{p.name}.pdf").write_bytes(r.pdf)
    print(f"{p.name:18} {len(r.images)} page(s) {r.images[0].size}")
