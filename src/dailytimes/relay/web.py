"""The website: sign in by email link, set interests, connect AI agents, pair printers,
see today's edition, export or delete your data."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from zoneinfo import available_timezones

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import ValidationError

from ..autobrief import Interests, Location, suggest
from ..autobrief.fetch import FetchError
from ..autobrief.sources import geocode
from ..render import brief_from_relay, load_profile, render
from . import sessions
from .autoedition import interests_of
from .store import StoreError, User

TEMPLATES = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                        autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,}$")
PREVIEW_PROFILES = {"a4": "a4", "letter": "letter", "receipt": "thermal-80mm", "receipt58": "thermal-58mm"}
TIMEZONES = sorted(available_timezones())


def _lines(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"[\n,]", text or "") if x.strip()]


def attach_web(app: FastAPI) -> None:
    store, settings, mailer = app.state.store, app.state.settings, app.state.mailer

    # ------------------------------------------------------------ helpers

    def current(request: Request) -> User | None:
        got = sessions.read(settings.secret, request.cookies.get(sessions.COOKIE))
        if not got:
            return None
        user = store.get_user(got[0])
        return user if user and user.session_epoch == got[1] else None

    def page(request: Request, template: str, user: User | None = None, /, status: int = 200, **ctx) -> HTMLResponse:
        csrf = sessions.csrf_token(settings.secret, user.id, user.session_epoch) if user else ""
        html = TEMPLATES.get_template(template).render(
            user=user, csrf=csrf, settings=settings, path=request.url.path, today=dt.date.today(), **ctx)
        return HTMLResponse(html, status_code=status, headers={"Cache-Control": "no-store"})

    def need_user(request: Request) -> User:
        user = current(request)
        if user is None:
            raise HTTPException(303, headers={"Location": "/"})
        return user

    async def need_post(request: Request) -> tuple[User, dict]:
        user = need_user(request)
        form = dict(await request.form())
        if not sessions.check_csrf(settings.secret, user.id, user.session_epoch, form.get("csrf")):
            raise HTTPException(403, "form expired, go back and try again")
        return user, form

    def sign_in(user: User, to: str = "/app") -> RedirectResponse:
        r = RedirectResponse(to, status_code=303)
        r.set_cookie(sessions.COOKIE, sessions.make(settings.secret, user.id, user.session_epoch),
                     max_age=sessions.MAX_AGE, httponly=True, secure=settings.secure_cookies, samesite="lax")
        return r

    # ------------------------------------------------------------ public pages

    @app.get("/", response_class=HTMLResponse)
    def landing(request: Request):
        user = current(request)
        if user:
            return RedirectResponse("/app", status_code=303)
        return page(request, "landing.html")

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy(request: Request):
        return page(request, "privacy.html", current(request))

    @app.post("/login", response_class=HTMLResponse)
    def login(request: Request, email: str = Form(...)):
        email = email.strip().lower()
        if not EMAIL.match(email):
            return page(request, "landing.html", error="That doesn't look like an email address.", email=email,
                        status=422)
        try:
            token = store.create_login(email)
        except StoreError as e:
            return page(request, "check_email.html", email=email, note=str(e))
        link = f"{settings.public_url}/auth/verify?token={token}"
        mailer.send(email, "Your Daily Times sign-in link",
                    f"Open this link to sign in to The Daily Times:\n\n{link}\n\n"
                    "It works once and expires in 15 minutes. If you didn't ask for it, ignore this email.\n")
        dev_link = link if (settings.dev and not settings.mail_configured) else None
        return page(request, "check_email.html", email=email, dev_link=dev_link)

    @app.get("/auth/verify", response_class=HTMLResponse)
    def verify_page(request: Request, token: str = ""):
        # A button, not an automatic sign-in: mail scanners that prefetch links can't use up the token.
        return page(request, "verify.html", token=token)

    @app.post("/auth/verify")
    def verify(request: Request, token: str = Form(...), tz: str = Form("UTC")):
        email = store.redeem_login(token)
        if not email:
            return page(request, "landing.html", error="That sign-in link has expired or was already used. "
                                                        "Ask for a new one.", status=400)
        user = store.user_by_email(email)
        new = user is None
        if new:
            user = store.create_user(email, tz if tz in TIMEZONES else "UTC")
            store.new_send_token(user, settings.secret)
        return sign_in(user, "/app/connect?welcome=1" if new else "/app")

    @app.post("/logout")
    async def logout(request: Request):
        await need_post(request)
        r = RedirectResponse("/", status_code=303)
        r.delete_cookie(sessions.COOKIE)
        return r

    # ------------------------------------------------------------ today's edition

    @app.get("/app", response_class=HTMLResponse)
    def dashboard(request: Request):
        user = need_user(request)
        app.state.auto(user)
        brief = store.todays_brief(user)
        receipts = store.receipts(brief["id"]) if brief else []
        devices = {d.id: d for d in store.devices(user)}
        printed = [(devices[r["device_id"]].name, r) for r in receipts if r["device_id"] in devices]
        return page(request, "dashboard.html", user, brief=brief, printed=printed, devices=list(devices.values()),
                    interests=interests_of(user), has_endpoint=store.has_send_token(user),
                    local_now=user.now())

    @app.post("/app/edition/build")
    async def build_now(request: Request):
        user, _ = await need_post(request)
        app.state.auto(user, force=True)
        return RedirectResponse("/app", status_code=303)

    @app.get("/app/edition.{fmt}")
    def edition_file(request: Request, fmt: str, profile: str = "a4", preview: int = 0):
        user = need_user(request)
        brief = store.todays_brief(user)
        if brief is None or fmt not in ("png", "pdf") or profile not in PREVIEW_PROFILES:
            raise HTTPException(404, "no edition today")
        prof = load_profile(PREVIEW_PROFILES[profile])
        if preview and fmt == "png" and prof.type == "page":
            prof = prof.model_copy(update={"dpi": 110})
        out = render(brief_from_relay(brief), prof, ("pdf",) if fmt == "pdf" else ("raster",))
        name = f"daily-times-{brief['date']}-{profile}.{fmt}"
        if fmt == "pdf":
            return Response(out.pdf, media_type="application/pdf",
                            headers={"Content-Disposition": f'inline; filename="{name}"', "Cache-Control": "no-store"})
        return Response(out.png_pages()[0], media_type="image/png", headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------ interests

    @app.get("/app/interests", response_class=HTMLResponse)
    def interests_page(request: Request, welcome: int = 0):
        user = need_user(request)
        return page(request, "interests.html", user, i=interests_of(user), welcome=welcome, timezones=TIMEZONES)

    @app.post("/app/interests/suggest", response_class=HTMLResponse)
    async def interests_suggest(request: Request):
        user, form = await need_post(request)
        about = (form.get("about") or "").strip()[:1000]
        got, place, method = suggest(about)
        current_i = interests_of(user)
        merged = got.model_copy(update={"location": current_i.location, "region": current_i.region})
        if place and not current_i.location:
            try:
                g = geocode(place)
                if g:
                    merged = merged.model_copy(update={"location": Location(name=g[0], lat=g[1], lon=g[2])})
            except FetchError:
                pass
        return page(request, "interests.html", user, i=merged, suggested=method, timezones=TIMEZONES,
                    welcome=int(form.get("welcome") or 0))

    @app.post("/app/interests", response_class=HTMLResponse)
    async def interests_save(request: Request):
        user, form = await need_post(request)
        old = interests_of(user)
        location = old.location
        city = (form.get("city") or "").strip()
        error = None
        if not city:
            location = None
        elif not location or city.lower() != location.name.lower():
            try:
                g = geocode(city)
                location = Location(name=g[0], lat=g[1], lon=g[2]) if g else None
                if not g:
                    error = f"Couldn't find a place called {city!r}; weather is off until you fix it."
            except FetchError:
                error = "Couldn't look up that place right now; try saving again."
        try:
            interests = Interests.model_validate({
                "about": (form.get("about") or "")[:1000],
                "topics": _lines(form.get("topics")),
                "subreddits": _lines(form.get("subreddits")),
                "tickers": _lines(form.get("tickers")),
                "arxiv": _lines(form.get("arxiv")),
                "feeds": _lines(form.get("feeds")),
                "hacker_news": form.get("hacker_news") == "on",
                "location": location.model_dump() if location else None,
                "region": (form.get("region") or "US").upper()[:2],
                "set_by": "you",
            })
        except ValidationError as e:
            msgs = "; ".join(err["msg"].removeprefix("Value error, ") for err in e.errors())
            return page(request, "interests.html", user, i=old, error=msgs, timezones=TIMEZONES, status=422)
        try:
            user = store.update_user(
                user, interests=interests.model_dump(), timezone=form.get("timezone") or user.timezone,
                edition_size=form.get("edition_size", "page"), auto_enabled=form.get("auto_enabled") == "on",
                auto_after=form.get("auto_after") or "05:30")
        except StoreError as e:
            return page(request, "interests.html", user, i=interests, error=str(e), timezones=TIMEZONES, status=422)
        if error:
            return page(request, "interests.html", user, i=interests, error=error, timezones=TIMEZONES)
        return RedirectResponse("/app/printers" if form.get("welcome") == "1" else "/app?saved=1",
                                status_code=303)

    # ------------------------------------------------------------ connect AI agents

    @app.get("/app/connect", response_class=HTMLResponse)
    def connect_page(request: Request, welcome: int = 0, rotated: int = 0):
        user = need_user(request)
        token = store.send_token(user, settings.secret)
        if token is None and not store.has_send_token(user):
            token = store.new_send_token(user, settings.secret)
        return page(request, "connect.html", user, ep=app.state.endpoints(token) if token else None,
                    welcome=welcome, rotated=rotated, prompt=setup_prompt(user),
                    email_enabled=bool(settings.inbound_secret), **connect_status(user))

    @app.post("/app/connect")
    async def connect_rotate(request: Request):
        user, _ = await need_post(request)
        store.new_send_token(user, settings.secret)
        return RedirectResponse("/app/connect?rotated=1", status_code=303)

    @app.get("/app/connect/status")
    def connect_status_json(request: Request):
        return JSONResponse(connect_status(need_user(request)), headers={"Cache-Control": "no-store"})

    def connect_status(user: User) -> dict:
        """What the connect page waits for: the AI filling in interests, and its first edition."""
        brief = store.todays_brief(user)
        return {"interests_by_ai": interests_of(user).set_by == "ai",
                "edition_by_ai": bool(brief and brief["source"] != "auto")}

    def setup_prompt(user: User) -> str:
        i = interests_of(user)
        size = ("an A4/Letter page printer: fill exactly one full page, 35-50 blocks" if user.edition_size == "page"
                else "a receipt printer: keep it to 10-20 blocks")
        bits = []
        if i.about:
            bits.append(f"What I told Daily Times about myself: {i.about}")
        if i.topics:
            bits.append("Topics: " + ", ".join(i.topics))
        if i.subreddits:
            bits.append("Subreddits: " + ", ".join("r/" + s for s in i.subreddits))
        if i.tickers:
            bits.append("Tickers: " + ", ".join(i.tickers))
        if i.location:
            bits.append(f"I live in {i.location.name}.")
        return TEMPLATES.get_template("setup_prompt.txt").render(
            size=size, interests="\n".join(bits) or "(use what you know about me)", timezone=user.timezone)

    # ------------------------------------------------------------ printers

    @app.get("/app/printers", response_class=HTMLResponse)
    def printers_page(request: Request):
        user = need_user(request)
        return page(request, "printers.html", user, devices=store.devices(user), code=None)

    @app.post("/app/printers", response_class=HTMLResponse)
    async def printers_add(request: Request):
        user, form = await need_post(request)
        code = store.create_pairing(user, (form.get("name") or "Printer").strip()[:60])
        return page(request, "printers.html", user, devices=store.devices(user), code=code,
                    name=form.get("name") or "Printer")

    @app.post("/app/printers/{device_id}/delete")
    async def printers_delete(request: Request, device_id: str):
        user, _ = await need_post(request)
        store.delete_device(user, device_id)
        return RedirectResponse("/app/printers", status_code=303)

    # ------------------------------------------------------------ account & privacy

    @app.get("/app/account", response_class=HTMLResponse)
    def account_page(request: Request):
        user = need_user(request)
        return page(request, "account.html", user)

    @app.get("/app/account/export")
    def account_export(request: Request):
        user = need_user(request)
        data = json.dumps(store.export_user(user), indent=2, default=str)
        return Response(data, media_type="application/json", headers={
            "Content-Disposition": f'attachment; filename="daily-times-export-{dt.date.today()}.json"'})

    @app.post("/app/account/signout-everywhere")
    async def signout_everywhere(request: Request):
        user, _ = await need_post(request)
        store.update_user(user, session_epoch=user.session_epoch + 1)
        r = RedirectResponse("/", status_code=303)
        r.delete_cookie(sessions.COOKIE)
        return r

    @app.post("/app/account/delete", response_class=HTMLResponse)
    async def account_delete(request: Request):
        user, form = await need_post(request)
        if (form.get("confirm") or "").strip().lower() != (user.email or "").lower():
            return page(request, "account.html", user, error="Type your email address exactly to confirm.",
                        status=422)
        store.delete_user(user)
        r = page(request, "deleted.html")
        r.delete_cookie(sessions.COOKIE)
        return r

    @app.exception_handler(HTTPException)
    async def http_errors(request: Request, exc: HTTPException):
        if exc.status_code == 303 and exc.headers and "Location" in exc.headers:
            return RedirectResponse(exc.headers["Location"], status_code=303)
        if request.url.path.startswith(("/v1/", "/u/", "/cron", "/mcp")) or "text/html" not in request.headers.get(
                "accept", ""):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
        return page(request, "error.html", current(request), status=exc.status_code, message=exc.detail)
