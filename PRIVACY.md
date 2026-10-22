# Privacy and security design

The Daily Times prints a personal paper, so it touches personal things. The design goal is to
hold as little as possible, for as short as possible, and to make what it does hold easy to
see, export and delete. The website's `/privacy` page is the user-facing version of this.

## Data held, and for how long

| data | why | kept |
|---|---|---|
| email address | sign-in links | until account deletion |
| interests (topics, subreddits, tickers, feeds, arXiv categories, city, free-text description) | build the auto edition | until changed or deleted |
| timezone, paper size, auto-edition settings | when and how to build | until changed or deleted |
| editions (finished pages) | printing, dashboard | **30 days** |
| print receipts (which printer printed which edition) | print once per day | **30 days** |
| seen-link history (normalised URLs) | don't repeat stories | **60 days** |
| printers (name, paired date, last seen) | pairing | until unpaired |
| endpoint and printer tokens | auth | **SHA-256 hashes only**; the plain token is shown once |
| sign-in links, pairing codes | auth | 15 minutes, single use, hashed |

Not held: passwords, IP addresses (beyond whatever the host's access logs keep), calendar,
mail, chats, analytics, device fingerprints. For weather, the user gives a city name; the
server stores that name and the city's centre coordinates from the geocoder, never a precise
location.

## Where data flows

- **AI agents** read the user's calendar/mail on their side and send only the finished
  edition. The write-only endpoint token can create editions and nothing else; it can't read
  anything back.
- **Printers** hold a device token that can read today's edition and report print status
  for that printer only.
- **Auto editions** are built server-side from keyless public sources (Google News search,
  Hacker News, Reddit, arXiv, Yahoo Finance, Open-Meteo, user-added feeds). The requests carry
  only the search terms, never the user's identity. Headlines and numbers are quoted as
  published; nothing is summarised, so nothing is invented.
- **Optional LLM**: only if the operator sets `ANTHROPIC_API_KEY`, and only when the user
  presses "Suggest sources", the free-text description is sent to Anthropic once. The
  interests page says which mode the server is in.

## Controls

- Export: Account → *Download my data* (JSON of everything above, no token hashes).
- Delete: Account → *Delete everything*. Immediate, all tables.
- Sign out everywhere: bumps a per-user epoch that invalidates every session cookie.
- Rotate the AI endpoint: Connect AI → *Make a new endpoint*. The old one stops working.
- Unpair a printer: its token stops working.

## Security notes

- Sessions are HMAC-signed cookies (`HttpOnly`, `SameSite=Lax`, `Secure` on https), 30 days.
  Every state-changing form carries a CSRF token bound to the user and session epoch.
- Sign-in links are single use, expire in 15 minutes, are rate-limited per address, and need
  a button press (so mail scanners that prefetch links can't use them up).
- The database is reached only by the server. On Supabase, Row Level Security is on with no
  policies, so the anon key reads nothing (`scripts/test_postgrest.sh` checks this).
- User-supplied feed URLs are fetched with SSRF protection: http(s) only, public IPs only
  (checked on every redirect hop), 2 MB and 8 s limits. Known gap: a DNS-rebinding race
  between the check and the connection; pin resolved IPs if you run this at scale.
- All brief content is validated strictly (schema, lengths, URL shapes) before it's stored,
  and HTML templates autoescape everything.
- No third-party scripts or fonts on the website; fonts are bundled.
- MCP: the endpoint token in the URL is the credential (connector UIs often accept only a
  URL). Treat the URL like a password; rotate it if it leaks. OAuth for MCP is a future step.

## Operator responsibilities

If you run a public instance you are the data controller: set `DAILYTIMES_OPERATOR` and
`DAILYTIMES_CONTACT`, keep `DAILYTIMES_SECRET` and the Supabase service key secret, enable
backups that respect the retention above, and publish any extra processing you add.
