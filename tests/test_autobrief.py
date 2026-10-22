"""Auto edition: suggestion, interests validation, source parsing (offline), composition, SSRF guard."""

import json

import httpx
import pytest
from pydantic import ValidationError

from dailytimes.autobrief import Interests, Location, build_auto_brief, suggest
from dailytimes.autobrief import compose, fetch, sources
from dailytimes.autobrief.suggest import heuristic
from dailytimes.blocks import short_link


def test_heuristic_suggestion(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    i, place, method = suggest("I do HCI and AR research, I live in Auckland. I follow r/augmentedreality, "
                               "hold $AAPL and $nvda, and like startups and climate tech.")
    assert method == "keywords" and place == "Auckland"
    assert "human-computer interaction" in " ".join(i.topics)
    assert "follow" not in i.topics and "hold" not in i.topics
    assert i.subreddits == ["augmentedreality"] and i.tickers == ["AAPL", "NVDA"]
    assert "cs.HC" in i.arxiv and i.hacker_news is True


def test_heuristic_on_empty_text():
    i, place = heuristic("")
    assert i.is_empty() and place is None


def test_interests_validation():
    ok = Interests(subreddits=["r/HCI", "/askscience"], tickers=["$aapl", "^GSPC", "AIR.NZ"], arxiv=["cs.HC"],
                   topics=["AI", "ai", "  climate   tech "])
    assert ok.subreddits == ["HCI", "askscience"] and ok.tickers == ["AAPL", "^GSPC", "AIR.NZ"]
    assert ok.topics == ["AI", "climate tech"]
    for bad in ({"subreddits": ["no spaces allowed"]}, {"tickers": ["DROP TABLE"]}, {"arxiv": ["HCI papers"]},
                {"feeds": ["javascript:alert(1)"]}, {"region": "usa"}, {"topics": ["x"] * 9}, {"unknown": 1}):
        with pytest.raises(ValidationError):
            Interests.model_validate(bad)


RSS = b"""<?xml version="1.0"?><rss><channel><title>Feed</title>
<item><title>Big result announced - Example Times</title><link>https://news.google.com/rss/articles/CBMiABC?oc=5</link><source url="https://ex.com">Example Times</source></item>
<item><title>Second &amp; story - Other Paper</title><link>https://news.google.com/rss/articles/CBMiDEF?oc=5</link><source url="https://o.com">Other Paper</source></item>
</channel></rss>"""
ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>r/HCI</title>
<entry><title>Paper of the week</title><link href="https://www.reddit.com/r/HCI/comments/abc/paper/"/><id>https://arxiv.org/abs/2610.00001v1</id></entry>
</feed>"""


class FakeResponse:
    def __init__(self, content=b"", data=None):
        self.content = content
        self._data = data

    def json(self):
        return self._data


def test_source_parsers_offline(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append((url, kw))
        if "news.google.com" in url:
            return FakeResponse(RSS)
        if "reddit.com" in url or "arxiv.org" in url:
            return FakeResponse(ATOM)
        if "algolia" in url:
            return FakeResponse(data={"hits": [{"title": "Low", "points": 50, "url": "https://a.com", "objectID": "1"},
                                               {"title": "High", "points": 900, "url": None, "objectID": "2"}]})
        if "yahoo" in url:
            return FakeResponse(data={"chart": {"result": [{"meta": {"currency": "USD", "regularMarketPrice": 110.0},
                                                            "indicators": {"quote": [{"close": [90, 100, 110]}]}}]}})
        if "open-meteo" in url:
            return FakeResponse(data={"daily": {"temperature_2m_max": [21.4], "temperature_2m_min": [12.6],
                                                "precipitation_probability_max": [40], "weather_code": [3]}})
        raise AssertionError(url)

    monkeypatch.setattr(fetch, "get", fake_get)
    news = sources.google_news("ai research", "NZ")
    assert news[0].text == "Big result announced (Example Times)" and news[1].text == "Second & story (Other Paper)"
    assert "gl=NZ" in calls[0][0] and "ai+research" in calls[0][0]
    hn = sources.hacker_news()
    assert hn[0].text == "High (900 points)" and hn[0].url == "https://news.ycombinator.com/item?id=2"
    assert sources.reddit("HCI")[0].text == "r/HCI: Paper of the week"
    assert sources.arxiv("cs.HC")[0].url == "https://arxiv.org/abs/2610.00001v1"
    q = sources.quote("AAPL")
    assert q.left == "AAPL" and q.right == "$110.00  +10.0%"
    assert sources.weather(-36.8, 174.7, "Auckland").right == "21° / 13°, overcast, 40% rain"


def test_build_auto_brief_composition(monkeypatch):
    monkeypatch.setattr(sources, "google_news", lambda t, region, n: [
        sources.Item(f"{t} story {k}", f"https://ex.com/{t}/{k}") for k in range(n)])
    monkeypatch.setattr(sources, "reddit", lambda s, n: [sources.Item(f"r/{s}: post", f"https://reddit.com/r/{s}/1")])
    monkeypatch.setattr(sources, "quote", lambda t: sources.Row(t, "$1.00  +1.0%"))
    monkeypatch.setattr(sources, "weather", lambda lat, lon, name: sources.Row(f"Weather, {name}", "20° / 10°"))

    def broken(*a, **k):
        raise fetch.FetchError("down")

    monkeypatch.setattr(sources, "arxiv", broken)
    i = Interests(topics=["ai", "climate"], subreddits=["HCI"], tickers=["AAPL"], arxiv=["cs.HC"],
                  location=Location(name="Auckland", lat=-36.8, lon=174.7))
    b = build_auto_brief(i, "page", seen={"https://ex.com/ai/0"})
    heads = [x.text for x in b.blocks if x.type == "heading"]
    assert heads == ["Today", "Ai", "Climate", "Communities", "Markets"]  # failed arXiv section simply absent
    urls = [x.url for x in b.blocks if getattr(x, "url", None)]
    assert "https://ex.com/ai/0" not in urls  # already seen
    assert len(urls) == len(set(urls))
    assert b.title == "Auto edition" and b.blocks[-1].type == "small" and "exactly as published" in b.blocks[-1].text
    slip = build_auto_brief(i, "slip")
    assert len(slip.blocks) < len(b.blocks)
    assert build_auto_brief(Interests(), "page") is None


def test_fair_share_between_sources():
    many = Interests(topics=[f"t{k}" for k in range(8)], subreddits=["a1", "b2"], hacker_news=True)
    per = compose._per_source(many, "page")
    assert per["topic"] == 2 and per["hn"] == 2
    few = Interests(topics=["only"])
    assert compose._per_source(few, "page")["topic"] == 4  # capped


@pytest.mark.parametrize("url", ["http://127.0.0.1/admin", "http://localhost:8787/", "http://169.254.169.254/latest",
                                 "http://10.0.0.5/feed", "file:///etc/passwd", "gopher://x.com/"])
def test_ssrf_guard_blocks_internal_targets(url):
    with pytest.raises(fetch.FetchError):
        fetch.get(url, untrusted=True)


def test_ssrf_guard_rechecks_redirects(monkeypatch):
    hops = []

    def handler(request):
        hops.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    monkeypatch.setattr(fetch, "_public_host", lambda h: (_ for _ in ()).throw(fetch.FetchError("private"))
                        if h == "127.0.0.1" else None)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(fetch.FetchError, match="private"):
        fetch.get("https://feeds.example.com/rss", untrusted=True, client=client)
    assert hops == ["https://feeds.example.com/rss"]


def test_size_cap(monkeypatch):
    big = b"x" * (fetch.MAX_BYTES + 10)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=big)))
    with pytest.raises(fetch.FetchError, match="too large"):
        fetch.get("https://example.com/huge", client=client)


def test_google_news_links_print_short():
    assert short_link("https://news.google.com/rss/articles/CBMiABCDEF?oc=5") == "via Google News"


def test_llm_suggestion_parses_and_falls_back(monkeypatch):
    import sys

    S = sys.modules["dailytimes.autobrief.suggest"]  # the package re-exports a function of the same name

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    anthropic = pytest.importorskip("anthropic")

    class Block:
        type = "text"
        text = json.dumps({"topics": ["human-computer interaction"], "subreddits": ["HCI"], "tickers": [],
                           "arxiv": ["cs.HC"], "hacker_news": False, "place": "Wellington", "region": "NZ"})

    class Resp:
        stop_reason = "end_turn"
        content = [Block()]

    seen = {}

    class FakeMessages:
        def create(self, **kw):
            seen.update(kw)
            return Resp()

    class FakeClient:
        def __init__(self, **kw):
            self.beta = type("B", (), {"messages": FakeMessages()})()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    i, place, method = S.suggest("I'm an HCI researcher in Wellington")
    assert method == "llm" and place == "Wellington" and i.region == "NZ" and i.arxiv == ["cs.HC"]
    assert seen["model"] == "claude-opus-5-5" and seen["fallbacks"] == "default"

    Block.text = "not json"
    i, place, method = S.suggest("I'm into HCI")
    assert method == "keywords" and "human-computer interaction" in i.topics
