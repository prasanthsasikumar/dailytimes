# Deploying The Daily Times

Three ways, from least to most effort. All run the same code.

| | where | data | good for |
|---|---|---|---|
| **A. Local** | your laptop or a Pi | SQLite file | trying it, a household |
| **B. Docker** | any server you own | SQLite on a volume | self-hosting for friends |
| **C. Vercel + Supabase** | serverless | Supabase Postgres | a public service |

## A. Local, five minutes

```bash
uv venv && uv pip install -e ".[dev]"
DAILYTIMES_DEV=1 .venv/bin/dailytimes serve --port 8787
```

Open http://localhost:8787, enter any email. With `DAILYTIMES_DEV=1` and no mail configured, the
sign-in link appears on the page. Never set `DAILYTIMES_DEV` on a public server.

## B. Docker on your own server

```bash
cp .env.example .env      # set DAILYTIMES_PUBLIC_URL, DAILYTIMES_SECRET, mail, CRON_SECRET
docker compose up -d
```

Put TLS in front, e.g. Caddy: `times.example.com { reverse_proxy 127.0.0.1:8787 }`.
Data lives in the `dailytimes-data` volume (one SQLite file). Back it up like any file.

## C. Vercel + Supabase

### 1. Database (Supabase)

1. Create a Supabase project (or use an existing one: all tables are prefixed `dt_`).
2. SQL editor: paste and run [`supabase/schema.sql`](supabase/schema.sql). It creates the tables
   and switches on Row Level Security with no policies, so the public anon key can read nothing.
3. Settings → API: copy the project URL and the **service_role** (secret) key.

The server talks to Supabase over its REST API with the service key. That's what lets it run
on serverless hosts without Postgres connections. The key never reaches a browser.

To put the tables in their own schema instead of `public`: create the schema, grant it to
`service_role`, add it to the API's exposed schemas, run the file with `set search_path`, and
set `SUPABASE_SCHEMA`.

### 2. Email (sign-in links)

Easiest: a [Resend](https://resend.com) API key with a verified sending domain. Set
`RESEND_API_KEY` and `MAIL_FROM`. Any SMTP server works instead (`SMTP_HOST`, `SMTP_PORT`,
`SMTP_USER`, `SMTP_PASSWORD`).

### 3. Vercel

1. Import the repository in Vercel. No build settings needed: `vercel.json` routes everything
   to `api/index.py`, and `requirements.txt` lists the dependencies.
2. Environment variables (Production):

   | name | value |
   |---|---|
   | `DAILYTIMES_PUBLIC_URL` | `https://your-domain` (no trailing slash) |
   | `DAILYTIMES_SECRET` | `openssl rand -hex 32` |
   | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | from step 1 |
   | `RESEND_API_KEY`, `MAIL_FROM` | from step 2 |
   | `CRON_SECRET` | any long random string (Vercel sends it to the cron job) |
   | `DAILYTIMES_OPERATOR`, `DAILYTIMES_CONTACT` | your name and a privacy contact; shown on the privacy page |
   | `ANTHROPIC_API_KEY` | optional: Claude suggests sources from a user's description |

3. Deploy, then add your domain.

The cron in `vercel.json` runs `/cron/daily` once a day (the Hobby plan's limit). It pre-builds
auto editions and applies retention. Editions are also built on demand when a printer or the
dashboard asks, so a daily cron is enough.

### Checks after deploying

```bash
curl https://your-domain/healthz                     # {"ok": true}
curl -s https://<project>.supabase.co/rest/v1/dt_users -H "apikey: <ANON key>"   # []  (RLS working)
```

Then sign up, set interests, create an endpoint, and send a test brief:

```bash
echo "# Test\n- Hello from the command line" | dailytimes send - --endpoint "https://your-domain/u/<token>/brief"
```

## Testing against Postgres locally

`scripts/test_postgrest.sh` starts a throwaway Postgres + PostgREST with Supabase's roles, loads
`supabase/schema.sql`, checks RLS, and runs the store tests against it. It needs Postgres
binaries on PATH and downloads PostgREST into `.cache/`.
