"""Store behaviour, run against SQLite always and against a real PostgREST/Supabase when
DAILYTIMES_TEST_POSTGREST_URL (+ _KEY) is set (see scripts/test_postgrest.sh)."""

import datetime as dt
import os
import uuid

import pytest

from dailytimes.blocks import Brief
from dailytimes.relay.db import SQLiteDB, SupabaseDB
from dailytimes.relay.store import DEDUP_DAYS, RETENTION_DAYS, Store, StoreError

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 21, 7, 0, tzinfo=UTC)

BACKENDS = ["sqlite"]
if os.environ.get("DAILYTIMES_TEST_POSTGREST_URL"):
    BACKENDS.append("postgrest")


def B(*blocks, **kw):
    return Brief.model_validate({"blocks": list(blocks), **kw})


def item(n, url=True):
    return {"type": "bullet", "text": f"item {n}", **({"url": f"https://news.example.com/{n}"} if url else {})}


@pytest.fixture(params=BACKENDS)
def store(request):
    if request.param == "sqlite":
        return Store(SQLiteDB(":memory:"))
    db = SupabaseDB(os.environ["DAILYTIMES_TEST_POSTGREST_URL"], os.environ.get("DAILYTIMES_TEST_POSTGREST_KEY", "x"))
    keys = {"dt_receipts": "brief_id", "dt_briefs": "id", "dt_history": "user_id", "dt_devices": "id",
            "dt_pairings": "code_hash", "dt_logins": "token_hash", "dt_users": "id"}
    for table, key in keys.items():  # start clean: PostgREST refuses unfiltered deletes
        db.delete(table, lt={key: "~"})
    return Store(db)


@pytest.fixture
def user(store):
    return store.create_user(f"{uuid.uuid4().hex[:8]}@example.com", "UTC", now=NOW)


@pytest.fixture
def device(store, user):
    return store.create_device(user, "kitchen", now=NOW)[0]


# ------------------------------------------------------------------ accounts and tokens

def test_users_and_send_tokens(store):
    u = store.create_user("Reader@Example.com", "Europe/London")
    assert store.user_by_email("reader@example.com").id == u.id
    assert store.user_for_send_token("dts_nope") is None and not store.has_send_token(u)
    t1 = store.rotate_send_token(u)
    assert store.user_for_send_token(t1).id == u.id
    t2 = store.rotate_send_token(u)
    assert store.user_for_send_token(t1) is None and store.user_for_send_token(t2).id == u.id
    with pytest.raises(StoreError):
        store.create_user("x@example.com", "Mars/Olympus")
    with pytest.raises(StoreError):
        store.update_user(u, email="hijack@example.com")
    u = store.update_user(u, interests={"topics": ["ai"]}, auto_enabled=False, edition_size="slip")
    assert u.interests == {"topics": ["ai"]} and not u.auto_enabled and u.edition_size == "slip"


def test_device_tokens_and_pairing(store, user):
    d, tok = store.create_device(user, "hall")
    assert store.device_for_token(tok).id == d.id and store.device_for_token("dtd_x") is None
    code = store.create_pairing(user, "office", now=NOW)
    assert len(code) == 9 and code[4] == "-"
    assert store.redeem_pairing("ZZZZ-ZZZZ", now=NOW) is None
    late = NOW + dt.timedelta(minutes=16)
    assert store.redeem_pairing(code, now=late) is None  # expired (and consumed)
    code = store.create_pairing(user, "office", now=NOW)
    dev, dtok = store.redeem_pairing(code.lower().replace("-", " "), now=NOW)
    assert dev.name == "office" and store.device_for_token(dtok).user_id == user.id
    assert store.redeem_pairing(code, now=NOW) is None  # single use
    assert {x.name for x in store.devices(user)} == {"hall", "office"}
    assert store.delete_device(user, dev.id) and store.device_for_token(dtok) is None


def test_login_links(store):
    tok = store.create_login("a@example.com", now=NOW)
    with pytest.raises(StoreError):
        store.create_login("a@example.com", now=NOW + dt.timedelta(seconds=10))
    assert store.redeem_login(tok, now=NOW) == "a@example.com"
    assert store.redeem_login(tok, now=NOW) is None
    tok = store.create_login("a@example.com", now=NOW + dt.timedelta(minutes=2))
    assert store.redeem_login(tok, now=NOW + dt.timedelta(minutes=30)) is None


# ------------------------------------------------------------------ queue

def test_newest_brief_of_the_day_wins(store, user, device):
    r1 = store.enqueue(user, B(item(1)), "webhook", now=NOW)
    r2 = store.enqueue(user, B(item(2)), "mcp", now=NOW + dt.timedelta(seconds=1))
    assert r1.status == r2.status == "queued" and r2.superseded == 1
    p = store.pending(device, now=NOW)
    assert p["id"] == r2.brief_id and p["source"] == "mcp"
    assert store.get_brief(user.id, r1.brief_id)["status"] == "superseded"
    assert store.todays_brief(user, now=NOW)["id"] == r2.brief_id


def test_identical_resend_is_duplicate(store, user, device):
    r1 = store.enqueue(user, B(item(1)), "mcp", now=NOW)
    r2 = store.enqueue(user, B(item(1)), "mcp", now=NOW)
    assert r2.status == "duplicate" and r2.brief_id == r1.brief_id


def test_queue_is_per_user(store, user, device):
    other = store.create_user("other-" + uuid.uuid4().hex[:6] + "@example.com")
    od, _ = store.create_device(other, "x")
    store.enqueue(user, B(item(1)), "mcp", now=NOW)
    assert store.pending(od, now=NOW) is None


def test_future_brief_waits_and_past_is_ignored(store, user, device):
    tomorrow = NOW.date() + dt.timedelta(days=1)
    r = store.enqueue(user, B(item(1), date=tomorrow.isoformat()), "mcp", now=NOW)
    assert store.pending(device, now=NOW) is None
    assert store.pending(device, now=NOW + dt.timedelta(days=1))["id"] == r.brief_id
    assert store.pending(device, now=NOW + dt.timedelta(days=2)) is None


def test_date_range_enforced(store, user):
    with pytest.raises(StoreError):
        store.enqueue(user, B(item(1), date="2026-12-25"), "mcp", now=NOW)
    with pytest.raises(StoreError):
        store.enqueue(user, B(item(1), date="2026-10-01"), "mcp", now=NOW)


def test_user_timezone_sets_the_day(store):
    u = store.create_user("akl-" + uuid.uuid4().hex[:6] + "@example.com", "Pacific/Auckland")  # UTC+13 in October
    r = store.enqueue(u, B(item(1)), "mcp", now=dt.datetime(2026, 10, 21, 12, 0, tzinfo=UTC))
    assert r.date == dt.date(2026, 10, 22)


def test_each_device_prints_once_per_day(store, user, device):
    d2, _ = store.create_device(user, "office")
    r = store.enqueue(user, B(item(1)), "mcp", now=NOW)
    store.report(device, r.brief_id, "printed", now=NOW)
    assert store.pending(device, now=NOW) is None
    assert store.pending(d2, now=NOW)["id"] == r.brief_id  # the other printer still gets it
    store.enqueue(user, B(item(2)), "mcp", now=NOW + dt.timedelta(hours=1))
    assert store.pending(device, now=NOW + dt.timedelta(hours=1)) is None  # already printed today
    assert store.pending(device, now=NOW + dt.timedelta(days=1)) is None  # nothing for tomorrow yet


def test_report_printed_records_history_and_dedups(store, user, device):
    r = store.enqueue(user, B({"type": "heading", "text": "News"}, item(1), item(2), item(3, url=False)), "mcp",
                      now=NOW)
    out = store.report(device, r.brief_id, "printed", now=NOW)
    assert out["status"] == "printed"
    assert {h["fingerprint"] for h in store.history(user)} == {
        "https://news.example.com/1", "https://news.example.com/2"}
    nxt = NOW + dt.timedelta(days=1)
    r2 = store.enqueue(user, B(
        {"type": "heading", "text": "News"}, item(1), item(4),
        {"type": "heading", "text": "Old stuff"}, item(2),
        {"type": "heading", "text": "Notes"}, item(3, url=False)), "mcp", now=nxt)
    assert r2.status == "queued"
    assert sorted(r2.dropped) == ["https://news.example.com/1", "https://news.example.com/2"]
    kept = store.pending(device, now=nxt)["blocks"]
    assert [b.get("text") for b in kept] == ["News", "item 4", "Notes", "item 3"]


def test_all_duplicates_means_nothing_queued(store, user, device):
    r = store.enqueue(user, B(item(1)), "mcp", now=NOW)
    store.report(device, r.brief_id, "printed", now=NOW)
    r2 = store.enqueue(user, B({"type": "heading", "text": "News"}, item(1)), "mcp", now=NOW + dt.timedelta(days=1))
    assert r2.status == "empty_after_dedup" and r2.brief_id is None


def test_dedup_window_is_60_days(store, user, device):
    r = store.enqueue(user, B(item(1)), "mcp", now=NOW)
    store.report(device, r.brief_id, "printed", now=NOW)
    within = NOW + dt.timedelta(days=DEDUP_DAYS)
    assert store.enqueue(user, B(item(1)), "mcp", now=within).status == "empty_after_dedup"
    after = NOW + dt.timedelta(days=DEDUP_DAYS + 1)
    assert store.enqueue(user, B(item(1)), "mcp", now=after).status == "queued"


def test_failed_report_keeps_brief_pending_and_print_wins(store, user, device):
    r = store.enqueue(user, B(item(1)), "mcp", now=NOW)
    store.report(device, r.brief_id, "failed", "paper out", now=NOW)
    p = store.pending(device, now=NOW)
    assert p["id"] == r.brief_id and p["attempts"] == 1 and p["last_error"] == "paper out"
    store.report(device, r.brief_id, "printed", now=NOW)
    assert store.report(device, r.brief_id, "failed", "late", now=NOW)["status"] == "printed"
    with pytest.raises(StoreError):
        store.report(device, r.brief_id, "eaten")
    with pytest.raises(KeyError):
        store.report(device, "br_nope", "printed")


def test_retention_purge(store, user, device):
    r = store.enqueue(user, B(item(1)), "mcp", now=NOW)
    store.report(device, r.brief_id, "printed", now=NOW)
    store.purge(NOW + dt.timedelta(days=RETENTION_DAYS + 1))
    assert store.get_brief(user.id, r.brief_id) is None
    assert store.receipts(r.brief_id) == []
    assert store.history(user)  # dedup history outlives briefs (60 days)
    store.purge(NOW + dt.timedelta(days=DEDUP_DAYS + 2))
    assert store.history(user) == []


def test_delete_and_export_user(store, user, device):
    r = store.enqueue(user, B(item(1)), "mcp", now=NOW)
    store.report(device, r.brief_id, "printed", now=NOW)
    store.rotate_send_token(user)
    data = store.export_user(user)
    assert data["user"]["email"] == user.email and len(data["briefs"]) == 1 and data["devices"][0]["name"] == "kitchen"
    assert "token_hash" not in str(data)
    store.delete_user(user)
    assert store.get_user(user.id) is None and store.devices(user) == [] and store.history(user) == []
    assert store.receipts(r.brief_id) == []
