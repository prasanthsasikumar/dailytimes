"""MCP server (streamable HTTP) exposing one tool: `print_brief`.

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

from ..blocks import MAX_BLOCKS, MAX_TOTAL_CHARS, Block, Brief
from starlette.types import Receive, Scope, Send

from .store import Store, StoreError

TOOL_DESCRIPTION = f"""\
Queue today's printed brief ("The Daily Times") for the user's printer. It prints
when someone next walks up to the printer. Calling again the same day replaces
the earlier brief (the newest one wins). The nameplate and date are added
automatically; don't add your own masthead.

Editorial rules:
- Concrete facts only: names, numbers, times, places. No filler, no motivational lines.
- Never invent anything. If you could not verify an item, leave it out.
- One source link per item, in that item's `url`. No link means no claim that needs one.
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
- text {{text, url?}} short paragraph
- bullet {{text, url?}} one list item
- row {{left, right}} e.g. "09:30 Design review" | "Room 4", or "EXMPL" | "$231.40 +1.2%"
- divider {{}}
- qr {{url, caption?}} at most one or two per brief, for the link worth opening on a phone
- small {{text}} small print: sources, caveats

`title` is an optional edition line such as "Thursday briefing". `date` (YYYY-MM-DD)
defaults to today in the printer's timezone.
"""


def build_mcp(store: Store) -> MCPServer:
    mcp = MCPServer(
        "dailytimes",
        title="The Daily Times",
        instructions="Use print_brief once a day to send the user's printed morning brief.",
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

    return mcp


class mcp_asgi:
    """ASGI endpoint for /mcp and /u/{token}/mcp. A class (not a function) so Starlette
    routes treat it as a raw ASGI app."""

    def __init__(self, store: Store):
        self.store = store

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        token = (scope.get("path_params") or {}).get("token")
        scope = dict(scope)
        if token:  # path token -> Authorization header, so the tool has one place to look
            headers = [(k, v) for k, v in scope["headers"] if k.lower() != b"authorization"]
            scope["headers"] = headers + [(b"authorization", f"Bearer {token}".encode())]
        scope["path"] = "/mcp"
        scope["raw_path"] = b"/mcp"
        server = build_mcp(self.store)
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
