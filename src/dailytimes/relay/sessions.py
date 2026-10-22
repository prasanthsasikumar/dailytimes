"""Signed session cookies and CSRF tokens. No session table: the cookie carries the
user id, an epoch (bumped by "sign out everywhere") and an expiry, HMAC-signed."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

COOKIE = "dt_session"
MAX_AGE = 30 * 24 * 3600


def _sign(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def make(secret: str, user_id: str, epoch: int, now: float | None = None) -> str:
    payload = json.dumps({"u": user_id, "e": epoch, "x": int((time.time() if now is None else now) + MAX_AGE)},
                         separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + _sign(secret, payload)


def read(secret: str, cookie: str | None, now: float | None = None) -> tuple[str, int] | None:
    if not cookie or "." not in cookie:
        return None
    b64, sig = cookie.rsplit(".", 1)
    try:
        payload = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
    except ValueError:
        return None
    if not hmac.compare_digest(_sign(secret, payload), sig):
        return None
    try:
        d = json.loads(payload)
    except ValueError:
        return None
    if d.get("x", 0) < (time.time() if now is None else now):
        return None
    return d["u"], int(d["e"])


def csrf_token(secret: str, user_id: str, epoch: int) -> str:
    return _sign(secret, f"csrf:{user_id}:{epoch}".encode())[:32]


def check_csrf(secret: str, user_id: str, epoch: int, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(csrf_token(secret, user_id, epoch), token)
