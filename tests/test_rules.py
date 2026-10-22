import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from dailytimes.agent.config import Rules
from dailytimes.agent.rules import AgentState, decide, gate

TZ = ZoneInfo("Pacific/Auckland")


def at(h, m=0, day=21):
    return dt.datetime(2026, 10, day, h, m, tzinfo=TZ)


RULES = Rules(print_after=dt.time(6, 30), cooldown_minutes=20, fallback="retry", fallback_after=dt.time(10, 0))


def test_too_early_is_ignored():
    d = decide(at(6, 29), AgentState(), RULES, have_brief=True)
    assert d.action == "ignore" and "too early" in d.reason


def test_first_presence_after_hour_prints():
    assert decide(at(6, 30), AgentState(), RULES, have_brief=True).action == "print"


def test_once_per_day():
    s = AgentState(printed_on=at(7).date())
    assert decide(at(12), s, RULES, True).reason == "already printed today"
    assert decide(at(7, day=22), s, RULES, True).action == "print"  # next day


def test_cooldown_between_attempts():
    s = AgentState(last_attempt=at(7, 0))
    d = decide(at(7, 5), s, RULES, True)
    assert d.action == "ignore" and d.reason.startswith("cooldown (15 min")
    assert decide(at(7, 20), s, RULES, True).action == "print"


def test_zero_cooldown():
    s = AgentState(last_attempt=at(7, 0))
    assert decide(at(7, 0), s, RULES.model_copy(update={"cooldown_minutes": 0}), True).action == "print"


def test_no_brief_retry():
    d = decide(at(11), AgentState(), RULES, have_brief=False)
    assert d.action == "ignore" and "retry" in d.reason


@pytest.mark.parametrize("fallback", ["placeholder", "skip"])
def test_fallback_only_after_fallback_hour(fallback):
    rules = RULES.model_copy(update={"fallback": fallback})
    assert decide(at(9, 59), AgentState(), rules, False).action == "ignore"
    assert decide(at(10, 0), AgentState(), rules, False).action == fallback
    assert decide(at(10, 0), AgentState(), rules, True).action == "print"  # a brief always wins


def test_gate_none_means_go():
    assert gate(at(8), AgentState(), RULES) is None


def test_yaml_sexagesimal_times():
    r = Rules.model_validate({"print_after": 390, "fallback_after": "09:15"})
    assert r.print_after == dt.time(6, 30) and r.fallback_after == dt.time(9, 15)


def test_state_roundtrip(tmp_path):
    p = tmp_path / "s" / "state.json"
    s = AgentState(printed_on=dt.date(2026, 10, 21), last_attempt=at(7), last_brief_id="br_1")
    s.save(p)
    assert AgentState.load(p) == s
    assert AgentState.load(tmp_path / "missing.json") == AgentState()
    (tmp_path / "bad.json").write_text("{nope")
    assert AgentState.load(tmp_path / "bad.json") == AgentState()
