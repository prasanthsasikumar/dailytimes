# PROGRESS

## Milestones
- [x] M1 Block schema + strict validation, relay (SQLite store, webhook, inbound email, MCP `print_brief`, device API)
- [ ] M2 Rendering: one layout engine, raster + PDF renderers, nameplate, YAML profiles
- [ ] M3 Output adapters (CUPS/IPP, ESC/POS, cat printer stub, email-to-print, file) + `printers` / `test-print` CLI
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
