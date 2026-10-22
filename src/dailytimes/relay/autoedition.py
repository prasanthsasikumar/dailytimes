"""When to build a user's auto edition (from their interests, public sources only).

Built lazily, the first time anything asks for today's edition (a printer poll,
the dashboard, or the daily cron) after the user's `auto_after` time, and only if
no AI agent has sent a brief for today. At most one attempt per local day.
A brief an agent sends later the same day still replaces it (newest wins).
"""

from __future__ import annotations

import datetime as dt
import logging

from pydantic import ValidationError

from ..autobrief import Interests, build_auto_brief
from .store import Store, User

log = logging.getLogger("dailytimes.auto")


def interests_of(user: User) -> Interests:
    try:
        return Interests.model_validate(user.interests or {})
    except ValidationError:
        return Interests()


def ensure_auto_edition(store: Store, user: User, now: dt.datetime | None = None, force: bool = False,
                        builder=build_auto_brief) -> dict | None:
    """Returns the enqueue result dict if an auto edition was built, else None."""
    if not user.auto_enabled and not force:
        return None
    interests = interests_of(user)
    if interests.is_empty():
        return None
    local = user.now(now)
    today = local.date().isoformat()
    if not force:
        if user.auto_built_on == today or local.strftime("%H:%M") < user.auto_after:
            return None
        if store.todays_brief(user, now):
            return None
    store.update_user(user, auto_built_on=today)  # claim the day first, so parallel polls don't all fetch
    seen = {h["fingerprint"] for h in store.history(user)}
    try:
        brief = builder(interests, user.edition_size, seen)
    except Exception:
        log.exception("auto edition failed for %s", user.id)
        return None
    if brief is None:
        return None
    return store.enqueue(user, brief, "auto", now).as_dict()
