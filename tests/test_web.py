"""The website end to end: sign-in, interests, endpoint, MCP/webhook, pairing, printing, export, delete."""

import re

import pytest
from fastapi.testclient import TestClient

from dailytimes.blocks import Brief
from dailytimes.relay import sessions
from dailytimes.relay.app import create_app
from dailytimes.relay.settings import Settings

MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
built = []


def fake_builder(interests, size, seen):
    built.append((interests, size, seen))
    return Brief.model_validate({"title": "Auto edition", "blocks": [
        {"type": "heading", "text": "Auto"}, {"type": "bullet", "text": "auto item", "url": "https://ex.com/auto"}]})


@pytest.fixture
def client():
    built.clear()
    settings = Settings.from_env(dev=True, public_url="http://testserver", secret="t" * 64, cron_secret="cron")
    app = create_app(":memory:", settings, auto_builder=fake_builder)
    with TestClient(app) as c:
        c.app_state = app.state
        yield c


def csrf_of(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def sign_up(c, email="reader@example.com", tz="Pacific/Auckland"):
    r = c.post("/login", data={"email": email})
    assert r.status_code == 200 and "Check your inbox" in r.text
    link = re.search(r'href="(http://testserver/auth/verify\?token=[^"]+)"', r.text).group(1)
    assert c.app_state.mailer.outbox[-1]["To"] == email
    page = c.get(link)
    token = re.search(r'name="token" value="([^"]+)"', page.text).group(1)
    r = c.post("/auth/verify", data={"token": token, "tz": tz}, follow_redirects=False)
    assert r.status_code == 303
    return r, token


def save_interests(c, csrf, **over):
    data = {"csrf": csrf, "topics": "human-computer interaction\nAI", "subreddits": "HCI", "tickers": "AAPL",
            "arxiv": "cs.HC", "feeds": "", "city": "", "region": "NZ", "timezone": "Pacific/Auckland",
            "auto_after": "00:00", "auto_enabled": "on", "edition_size": "page", **over}
    return c.post("/app/interests", data=data, follow_redirects=False)


def test_landing_and_privacy_pages(client):
    r = client.get("/")
    assert r.status_code == 200 and "Privacy, plainly" in r.text
    assert "No ads, no analytics" in client.get("/privacy").text
    assert client.get("/app", follow_redirects=False).headers["location"] == "/"


def test_sign_up_flow_and_single_use_link(client):
    r, token = sign_up(client)
    assert r.headers["location"] == "/app/interests?welcome=1"
    user = client.app_state.store.user_by_email("reader@example.com")
    assert user.timezone == "Pacific/Auckland"
    # link is single use
    r = client.post("/auth/verify", data={"token": token}, follow_redirects=False)
    assert r.status_code == 400 and "expired" in r.text
    # second sign-in is the same account and goes to the dashboard
    client.cookies.clear()
    store = client.app_state.store
    store.db.update("dt_logins", {"created_at": "2000-01-01T00:00:00+00:00"}, eq={"email": "reader@example.com"})
    r, _ = sign_up(client)
    assert r.headers["location"] == "/app"


def test_login_rejects_bad_email_and_rate_limits(client):
    assert client.post("/login", data={"email": "nope"}).status_code == 422
    client.post("/login", data={"email": "x@example.com"})
    r = client.post("/login", data={"email": "x@example.com"})
    assert "less than a minute ago" in r.text


def test_forms_need_csrf(client):
    sign_up(client)
    r = client.post("/app/interests", data={"csrf": "forged"}, follow_redirects=False)
    assert r.status_code == 403


def test_interests_suggest_and_save(client):
    sign_up(client)
    csrf = csrf_of(client.get("/app/interests?welcome=1").text)
    r = client.post("/app/interests/suggest", data={"csrf": csrf, "about": "I do HCI research and follow r/HCI, $AAPL"})
    assert "human-computer interaction" in r.text and "HCI" in r.text and "AAPL" in r.text
    r = save_interests(client, csrf, welcome="1")
    assert r.status_code == 303 and r.headers["location"] == "/app/connect?welcome=1"
    user = client.app_state.store.user_by_email("reader@example.com")
    assert user.interests["tickers"] == ["AAPL"] and user.interests["region"] == "NZ"
    r = save_interests(client, csrf, subreddits="not a sub!")
    assert r.status_code == 422 and "subreddit" in r.text


def test_dashboard_builds_auto_edition_and_serves_files(client):
    sign_up(client)
    csrf = csrf_of(client.get("/app").text)
    assert "Tell us what you" in client.get("/app").text  # no interests yet, nothing built
    save_interests(client, csrf)
    r = client.get("/app")
    assert "Auto edition, built from your interests" in r.text and len(built) == 1
    client.get("/app")
    assert len(built) == 1  # at most one attempt per day
    png = client.get("/app/edition.png?profile=a4&preview=1")
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    pdf = client.get("/app/edition.pdf?profile=letter")
    assert pdf.content[:5] == b"%PDF-"
    assert client.get("/app/edition.pdf?profile=bogus").status_code == 404


def test_endpoint_mcp_webhook_and_newest_wins(client):
    sign_up(client)
    csrf = csrf_of(client.get("/app/connect").text)
    r = client.post("/app/connect", data={"csrf": csrf})
    mcp = re.search(r'value="(http://testserver/u/[^"]+/mcp)"', r.text).group(1)
    webhook = re.search(r'value="(http://testserver/u/[^"]+/brief)"', r.text).group(1)
    assert "Then paste this prompt" in r.text and "print_brief" in r.text

    res = client.post(mcp, headers=MCP_HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "print_brief", "arguments": {"blocks": [{"type": "text", "text": "from mcp"}]}}}).json()["result"]
    assert res["isError"] is False
    r = client.post(webhook, content="# Hi\n- webhook item https://ex.com/w", headers={"content-type": "text/plain"})
    assert r.status_code == 202 and r.json()["superseded"] == 1
    assert "Sent by your AI via webhook." in client.get("/app").text

    # rotating the endpoint kills the old one
    client.post("/app/connect", data={"csrf": csrf})
    assert client.post(webhook, content="- x", headers={"content-type": "text/plain"}).status_code == 401
    res = client.post(mcp, headers=MCP_HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "print_brief", "arguments": {"blocks": [{"type": "text", "text": "x"}]}}}).json()["result"]
    assert res["isError"] and "unauthorized" in res["content"][0]["text"]


def test_pairing_printing_and_receipts(client):
    sign_up(client)
    csrf = csrf_of(client.get("/app").text)
    save_interests(client, csrf)
    r = client.post("/app/printers", data={"csrf": csrf, "name": "Kitchen"})
    code = re.search(r"--code ([A-Z0-9]{4}-[A-Z0-9]{4})", r.text).group(1)
    r = client.post("/v1/pair", json={"code": code.lower()})
    assert r.status_code == 200 and r.json()["name"] == "Kitchen"
    assert client.post("/v1/pair", json={"code": code}).status_code == 404  # single use
    h = {"Authorization": f"Bearer {r.json()['token']}"}

    b = client.get("/v1/device/brief", headers=h).json()
    assert b["source"] == "auto"
    assert client.post(f"/v1/device/briefs/{b['id']}/status", headers=h, json={"status": "printed"}).json()[
        "status"] == "printed"
    assert client.get("/v1/device/brief", headers=h).status_code == 204
    assert "Printed on Kitchen" in client.get("/app").text
    assert "Kitchen" in client.get("/app/printers").text

    device_id = client.app_state.store.devices(client.app_state.store.user_by_email("reader@example.com"))[0].id
    client.post(f"/app/printers/{device_id}/delete", data={"csrf": csrf})
    assert client.get("/v1/device/brief", headers=h).status_code == 401


def test_two_printers_each_print_once(client):
    sign_up(client)
    store = client.app_state.store
    user = store.user_by_email("reader@example.com")
    (_, t1), (_, t2) = store.create_device(user, "A"), store.create_device(user, "B")
    send = store.rotate_send_token(user)
    client.post("/v1/briefs", json={"blocks": [{"type": "text", "text": "hello"}]},
                headers={"Authorization": f"Bearer {send}"})
    for tok in (t1, t2):
        h = {"Authorization": f"Bearer {tok}"}
        b = client.get("/v1/device/brief", headers=h).json()
        client.post(f"/v1/device/briefs/{b['id']}/status", headers=h, json={"status": "printed"})
        assert client.get("/v1/device/brief", headers=h).status_code == 204


def test_export_signout_everywhere_and_delete(client):
    sign_up(client)
    csrf = csrf_of(client.get("/app/account").text)
    data = client.get("/app/account/export").json()
    assert data["user"]["email"] == "reader@example.com" and "token_hash" not in str(data)

    cookie = client.cookies.get(sessions.COOKIE)
    client.post("/app/account/signout-everywhere", data={"csrf": csrf})
    client.cookies.set(sessions.COOKIE, cookie)
    assert client.get("/app", follow_redirects=False).status_code == 303  # old session is dead

    sign_up(client, "other@example.com")
    csrf = csrf_of(client.get("/app/account").text)
    assert client.post("/app/account/delete", data={"csrf": csrf, "confirm": "wrong"}).status_code == 422
    r = client.post("/app/account/delete", data={"csrf": csrf, "confirm": "other@example.com"})
    assert "account is gone" in r.text
    assert client.app_state.store.user_by_email("other@example.com") is None


def test_cron_requires_secret_and_builds(client):
    assert client.get("/cron/daily").status_code == 401
    sign_up(client)
    save_interests(client, csrf_of(client.get("/app/account").text))
    r = client.get("/cron/daily", headers={"Authorization": "Bearer cron"})
    assert r.status_code == 200 and r.json()["auto_editions"] == 1


def test_session_cookie_tamper_and_expiry():
    tok = sessions.make("s", "usr_1", 0)
    assert sessions.read("s", tok) == ("usr_1", 0)
    assert sessions.read("other", tok) is None
    assert sessions.read("s", tok[:-1] + ("0" if tok[-1] != "0" else "1")) is None
    assert sessions.read("s", sessions.make("s", "u", 0, now=0)) is None  # expired
    assert sessions.check_csrf("s", "u", 1, sessions.csrf_token("s", "u", 1))
    assert not sessions.check_csrf("s", "u", 2, sessions.csrf_token("s", "u", 1))
