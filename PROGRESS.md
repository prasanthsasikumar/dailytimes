# PROGRESS

## Milestones
- [x] M1 Block schema + strict validation, relay (SQLite store, webhook, inbound email, MCP `print_brief`, device API)
- [x] M2 Rendering: one layout engine, raster + PDF renderers, nameplate, YAML profiles
- [x] M3 Output adapters (CUPS/IPP, ESC/POS, cat printer stub, email-to-print, file) + `printers` / `test-print` CLI
- [ ] M4 Device agent: polling, presence backends, print rules, status reporting
- [ ] M5 SETUP_PROMPT.md, deploy files (docker-compose, systemd, launchd), README, demo.sh

## Decisions (ambiguities resolved with the simplest reasonable option)
- **One token per device.** The same token is used by the assistant (MCP/webhook/email) and the device agent. Simpler to set up; a leaked token can at worst queue or mark briefs.
- **MCP auth** accepts `Authorization: Bearer` or `?token=` on the URL, because several connector UIs only take a URL. No OAuth in the MVP.
- **Registration** is open when `DAILYTIMES_ADMIN_TOKEN` is unset (local dev); otherwise it requires the admin token.
- **"Brief of the day"** is keyed by the device's local date (IANA timezone set at registration). Brief dates may be yesterday..+7 days; pending briefs for past dates expire on the next poll.
- **Newest wins**: a new brief for a date supersedes pending ones for that date; byte-identical content is a no-op (`duplicate`).
- **Dedup** works on items that have a source link (text/bullet/qr `url`), canonicalised (no www, no tracking params). History is written when the agent reports `printed`, not when queued, so same-day revisions don't dedup against each other. Items seen in the last 60 days are dropped and reported back as `dropped_duplicates`; headings left empty are pruned; if nothing is left the brief isn't queued (`empty_after_dedup`). Items without links (calendar rows, prices) are never deduped.
- **Validation**: unknown fields/types rejected; control characters rejected; newlines are only allowed in text/bullet/small (collapsed to spaces); urls must be absolute http(s) with a dotted host. Limits: 80 blocks, 6000 chars.
- **Inbound email** recipient is `brief+<token>@<domain>`; body is a JSON brief or markdown-ish text. The webhook MIME transport is implemented; IMAP is a stub.
- **MCP SDK** is mcp 2.x (`MCPServer`), stateless streamable HTTP with JSON responses, mounted at `/mcp` on the FastAPI app. The SDK's localhost-only DNS-rebinding guard is disabled because the relay runs behind a public hostname; every call is token-authenticated.
- **Fonts** are bundled (OFL): UnifrakturMaguntia for the nameplate, PT Serif/PT Sans/PT Sans Narrow for text, so output is identical on a Pi, a Mac and in Docker. Characters the fonts lack (emoji, arrows) are substituted (`→` -> `->`) or dropped rather than printed as boxes.
- **Layout** is measured in points with ReportLab's unhinted widths; the raster renderer paints the same ops at the profile dpi (1-bit, no anti-aliasing for mono profiles). QR codes are drawn as modules (vector rects in PDF, whole-pixel modules in raster) so they stay scannable.
- **Newspaper layout**: page 1 masthead (nameplate, double rule, date bar with Vol./No. = day of year), N columns with gutter rules, columns balanced on the last page, compact continuation header and "Page i of n" on later pages. A divider directly before a section heading is dropped (the heading has its own rule). Keep rules: headings stay with the next line, links with their item, no one-line widows/orphans.
- **Slip layout**: compact nameplate, inverted-bar section headings, dotted leaders on rows, centred QR.
- **Letter** profiles use US date order; everything else uses day-month-year.
- **Profiles** are looked up in `$DAILYTIMES_PROFILES`, `~/.config/dailytimes/profiles`, then the package. Shipped: thermal-58mm, thermal-80mm, a4, letter, plus a5 and receipt-80mm-pdf.
- **Adapters** declare the formats they accept; the caller renders for the printer's profile in that format (`adapters.print_brief`). Registry + `dailytimes.adapters` entry-point group, so a new printer type is one adapter class + one profile YAML (tested with a throwaway adapter).
- **CUPS/IPP**: `printer=` uses `lp` (PDF on stdin, `media=` from the profile, custom length for receipt PDFs). `uri=` sends an IPP/1.1 Print-Job with a small built-in encoder (no pycups/ipptool dependency). Printers that refuse PDF get a hint to add a driverless CUPS queue (`lpadmin … -m everywhere`). Discovery: `lpstat -v` + mDNS (`_ipp._tcp`, `_ipps._tcp`, and `_pdl-datastream._tcp` = raw 9100, suggested as ESC/POS).
- **ESC/POS** uses python-escpos `GS v 0` raster in 512-line fragments; cuts only if the profile has a cutter, otherwise feeds past the tear bar. Dry-run encodes into `escpos.printer.Dummy`.
- **Cat printers**: encoder implemented and tested; BLE transport is a stub (docs/cat-printers.md).
- **Email-to-print** sends the PDF over SMTP (STARTTLS, or implicit TLS on 465); `password_env` keeps secrets out of config.
- **Profile suggestion** is name/model heuristics (58/80 mm keywords, TM-/TSP/POS models); page printers get Letter in US/CA/MX/PH/... locales, A4 elsewhere.
