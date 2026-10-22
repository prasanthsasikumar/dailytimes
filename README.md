# The Daily Times

Your AI assistant (Claude, ChatGPT, Codex, ...) writes you a short daily brief. It prints on
**whatever printer you own**, a 58 mm receipt printer, an office laser, a cat printer, an
email-to-print inkjet, and only **when someone walks up to it**.

```
 assistant ──MCP / webhook / email──▶  relay  ◀──poll── device agent ──▶ printer
 (daily task, print_brief tool)       (queue,           (Pi / Mac /       (thermal, CUPS,
                                       dedup)            Linux box)        IPP, email, file)
                                                            ▲
                                                 presence: PIR / mmWave /
                                                 button / phone / HTTP
```

- **Relay** (FastAPI + SQLite): per-device accounts and tokens, one MCP tool `print_brief`,
  an HTTPS webhook and inbound email. Holds one brief per device per day (the newest wins) and
  a 60-day history so the same story never prints twice. It stores layout blocks, never pixels,
  and doesn't know or care what printer you have.
- **Renderer** (shared library): one layout engine, two renderers: raster at any dot width
  (384/576/832 px...) and PDF (A4, Letter, A5, receipt-length). Every brief gets a
  *The Daily Times* nameplate with the date: compact on receipts, a broadsheet masthead on paper.
- **Adapters**: CUPS/IPP, ESC/POS (USB, serial, network 9100), Bluetooth cat printers (encoder
  plus stub), email-to-print, file. A new printer type is one adapter plus one profile YAML.
- **Agent**: polls the relay, waits for presence, prints once per day, reports back.

| 58 mm thermal | A4 / Letter |
|---|---|
| compact nameplate, inverted section bars, dotted leaders | blackletter masthead, date bar, two balanced columns |

Run `./scripts/demo.sh` to see all four in `./out`. For a full A4 front page, render
`examples/full_page_brief.json`: `dailytimes render examples/full_page_brief.json --profile a4`.
All sample content is fictional placeholder data.

## Quick start (local, five minutes)

```bash
uv venv && uv pip install -e ".[dev]"          # or: python -m venv .venv && pip install -e ".[dev]"
.venv/bin/pytest -q                            # 118 tests
./scripts/demo.sh                              # relay + agent + MCP + webhook + presence -> ./out
```

The demo starts a relay, registers a device, sends a draft brief by webhook and the final one
over MCP (the draft gets superseded), starts an agent with four file outputs, simulates someone
walking up, and leaves `out/brief-thermal-58mm.png`, `brief-thermal-80mm.png`, `brief-a4.pdf`
and `brief-letter.pdf`.

## Set it up for real

### 1. Run the relay

```bash
export DAILYTIMES_ADMIN_TOKEN=$(openssl rand -hex 24)
export DAILYTIMES_PUBLIC_URL=https://times.example.com
docker compose up -d
```

Put it behind a TLS reverse proxy (Caddy: `times.example.com { reverse_proxy 127.0.0.1:8787 }`).
Without Docker: `pip install "dailytimes[relay]" && dailytimes relay`.

| env var | meaning |
|---|---|
| `DAILYTIMES_DB` | SQLite path (default `./dailytimes.db`, `/data/dailytimes.db` in Docker) |
| `DAILYTIMES_ADMIN_TOKEN` | required to register devices; if unset, registration is open (dev only) |
| `DAILYTIMES_PUBLIC_URL` | used to build the URLs returned at registration |
| `DAILYTIMES_INBOUND_DOMAIN` / `_SECRET` | inbound email address domain / shared secret for `/v1/inbound/email` |

### 2. Register a printer

```bash
dailytimes register --relay https://times.example.com --name "Kitchen" \
  --timezone Pacific/Auckland --admin-token $DAILYTIMES_ADMIN_TOKEN
```

You get a device token plus three ways to send it a brief: `mcp_url`, `webhook_url` and
`email_address`.

### 3. Connect your assistant

Follow [SETUP_PROMPT.md](SETUP_PROMPT.md): add the `mcp_url` as a connector and paste the
prompt. It creates a daily task that uses what the assistant knows about you (field of work,
job search, subreddits, stocks, calendar, email) and calls `print_brief`.

### 4. Set up the printer machine

On a Raspberry Pi, Mac or Linux box next to the printer:

```bash
pip install "dailytimes[escpos,mdns]"      # add [gpio] on a Pi with a sensor
dailytimes printers                         # what's connected, and which profile to use
dailytimes test-print --adapter escpos --profile thermal-80mm --opt connection=usb \
  --opt vendor_id=0x04b8 --opt product_id=0x0e28
cp deploy/agent.example.yaml ~/.config/dailytimes/agent.yaml   # edit relay, outputs, presence
DAILYTIMES_TOKEN=dt_... dailytimes agent --config ~/.config/dailytimes/agent.yaml
```

Then install it as a service: `deploy/systemd/dailytimes-agent.service` (Linux/Pi) or
`deploy/launchd/org.dailytimes.agent.plist` (macOS).

## Printers

| profile | for | output |
|---|---|---|
| `thermal-58mm` | 58 mm receipt printers, cat printers (384 dots) | 1-bit raster, slip layout |
| `thermal-80mm` | 80 mm receipt printers (576 dots), cutter | 1-bit raster, slip layout |
| `a4` / `letter` | laser/inkjet via CUPS, IPP, email | PDF, two-column newspaper |
| `a5` | A5 or A4 folded | PDF, one column |
| `receipt-80mm-pdf` | thermal printers with a CUPS driver | receipt-length PDF |

`dailytimes profiles` lists them. Write your own YAML in `~/.config/dailytimes/profiles/`:

```yaml
name: thermal-104mm
type: thermal            # thermal (raster) | page (PDF)
layout: slip             # slip | newspaper
dpi: 203
paper_width_mm: 112
printable_width_px: 832
margins_mm: [4, 2, 6, 2] # top right bottom left
cutter: true
color: false
columns: 1
font_scale: 1.0
fill_page: false         # page profiles: scale type 0.85x-1.3x so the brief fills exactly one page
```

| adapter | options | takes |
|---|---|---|
| `cups` | `printer` (queue) or `uri` (`ipp://host/ipp/print`, driverless), `copies`, `lp_options` | PDF |
| `escpos` | `connection: usb\|serial\|network`, `vendor_id`/`product_id`, `device`/`baudrate`, `host`/`port` | raster |
| `catprinter` | `address`, `energy` (BLE transport is a stub, see [docs/cat-printers.md](docs/cat-printers.md)) | raster |
| `email` | `to`, `smtp_host`, `smtp_port`, `username`, `password_env`, `from_addr` | PDF |
| `file` | `directory`, `formats: [pdf, png]`, `name` | either |

Every adapter has a dry-run mode (`--dry-run`, or `dry_run: true` per output) that does all the
encoding (lp command, IPP request, ESC/POS bytes, MIME message) without touching hardware.

**Adding a printer type**: subclass `dailytimes.adapters.Adapter`, set `name` and `formats`,
implement `send(job)`, and either decorate it with `@register` or expose it in the
`dailytimes.adapters` entry-point group of your own package. Add a profile YAML. Nothing else changes.

## Presence

| backend | hardware | options |
|---|---|---|
| `manual` | none: phone shortcut, smart button, `dailytimes presence`; optional GPIO push button | `host`, `port`, `button_pin` |
| `gpio` | PIR (HC-SR501, AM312) or mmWave (LD2410 OUT) on a Pi pin | `pin`, `active_high`, `pull_up`, `bounce_ms`, `hold_seconds` |
| `simulated` | none: Enter key or a timer | `stdin`, `every_seconds` |
| `ble` | phone/tag proximity via RSSI (stub, needs `bleak` and a stable advertiser) | `name`/`address`, `rssi_threshold` |

Rules: print once per day, on the first presence after `print_after`; at most one attempt per
`cooldown_minutes`; if no brief has arrived, `retry` later (default) or after `fallback_after`
print a `placeholder` slip / `skip` the day. The agent reports `printed`/`failed` to the relay.

## The brief format

```json
{
  "title": "Thursday briefing",
  "blocks": [
    {"type": "heading", "text": "Today"},
    {"type": "row", "left": "09:30 Design review", "right": "Room 4"},
    {"type": "heading", "text": "Your field"},
    {"type": "heading", "level": 2, "text": "Lead story headline"},
    {"type": "text", "text": "One or two sentences.", "url": "https://source.example/story"},
    {"type": "bullet", "text": "One item.", "url": "https://source.example/item"},
    {"type": "divider"},
    {"type": "qr", "url": "https://source.example/story", "caption": "Read more"},
    {"type": "small", "text": "Sources: calendar, arXiv."}
  ]
}
```

Strict validation: unknown fields or types are rejected, URLs must be absolute http(s), there
are length limits per block, and at most 80 blocks and 6000 characters. The webhook and email
also accept plain text: `# Heading`, `## Headline`, `- bullet https://link`, `left | right`,
`---`, `qr: URL caption`, `small: text`, anything else is a paragraph.

## API

| method | path | auth | |
|---|---|---|---|
| POST | `/v1/devices` | admin | register: `{name, timezone}` → token + URLs |
| POST | `/mcp` | device | MCP streamable HTTP, tool `print_brief`; token via Bearer or `?token=` |
| POST | `/v1/briefs` | device | webhook: JSON brief, or `text/plain` with optional `X-Brief-Title` |
| POST | `/v1/inbound/email` | `X-Inbound-Secret` | raw RFC 822 from your mail provider; recipient `brief+<token>@domain` |
| GET | `/v1/device/brief` | device | today's pending brief, or 204 |
| POST | `/v1/device/briefs/{id}/status` | device | `{status: printed\|failed, detail}` |
| GET | `/v1/device`, `/v1/device/history` | device | device info, dedup history |

Send responses include `status` (`queued`, `duplicate`, `empty_after_dedup`),
`dropped_duplicates` (links printed in the last 60 days) and `superseded`.

## Layout

```
src/dailytimes/
  blocks.py            schema, validation, link canonicalisation, plain-text parser
  relay/               FastAPI app, SQLite store, MCP server, inbound email transports
  render/              profiles, fonts (bundled, OFL), layout engine, raster + PDF renderers
  profiles/*.yaml      shipped printer profiles
  adapters/            cups+ipp, escpos, catprinter, email, file, discovery
  agent/               config, rules, presence backends, runner
  cli.py               dailytimes relay|register|send|render|profiles|printers|test-print|print|agent|presence
scripts/demo.sh        end-to-end demo
deploy/                agent example config, systemd unit, launchd plist
```

Decisions and progress are in [PROGRESS.md](PROGRESS.md).
