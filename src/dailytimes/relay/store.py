"""SQLite persistence: devices, the per-device brief queue, and 60-day dedup history.

Queue rules
- One brief per device per local date is live: enqueueing a newer one for the
  same date marks older pending ones `superseded` ("newest brief of the day wins").
- Re-sending identical content for the same date is a no-op (`duplicate`).
- Pending briefs for past dates become `expired` when the device next polls.
- When the agent reports `printed`, each linked item's fingerprint goes into
  history; items seen in the last 60 days are dropped from future briefs.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from ..blocks import CONTENT_TYPES, Brief, BlockList, fingerprint

DEDUP_DAYS = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS briefs (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(id),
    brief_date TEXT NOT NULL,
    title TEXT,
    blocks TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    printed_at TEXT
);
CREATE INDEX IF NOT EXISTS briefs_queue ON briefs(device_id, brief_date, status);
CREATE TABLE IF NOT EXISTS history (
    device_id TEXT NOT NULL REFERENCES devices(id),
    fingerprint TEXT NOT NULL,
    printed_on TEXT NOT NULL,
    PRIMARY KEY (device_id, fingerprint)
);
"""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


@dataclass
class Device:
    id: str
    name: str
    timezone: str

    def today(self, now: dt.datetime | None = None) -> dt.date:
        now = now or dt.datetime.now(dt.timezone.utc)
        return now.astimezone(ZoneInfo(self.timezone)).date()


@dataclass
class EnqueueResult:
    brief_id: str | None
    status: str  # queued | duplicate | empty_after_dedup
    date: dt.date
    dropped: list[str] = field(default_factory=list)
    superseded: int = 0

    def as_dict(self) -> dict:
        return {
            "brief_id": self.brief_id,
            "status": self.status,
            "date": self.date.isoformat(),
            "dropped_duplicates": self.dropped,
            "superseded": self.superseded,
        }


class StoreError(ValueError):
    pass


class Store:
    def __init__(self, path: str = ":memory:"):
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(SCHEMA)
        self._lock = threading.RLock()

    # ------------------------------------------------------------ devices

    def create_device(self, name: str, timezone: str = "UTC") -> tuple[Device, str]:
        try:
            ZoneInfo(timezone)
        except Exception as e:
            raise StoreError(f"unknown timezone {timezone!r}") from e
        device = Device(id="dev_" + uuid.uuid4().hex[:12], name=name, timezone=timezone)
        token = "dt_" + secrets.token_urlsafe(24)
        with self._lock:
            self._db.execute(
                "INSERT INTO devices (id, name, token_hash, timezone, created_at) VALUES (?,?,?,?,?)",
                (device.id, name, hash_token(token), timezone, _now()),
            )
        return device, token

    def device_for_token(self, token: str | None) -> Device | None:
        if not token:
            return None
        row = self._db.execute(
            "SELECT id, name, timezone FROM devices WHERE token_hash = ?", (hash_token(token),)
        ).fetchone()
        return Device(**dict(row)) if row else None

    def get_device(self, device_id: str) -> Device | None:
        row = self._db.execute("SELECT id, name, timezone FROM devices WHERE id = ?", (device_id,)).fetchone()
        return Device(**dict(row)) if row else None

    # ------------------------------------------------------------ queue

    def enqueue(self, device: Device, brief: Brief, source: str, now: dt.datetime | None = None) -> EnqueueResult:
        today = device.today(now)
        date = brief.date or today
        if not (today - dt.timedelta(days=1) <= date <= today + dt.timedelta(days=7)):
            raise StoreError(f"brief date {date} must be between yesterday and a week from today ({today})")

        with self._lock:
            blocks, dropped = self._drop_seen(device, brief, today)
            if not any(b.type in CONTENT_TYPES for b in blocks):
                return EnqueueResult(None, "empty_after_dedup", date, dropped)
            final = Brief.model_construct(blocks=blocks, title=brief.title, date=date)
            chash = final.content_hash()
            same = self._db.execute(
                "SELECT id FROM briefs WHERE device_id=? AND brief_date=? AND content_hash=? "
                "AND status IN ('pending','printed')",
                (device.id, date.isoformat(), chash),
            ).fetchone()
            if same:
                return EnqueueResult(same["id"], "duplicate", date, dropped)
            cur = self._db.execute(
                "UPDATE briefs SET status='superseded' WHERE device_id=? AND brief_date=? AND status='pending'",
                (device.id, date.isoformat()),
            )
            brief_id = "br_" + uuid.uuid4().hex[:16]
            self._db.execute(
                "INSERT INTO briefs (id, device_id, brief_date, title, blocks, content_hash, source, status, created_at)"
                " VALUES (?,?,?,?,?,?,?, 'pending', ?)",
                (brief_id, device.id, date.isoformat(), brief.title, json.dumps(final.blocks_json()), chash, source, _now()),
            )
            return EnqueueResult(brief_id, "queued", date, dropped, cur.rowcount)

    def _drop_seen(self, device: Device, brief: Brief, today: dt.date):
        since = (today - dt.timedelta(days=DEDUP_DAYS)).isoformat()
        seen = {
            r["fingerprint"]
            for r in self._db.execute(
                "SELECT fingerprint FROM history WHERE device_id=? AND printed_on >= ?", (device.id, since)
            )
        }
        kept, dropped = [], []
        for b in brief.blocks:
            fp = fingerprint(b)
            if fp and fp in seen:
                dropped.append(fp)
            else:
                kept.append(b)
        return (_prune_empty_sections(kept) if dropped else kept), dropped

    def pending(self, device: Device, now: dt.datetime | None = None) -> dict | None:
        today = device.today(now).isoformat()
        with self._lock:
            self._db.execute(
                "UPDATE briefs SET status='expired' WHERE device_id=? AND status='pending' AND brief_date < ?",
                (device.id, today),
            )
            row = self._db.execute(
                "SELECT * FROM briefs WHERE device_id=? AND status='pending' AND brief_date=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (device.id, today),
            ).fetchone()
        return _brief_row(row) if row else None

    def get_brief(self, device: Device, brief_id: str) -> dict | None:
        row = self._db.execute("SELECT * FROM briefs WHERE id=? AND device_id=?", (brief_id, device.id)).fetchone()
        return _brief_row(row) if row else None

    def report(self, device: Device, brief_id: str, status: str, detail: str | None = None,
               now: dt.datetime | None = None) -> dict:
        if status not in ("printed", "failed"):
            raise StoreError("status must be 'printed' or 'failed'")
        with self._lock:
            row = self._db.execute("SELECT * FROM briefs WHERE id=? AND device_id=?", (brief_id, device.id)).fetchone()
            if not row:
                raise KeyError(brief_id)
            if status == "failed":
                self._db.execute(
                    "UPDATE briefs SET attempts=attempts+1, last_error=? WHERE id=?", (detail, brief_id)
                )
            else:
                stamp = _now()
                self._db.execute(
                    "UPDATE briefs SET status='printed', printed_at=?, attempts=attempts+1 WHERE id=?",
                    (stamp, brief_id),
                )
                today = device.today(now)
                for b in BlockList.validate_python(json.loads(row["blocks"])):
                    fp = fingerprint(b)
                    if fp:
                        self._db.execute(
                            "INSERT INTO history (device_id, fingerprint, printed_on) VALUES (?,?,?) "
                            "ON CONFLICT(device_id, fingerprint) DO UPDATE SET printed_on=excluded.printed_on",
                            (device.id, fp, today.isoformat()),
                        )
                self.prune_history(device, today)
        return self.get_brief(device, brief_id)

    def prune_history(self, device: Device, today: dt.date) -> int:
        cutoff = (today - dt.timedelta(days=DEDUP_DAYS)).isoformat()
        cur = self._db.execute("DELETE FROM history WHERE device_id=? AND printed_on < ?", (device.id, cutoff))
        return cur.rowcount

    def history(self, device: Device) -> list[dict]:
        return [dict(r) for r in self._db.execute(
            "SELECT fingerprint, printed_on FROM history WHERE device_id=? ORDER BY printed_on DESC", (device.id,)
        )]


def _brief_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["blocks"] = json.loads(d["blocks"])
    d["date"] = d.pop("brief_date")
    del d["content_hash"]
    return d


def _prune_empty_sections(blocks: list) -> list:
    """Drop headings (and trailing dividers) left with no content after dedup."""
    out: list = []
    for i, b in enumerate(blocks):
        if b.type == "heading":
            nxt = next((n for n in blocks[i + 1:] if n.type != "divider"), None)
            if nxt is None or (nxt.type == "heading" and nxt.level <= b.level):
                continue
        out.append(b)
    while out and out[-1].type == "divider":
        out.pop()
    return out
