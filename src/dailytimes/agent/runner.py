"""The device agent: polls the relay, waits for presence, prints, reports back."""

from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Callable
from zoneinfo import ZoneInfo

import httpx

from ..adapters import AdapterError, get_adapter, print_brief
from ..blocks import Brief
from ..render import brief_from_relay, load_profile
from .config import AgentConfig
from .presence import make_backend
from .rules import AgentState, decide, gate

log = logging.getLogger("dailytimes.agent")


class RelayClient:
    def __init__(self, url: str, token: str, http: httpx.Client | None = None):
        self.url = url.rstrip("/")
        self.http = http or httpx.Client(timeout=20)
        self.headers = {"Authorization": f"Bearer {token}"}

    def device(self) -> dict:
        r = self.http.get(f"{self.url}/v1/device", headers=self.headers)
        r.raise_for_status()
        return r.json()

    def pending(self) -> dict | None:
        r = self.http.get(f"{self.url}/v1/device/brief", headers=self.headers)
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()

    def report(self, brief_id: str, status: str, detail: str | None = None) -> dict:
        r = self.http.post(f"{self.url}/v1/device/briefs/{brief_id}/status", headers=self.headers,
                           json={"status": status, "detail": detail})
        r.raise_for_status()
        return r.json()


class Agent:
    def __init__(self, cfg: AgentConfig, relay: RelayClient | None = None,
                 clock: Callable[[], dt.datetime] | None = None):
        self.cfg = cfg
        self.relay = relay or RelayClient(cfg.relay.url, cfg.relay.token)
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self.state = AgentState.load(cfg.state_path)
        self.cached: dict | None = None
        self.lock = threading.Lock()
        self.outputs = [(load_profile(o.profile), get_adapter(o.adapter, dry_run=o.dry_run, **o.options))
                        for o in cfg.outputs]
        self._tz: ZoneInfo | None = ZoneInfo(cfg.timezone) if cfg.timezone else None

    @property
    def tz(self) -> ZoneInfo:
        if self._tz is None:
            try:
                self._tz = ZoneInfo(self.relay.device()["timezone"])
            except Exception:
                log.warning("couldn't read device timezone from relay; using UTC for now")
                return ZoneInfo("UTC")
        return self._tz

    def now(self) -> dt.datetime:
        return self.clock().astimezone(self.tz)

    # ------------------------------------------------------------ relay

    def poll(self) -> dict | None:
        try:
            self.cached = self.relay.pending()
        except httpx.HTTPError as e:
            log.warning("poll failed: %s", e)
        return self.cached

    def _fresh_brief(self, today: dt.date) -> dict | None:
        try:
            return self.relay.pending()
        except httpx.HTTPError as e:
            log.warning("relay unreachable (%s); using cached brief", e)
            c = self.cached
            return c if c and c.get("date") == today.isoformat() else None

    # ------------------------------------------------------------ presence

    def handle_presence(self, source: str = "presence") -> dict:
        with self.lock:
            now = self.now()
            blocked = gate(now, self.state, self.cfg.rules)
            if blocked:
                log.info("presence (%s): %s", source, blocked.reason)
                return {"action": blocked.action, "reason": blocked.reason}
            brief_data = self._fresh_brief(now.date())
            decision = decide(now, self.state, self.cfg.rules, brief_data is not None)
            self.state.last_attempt = now
            result = {"action": decision.action, "reason": decision.reason}
            try:
                if decision.action == "print":
                    result.update(self._print(brief_from_relay(brief_data), now.date(), brief_data["id"]))
                elif decision.action == "placeholder":
                    result.update(self._print(placeholder_brief(), now.date(), None))
                elif decision.action == "skip":
                    self.state.printed_on = now.date()
            finally:
                self.state.save(self.cfg.state_path)
            log.info("presence (%s): %s", source, result)
            return result

    def _print(self, brief: Brief, today: dt.date, brief_id: str | None) -> dict:
        results = []
        for profile, adapter in self.outputs:
            try:
                r = print_brief(brief, profile, adapter, brief_id=brief_id, date=today)
                results.append({"adapter": adapter.name, "profile": profile.name, "ok": r.ok, "detail": r.detail,
                                "dry_run": r.dry_run})
            except (AdapterError, OSError, RuntimeError) as e:
                results.append({"adapter": adapter.name, "profile": profile.name, "ok": False, "detail": str(e)})
        ok = any(r["ok"] for r in results)
        failures = "; ".join(f"{r['adapter']}: {r['detail']}" for r in results if not r["ok"]) or None
        if ok:
            self.state.printed_on = today
            self.state.last_brief_id = brief_id
        if brief_id:
            try:
                self.relay.report(brief_id, "printed" if ok else "failed", failures)
            except httpx.HTTPError as e:
                log.warning("couldn't report status to relay: %s", e)
        return {"printed": ok, "outputs": results, "brief_id": brief_id}


def placeholder_brief() -> Brief:
    return Brief.model_validate({"blocks": [
        {"type": "text", "text": "No brief arrived from your assistant yet today."},
        {"type": "small", "text": "Check that the daily task ran and can reach the Daily Times relay."},
    ]})


def run_agent(cfg: AgentConfig, once: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    agent = Agent(cfg)
    if once:
        agent.poll()
        print(agent.handle_presence("once"))
        return
    backend = make_backend(cfg.presence.backend, agent.handle_presence, **cfg.presence.options)
    backend.start()
    stop = threading.Event()
    log.info("agent running: %d output(s), presence=%s, poll every %ss", len(agent.outputs),
             cfg.presence.backend, cfg.poll_interval_seconds)
    try:
        while not stop.is_set():
            b = agent.poll()
            log.debug("polled: %s", b and b.get("id"))
            stop.wait(cfg.poll_interval_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        backend.stop()
