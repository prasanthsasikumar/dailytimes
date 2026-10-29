"""MCP server (streamable HTTP) exposing two tools: `print_brief` (today's edition) and
`set_interests` (what the auto edition covers, filled in by the AI at setup).

Auth: the user's write-only send token, in the URL path (`/u/<token>/mcp`, the
"unique endpoint" shown on the dashboard), as `Authorization: Bearer <token>`, or
as `?token=<token>`.

Serverless-safe: `mcp_asgi` builds a fresh stateless server per request, so it
never depends on ASGI lifespan events (which some hosts don't run).
"""

from __future__ import annotations

import datetime as dt

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from ..autobrief import Interests, Location
from ..autobrief.fetch import FetchError
from ..autobrief.sources import geocode
from ..blocks import MAX_BLOCKS, MAX_TOTAL_CHARS, Block, Brief
from starlette.types import Receive, Scope, Send

from .autoedition import interests_of
from .store import Store, StoreError

TOOL_DESCRIPTION = f"""\
Queue today's printed brief ("The Daily Times") for the user's printer. It prints
when someone next walks up to the printer. Calling again the same day replaces
the earlier brief (the newest one wins). The nameplate and date are added
automatically; don't add your own masthead.

Editorial rules:
- Concrete facts only: names, numbers, times, places. No filler, no motivational lines.
- Never invent anything. If you could not verify an item, leave it out.
- One source link per item, in that item's `url`: a public page anyone can open. Never a
  mail, calendar or drive link; for an email item, name the sender in the text instead.
- Lead each news item with `lead`: one to four words naming the subject ("Meta Q3",
  "Even Realities"), printed in bold. The text is then one clean sentence, not a chain of
  clauses joined by semicolons.
- Order: the user's own things first (calendar, email that needs action, deadlines), then
  one top story (a level 2 headline and a text block of two or three sentences), then 3-5
  items per section. Fewer strong items beat many thin ones.
- Say why it matters: when an item bears on the user's work, end with a short clause saying how.
- Round numbers for reading: two or three significant figures ("3.6M", "more than double")
  unless the exact figure is the point. Copy names exactly as the source spells them.
- Keep it short per item: one or two sentences, every line earns its place. Overall length
  follows the user's printer (their instructions say which): a receipt printer takes 10-20
  blocks; a full A4/Letter page holds 35-50 blocks and the layout scales the type so the
  brief fills exactly one page. If unsure, aim for 20-30.
  Hard limits: {MAX_BLOCKS} blocks, {MAX_TOTAL_CHARS} characters.
- Plain text only: no markdown, no emoji.
- Items the user already saw in the last 60 days are dropped automatically and listed in
  the result under `dropped_duplicates`.

Blocks:
- heading {{text, level: 1 section | 2 headline}}
- text {{text, lead?, url?}} short paragraph
- bullet {{text, lead?, url?}} one list item
- row {{left, right}} e.g. "09:30 Design review" | "Room 4", or "EXMPL" | "$231.40 +1.2%"
- divider {{}}
- qr {{url, caption?}} at most one or two per brief, for the link worth opening on a phone
- small {{text}} small print: one line naming sources; one line at most for anything left out

`title` is an optional edition line such as "Thursday briefing". `date` (YYYY-MM-DD)
defaults to today in the printer's timezone.
"""


INTERESTS_DESCRIPTION = """\
Save what the user's auto edition should cover: the paper The Daily Times builds from
public sources on mornings when no AI sends a brief. Call it once when the user connects
The Daily Times, filling it from what you know about them (memory, past conversations,
connected apps), and again only when they ask to change it; not on the daily run. The user
reviews and edits the result on the website.

Every argument is optional; the ones you pass replace what is saved, the rest stay.
- about: two or three sentences on who they are and what they follow, in plain words
- topics: up to 8 news searches, specific beats ("human-computer interaction", "All Blacks")
- subreddits: up to 6 names without r/
- tickers: up to 10 Yahoo Finance symbols (AAPL, ^GSPC, AIR.NZ)
- arxiv: up to 5 categories (cs.HC, cs.LG) for researchers
- feeds: up to 6 RSS/Atom URLs you know exist
- hacker_news: true for people in software or startups
- city: their city, for the weather box
- region: two-letter country code for their Google News edition
Only include what you have reason to believe; leave a field out rather than guess.
"""


def build_mcp(store: Store, edit_url: str = "") -> MCPServer:
    mcp = MCPServer(
        "dailytimes",
        title="The Daily Times",
        instructions="Use print_brief once a day to send the user's printed morning brief. When the user "
                     "first connects, call set_interests once with what you know about them.",
    )

    @mcp.tool(name="print_brief", title="Print today's brief", description=TOOL_DESCRIPTION)
    def print_brief(
        blocks: list[Block],
        ctx: Context,
        title: str | None = None,
        date: dt.date | None = None,
    ) -> dict:
        user = store.user_for_send_token(_token_from(ctx))
        if user is None:
            raise ToolError("unauthorized: missing or unknown Daily Times endpoint token")
        try:
            brief = Brief(blocks=blocks, title=title, date=date)
        except ValidationError as e:
            raise ToolError(_friendly(e)) from None
        try:
            result = store.enqueue(user, brief, source="mcp")
        except StoreError as e:
            raise ToolError(str(e)) from None
        return result.as_dict()

    @mcp.tool(name="set_interests", title="Set what the auto edition covers", description=INTERESTS_DESCRIPTION)
    def set_interests(
        ctx: Context,
        about: str | None = None,
        topics: list[str] | None = None,
        subreddits: list[str] | None = None,
        tickers: list[str] | None = None,
        arxiv: list[str] | None = None,
        feeds: list[str] | None = None,
        hacker_news: bool | None = None,
        city: str | None = None,
        region: str | None = None,
    ) -> dict:
        user = store.user_for_send_token(_token_from(ctx))
        if user is None:
            raise ToolError("unauthorized: missing or unknown Daily Times endpoint token")
        given = {"about": about[:1000] if about else about, "topics": topics, "subreddits": subreddits,
                 "tickers": tickers, "arxiv": arxiv, "feeds": feeds, "hacker_news": hacker_news,
                 "region": region.upper() if region else region}
        data = interests_of(user).model_dump() | {k: v for k, v in given.items() if v is not None} | {"set_by": "ai"}
        note = None
        if city:
            try:
                g = geocode(city)
            except FetchError:
                g, note = None, f"couldn't look up {city!r} right now; the city was not changed"
            if g:
                data["location"] = Location(name=g[0], lat=g[1], lon=g[2]).model_dump()
            elif not note:
                note = f"no place called {city!r}; the city was not changed"
        try:
            interests = Interests.model_validate(data)
        except ValidationError as e:
            raise ToolError(_friendly(e).replace("invalid brief", "invalid interests")) from None
        store.update_user(user, interests=interests.model_dump())
        saved = interests.model_dump(exclude={"set_by"})
        saved["location"] = interests.location.name if interests.location else None
        return {"saved": saved, "note": note, "review_url": edit_url or None}

    return mcp


class mcp_asgi:
    """ASGI endpoint for /mcp and /u/{token}/mcp. A class (not a function) so Starlette
    routes treat it as a raw ASGI app."""

    def __init__(self, store: Store, edit_url: str = ""):
        self.store, self.edit_url = store, edit_url

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        token = (scope.get("path_params") or {}).get("token")
        scope = dict(scope)
        if token:  # path token -> Authorization header, so the tool has one place to look
            headers = [(k, v) for k, v in scope["headers"] if k.lower() != b"authorization"]
            scope["headers"] = headers + [(b"authorization", f"Bearer {token}".encode())]
        scope["path"] = "/mcp"
        scope["raw_path"] = b"/mcp"
        server = build_mcp(self.store, self.edit_url)
        inner = server.streamable_http_app(stateless_http=True, json_response=True, streamable_http_path="/mcp",
                                           host="0.0.0.0")
        async with server.session_manager.run():
            await inner(scope, receive, send)


def _token_from(ctx: Context) -> str | None:
    headers = ctx.headers or {}
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    request = ctx.request_context.request
    params = getattr(request, "query_params", None)
    return params.get("token") if params is not None else None


def _friendly(e: ValidationError) -> str:
    lines = []
    for err in e.errors():
        loc = ".".join(str(p) for p in err["loc"])
        lines.append(f"{loc or 'brief'}: {err['msg']}")
    return "invalid brief: " + "; ".join(lines)
