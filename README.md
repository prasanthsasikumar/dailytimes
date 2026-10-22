# The Daily Times

A one-page newspaper about your day, printed on **whatever printer you own**, when someone
walks up to it.

- **Sign up with an email and say what you're into.** Every morning you get an *auto edition*:
  headlines, new papers, community posts, prices and weather from public sources, quoted exactly
  as published.
- **Connect any AI agent** (Claude, ChatGPT, Codex, Claude Code, n8n, a cron script) to your
  private endpoint and it sends a richer *personal edition* using what it can see: your
  calendar, inbox, chats. The newest edition of the day wins.
- **Pair a printer** with a code: a receipt printer, a laser, an inkjet, an email-to-print
  address, or none (read or print the PDF from the website).
- **Private by design**: we store an email, interests and 30 days of editions. Never your
  calendar or mail. Export or delete everything any time. Open source; run your own.

```
                          ┌──────── The Daily Times server ────────┐
 any AI agent ──MCP/webhook/email──▶  editions · dedup · accounts    │◀──poll── printer agent ──▶ printer
 (your endpoint)          │  auto edition from public sources      │          (Pi/Mac/Linux)   (receipt, laser,
 you ── website ─────────▶│  dashboard · pairing · export/delete   │              ▲             inkjet, email, PDF)
                          └──── Vercel + Supabase, or Docker/SQLite ┘     presence: sensor, button, phone
```

| receipt (58/80 mm) | A4 / Letter |
|---|---|
| compact nameplate, inverted section bars, dotted leaders | blackletter masthead, date bar, two balanced columns, filled to exactly one page |

## Try it locally (five minutes)

```bash
uv venv && uv pip install -e ".[dev]"            # or: python -m venv .venv && pip install -e ".[dev]"
DAILYTIMES_DEV=1 .venv/bin/dailytimes serve      # http://localhost:8787 (sign-in link shown on screen)
```

Sign up, describe your interests, press *Build today's auto edition now*, open the PDF.

```bash
.venv/bin/pytest -q            # the test suite
./scripts/demo.sh              # server + MCP + webhook + printer agent + presence -> ./out (58mm, 80mm, A4, Letter)
./scripts/test_postgrest.sh    # the storage tests against real Postgres + PostgREST (Supabase's stack)
```

## Run it for real

- **Your own server**: `docker compose up -d` with SQLite on a volume.
- **Public service**: Vercel (the app) + Supabase (the database).

Both are step by step in [DEPLOY.md](DEPLOY.md); every setting is in [.env.example](.env.example).
Privacy and security design: [PRIVACY.md](PRIVACY.md).

## Use it

1. **Sign up** on the website and fill in *Interests* (or write a sentence and press *Suggest
   sources*). Choose full page or receipt.
2. **Connect AI**: create your endpoint, add the MCP URL as a connector in Claude or ChatGPT
   (or `claude mcp add --transport http dailytimes <url>`), and paste the generated prompt.
   It's [SETUP_PROMPT.md](SETUP_PROMPT.md), pre-filled with your interests and paper size.
3. **Printers**: get a pairing code, then on the machine next to the printer:

   ```bash
   pipx install "dailytimes[escpos,mdns]"          # add [gpio] on a Pi with a motion sensor
   dailytimes setup --server https://your-server --code ABCD-EFGH
   dailytimes agent                                 # or: dailytimes service, to run at boot
   ```

   `setup` finds printers (CUPS queues, Bonjour/IPP Everywhere, USB and serial receipt
   printers), suggests a paper profile, prints a test page, and writes
   `~/.config/dailytimes/agent.yaml`.

Without the website (headless, one household): set `DAILYTIMES_ADMIN_TOKEN` and use
`dailytimes register` to get a printer token and an endpoint directly.

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
| POST | `/u/{token}/mcp` (also `/mcp` + Bearer or `?token=`) | endpoint token | MCP streamable HTTP, one tool: `print_brief` |
| POST | `/u/{token}/brief` (also `/v1/briefs` + Bearer) | endpoint token | webhook: JSON brief, or `text/plain` with optional `X-Brief-Title` |
| POST | `/v1/inbound/email` | `X-Inbound-Secret` | raw RFC 822 from your mail provider; recipient `brief+<token>@domain` |
| POST | `/v1/pair` | pairing code | `{code}` → printer token |
| GET | `/v1/device/brief` | printer token | today's edition for this printer, or 204 (builds the auto edition if due) |
| POST | `/v1/device/briefs/{id}/status` | printer token | `{status: printed\|failed, detail}` |
| GET | `/v1/device`, `/v1/device/history` | printer token | printer info, seen-link history |
| POST | `/v1/devices` | admin token | headless account + printer + endpoint |
| GET | `/cron/daily` | `CRON_SECRET` | pre-build auto editions, apply retention |

The **endpoint token** can only add editions; the **printer token** can only read today's
edition and report its own status. Both are stored hashed.

Send responses include `status` (`queued`, `duplicate`, `empty_after_dedup`),
`dropped_duplicates` (links already printed in the last 60 days) and `superseded`.

## Code map

```
src/dailytimes/
  blocks.py            brief schema, strict validation, link canonicalisation, plain-text parser
  relay/               the server: app (API), web (website), store, db (SQLite | Supabase),
                       mcp_server, sessions, mailer, autoedition, inbound email, templates/
  autobrief/           interests, public sources, composer, source suggestion, SSRF-safe fetch
  render/              profiles, bundled fonts (OFL), layout engine, raster + PDF renderers
  adapters/            cups+ipp, escpos, catprinter, email, file, discovery
  agent/               printer agent: config, rules, presence, runner, setup wizard
  cli.py               dailytimes serve|register|send|setup|service|agent|presence|printers|test-print|render|...
api/index.py           Vercel entry point          supabase/schema.sql   database schema (+ RLS)
scripts/               demo.sh, test_postgrest.sh  deploy/               example agent config, unit files
```

Decisions and progress are in [PROGRESS.md](PROGRESS.md).
