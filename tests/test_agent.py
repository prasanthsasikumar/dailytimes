"""Agent against a real in-process relay, printing through the file adapter."""

import datetime as dt

import httpx
import pytest
from fastapi.testclient import TestClient

from dailytimes.agent.config import AgentConfig, load_config
from dailytimes.agent.runner import Agent, RelayClient
from dailytimes.relay.app import create_app
from dailytimes.relay.settings import Settings

UTC = dt.timezone.utc


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def relay():
    app = create_app(":memory:", Settings.from_env(public_url="http://testserver", secret="x" * 64))
    with TestClient(app) as c:
        _, _, token, send = app.state.store.register_device_account("hall", "UTC")
        c.headers_for = {"Authorization": f"Bearer {send}"}  # AI agent sending briefs
        c.device_headers = {"Authorization": f"Bearer {token}"}
        c.token = token
        yield c


def make_agent(relay, tmp_path, clock, **rules):
    cfg = AgentConfig.model_validate({
        "relay": {"url": "http://testserver", "token": relay.token},
        "outputs": [
            {"profile": "thermal-58mm", "adapter": "file", "options": {"directory": str(tmp_path / "out")}},
            {"profile": "a4", "adapter": "file", "options": {"directory": str(tmp_path / "out")}},
        ],
        "rules": {"print_after": "06:00", "cooldown_minutes": 10, **rules},
        "state_file": str(tmp_path / "state.json"),
    })
    return Agent(cfg, relay=RelayClient("http://testserver", relay.token, http=relay), clock=clock)


def send(relay, sample_dict):
    r = relay.post("/v1/briefs", json=sample_dict, headers=relay.headers_for)
    assert r.status_code == 202
    return r.json()["brief_id"]


def test_end_to_end_print_once_and_report(relay, tmp_path, sample_dict):
    clock = Clock(dt.datetime.now(UTC).replace(hour=7, minute=0))
    agent = make_agent(relay, tmp_path, clock)
    assert agent.tz.key == "UTC"  # learned from the relay

    assert agent.handle_presence("t")["reason"].startswith("no brief yet")
    bid = send(relay, sample_dict)
    clock.t += dt.timedelta(minutes=11)
    res = agent.handle_presence("t")
    assert res["action"] == "print" and res["printed"] and res["brief_id"] == bid
    files = sorted(p.name for p in (tmp_path / "out").iterdir())
    day = clock.t.date().isoformat()
    assert files == [f"daily-times-{day}-a4.pdf", f"daily-times-{day}-thermal-58mm.png"]

    # relay knows, and the agent won't print again today
    assert relay.get(f"/v1/device/briefs/{bid}", headers=relay.device_headers).json()["status"] == "printed"
    clock.t += dt.timedelta(hours=2)
    assert agent.handle_presence("t")["reason"] == "already printed today"


def test_state_survives_restart(relay, tmp_path, sample_dict):
    clock = Clock(dt.datetime.now(UTC).replace(hour=8))
    send(relay, sample_dict)
    assert make_agent(relay, tmp_path, clock).handle_presence("t")["printed"]
    sample_dict["title"] = "Second edition"
    send(relay, sample_dict)  # new brief for the same day
    clock.t += dt.timedelta(hours=1)
    assert make_agent(relay, tmp_path, clock).handle_presence("t")["reason"] == "already printed today"


def test_failed_output_reports_failed_and_retries_after_cooldown(relay, tmp_path, sample_dict):
    clock = Clock(dt.datetime.now(UTC).replace(hour=9))
    agent = make_agent(relay, tmp_path, clock)
    agent.outputs = [(agent.outputs[0][0], __import__("dailytimes.adapters", fromlist=["x"]).get_adapter(
        "escpos", connection="network"))]  # no host -> AdapterError
    bid = send(relay, sample_dict)
    res = agent.handle_presence("t")
    assert res["printed"] is False and "host" in res["outputs"][0]["detail"]
    b = relay.get(f"/v1/device/briefs/{bid}", headers=relay.device_headers).json()
    assert b["status"] == "failed" and b["attempts"] == 1
    assert relay.get("/v1/device/brief", headers=relay.device_headers).json()["id"] == bid  # still printable
    assert agent.handle_presence("t")["reason"].startswith("cooldown")


def test_placeholder_fallback(relay, tmp_path):
    clock = Clock(dt.datetime.now(UTC).replace(hour=11))
    agent = make_agent(relay, tmp_path, clock, fallback="placeholder", fallback_after="10:00")
    res = agent.handle_presence("t")
    assert res["action"] == "placeholder" and res["printed"] and res["brief_id"] is None
    assert agent.state.printed_on == clock.t.date()


def test_relay_down_uses_cached_brief(relay, tmp_path, sample_dict):
    clock = Clock(dt.datetime.now(UTC).replace(hour=7))
    agent = make_agent(relay, tmp_path, clock)
    send(relay, sample_dict)
    assert agent.poll() is not None

    class Down(RelayClient):
        def pending(self):
            raise httpx.ConnectError("down")

        def report(self, *a, **k):
            raise httpx.ConnectError("down")

    agent.relay = Down("http://testserver", relay.token, http=relay)
    res = agent.handle_presence("t")
    assert res["printed"] is True


def test_config_file_and_token_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DAILYTIMES_TOKEN", "dt_env")
    f = tmp_path / "agent.yaml"
    f.write_text("relay: {url: 'http://r'}\noutputs: [{profile: a4, adapter: file}]\n"
                 "rules: {print_after: 06:30}\npresence: {backend: gpio, options: {pin: 17}}\n")
    cfg = load_config(f)
    assert cfg.relay.token == "dt_env" and cfg.rules.print_after == dt.time(6, 30)
    monkeypatch.delenv("DAILYTIMES_TOKEN")
    with pytest.raises(Exception, match="token"):
        load_config(f)
