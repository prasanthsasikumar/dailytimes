"""CUPS / IPP adapter: PDF to any installed queue (`lp`) or straight to a driverless IPP printer.

Options
  printer   CUPS queue name (see `lpstat -e`); uses `lp -d <printer>`
  uri       ipp://host/ipp/print or ipps://...; sends an IPP Print-Job directly (IPP Everywhere)
  copies    number of copies (default 1)
  lp_options  extra `-o` options for lp, e.g. ["fit-to-page", "sides=one-sided"]
Neither printer nor uri: the CUPS default destination.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

from .base import Adapter, AdapterError, DiscoveredPrinter, PrintJob, PrintResult, register
from .discovery import suggest_profile
from . import ipp

MDNS_TYPES = {
    "_ipp._tcp.local.": ("cups", "ipp"),
    "_ipps._tcp.local.": ("cups", "ipps"),
    "_pdl-datastream._tcp.local.": ("escpos", "raw"),
}


@register
class CupsAdapter(Adapter):
    name = "cups"
    formats = ("pdf",)

    def media(self, job: PrintJob) -> str:
        p = job.profile
        if p.page_size == "receipt" or p.type == "thermal":
            width_mm = p.paper_width_mm or p.printable_width_px / p.dpi * 25.4
            height_mm = job.rendered.images[0].height / p.dpi * 25.4 if job.rendered.images else 300
            return f"Custom.{width_mm:.0f}x{max(height_mm, 50):.0f}mm"
        return p.page_size

    def lp_command(self, job: PrintJob) -> list[str]:
        cmd = ["lp", "-t", job.title, "-o", f"media={self.media(job)}"]
        if printer := self.options.get("printer"):
            cmd[1:1] = ["-d", printer]
        if (copies := int(self.options.get("copies", 1))) > 1:
            cmd += ["-n", str(copies)]
        for opt in self.options.get("lp_options", []) or []:
            cmd += ["-o", opt]
        return cmd + ["-"]

    def send(self, job: PrintJob) -> PrintResult:
        if job.rendered.pdf is None:
            raise AdapterError("cups adapter needs a PDF render")
        if uri := self.options.get("uri"):
            if self.dry_run:
                req = ipp.encode_request(ipp.PRINT_JOB, uri, job_name=job.title, document_format="application/pdf")
                return PrintResult(True, self.name, f"would POST IPP Print-Job to {ipp.http_url(uri)}", True,
                                   data={"ipp_request": req, "bytes": len(req) + len(job.rendered.pdf)})
            status, text = ipp.print_job(uri, job.rendered.pdf, job.title)
            if status >= 0x0100:
                hint = ""
                if status == 0x040A:
                    hint = (" (this printer doesn't take PDF directly; add it to CUPS with "
                            f"`lpadmin -p dailytimes -E -v {uri} -m everywhere` and use printer=dailytimes)")
                return PrintResult(False, self.name, f"IPP {text}{hint}")
            return PrintResult(True, self.name, f"IPP {text}")

        cmd = self.lp_command(job)
        if self.dry_run:
            return PrintResult(True, self.name, "would run: " + " ".join(cmd), True, data={"command": cmd})
        if not shutil.which("lp"):
            raise AdapterError("`lp` not found; install CUPS or use the uri option")
        proc = subprocess.run(cmd, input=job.rendered.pdf, capture_output=True, timeout=60)
        if proc.returncode != 0:
            return PrintResult(False, self.name, proc.stderr.decode(errors="replace").strip() or "lp failed")
        return PrintResult(True, self.name, proc.stdout.decode(errors="replace").strip())

    # ------------------------------------------------------------ discovery

    @classmethod
    def discover(cls, timeout: float = 2.0) -> list[DiscoveredPrinter]:
        return cls.discover_lpstat() + discover_mdns(timeout)

    @staticmethod
    def parse_lpstat(output: str) -> list[DiscoveredPrinter]:
        """Parse `lpstat -v` lines: 'device for NAME: URI'."""
        out = []
        for line in output.splitlines():
            if line.startswith("device for ") and ": " in line:
                name, uri = line[len("device for "):].split(": ", 1)
                out.append(DiscoveredPrinter(name, "cups", {"printer": name}, "lpstat", uri.strip(),
                                             suggest_profile(name, uri)))
        return out

    @classmethod
    def discover_lpstat(cls) -> list[DiscoveredPrinter]:
        if not shutil.which("lpstat"):
            return []
        try:
            proc = subprocess.run(["lpstat", "-v"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return []
        return cls.parse_lpstat(proc.stdout)


def discover_mdns(timeout: float = 2.0) -> list[DiscoveredPrinter]:
    """Browse Bonjour/mDNS for IPP Everywhere (and raw port-9100) printers."""
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        return []
    import time

    found: dict[str, DiscoveredPrinter] = {}
    zc = Zeroconf()

    class Listener:
        def add_service(self, zc_, type_, name):
            info = zc_.get_service_info(type_, name, timeout=int(timeout * 1000))
            if not info or not info.parsed_addresses():
                return
            props = {k.decode(errors="ignore"): (v or b"").decode(errors="ignore") for k, v in info.properties.items()}
            host = info.parsed_addresses()[0]
            adapter, kind = MDNS_TYPES[type_]
            model = props.get("ty") or props.get("product", "").strip("()") or name
            label = name.split("." + type_)[0]
            if adapter == "cups":
                rp = props.get("rp", "ipp/print")
                options: dict[str, Any] = {"uri": f"{kind}://{host}:{info.port}/{rp}"}
            else:
                options = {"connection": "network", "host": host, "port": info.port}
            found[f"{type_}{name}"] = DiscoveredPrinter(label, adapter, options, "mdns", model,
                                                        suggest_profile(label, model, props.get("pdl", "")))

        def update_service(self, *a):
            pass

        def remove_service(self, *a):
            pass

    try:
        browsers = [ServiceBrowser(zc, t, Listener()) for t in MDNS_TYPES]
        time.sleep(timeout)
        for b in browsers:
            b.cancel()
    finally:
        zc.close()
    return list(found.values())
