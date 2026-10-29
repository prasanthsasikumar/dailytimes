import json
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from dailytimes.relay.app import create_app
from dailytimes.relay.settings import Settings

MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


@pytest.fixture
def client():
    settings = Settings.from_env(public_url="http://testserver", secret="x" * 64, admin_token="admin",
                                 inbound_secret="s3cret", inbound_domain="in.test")
    app = create_app(":memory:", settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def device(client):
    r = client.post("/v1/devices", json={"name": "kitchen"}, headers={"Authorization": "Bearer admin"})
    assert r.status_code == 201
    data = r.json()
    data["h"] = {"Authorization": f"Bearer {data['token']}"}  # printer
    data["s"] = {"Authorization": f"Bearer {data['send_token']}"}  # AI agents
    return data


def mcp_call(client, args, token=None, query_token=None):
    url = "/mcp" + (f"?token={query_token}" if query_token else "")
    headers = dict(MCP_HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = client.post(url, headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "print_brief", "arguments": args}})
    assert r.status_code == 200
    return r.json()["result"]


def test_registration_requires_admin(client):
    open_settings = Settings.from_env(public_url="http://testserver", secret="x" * 64, admin_token="")
    with TestClient(create_app(":memory:", open_settings)) as c:
        assert c.post("/v1/devices", json={"name": "x"}).status_code == 403  # no admin token: website only
    assert client.post("/v1/devices", json={"name": "x"}).status_code == 401
    r = client.post("/v1/devices", json={"name": "x", "timezone": "Nowhere/Land"},
                    headers={"Authorization": "Bearer admin"})
    assert r.status_code == 422


def test_register_returns_endpoints(device):
    assert device["token"].startswith("dtd_") and device["send_token"].startswith("dts_")
    assert device["mcp_url"] == f"http://testserver/u/{device['send_token']}/mcp"
    assert device["webhook_url"] == f"http://testserver/u/{device['send_token']}/brief"
    assert device["email_address"] == f"brief+{device['send_token']}@in.test"


def test_webhook_json_roundtrip(client, device, sample_dict):
    r = client.post("/v1/briefs", json=sample_dict, headers=device["s"])
    assert r.status_code == 202 and r.json()["status"] == "queued"
    assert client.post("/v1/briefs", json=sample_dict, headers=device["h"]).status_code == 401  # printer can't send
    p = client.get("/v1/device/brief", headers=device["h"]).json()
    assert p["title"] == "Sample edition" and len(p["blocks"]) == len(sample_dict["blocks"])
    assert p["blocks"][0] == {"type": "heading", "text": "Today", "level": 1}


def test_webhook_rejects_bad_auth_and_bad_body(client, device):
    assert client.post("/v1/briefs", json={"blocks": []}).status_code == 401
    assert client.post("/v1/briefs", json={"blocks": []}, headers={"Authorization": "Bearer dt_x"}).status_code == 401
    r = client.post("/v1/briefs", json={"blocks": [{"type": "nope"}]}, headers=device["s"])
    assert r.status_code == 422
    r = client.post("/v1/briefs", content=b"{not json", headers={**device["s"], "content-type": "application/json"})
    assert r.status_code == 400


def test_webhook_plaintext(client, device):
    r = client.post(device["webhook_url"].replace("http://testserver", ""), content="# Today\n- Ship it https://ex.com/a\n",
                    headers={"content-type": "text/plain", "x-brief-title": "Text edition"})
    assert r.status_code == 202
    p = client.get("/v1/device/brief", headers=device["h"]).json()
    assert p["title"] == "Text edition" and p["blocks"][1]["url"] == "https://ex.com/a"


def test_mcp_lists_tool_with_editorial_rules(client, device):
    r = client.post("/mcp", headers={**MCP_HEADERS, **device["s"]},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    tools = r.json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["print_brief", "set_interests"]
    desc = tools[0]["description"]
    for rule in ("Concrete facts only", "Never invent", "One source link per item", "Keep it short"):
        assert rule in desc
    schema = json.dumps(tools[0]["inputSchema"])
    for t in ("heading", "text", "bullet", "row", "divider", "qr", "small"):
        assert f'"{t}"' in schema


def test_mcp_path_token(client, device):
    path = device["mcp_url"].replace("http://testserver", "")
    r = client.post(path, headers=MCP_HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "print_brief", "arguments": {"blocks": [{"type": "text", "text": "via path"}]}}})
    assert r.status_code == 200 and r.json()["result"]["isError"] is False
    r = client.post("/u/dts_wrong/mcp", headers=MCP_HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "print_brief", "arguments": {"blocks": [{"type": "text", "text": "x"}]}}})
    assert r.json()["result"]["isError"]


def test_mcp_print_brief_header_and_query_token(client, device, sample_dict):
    res = mcp_call(client, sample_dict, token=device["send_token"])
    assert res["isError"] is False
    first = json.loads(res["content"][0]["text"])
    assert first["status"] == "queued"
    res = mcp_call(client, {"blocks": [{"type": "text", "text": "newer"}]}, query_token=device["send_token"])
    second = json.loads(res["content"][0]["text"])
    assert second["superseded"] == 1
    assert client.get("/v1/device/brief", headers=device["h"]).json()["id"] == second["brief_id"]


def test_mcp_rejects_unauthorized_and_invalid(client, device):
    res = mcp_call(client, {"blocks": [{"type": "text", "text": "x"}]})
    assert res["isError"] and "unauthorized" in res["content"][0]["text"]
    res = mcp_call(client, {"blocks": [{"type": "text", "text": "x"}]}, token=device["token"])
    assert res["isError"] and "unauthorized" in res["content"][0]["text"]  # printer token can't send
    res = mcp_call(client, {"blocks": [{"type": "text", "text": "x", "url": "not a url"}]}, token=device["send_token"])
    assert res["isError"]
    res = mcp_call(client, {"blocks": [{"type": "heading", "text": "only"}]}, token=device["send_token"])
    assert res["isError"] and "no content" in res["content"][0]["text"]


def _mail(to, body, subject="Inbox edition"):
    m = EmailMessage()
    m["From"] = "assistant@example.com"
    m["To"] = to
    m["Subject"] = subject
    m.set_content(body)
    return m.as_bytes()


def test_inbound_email_text_and_json(client, device):
    raw = _mail(device["email_address"], "# Mail\n- From email https://ex.com/m\n\n-- \nSent from my phone")
    r = client.post("/v1/inbound/email", content=raw, headers={"x-inbound-secret": "s3cret"})
    assert r.status_code == 202, r.text
    p = client.get("/v1/device/brief", headers=device["h"]).json()
    assert p["source"] == "email" and p["title"] == "Inbox edition"
    assert [b["type"] for b in p["blocks"]] == ["heading", "bullet"]

    raw = _mail(device["email_address"], json.dumps({"blocks": [{"type": "text", "text": "json mail"}]}))
    r = client.post("/v1/inbound/email", content=raw, headers={"x-inbound-secret": "s3cret"})
    assert r.status_code == 202
    assert client.get("/v1/device/brief", headers=device["h"]).json()["blocks"][0]["text"] == "json mail"


def test_inbound_email_rejects(client, device):
    raw = _mail(device["email_address"], "- x")
    assert client.post("/v1/inbound/email", content=raw).status_code == 401
    raw = _mail("someone@in.test", "- x")
    assert client.post("/v1/inbound/email", content=raw, headers={"x-inbound-secret": "s3cret"}).status_code == 422
    raw = _mail("brief+dt_unknown@in.test", "- x")
    assert client.post("/v1/inbound/email", content=raw, headers={"x-inbound-secret": "s3cret"}).status_code == 401


def test_device_poll_and_report(client, device, sample_dict):
    assert client.get("/v1/device/brief", headers=device["h"]).status_code == 204
    bid = client.post("/v1/briefs", json=sample_dict, headers=device["s"]).json()["brief_id"]
    r = client.post(f"/v1/device/briefs/{bid}/status", json={"status": "printed"}, headers=device["h"])
    assert r.status_code == 200 and r.json()["status"] == "printed"
    assert client.get("/v1/device/brief", headers=device["h"]).status_code == 204
    assert len(client.get("/v1/device/history", headers=device["h"]).json()["items"]) == 5  # qr repeats one url
    assert client.post("/v1/device/briefs/br_x/status", json={"status": "printed"},
                       headers=device["h"]).status_code == 404
    assert client.get("/v1/device", headers=device["h"]).json()["name"] == "kitchen"
