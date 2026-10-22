#!/usr/bin/env bash
# End-to-end demo: relay -> register -> brief via webhook and MCP -> agent -> presence -> printouts.
# Outputs the same brief as 58mm PNG, 80mm PNG, A4 PDF and Letter PDF in ./out for visual review.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-.venv/bin/python}
DT=("$PY" -m dailytimes.cli)
free_port() { "$PY" -c "import socket; s=socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1])"; }
RELAY_PORT=${RELAY_PORT:-$(free_port)}
AGENT_PORT=${AGENT_PORT:-$(free_port)}
RELAY="http://127.0.0.1:${RELAY_PORT}"
OUT=./out
WORK=$OUT/demo
PIDS=()

cleanup() { for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT

wait_for() {  # url, name
  for _ in $(seq 1 50); do curl -fsS "$1" >/dev/null 2>&1 && return 0; sleep 0.2; done
  echo "!! $2 did not start; see $WORK/*.log" >&2; exit 1
}
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
json() { "$PY" -c "import json,sys; d=json.load(sys.stdin); print(d$1)"; }

# register the device in this machine's timezone so the masthead date is your today
TZ_NAME=${TZ_NAME:-$("$PY" -c "import os; p=os.path.realpath('/etc/localtime'); print(p.split('zoneinfo/')[1] if 'zoneinfo/' in p else 'UTC')")}

rm -rf "$WORK" "$OUT"/brief-*; mkdir -p "$WORK"

ADMIN=demo-admin-$RANDOM
step "1. Start the server on $RELAY"
DAILYTIMES_ADMIN_TOKEN="$ADMIN" DAILYTIMES_DEV=1 "${DT[@]}" serve --host 127.0.0.1 --port "$RELAY_PORT" \
  --db "$WORK/relay.db" --public-url "$RELAY" --log-level warning >"$WORK/relay.log" 2>&1 &
PIDS+=($!)
wait_for "$RELAY/healthz" relay
echo "relay up"

step "2. Create an account with one printer (headless; on the website this is sign-up + pairing)"
REG=$("${DT[@]}" register --relay "$RELAY" --name "Demo kitchen printer" --timezone "$TZ_NAME" --admin-token "$ADMIN")
TOKEN=$(echo "$REG" | json "['token']")          # the printer's token (read today's brief, report status)
SEND=$(echo "$REG" | json "['send_token']")      # the AI agents' write-only token
MCP_URL=$(echo "$REG" | json "['mcp_url']")
WEBHOOK_URL=$(echo "$REG" | json "['webhook_url']")
echo "$REG" | json "['device_id']" | sed "s|^|printer: |; s|$| ($TZ_NAME)|"
echo "unique endpoint for any AI agent: ${MCP_URL/$SEND/<token>}"

step "3. Send an early draft via the HTTPS webhook (markdown-ish text)"
curl -fsS -X POST "$WEBHOOK_URL" -H "Content-Type: text/markdown" -H "X-Brief-Title: Early draft" \
  --data-binary @examples/draft_brief.md
echo

step "4. Send the final brief via MCP (tools/call print_brief), as an assistant would"
"${DT[@]}" send examples/sample_brief.json --endpoint "$MCP_URL" | tee "$WORK/mcp.json"
BRIEF_ID=$(json "['brief_id']" <"$WORK/mcp.json")
grep -q '"superseded": 1' "$WORK/mcp.json" && echo "-> newest brief of the day wins: the webhook draft was superseded"

step "5. Start the device agent with four outputs (58mm, 80mm, A4, Letter) and manual presence"
cat >"$WORK/agent.yaml" <<YAML
relay: {url: "$RELAY", token: "$TOKEN"}
poll_interval_seconds: 5
state_file: $WORK/agent-state.json
presence: {backend: manual, options: {host: 127.0.0.1, port: $AGENT_PORT}}
rules: {print_after: "00:00", cooldown_minutes: 0, fallback: retry}
outputs:
  - {profile: thermal-58mm, adapter: file, options: {directory: $OUT, name: "brief-{profile}", formats: [png]}}
  - {profile: thermal-80mm, adapter: file, options: {directory: $OUT, name: "brief-{profile}", formats: [png]}}
  - {profile: a4,           adapter: file, options: {directory: $OUT, name: "brief-{profile}", formats: [pdf]}}
  - {profile: letter,       adapter: file, options: {directory: $OUT, name: "brief-{profile}", formats: [pdf]}}
YAML
"${DT[@]}" agent --config "$WORK/agent.yaml" >"$WORK/agent.log" 2>&1 &
PIDS+=($!)
wait_for "http://127.0.0.1:$AGENT_PORT/status" agent
echo "agent up; nothing printed yet: $(ls $OUT/brief-* 2>/dev/null | wc -l | tr -d ' ') files"

step "6. Simulate someone walking up to the printer"
"${DT[@]}" presence --url "http://127.0.0.1:$AGENT_PORT" | "$PY" -m json.tool

step "7. Relay status for the brief"
curl -fsS "$RELAY/v1/device/briefs/$BRIEF_ID" -H "Authorization: Bearer $TOKEN" | json "['status']" | sed 's/^/status: /'

step "8. Walk past again: prints once per day"
"${DT[@]}" presence --url "http://127.0.0.1:$AGENT_PORT"

step "Outputs for visual review"
missing=0
for f in brief-thermal-58mm.png brief-thermal-80mm.png brief-a4.pdf brief-letter.pdf; do
  if [ -s "$OUT/$f" ]; then printf '  %-28s %8s bytes\n' "$OUT/$f" "$(wc -c <"$OUT/$f" | tr -d ' ')"; else echo "  MISSING $OUT/$f"; missing=1; fi
done
[ "$missing" = 0 ] || { echo "demo failed"; exit 1; }
echo; echo "Demo OK."
