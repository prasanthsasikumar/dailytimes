import datetime as dt

import pytest

from dailytimes.blocks import Brief
from dailytimes.relay.store import DEDUP_DAYS, Store, StoreError

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 21, 7, 0, tzinfo=UTC)


def B(*blocks, **kw):
    return Brief.model_validate({"blocks": list(blocks), **kw})


def item(n, url=True):
    return {"type": "bullet", "text": f"item {n}", **({"url": f"https://news.example.com/{n}"} if url else {})}


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture
def device(store):
    d, _ = store.create_device("kitchen", "UTC")
    return d


def test_tokens_resolve_and_unknown_tz_rejected(store):
    d, token = store.create_device("hall", "Europe/London")
    assert store.device_for_token(token).id == d.id
    assert store.device_for_token("dt_wrong") is None
    assert store.device_for_token(None) is None
    with pytest.raises(StoreError):
        store.create_device("x", "Mars/Olympus")


def test_newest_brief_of_the_day_wins(store, device):
    r1 = store.enqueue(device, B(item(1)), "webhook", now=NOW)
    r2 = store.enqueue(device, B(item(2)), "mcp", now=NOW)
    assert r1.status == r2.status == "queued" and r2.superseded == 1
    p = store.pending(device, now=NOW)
    assert p["id"] == r2.brief_id and p["source"] == "mcp"
    assert store.get_brief(device, r1.brief_id)["status"] == "superseded"


def test_identical_resend_is_duplicate(store, device):
    r1 = store.enqueue(device, B(item(1)), "mcp", now=NOW)
    r2 = store.enqueue(device, B(item(1)), "mcp", now=NOW)
    assert r2.status == "duplicate" and r2.brief_id == r1.brief_id
    assert store.pending(device, now=NOW)["id"] == r1.brief_id


def test_queue_is_per_device(store, device):
    other, _ = store.create_device("office", "UTC")
    store.enqueue(device, B(item(1)), "mcp", now=NOW)
    assert store.pending(other, now=NOW) is None


def test_future_brief_waits_and_past_expires(store, device):
    tomorrow = NOW.date() + dt.timedelta(days=1)
    r = store.enqueue(device, B(item(1), date=tomorrow.isoformat()), "mcp", now=NOW)
    assert store.pending(device, now=NOW) is None
    assert store.pending(device, now=NOW + dt.timedelta(days=1))["id"] == r.brief_id
    assert store.pending(device, now=NOW + dt.timedelta(days=2)) is None
    assert store.get_brief(device, r.brief_id)["status"] == "expired"


def test_date_range_enforced(store, device):
    with pytest.raises(StoreError):
        store.enqueue(device, B(item(1), date="2026-12-25"), "mcp", now=NOW)
    with pytest.raises(StoreError):
        store.enqueue(device, B(item(1), date="2026-10-01"), "mcp", now=NOW)


def test_device_timezone_sets_the_day(store):
    d, _ = store.create_device("akl", "Pacific/Auckland")  # UTC+13 in October
    r = store.enqueue(d, B(item(1)), "mcp", now=dt.datetime(2026, 10, 21, 12, 0, tzinfo=UTC))
    assert r.date == dt.date(2026, 10, 22)


def test_report_printed_records_history_and_dedups(store, device):
    r = store.enqueue(device, B({"type": "heading", "text": "News"}, item(1), item(2), item(3, url=False)), "mcp", now=NOW)
    store.report(device, r.brief_id, "printed", now=NOW)
    assert store.get_brief(device, r.brief_id)["status"] == "printed"
    assert store.pending(device, now=NOW) is None
    assert {h["fingerprint"] for h in store.history(device)} == {
        "https://news.example.com/1", "https://news.example.com/2"}

    nxt = NOW + dt.timedelta(days=1)
    r2 = store.enqueue(device, B(
        {"type": "heading", "text": "News"}, item(1), item(4),
        {"type": "heading", "text": "Old stuff"}, item(2),
        {"type": "heading", "text": "Notes"}, item(3, url=False),
    ), "mcp", now=nxt)
    assert r2.status == "queued"
    assert r2.dropped == ["https://news.example.com/1", "https://news.example.com/2"]
    kept = store.pending(device, now=nxt)["blocks"]
    assert [b.get("text") for b in kept] == ["News", "item 4", "Notes", "item 3"]  # empty "Old stuff" pruned


def test_all_duplicates_means_nothing_queued(store, device):
    r = store.enqueue(device, B(item(1)), "mcp", now=NOW)
    store.report(device, r.brief_id, "printed", now=NOW)
    r2 = store.enqueue(device, B({"type": "heading", "text": "News"}, item(1)), "mcp", now=NOW + dt.timedelta(days=1))
    assert r2.status == "empty_after_dedup" and r2.brief_id is None


def test_dedup_window_is_60_days(store, device):
    r = store.enqueue(device, B(item(1)), "mcp", now=NOW)
    store.report(device, r.brief_id, "printed", now=NOW)
    within = NOW + dt.timedelta(days=DEDUP_DAYS)
    assert store.enqueue(device, B(item(1)), "mcp", now=within).status == "empty_after_dedup"
    after = NOW + dt.timedelta(days=DEDUP_DAYS + 1)
    assert store.enqueue(device, B(item(1)), "mcp", now=after).status == "queued"


def test_history_pruned_after_window(store, device):
    r = store.enqueue(device, B(item(1)), "mcp", now=NOW)
    store.report(device, r.brief_id, "printed", now=NOW)
    later = NOW + dt.timedelta(days=DEDUP_DAYS + 5)
    r2 = store.enqueue(device, B(item(9)), "mcp", now=later)
    store.report(device, r2.brief_id, "printed", now=later)
    assert [h["fingerprint"] for h in store.history(device)] == ["https://news.example.com/9"]


def test_failed_report_keeps_brief_pending(store, device):
    r = store.enqueue(device, B(item(1)), "mcp", now=NOW)
    store.report(device, r.brief_id, "failed", "paper out", now=NOW)
    p = store.pending(device, now=NOW)
    assert p["id"] == r.brief_id and p["attempts"] == 1 and p["last_error"] == "paper out"
    with pytest.raises(StoreError):
        store.report(device, r.brief_id, "eaten")
    with pytest.raises(KeyError):
        store.report(device, "br_nope", "printed")
