"""When to print. Pure functions so every rule is unit-testable.

- Print at most once per local day,
- on the first presence at or after `print_after`,
- not more often than `cooldown_minutes` between attempts,
- and if no brief has arrived: `retry` on a later presence, or after `fallback_after`
  print a `placeholder` slip / `skip` the day.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Rules


@dataclass
class AgentState:
    printed_on: dt.date | None = None
    last_attempt: dt.datetime | None = None
    last_brief_id: str | None = None

    @classmethod
    def load(cls, path: Path) -> "AgentState":
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls()
        return cls(
            printed_on=dt.date.fromisoformat(d["printed_on"]) if d.get("printed_on") else None,
            last_attempt=dt.datetime.fromisoformat(d["last_attempt"]) if d.get("last_attempt") else None,
            last_brief_id=d.get("last_brief_id"),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        d = asdict(self)
        d["printed_on"] = self.printed_on.isoformat() if self.printed_on else None
        d["last_attempt"] = self.last_attempt.isoformat() if self.last_attempt else None
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d))
        tmp.replace(path)


@dataclass(frozen=True)
class Decision:
    action: str  # print | ignore | placeholder | skip
    reason: str


def gate(now: dt.datetime, state: AgentState, rules: Rules) -> Decision | None:
    """Checks that don't need the relay. `now` is timezone-aware local time. None = go ahead."""
    if state.printed_on == now.date():
        return Decision("ignore", "already printed today")
    if now.time() < rules.print_after:
        return Decision("ignore", f"too early (prints after {rules.print_after:%H:%M})")
    if state.last_attempt is not None:
        wait = dt.timedelta(minutes=rules.cooldown_minutes) - (now - state.last_attempt)
        if wait > dt.timedelta(0):
            return Decision("ignore", f"cooldown ({math.ceil(wait.total_seconds() / 60)} min left)")
    return None


def decide(now: dt.datetime, state: AgentState, rules: Rules, have_brief: bool) -> Decision:
    blocked = gate(now, state, rules)
    if blocked:
        return blocked
    if have_brief:
        return Decision("print", "first presence today with a brief waiting")
    if rules.fallback != "retry" and now.time() >= rules.fallback_after:
        return Decision(rules.fallback, f"no brief by {rules.fallback_after:%H:%M}")
    return Decision("ignore", "no brief yet; will retry on next presence")
