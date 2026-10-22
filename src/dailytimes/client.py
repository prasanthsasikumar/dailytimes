"""Send a brief to a Daily Times endpoint, the way an AI agent integration would."""

from __future__ import annotations

import asyncio
import json

import httpx

from .blocks import Brief


def send_webhook(url: str, brief: Brief, token: str | None = None) -> dict:
    """POST to the unique webhook URL (…/u/<token>/brief), or to /v1/briefs with a Bearer token."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = httpx.post(url, content=brief.model_dump_json(exclude_none=True), headers=headers, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"server rejected brief: {r.status_code} {r.text}")
    return r.json()


def send_mcp(url: str, brief: Brief) -> dict:
    """Call the `print_brief` MCP tool at the unique MCP URL (…/u/<token>/mcp)."""
    from mcp.client import Client

    args = json.loads(brief.model_dump_json(exclude_none=True))

    async def go() -> dict:
        async with Client(url) as c:
            res = await c.call_tool("print_brief", args)
            text = res.content[0].text if res.content else ""
            if res.is_error:
                raise RuntimeError(f"print_brief failed: {text}")
            return json.loads(text)

    return asyncio.run(go())


def send(endpoint: str, brief: Brief) -> dict:
    """Pick MCP or webhook from the endpoint URL."""
    if endpoint.rstrip("/").endswith("/mcp"):
        return send_mcp(endpoint, brief)
    return send_webhook(endpoint, brief)
