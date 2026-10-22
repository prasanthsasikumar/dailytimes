# Bluetooth "cat printers"

Cheap BLE thermal printers sold as GB01, GB02, GB03, GT01, MX05–MX10, YT01 and many
unbranded clones (often with a cat face). They are not ESC/POS. They take a proprietary
packet stream over a BLE GATT characteristic.

## What's implemented

`dailytimes.adapters.catprinter` has:

- the packet framing and CRC-8,
- a full image encoder (`encode_image`) producing the command stream for a 384-px raster,
- an adapter whose **dry-run** returns that stream; the real BLE transport raises
  `NotImplementedError`-style `AdapterError` for now.

Use the `thermal-58mm` profile (384 px at 203 dpi).

## Protocol notes

- Service `0000ae30-…`, write characteristic **`0000ae01-0000-1000-8000-00805f9b34fb`**
  (write without response), notify characteristic `ae02` (status, "paper out", flow control).
- Packet: `51 78 CMD 00 LEN 00 DATA… CRC8(DATA) FF`, where CRC-8 uses polynomial 0x07 with init 0.
- Commands used:
  | cmd  | meaning | data |
  |------|---------|------|
  | `A4` | quality | `33` |
  | `A6` | lattice start / end | 11 fixed bytes (see source) |
  | `AF` | energy (darkness) | uint16 LE, ~0x2000–0x6000 |
  | `BE` | drawing mode | `00` image, `01` text |
  | `BD` | speed | 1 byte |
  | `A2` | draw one 384-px line | 48 bytes, **LSB-first**, 1 = black |
  | `A1` | feed paper | uint16 LE lines |
- Some newer models (MXW01 etc.) use a different command set (`22 21 …` framing). They'd
  need their own encoder, but the same adapter interface.
- Throughput: send in chunks of ≤ MTU-3 bytes; on the GB series, pause when `ae02`
  notifies flow-control `51 78 AE 01 01 00 10 70 FF` and resume on `…00…`.

## Finishing the transport

Install `bleak` (`pip install dailytimes[ble]`), connect to `address`, and write
`encode_image(img)` to `ae01` in chunks while honouring flow control. Prior art:
rbaron/catprinter, NaitLee/Cat-Printer, amber-sixel/gb01print.
