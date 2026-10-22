"""Accounts, printers, the daily edition queue and the 60-day dedup history.

Model
- A **user** has interests, a timezone and one write-only **send token** that any of
  their AI agents use to submit briefs (MCP, webhook, email).
- A user has any number of **devices** (printer agents), each with its own token that
  can only read today's brief and report print status. Devices are added by pairing code.
- Briefs belong to the user: every device prints the same edition. Per day, the newest
  brief wins; each device prints at most one brief per local day (tracked in receipts).
- When any device prints, the brief's links go into the user's history; links printed
  in the last 60 days are dropped from later briefs.
- Briefs older than RETENTION_DAYS are deleted.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from ..blocks import CONTENT_TYPES, BlockList, Brief, fingerprint
from .db import Database, DuplicateError, SQLiteDB

DEDUP_DAYS = 60
RETENTION_DAYS = 30
LOGIN_MINUTES = 15
PAIRING_MINUTES = 15
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="microseconds")


@dataclass
class User:
    id: str
    email: str | None
    timezone: str
    interests: dict = field(default_factory=dict)
    edition_size: str = "page"
    auto_enabled: bool = True
    auto_after: str = "05:30"
    auto_built_on: str | None = None
    session_epoch: int = 0
    created_at: str = ""

    @classmethod
    def from_row(cls, r: dict) -> "User":
        return cls(id=r["id"], email=r["email"], timezone=r["timezone"], interests=json.loads(r["interests"] or "{}"),
                   edition_size=r["edition_size"], auto_enabled=bool(r["auto_enabled"]), auto_after=r["auto_after"],
                   auto_built_on=r["auto_built_on"], session_epoch=int(r["session_epoch"]), created_at=r["created_at"])

    def now(self, now: dt.datetime | None = None) -> dt.datetime:
        return (now or _utcnow()).astimezone(ZoneInfo(self.timezone))

    def today(self, now: dt.datetime | None = None) -> dt.date:
        return self.now(now).date()


@dataclass
class Device:
    id: str
    user_id: str
    name: str
    timezone: str = "UTC"
    created_at: str = ""
    last_seen_at: str | None = None

    def today(self, now: dt.datetime | None = None) -> dt.date:
        return (now or _utcnow()).astimezone(ZoneInfo(self.timezone)).date()


@dataclass
class EnqueueResult:
    brief_id: str | None
    status: str  # queued | duplicate | empty_after_dedup
    date: dt.date
    dropped: list[str] = field(default_factory=list)
    superseded: int = 0

    def as_dict(self) -> dict:
        return {"brief_id": self.brief_id, "status": self.status, "date": self.date.isoformat(),
                "dropped_duplicates": self.dropped, "superseded": self.superseded}


class StoreError(ValueError):
    pass


def _check_tz(tz: str) -> str:
    try:
        ZoneInfo(tz)
    except Exception as e:
        raise StoreError(f"unknown timezone {tz!r}") from e
    return tz


class Store:
    def __init__(self, db: Database | str = ":memory:"):
        self.db = SQLiteDB(db) if isinstance(db, str) else db
        self._purged_at: dt.datetime | None = None

    # ------------------------------------------------------------ users

    def create_user(self, email: str | None, timezone: str = "UTC", now: dt.datetime | None = None) -> User:
        uid = "usr_" + uuid.uuid4().hex[:16]
        row = {"id": uid, "email": email.lower().strip() if email else None, "created_at": _iso(now or _utcnow()),
               "timezone": _check_tz(timezone)}
        self.db.insert("dt_users", row)
        return self.get_user(uid)

    def get_user(self, user_id: str) -> User | None:
        r = self.db.one("dt_users", id=user_id)
        return User.from_row(r) if r else None

    def user_by_email(self, email: str) -> User | None:
        r = self.db.one("dt_users", email=email.lower().strip())
        return User.from_row(r) if r else None

    def has_send_token(self, user: User) -> bool:
        r = self.db.one("dt_users", id=user.id)
        return bool(r and r["send_token_hash"])

    def update_user(self, user: User, **values) -> User:
        allowed = {"timezone", "interests", "edition_size", "auto_enabled", "auto_after", "auto_built_on",
                   "session_epoch"}
        bad = set(values) - allowed
        if bad:
            raise StoreError(f"can't update {', '.join(bad)}")
        if "timezone" in values:
            _check_tz(values["timezone"])
        if "interests" in values and not isinstance(values["interests"], str):
            values["interests"] = json.dumps(values["interests"])
        if "auto_enabled" in values:
            values["auto_enabled"] = int(bool(values["auto_enabled"]))
        if "edition_size" in values and values["edition_size"] not in ("page", "slip"):
            raise StoreError("edition_size must be page or slip")
        self.db.update("dt_users", values, eq={"id": user.id})
        return self.get_user(user.id)

    def auto_users(self) -> list[User]:
        return [User.from_row(r) for r in self.db.select("dt_users", eq={"auto_enabled": 1})]

    def rotate_send_token(self, user: User) -> str:
        token = "dts_" + secrets.token_urlsafe(24)
        self.db.update("dt_users", {"send_token_hash": hash_token(token)}, eq={"id": user.id})
        return token

    def user_for_send_token(self, token: str | None) -> User | None:
        if not token:
            return None
        r = self.db.one("dt_users", send_token_hash=hash_token(token))
        return User.from_row(r) if r else None

    def delete_user(self, user: User) -> None:
        """Erase everything we hold about a user."""
        for b in self.db.select("dt_briefs", eq={"user_id": user.id}):
            self.db.delete("dt_receipts", eq={"brief_id": b["id"]})
        for t in ("dt_briefs", "dt_history", "dt_devices", "dt_pairings"):
            self.db.delete(t, eq={"user_id": user.id})
        if user.email:
            self.db.delete("dt_logins", eq={"email": user.email})
        self.db.delete("dt_users", eq={"id": user.id})

    def export_user(self, user: User) -> dict:
        devices = [{k: d[k] for k in ("id", "name", "created_at", "last_seen_at")}
                   for d in self.db.select("dt_devices", eq={"user_id": user.id})]
        briefs = [_brief_row(b) for b in self.db.select("dt_briefs", eq={"user_id": user.id}, order="created_at")]
        return {
            "user": {"id": user.id, "email": user.email, "timezone": user.timezone, "created_at": user.created_at,
                     "interests": user.interests, "edition_size": user.edition_size,
                     "auto_enabled": user.auto_enabled, "auto_after": user.auto_after},
            "devices": devices,
            "briefs": briefs,
            "history": self.history(user),
        }

    # ------------------------------------------------------------ sign-in links

    def create_login(self, email: str, now: dt.datetime | None = None) -> str:
        now = now or _utcnow()
        email = email.lower().strip()
        recent = self.db.select("dt_logins", eq={"email": email}, gte={"created_at": _iso(now - dt.timedelta(seconds=60))})
        if recent:
            raise StoreError("a sign-in link was sent less than a minute ago; check your inbox")
        token = secrets.token_urlsafe(32)
        self.db.insert("dt_logins", {"token_hash": hash_token(token), "email": email, "created_at": _iso(now),
                                     "expires_at": _iso(now + dt.timedelta(minutes=LOGIN_MINUTES))})
        return token

    def redeem_login(self, token: str, now: dt.datetime | None = None) -> str | None:
        """Single use: returns the email, or None if unknown/expired/used."""
        now = now or _utcnow()
        r = self.db.one("dt_logins", token_hash=hash_token(token))
        if not r:
            return None
        self.db.delete("dt_logins", eq={"token_hash": r["token_hash"]})
        if r["expires_at"] < _iso(now):
            return None
        return r["email"]

    # ------------------------------------------------------------ devices

    def create_device(self, user: User, name: str, now: dt.datetime | None = None) -> tuple[Device, str]:
        did = "dev_" + uuid.uuid4().hex[:12]
        token = "dtd_" + secrets.token_urlsafe(24)
        self.db.insert("dt_devices", {"id": did, "user_id": user.id, "name": name[:60], "token_hash": hash_token(token),
                                      "created_at": _iso(now or _utcnow())})
        return self._device(self.db.one("dt_devices", id=did)), token

    def _device(self, r: dict) -> Device:
        u = self.get_user(r["user_id"])
        return Device(id=r["id"], user_id=r["user_id"], name=r["name"], timezone=u.timezone if u else "UTC",
                      created_at=r["created_at"], last_seen_at=r["last_seen_at"])

    def device_for_token(self, token: str | None, touch: bool = False) -> Device | None:
        if not token:
            return None
        r = self.db.one("dt_devices", token_hash=hash_token(token))
        if not r:
            return None
        if touch:
            self.db.update("dt_devices", {"last_seen_at": _iso(_utcnow())}, eq={"id": r["id"]})
        return self._device(r)

    def devices(self, user: User) -> list[Device]:
        return [self._device(r) for r in self.db.select("dt_devices", eq={"user_id": user.id}, order="created_at")]

    def delete_device(self, user: User, device_id: str) -> bool:
        return self.db.delete("dt_devices", eq={"id": device_id, "user_id": user.id}) > 0

    def create_pairing(self, user: User, device_name: str, now: dt.datetime | None = None) -> str:
        now = now or _utcnow()
        self.db.delete("dt_pairings", lt={"expires_at": _iso(now)})
        raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        self.db.insert("dt_pairings", {"code_hash": hash_token(raw), "user_id": user.id,
                                       "device_name": (device_name or "Printer")[:60],
                                       "expires_at": _iso(now + dt.timedelta(minutes=PAIRING_MINUTES))})
        return f"{raw[:4]}-{raw[4:]}"

    def redeem_pairing(self, code: str, now: dt.datetime | None = None) -> tuple[Device, str] | None:
        now = now or _utcnow()
        raw = "".join(c for c in code.upper() if c.isalnum())
        r = self.db.one("dt_pairings", code_hash=hash_token(raw))
        if not r:
            return None
        self.db.delete("dt_pairings", eq={"code_hash": r["code_hash"]})
        if r["expires_at"] < _iso(now):
            return None
        user = self.get_user(r["user_id"])
        return self.create_device(user, r["device_name"], now) if user else None

    # ------------------------------------------------------------ briefs

    def enqueue(self, user: User, brief: Brief, source: str, now: dt.datetime | None = None) -> EnqueueResult:
        now = now or _utcnow()
        today = user.today(now)
        date = brief.date or today
        if not (today - dt.timedelta(days=1) <= date <= today + dt.timedelta(days=7)):
            raise StoreError(f"brief date {date} must be between yesterday and a week from today ({today})")

        blocks, dropped = self._drop_seen(user, brief, today)
        if not any(b.type in CONTENT_TYPES for b in blocks):
            return EnqueueResult(None, "empty_after_dedup", date, dropped)
        final = Brief.model_construct(blocks=blocks, title=brief.title, date=date)
        chash = final.content_hash()
        for same in self.db.select("dt_briefs", eq={"user_id": user.id, "brief_date": date.isoformat(),
                                                    "content_hash": chash, "status": "pending"}, limit=1):
            return EnqueueResult(same["id"], "duplicate", date, dropped)
        superseded = self.db.update("dt_briefs", {"status": "superseded"},
                                    eq={"user_id": user.id, "brief_date": date.isoformat(), "status": "pending"})
        brief_id = "br_" + uuid.uuid4().hex[:16]
        self.db.insert("dt_briefs", {
            "id": brief_id, "user_id": user.id, "brief_date": date.isoformat(), "title": brief.title,
            "blocks": json.dumps(final.blocks_json()), "content_hash": chash, "source": source,
            "status": "pending", "created_at": _iso(now)})
        if self._purged_at is None or now - self._purged_at > dt.timedelta(hours=1):
            self.purge(now)
        return EnqueueResult(brief_id, "queued", date, dropped, superseded)

    def _drop_seen(self, user: User, brief: Brief, today: dt.date):
        since = (today - dt.timedelta(days=DEDUP_DAYS)).isoformat()
        seen = {r["fingerprint"] for r in self.db.select("dt_history", eq={"user_id": user.id},
                                                         gte={"printed_on": since})}
        kept, dropped = [], []
        for b in brief.blocks:
            fp = fingerprint(b)
            if fp and fp in seen:
                dropped.append(fp)
            else:
                kept.append(b)
        return (_prune_empty_sections(kept) if dropped else kept), dropped

    def todays_brief(self, user: User, now: dt.datetime | None = None) -> dict | None:
        """The live edition for the user's today (what the dashboard shows)."""
        rows = self.db.select("dt_briefs", eq={"user_id": user.id, "brief_date": user.today(now).isoformat(),
                                               "status": "pending"}, order="created_at", desc=True, limit=1)
        return _brief_row(rows[0]) if rows else None

    def pending(self, device: Device, now: dt.datetime | None = None) -> dict | None:
        """What this device should print now: today's edition, unless it already printed one today."""
        today = device.today(now).isoformat()
        if self.db.select("dt_receipts", eq={"device_id": device.id, "brief_date": today, "status": "printed"},
                          limit=1):
            return None
        rows = self.db.select("dt_briefs", eq={"user_id": device.user_id, "brief_date": today, "status": "pending"},
                              order="created_at", desc=True, limit=1)
        if not rows:
            return None
        b = _brief_row(rows[0])
        r = self.db.one("dt_receipts", brief_id=b["id"], device_id=device.id)
        b["attempts"] = r["attempts"] if r else 0
        b["last_error"] = r["detail"] if r else None
        return b

    def get_brief(self, user_id: str, brief_id: str) -> dict | None:
        r = self.db.one("dt_briefs", id=brief_id, user_id=user_id)
        return _brief_row(r) if r else None

    def receipts(self, brief_id: str) -> list[dict]:
        return self.db.select("dt_receipts", eq={"brief_id": brief_id})

    def report(self, device: Device, brief_id: str, status: str, detail: str | None = None,
               now: dt.datetime | None = None) -> dict:
        if status not in ("printed", "failed"):
            raise StoreError("status must be 'printed' or 'failed'")
        now = now or _utcnow()
        row = self.db.one("dt_briefs", id=brief_id, user_id=device.user_id)
        if not row:
            raise KeyError(brief_id)
        prev = self.db.one("dt_receipts", brief_id=brief_id, device_id=device.id)
        if prev and prev["status"] == "printed":
            status = "printed"  # a late failure report doesn't undo a print
        self.db.upsert("dt_receipts", {
            "brief_id": brief_id, "device_id": device.id, "brief_date": row["brief_date"], "status": status,
            "attempts": (prev["attempts"] if prev else 0) + 1, "detail": detail, "updated_at": _iso(now)},
            key=("brief_id", "device_id"))
        if status == "printed":
            today = device.today(now).isoformat()
            for b in BlockList.validate_python(json.loads(row["blocks"])):
                fp = fingerprint(b)
                if fp:
                    self.db.upsert("dt_history", {"user_id": device.user_id, "fingerprint": fp, "printed_on": today},
                                   key=("user_id", "fingerprint"))
        out = _brief_row(row)
        out["receipt"] = self.db.one("dt_receipts", brief_id=brief_id, device_id=device.id)
        out["status"] = out["receipt"]["status"]
        return out

    def history(self, user: User) -> list[dict]:
        return [{"fingerprint": r["fingerprint"], "printed_on": r["printed_on"]}
                for r in self.db.select("dt_history", eq={"user_id": user.id}, order="printed_on", desc=True)]

    def purge(self, now: dt.datetime | None = None) -> None:
        """Retention: drop old briefs, receipts, history and expired codes."""
        now = now or _utcnow()
        self._purged_at = now
        cutoff_day = (now - dt.timedelta(days=RETENTION_DAYS)).date().isoformat()
        self.db.delete("dt_briefs", lt={"brief_date": cutoff_day})
        self.db.delete("dt_receipts", lt={"brief_date": cutoff_day})
        self.db.delete("dt_history", lt={"printed_on": (now - dt.timedelta(days=DEDUP_DAYS + 1)).date().isoformat()})
        self.db.delete("dt_logins", lt={"expires_at": _iso(now)})
        self.db.delete("dt_pairings", lt={"expires_at": _iso(now)})

    # ------------------------------------------------------------ CLI/admin registration

    def register_device_account(self, name: str, timezone: str = "UTC") -> tuple[User, Device, str, str]:
        """Admin path for headless installs: a user without email, one device, one send token."""
        user = self.create_user(None, timezone)
        device, device_token = self.create_device(user, name)
        return user, device, device_token, self.rotate_send_token(user)


def _brief_row(r: dict) -> dict:
    return {"id": r["id"], "user_id": r["user_id"], "date": r["brief_date"], "title": r["title"],
            "blocks": json.loads(r["blocks"]), "source": r["source"], "status": r["status"],
            "created_at": r["created_at"]}


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


__all__ = ["Store", "StoreError", "User", "Device", "EnqueueResult", "DuplicateError", "hash_token",
           "DEDUP_DAYS", "RETENTION_DAYS"]
