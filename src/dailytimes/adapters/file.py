"""File adapter: write PDF and/or PNG into a folder (previews, shared folders, hot folders).

Options
  directory   target folder (default ./out)
  formats     list of pdf/png to write (default: the profile's natural format)
  name        filename stem; {date} and {profile} are substituted (default daily-times-{date}-{profile})
"""

from __future__ import annotations

from pathlib import Path

from .base import Adapter, PrintJob, PrintResult, register

_FMT = {"png": "raster", "pdf": "pdf", "raster": "raster"}


@register
class FileAdapter(Adapter):
    name = "file"
    formats = ("pdf", "raster")

    def formats_for(self, profile):
        wanted = self.options.get("formats")
        if wanted:
            return tuple(dict.fromkeys(_FMT[f] for f in wanted))
        return (profile.preferred_output,)

    def paths(self, job: PrintJob) -> list[tuple[Path, bytes]]:
        folder = Path(self.options.get("directory", "out"))
        stem = self.options.get("name", "daily-times-{date}-{profile}").format(
            date=job.date.isoformat(), profile=job.profile.name)
        out = []
        if job.rendered.pdf is not None:
            out.append((folder / f"{stem}.pdf", job.rendered.pdf))
        pngs = job.rendered.png_pages()
        for i, png in enumerate(pngs):
            suffix = f"-p{i + 1}" if len(pngs) > 1 else ""
            out.append((folder / f"{stem}{suffix}.png", png))
        return out

    def send(self, job: PrintJob) -> PrintResult:
        files = self.paths(job)
        names = [str(p) for p, _ in files]
        if self.dry_run:
            return PrintResult(True, self.name, "would write " + ", ".join(names), True, names)
        for path, data in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return PrintResult(True, self.name, "wrote " + ", ".join(names), False, names)
