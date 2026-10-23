"""The Daily Times server: website, relay API, MCP endpoint, printer API and cron.

One ASGI app, deployable to Vercel (serverless; data in Supabase) or anywhere
that runs Python (Docker, a Pi; data in SQLite).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from starlette.routing import Route

from ..blocks import Brief, parse_plaintext
from ..render.fonts import FONT_DIR
from .autoedition import ensure_auto_edition
from .db import Database, database_from_env
from .inbound import InboundError, parse_mime
from .mailer import Mailer
from .mcp_server import mcp_asgi
from .settings import Settings
from .store import Device, Store, StoreError, User


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    timezone: str = "UTC"


class PairRequest(BaseModel):
    code: str = Field(min_length=8, max_length=12)


class StatusReport(BaseModel):
    status: str
    detail: str | None = Field(default=None, max_length=500)


def _bearer(authorization: str) -> str | None:
    return authorization[7:].strip() if authorization.lower().startswith("bearer ") else None


def create_app(db: Database | str | None = None, settings: Settings | None = None, auto_builder=None) -> FastAPI:
    settings = settings or Settings.from_env()
    if db is None:
        db = database_from_env()
    store = Store(db)
    mailer = Mailer(settings)

    app = FastAPI(title="The Daily Times", version="0.2.0", docs_url=None, redoc_url=None)
    app.state.store, app.state.settings, app.state.mailer = store, settings, mailer
    app.state.auto_builder = auto_builder

    def auto(user: User, force: bool = False):
        kw = {"builder": app.state.auto_builder} if app.state.auto_builder else {}
        return ensure_auto_edition(store, user, force=force, **kw)

    app.state.auto = auto

    def sender(authorization: str = Header(default="")) -> User:
        user = store.user_for_send_token(_bearer(authorization))
        if user is None:
            raise HTTPException(401, "missing or unknown endpoint token")
        return user

    def device_auth(authorization: str = Header(default="")) -> Device:
        device = store.device_for_token(_bearer(authorization), touch=True)
        if device is None:
            raise HTTPException(401, "missing or unknown printer token")
        return device

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # ---------------------------------------------------------- headless registration (CLI)

    @app.post("/v1/devices", status_code=201)
    def register(req: RegisterRequest, authorization: str = Header(default="")):
        """Admin-only account+printer creation for installs without the website."""
        if settings.admin_token:
            if authorization != f"Bearer {settings.admin_token}":
                raise HTTPException(401, "registration needs the admin token")
        elif not settings.dev:
            raise HTTPException(403, "set DAILYTIMES_ADMIN_TOKEN to allow CLI registration, or sign up on the website")
        try:
            user, device, device_token, send_token = store.register_device_account(req.name, req.timezone)
        except StoreError as e:
            raise HTTPException(422, str(e))
        return {"user_id": user.id, "device_id": device.id, "name": device.name, "timezone": user.timezone,
                "token": device_token, "send_token": send_token, **endpoints(send_token)}

    def endpoints(send_token: str) -> dict:
        base = settings.public_url
        return {"mcp_url": f"{base}/u/{send_token}/mcp", "webhook_url": f"{base}/u/{send_token}/brief",
                "email_address": f"brief+{send_token}@{settings.inbound_domain}"}

    app.state.endpoints = endpoints

    # ---------------------------------------------------------- AI agents send briefs

    async def _accept(request: Request, user: User, source: str) -> dict:
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
        except UnicodeDecodeError:
            raise HTTPException(400, "body is not UTF-8")
        return _enqueue(user, brief, source)

    def _enqueue(user: User, brief: Brief, source: str) -> dict:
        try:
            return store.enqueue(user, brief, source).as_dict()
        except StoreError as e:
            raise HTTPException(422, str(e))

    @app.post("/v1/briefs", status_code=202)
    async def webhook(request: Request, user: User = Depends(sender)):
        """Webhook with `Authorization: Bearer <endpoint token>`: JSON brief or markdown-ish text."""
        return await _accept(request, user, "webhook")

    @app.post("/u/{token}/brief", status_code=202)
    async def webhook_path(token: str, request: Request):
        """Same, with the token in the URL (the unique endpoint)."""
        user = store.user_for_send_token(token)
        if user is None:
            raise HTTPException(401, "unknown endpoint")
        return await _accept(request, user, "webhook")

    @app.post("/v1/inbound/email", status_code=202)
    async def inbound_email(request: Request, x_inbound_secret: str = Header(default="")):
        """Raw RFC 822 message from a mail provider's inbound webhook."""
        if settings.inbound_secret and x_inbound_secret != settings.inbound_secret:
            raise HTTPException(401, "bad inbound secret")
        try:
            msg = parse_mime(await request.body())
        except (InboundError, ValueError) as e:
            raise HTTPException(422, str(e))
        user = store.user_for_send_token(msg.token)
        if user is None:
            raise HTTPException(401, "unknown endpoint token in recipient address")
        return _enqueue(user, msg.brief, "email")

    # ---------------------------------------------------------- printers

    @app.post("/v1/pair")
    def pair(req: PairRequest):
        got = store.redeem_pairing(req.code)
        if got is None:
            raise HTTPException(404, "unknown or expired pairing code")
        device, token = got
        return {"device_id": device.id, "name": device.name, "timezone": device.timezone, "token": token,
                "server": settings.public_url}

    @app.get("/v1/device")
    def me(device: Device = Depends(device_auth)):
        return {"device_id": device.id, "name": device.name, "timezone": device.timezone,
                "today": device.today().isoformat()}

    @app.get("/v1/device/brief")
    def pending(device: Device = Depends(device_auth)):
        user = store.get_user(device.user_id)
        if user:
            auto(user)
        brief = store.pending(device)
        if brief is None:
            return Response(status_code=204)
        return brief

    @app.get("/v1/device/briefs/{brief_id}")
    def get_brief(brief_id: str, device: Device = Depends(device_auth)):
        brief = store.get_brief(device.user_id, brief_id)
        if brief is None:
            raise HTTPException(404, "no such brief")
        receipt = next((r for r in store.receipts(brief_id) if r["device_id"] == device.id), None)
        brief["edition_status"] = brief["status"]  # pending | superseded, for the whole account
        brief["status"] = receipt["status"] if receipt else "pending"  # this printer's view
        brief["attempts"] = receipt["attempts"] if receipt else 0
        brief["last_error"] = receipt["detail"] if receipt else None
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
        user = store.get_user(device.user_id)
        return {"items": store.history(user) if user else []}

    # ---------------------------------------------------------- cron (Vercel Cron or any scheduler)

    @app.get("/cron/daily")
    def cron(authorization: str = Header(default="")):
        if not settings.cron_secret or authorization != f"Bearer {settings.cron_secret}":
            raise HTTPException(401, "cron needs CRON_SECRET")
        built = sum(1 for u in store.auto_users() if auto(u))
        store.purge()
        return {"auto_editions": built}

    # ---------------------------------------------------------- MCP + website

    mcp = mcp_asgi(store)
    app.router.routes.append(Route("/mcp", endpoint=mcp, methods=["GET", "POST", "DELETE"]))
    app.router.routes.append(Route("/u/{token}/mcp", endpoint=mcp, methods=["GET", "POST", "DELETE"]))

    from .web import attach_web

    attach_web(app)
    app.mount("/static/fonts", StaticFiles(directory=str(FONT_DIR)), name="fonts")
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
    return app


def main() -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run(create_app(), host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8787")))
