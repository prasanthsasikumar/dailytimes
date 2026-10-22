"""Clients for sending briefs to a relay (what an assistant integration does)."""

from __future__ import annotations

import asyncio
import json

import httpx

from .blocks import Brief


def send_webhook(relay: str, token: str, brief: Brief) -> dict:
    r = httpx.post(f"{relay.rstrip('/')}/v1/briefs", content=brief.model_dump_json(exclude_none=True),
                   headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"relay rejected brief: {r.status_code} {r.text}")
    return r.json()


def send_mcp(relay: str, token: str, brief: Brief) -> dict:
    """Call the `print_brief` MCP tool exactly as an assistant would."""
    from mcp.client import Client

    args = json.loads(brief.model_dump_json(exclude_none=True))

    async def go() -> dict:
        async with Client(f"{relay.rstrip('/')}/mcp?token={token}") as c:
            res = await c.call_tool("print_brief", args)
            text = res.content[0].text if res.content else ""
            if res.is_error:
                raise RuntimeError(f"print_brief failed: {text}")
            return json.loads(text)

    return asyncio.run(go())
