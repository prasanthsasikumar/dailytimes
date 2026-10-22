"""MCP server (streamable HTTP) exposing one tool: `print_brief`.

Auth: the device token, as `Authorization: Bearer <token>` or `?token=<token>`
on the MCP URL (for connector UIs that only take a URL).
"""

from __future__ import annotations

import datetime as dt

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from ..blocks import MAX_BLOCKS, MAX_TOTAL_CHARS, Block, Brief
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
        device = store.device_for_token(_token_from(ctx))
        if device is None:
            raise ToolError("unauthorized: missing or unknown device token")
        try:
            brief = Brief(blocks=blocks, title=title, date=date)
        except ValidationError as e:
            raise ToolError(_friendly(e)) from None
        try:
            result = store.enqueue(device, brief, source="mcp")
        except StoreError as e:
            raise ToolError(str(e)) from None
        return result.as_dict()

    return mcp


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
