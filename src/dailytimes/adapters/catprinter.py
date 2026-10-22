"""Bluetooth LE "cat printers" (GB01/GB02/GB03/GT01/MX05-MX10 and clones).

Status: interface + packet encoder implemented; BLE transport is a stub.
See docs/cat-printers.md for the protocol notes. Dry-run encodes the full
command stream so the encoder is tested without hardware.

Options
  address   BLE address (or macOS UUID) of the printer
  energy    print darkness 0..0xFFFF (default 0x3000)
"""

from __future__ import annotations

from PIL import Image

from .base import Adapter, AdapterError, DiscoveredPrinter, PrintJob, PrintResult, register

WIDTH = 384  # dots per line on all common models
TX_CHARACTERISTIC = "0000ae01-0000-1000-8000-00805f9b34fb"

CMD_DRAW_BITMAP = 0xA2
CMD_FEED = 0xA1
CMD_LATTICE = 0xA6
CMD_ENERGY = 0xAF
CMD_QUALITY = 0xA4
CMD_DRAW_MODE = 0xBE
CMD_SPEED = 0xBD

LATTICE_START = bytes([0xAA, 0x55, 0x17, 0x38, 0x44, 0x5F, 0x5F, 0x5F, 0x44, 0x38, 0x2C])
LATTICE_END = bytes([0xAA, 0x55, 0x17, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x17])


def _crc8_table() -> list[int]:
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
        table.append(c)
    return table


_CRC = _crc8_table()


def crc8(data: bytes) -> int:
    c = 0
    for b in data:
        c = _CRC[(c ^ b) & 0xFF]
    return c


def packet(cmd: int, data: bytes) -> bytes:
    """0x51 0x78 CMD 0x00 LEN 0x00 DATA CRC8(DATA) 0xFF"""
    return bytes([0x51, 0x78, cmd, 0x00, len(data), 0x00]) + data + bytes([crc8(data), 0xFF])


def encode_line(row: list[bool]) -> bytes:
    out = bytearray(WIDTH // 8)
    for i, black in enumerate(row[:WIDTH]):
        if black:
            out[i // 8] |= 1 << (i % 8)  # LSB-first
    return bytes(out)


def encode_image(img: Image.Image, energy: int = 0x3000, feed: int = 80) -> bytes:
    if img.width != WIDTH:
        raise AdapterError(f"cat printers print {WIDTH}px lines; render with a 384px profile (thermal-58mm)")
    img = img.convert("1")
    px = img.load()
    out = bytearray()
    out += packet(CMD_QUALITY, bytes([0x33]))
    out += packet(CMD_LATTICE, LATTICE_START)
    out += packet(CMD_ENERGY, energy.to_bytes(2, "little"))
    out += packet(CMD_DRAW_MODE, bytes([0x00]))  # 0 = image mode
    out += packet(CMD_SPEED, bytes([0x23]))
    for y in range(img.height):
        out += packet(CMD_DRAW_BITMAP, encode_line([px[x, y] == 0 for x in range(WIDTH)]))
    out += packet(CMD_FEED, feed.to_bytes(2, "little"))
    out += packet(CMD_LATTICE, LATTICE_END)
    return bytes(out)


@register
class CatPrinterAdapter(Adapter):
    name = "catprinter"
    formats = ("raster",)

    def send(self, job: PrintJob) -> PrintResult:
        if not job.rendered.images:
            raise AdapterError("catprinter adapter needs a raster render")
        energy = int(self.options.get("energy", 0x3000))
        data = b"".join(encode_image(img, energy) for img in job.rendered.images)
        if self.dry_run:
            return PrintResult(True, self.name, f"encoded {len(data)} bytes for BLE characteristic ae01", True,
                               data={"bytes": len(data), "stream": data})
        self._need("address")
        raise AdapterError(
            "BLE transport for cat printers is not implemented yet (see docs/cat-printers.md). "
            "Use dry_run to inspect the stream, or print via a 58mm ESC/POS printer.")

    @classmethod
    def discover(cls, timeout: float = 2.0) -> list[DiscoveredPrinter]:
        return []  # BLE scan lands with the transport (bleak)
