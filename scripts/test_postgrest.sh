#!/usr/bin/env bash
# Run the store tests against a real Postgres + PostgREST (what Supabase uses), locally.
# Needs Postgres binaries (initdb, pg_ctl, psql) on PATH; downloads PostgREST into .cache/.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-.venv/bin/python}
VER=v16.5
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) ASSET=postgrest-$VER-macos-aarch64.tar.xz ;;
  Darwin-x86_64) ASSET=postgrest-$VER-macos-x86-64.tar.xz ;;
  Linux-x86_64) ASSET=postgrest-$VER-linux-static-x86-64.tar.xz ;;
  Linux-aarch64) ASSET=postgrest-$VER-ubuntu-aarch64.tar.xz ;;
  *) echo "no PostgREST build for this platform" >&2; exit 1 ;;
esac
BIN=.cache/postgrest/postgrest
if [ ! -x "$BIN" ]; then
  mkdir -p .cache/postgrest
  curl -fsSL "https://github.com/PostgREST/postgrest/releases/download/$VER/$ASSET" | tar -xJ -C .cache/postgrest
fi

# macOS builds link Homebrew's libpq; any Postgres install's libdir has the same library.
[ "$(uname -s)" = Darwin ] && export DYLD_FALLBACK_LIBRARY_PATH="$(pg_config --libdir)${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"

WORK=$(mktemp -d); PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; pg_ctl -D "$WORK/pg" stop -m fast >/dev/null 2>&1 || true; rm -rf "$WORK"; }
trap cleanup EXIT
free_port() { "$PY" -c "import socket; s=socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1])"; }
PGPORT=$(free_port); APIPORT=$(free_port)

initdb -D "$WORK/pg" -U postgres --auth=trust >/dev/null
pg_ctl -D "$WORK/pg" -o "-p $PGPORT -k $WORK -c listen_addresses=127.0.0.1" -l "$WORK/pg.log" start >/dev/null
for _ in $(seq 1 50); do psql -h 127.0.0.1 -p "$PGPORT" -U postgres -c 'select 1' >/dev/null 2>&1 && break; sleep 0.2; done

# Supabase-like roles: the API connects as `authenticator` and switches to the JWT's role.
psql -q -h 127.0.0.1 -p "$PGPORT" -U postgres -v ON_ERROR_STOP=1 <<SQL
create role anon nologin;
create role service_role nologin bypassrls;
create role authenticator login noinherit;
grant anon, service_role to authenticator;
grant usage on schema public to anon, service_role;
alter default privileges in schema public grant all on tables to anon, service_role;
SQL
psql -q -h 127.0.0.1 -p "$PGPORT" -U postgres -v ON_ERROR_STOP=1 -f supabase/schema.sql

SECRET=$("$PY" -c "import secrets; print(secrets.token_hex(32))")
KEY=$("$PY" -c "import jwt,sys; print(jwt.encode({'role': 'service_role'}, sys.argv[1], algorithm='HS256'))" "$SECRET")
ANON=$("$PY" -c "import jwt,sys; print(jwt.encode({'role': 'anon'}, sys.argv[1], algorithm='HS256'))" "$SECRET")
PGRST_DB_URI="postgres://authenticator@127.0.0.1:$PGPORT/postgres" PGRST_DB_SCHEMAS=public PGRST_DB_ANON_ROLE=anon \
  PGRST_JWT_SECRET="$SECRET" PGRST_SERVER_PORT="$APIPORT" PGRST_SERVER_HOST=127.0.0.1 "$BIN" >"$WORK/pgrst.log" 2>&1 &
PIDS+=($!); disown $!
for _ in $(seq 1 50); do curl -fs "http://127.0.0.1:$APIPORT/" >/dev/null 2>&1 && break; sleep 0.2; done

# PostgREST serves /, Supabase serves /rest/v1; a tiny prefix shim keeps SupabaseDB unchanged.
SHIM=$(free_port)
"$PY" - "$SHIM" "$APIPORT" <<'PY' &
import http.server, sys, urllib.request
port, api = int(sys.argv[1]), sys.argv[2]
class H(http.server.BaseHTTPRequestHandler):
    def _go(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) or None
        req = urllib.request.Request(f"http://127.0.0.1:{api}" + self.path.removeprefix("/rest/v1"), data=body,
                                     method=self.command, headers={k: v for k, v in self.headers.items() if k.lower() != "host"})
        try:
            r = urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            r = e
        data = r.read(); self.send_response(r.status)
        for k, v in r.headers.items():
            if k.lower() not in ("transfer-encoding", "connection", "content-length"): self.send_header(k, v)
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    do_GET = do_POST = do_PATCH = do_DELETE = _go
    def log_message(self, *a): pass
http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
PY
PIDS+=($!); disown $!
sleep 0.5

echo "RLS check: the anon key must not read users"
code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$APIPORT/dt_users" -H "Authorization: Bearer $ANON")
rows=$(curl -s "http://127.0.0.1:$APIPORT/dt_users" -H "Authorization: Bearer $ANON")
echo "  anon -> HTTP $code $rows"
[ "$rows" = "[]" ] || [ "$code" = "401" ] || [ "$code" = "403" ] || { echo "RLS FAILED"; exit 1; }

DAILYTIMES_TEST_POSTGREST_URL="http://127.0.0.1:$SHIM" DAILYTIMES_TEST_POSTGREST_KEY="$KEY" \
  "$PY" -m pytest -q tests/test_store.py "$@"
