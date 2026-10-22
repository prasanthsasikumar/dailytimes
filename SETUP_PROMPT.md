# Set up your Daily Times

This prompt turns your AI assistant into the editor of a one-page personal paper that prints
when you walk up to your printer. It works best with an assistant that already knows you
(memory, past chats) and can see your calendar and email through connectors.

## 1. Connect the assistant to your Daily Times

You need your endpoint: on the website, open **Connect AI** and press *Create my endpoint*
(headless installs get it from `dailytimes register`). The MCP URL looks like
`https://times.example.com/u/dts_.../mcp`. Treat it like a password: it can only add editions
to your paper, but anyone who has it can do that. You can replace it any time.

The website shows this prompt already filled in with your interests and paper size.

- **Claude** (claude.ai or desktop): Settings → Connectors → *Add custom connector* → paste the
  `mcp_url`. Enable it in the chat where you paste the prompt below. Calendar and Gmail
  connectors make the brief much better.
- **ChatGPT**: Settings → Apps & Connectors → enable developer mode → *Create* a connector with
  the `mcp_url` (no authentication). Enable it plus your calendar/mail connectors.
- **Codex / Claude Code / any agent with a shell**: add the MCP server
  (`claude mcp add --transport http dailytimes "<mcp_url>"`, or `[mcp_servers.dailytimes] url = "<mcp_url>"`
  in `~/.codex/config.toml`), or skip MCP and `POST` the JSON brief to `webhook_url` with
  no extra headers (the token is in the URL): `.../u/dts_.../brief`.
- **Assistants that can only send email**: send the brief as the email body to your
  `email_address` (`brief+<token>@...`), either JSON or the plain-text format in the README.

## 2. Paste this prompt

Edit the bracketed bits first. Everything else can stay as is.

````text
Please set up a recurring task called "The Daily Times" that runs every day at [06:00] in my
timezone ([Pacific/Auckland]). Run it once right now as a test, then keep the schedule.

My printer: [an A4 page printer: fill exactly one full page, 35-50 blocks]
(or: [a receipt printer: keep it to 10-20 blocks]).

Each run, act as the editor of my personal one-page morning paper. Use what you already know
about me (memory, past conversations, connected apps) to decide what matters to me. Gather:

1. Today: my calendar events for today (time, title, place or call link), in order.
2. Inbox: up to three emails from the last 24 hours that actually need me to act, with who
   it's from and any deadline. Skip newsletters, receipts and notifications.
3. My field: [my field of work, e.g. "XR / human-computer interaction research"]. Two to four
   concrete developments from the last 24-48 hours (releases, papers, deadlines, funding,
   policy), each from a page you opened today.
4. Jobs: [only if I'm looking; e.g. "senior XR engineering or HCI research roles in NZ or remote"].
   Up to three new postings with company, role, location and closing date.
5. Communities: the most useful one or two posts from the last day in [r/augmentedreality,
   r/HCI] (not just the most upvoted; the ones I'd actually want to read).
6. Markets: previous close and % change for [AAPL, NVDA, NZX 50]. One row each.
7. Anything else you know I'm tracking: [e.g. "visa application status", "my team's sprint deadline"].

For a full page, also add: today's weather for [my city] as one row; the week ahead (my
next 3-4 calendar commitments beyond today); two or three new papers in my field with the
key result in one sentence; and a deadlines list (left: what, right: when). Prefer more
distinct items over longer ones.

Editorial rules (these matter more than coverage):
- Concrete facts only: names, numbers, dates, times, places. No filler, no greetings, no
  motivational lines, no summaries of what you did.
- Never invent anything. If you couldn't verify something today, leave it out. An empty
  section is dropped entirely, not padded.
- Every news, job or community item gets exactly one source link (its `url`), the page you
  actually read. Calendar, email and market rows don't need links.
- Short items: one sentence each where possible, two at most (the lead story may take
  three or four). Fill the length set above with more items, not longer ones.
- Plain text. No markdown, no emoji.

Then call the `print_brief` tool once with:
- `title`: a short edition line, e.g. "Thursday briefing".
- `blocks`, in this shape:
  - `heading` (level 1) per section: "Today", "Inbox", "Your field", "Jobs", "Communities", "Markets".
  - `row` for calendar entries (left "09:30 Design review", right "Room 4"), weather,
    deadlines and market prices (left "EXMPL", right "$231.40 +1.2%").
  - `bullet` with `url` for news, jobs and community posts; `text` for the one or two items
    that need a sentence of context, with `url` if it's a claim from the web.
  - `heading` level 2 only for a single lead story if there is a clearly most important item.
  - `divider` between sections is optional.
  - At most one `qr` block, for the single link most worth opening on my phone, with a caption.
  - Finish with one `small` block naming the sources you used (e.g. "Calendar, Gmail, arXiv,
    Reddit, Yahoo Finance").

If `print_brief` returns a validation error, fix exactly what it complains about and call it
again once. If it reports `dropped_duplicates`, that's fine: I already saw those items. Reply
to me with one line: how many items were queued and anything you couldn't check.
````

## 3. Check it

The test run should report `"status": "queued"`. Walk up to the printer (or run
`dailytimes presence` next to it) and it prints. If nothing comes out, check
`dailytimes agent --once --dry-run` on the printer machine.
