"""Vercel entry point: the whole Daily Times server as one ASGI function.

Vercel's rewrite (vercel.json) sends every request here as /api/index and drops the
original path, so the rewrite passes it along as `__path`; we restore it before the
app sees the request.
"""

import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dailytimes.relay.app import create_app  # noqa: E402

_app = create_app()


def restore_path(scope: dict) -> dict:
    if scope["type"] not in ("http", "websocket"):
        return scope
    params = parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
    original = next((v for k, v in params if k == "__path"), None)
    if original is None:
        return scope
    scope = dict(scope)
    path = "/" + original.lstrip("/")
    scope["path"] = path
    scope["raw_path"] = path.encode()
    scope["query_string"] = urlencode([(k, v) for k, v in params if k != "__path"]).encode("latin-1")
    return scope


async def app(scope, receive, send):
    await _app(restore_path(scope), receive, send)
