"""ESC/POS adapter (python-escpos): USB, serial or network (port 9100), raster mode.

Options
  connection  usb | serial | network
  usb:        vendor_id, product_id (ints or hex strings like "0x0416")
  serial:     device (/dev/ttyUSB0), baudrate (default 9600)
  network:    host, port (default 9100)
  impl        bitImageRaster (default, GS v 0) | graphics | bitImageColumn
  dump_path   dry-run only: write the encoded ESC/POS bytes here
"""

from __future__ import annotations

import glob
from pathlib import Path

from .base import Adapter, AdapterError, DiscoveredPrinter, PrintJob, PrintResult, register
from .discovery import suggest_profile

# Vendors that ship ESC/POS receipt printers over USB.
POS_VENDORS = {0x04B8: "Epson", 0x0519: "Star", 0x0416: "Winbond (generic POS-58/80)", 0x0483: "STM (generic POS)",
               0x0FE6: "ICS (generic POS)", 0x28E9: "GD32 (generic POS)", 0x1FC9: "NXP (generic POS)",
               0x154F: "SNBC", 0x0DD4: "Custom", 0x1504: "Bixolon", 0x2D37: "Zjiang"}


def _int(v) -> int:
    return int(v, 0) if isinstance(v, str) else int(v)


@register
class EscposAdapter(Adapter):
    name = "escpos"
    formats = ("raster",)

    def _printer(self):
        from escpos import printer as P

        conn = self.options.get("connection", "network")
        if conn == "usb":
            self._need("vendor_id", "product_id")
            return P.Usb(_int(self.options["vendor_id"]), _int(self.options["product_id"]))
        if conn == "serial":
            self._need("device")
            return P.Serial(devfile=self.options["device"], baudrate=int(self.options.get("baudrate", 9600)))
        if conn == "network":
            self._need("host")
            return P.Network(self.options["host"], port=int(self.options.get("port", 9100)), timeout=10)
        raise AdapterError(f"unknown escpos connection {conn!r}")

    def encode(self, printer, job: PrintJob) -> None:
        impl = self.options.get("impl", "bitImageRaster")
        for img in job.rendered.images:
            printer.image(img, impl=impl, fragment_height=512)
        if job.profile.cutter:
            printer.cut()
        else:
            printer.ln(4)  # feed past the tear bar

    def send(self, job: PrintJob) -> PrintResult:
        if not job.rendered.images:
            raise AdapterError("escpos adapter needs a raster render")
        width = job.rendered.images[0].width
        if job.profile.printable_width_px and width > job.profile.printable_width_px:
            raise AdapterError(f"image is {width}px but printer prints {job.profile.printable_width_px}px")
        if self.dry_run:
            from escpos.printer import Dummy

            d = Dummy()
            self.encode(d, job)
            data = d.output
            arts = []
            if dump := self.options.get("dump_path"):
                Path(dump).write_bytes(data)
                arts.append(str(dump))
            return PrintResult(True, self.name, f"encoded {len(data)} bytes of ESC/POS ({width}px raster)", True,
                               arts, {"bytes": len(data), "escpos": data})
        p = self._printer()
        try:
            self.encode(p, job)
        finally:
            try:
                p.close()
            except Exception:
                pass
        return PrintResult(True, self.name, f"sent {width}px raster via {self.options.get('connection', 'network')}")

    @classmethod
    def discover(cls, timeout: float = 2.0) -> list[DiscoveredPrinter]:
        found = []
        for dev in sorted(set(glob.glob("/dev/usb/lp*") + glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
                              + glob.glob("/dev/tty.usbserial*") + glob.glob("/dev/rfcomm*"))):
            found.append(DiscoveredPrinter(Path(dev).name, "escpos", {"connection": "serial", "device": dev},
                                           "serial", "", suggest_profile("receipt")))
        try:
            import usb.core  # pyusb, pulled in by python-escpos

            for d in usb.core.find(find_all=True) or []:
                if d.idVendor in POS_VENDORS:
                    name = POS_VENDORS[d.idVendor]
                    try:
                        name = f"{d.manufacturer or ''} {d.product or ''}".strip() or name
                    except Exception:
                        pass
                    found.append(DiscoveredPrinter(
                        name, "escpos",
                        {"connection": "usb", "vendor_id": hex(d.idVendor), "product_id": hex(d.idProduct)},
                        "usb", POS_VENDORS[d.idVendor], suggest_profile(name, "receipt")))
        except Exception:  # no libusb backend, no permissions: discovery is best-effort
            pass
        return found
