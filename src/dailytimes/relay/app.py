"""The relay: FastAPI app with device accounts, webhook, inbound email, device API and MCP."""

from __future__ import annotations

import contextlib
import json
import os

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError

from ..blocks import Brief, parse_plaintext
from .inbound import InboundError, parse_mime
from .mcp_server import build_mcp
from .store import Device, Store, StoreError


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    timezone: str = "UTC"


class StatusReport(BaseModel):
    status: str
    detail: str | None = Field(default=None, max_length=500)


def create_app(db_path: str | None = None, admin_token: str | None = None, public_url: str | None = None,
               inbound_domain: str | None = None, inbound_secret: str | None = None) -> FastAPI:
    db_path = db_path or os.environ.get("DAILYTIMES_DB", "dailytimes.db")
    admin_token = admin_token if admin_token is not None else os.environ.get("DAILYTIMES_ADMIN_TOKEN", "")
    public_url = (public_url or os.environ.get("DAILYTIMES_PUBLIC_URL", "http://localhost:8787")).rstrip("/")
    inbound_domain = inbound_domain or os.environ.get("DAILYTIMES_INBOUND_DOMAIN", "in.example.com")
    inbound_secret = inbound_secret if inbound_secret is not None else os.environ.get("DAILYTIMES_INBOUND_SECRET", "")

    store = Store(db_path)
    mcp = build_mcp(store)
    # host="0.0.0.0": the relay sits behind a public hostname, so the SDK's localhost-only
    # DNS-rebinding guard would reject real traffic. Every call is token-authenticated instead.
    mcp_app = mcp.streamable_http_app(stateless_http=True, json_response=True, streamable_http_path="/mcp",
                                      host="0.0.0.0")

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="Daily Times relay", version="0.1.0", lifespan=lifespan)
    app.state.store = store

    def device_auth(authorization: str = Header(default="")) -> Device:
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else None
        device = store.device_for_token(token)
        if device is None:
            raise HTTPException(401, "missing or unknown device token")
        return device

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post("/v1/devices", status_code=201)
    def register(req: RegisterRequest, authorization: str = Header(default="")):
        if admin_token and authorization != f"Bearer {admin_token}":
            raise HTTPException(401, "registration needs the admin token")
        try:
            device, token = store.create_device(req.name, req.timezone)
        except StoreError as e:
            raise HTTPException(422, str(e))
        return {
            "device_id": device.id,
            "name": device.name,
            "timezone": device.timezone,
            "token": token,
            "mcp_url": f"{public_url}/mcp?token={token}",
            "webhook_url": f"{public_url}/v1/briefs",
            "email_address": f"brief+{token}@{inbound_domain}",
        }

    @app.get("/v1/device")
    def me(device: Device = Depends(device_auth)):
        return {"device_id": device.id, "name": device.name, "timezone": device.timezone,
                "today": device.today().isoformat()}

    # ---------------------------------------------------------- sending briefs

    @app.post("/v1/briefs", status_code=202)
    async def webhook(request: Request, device: Device = Depends(device_auth)):
        """Accept a brief as JSON (`{"blocks": [...]}`) or as markdown-ish text/plain."""
        body = await request.body()
        ctype = request.headers.get("content-type", "application/json").split(";")[0].strip()
        try:
            if ctype in ("text/plain", "text/markdown"):
                brief = parse_plaintext(body.decode("utf-8"), title=request.headers.get("x-brief-title"))
            else:
                brief = Brief.model_validate(json.loads(body or b"null"))
        except json.JSONDecodeError:
            raise HTTPException(400, "body is not valid JSON")
        except ValidationError as e:
            raise HTTPException(422, json.loads(e.json(include_url=False, include_context=False)))
        return _enqueue(device, brief, "webhook")

    @app.post("/v1/inbound/email", status_code=202)
    async def inbound_email(request: Request, x_inbound_secret: str = Header(default="")):
        """Raw RFC 822 message from a mail provider's inbound webhook."""
        if inbound_secret and x_inbound_secret != inbound_secret:
            raise HTTPException(401, "bad inbound secret")
        try:
            msg = parse_mime(await request.body())
        except (InboundError, ValueError) as e:
            raise HTTPException(422, str(e))
        device = store.device_for_token(msg.token)
        if device is None:
            raise HTTPException(401, "unknown device token in recipient address")
        return _enqueue(device, msg.brief, "email")

    def _enqueue(device: Device, brief: Brief, source: str) -> dict:
        try:
            return store.enqueue(device, brief, source).as_dict()
        except StoreError as e:
            raise HTTPException(422, str(e))

    # ---------------------------------------------------------- device agent API

    @app.get("/v1/device/brief")
    def pending(device: Device = Depends(device_auth)):
        brief = store.pending(device)
        if brief is None:
            return Response(status_code=204)
        return brief

    @app.get("/v1/device/briefs/{brief_id}")
    def get_brief(brief_id: str, device: Device = Depends(device_auth)):
        brief = store.get_brief(device, brief_id)
        if brief is None:
            raise HTTPException(404, "no such brief")
        return brief

    @app.post("/v1/device/briefs/{brief_id}/status")
    def report(brief_id: str, body: StatusReport, device: Device = Depends(device_auth)):
        try:
            return store.report(device, brief_id, body.status, body.detail)
        except KeyError:
            raise HTTPException(404, "no such brief")
        except StoreError as e:
            raise HTTPException(422, str(e))

    @app.get("/v1/device/history")
    def history(device: Device = Depends(device_auth)):
        return {"items": store.history(device)}

    app.router.routes.extend(mcp_app.routes)
    return app


def main() -> None:  # pragma: no cover - thin wrapper
    import uvicorn

    uvicorn.run(create_app(), host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8787")))
