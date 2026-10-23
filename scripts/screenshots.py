"""Screenshots of the website for the README, with a fictional account.

Runs a throwaway local server (SQLite in a temp dir), seeds it, and drives Chrome.
    uv pip install playwright && .venv/bin/python scripts/screenshots.py
Uses the installed Google Chrome (channel="chrome"); no browser download.
"""

import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

from dailytimes.blocks import Brief
from dailytimes.relay import sessions
from dailytimes.relay.store import Store

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"
SECRET = "s" * 64


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    return s.getsockname()[1]


def seed(db: Path) -> str:
    store = Store(str(db))
    u = store.create_user("reader@example.com", "Pacific/Auckland")
    u = store.update_user(u, edition_size="page", auto_after="06:30", interests={
        "about": "I'm a UX researcher in Wellington. I follow AI, climate tech and rugby, read r/userexperience, "
                 "and hold $AAPL.",
        "topics": ["user experience design", "artificial intelligence", "climate tech", "rugby"],
        "subreddits": ["userexperience"], "tickers": ["AAPL", "MSFT"], "arxiv": ["cs.HC"], "feeds": [],
        "hacker_news": True, "location": {"name": "Wellington", "lat": -41.29, "lon": 174.78}, "region": "NZ"})
    store.rotate_send_token(u)
    kitchen, _ = store.create_device(u, "Kitchen receipt printer")
    store.create_device(u, "Office laser")
    store.db.update("dt_devices", {"last_seen_at": "2026-10-23T18:42:00+00:00"}, eq={"id": kitchen.id})
    brief = Brief.model_validate(json.loads((ROOT / "examples" / "full_page_brief.json").read_text()))
    r = store.enqueue(u, brief.model_copy(update={"title": "Morning briefing"}), "mcp")
    store.report(kitchen, r.brief_id, "printed")
    return u.id


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "shots.db"
    user_id = seed(db)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {"DAILYTIMES_DB": str(db), "DAILYTIMES_SECRET": SECRET, "DAILYTIMES_PUBLIC_URL": "https://times.example.com",
           "DAILYTIMES_OPERATOR": "Example Operator", "PATH": "/usr/bin:/bin"}
    server = subprocess.Popen([sys.executable, "-m", "dailytimes.cli", "serve", "--host", "127.0.0.1", "--port",
                               str(port), "--log-level", "warning"], env=env)
    try:
        for _ in range(100):
            try:
                if httpx.get(base + "/healthz").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            shot = lambda page, name, full=False: page.screenshot(path=str(OUT / name), full_page=full)  # noqa: E731

            pub = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=2)
            pub.goto(base + "/"); pub.wait_for_load_state("networkidle")
            shot(pub, "site-landing.png")
            phone = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=3)
            phone.goto(base + "/"); phone.wait_for_load_state("networkidle")
            shot(phone, "site-landing-phone.png")

            ctx = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=2)
            ctx.add_cookies([{"name": sessions.COOKIE, "value": sessions.make(SECRET, user_id, 0),
                              "url": base}])
            page = ctx.new_page()
            page.goto(base + "/app"); page.wait_for_load_state("networkidle")
            shot(page, "site-dashboard.png")
            page.goto(base + "/app/interests"); page.wait_for_load_state("networkidle")
            shot(page, "site-interests.png", full=True)
            page.goto(base + "/app/connect")
            page.click("text=Make a new endpoint"); page.wait_for_load_state("networkidle")
            shot(page, "site-connect.png")
            page.goto(base + "/app/printers")
            page.fill("#name", "Hall printer"); page.click("text=Get pairing code"); page.wait_for_load_state("networkidle")
            shot(page, "site-printers.png")
            browser.close()
    finally:
        server.terminate()
    for f in sorted(OUT.glob("site-*.png")):
        print(f.relative_to(ROOT))


if __name__ == "__main__":
    main()
