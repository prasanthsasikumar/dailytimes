"""Public, keyless sources for the auto edition.

Every fetcher returns literal facts from its source (headline text, prices, forecast
numbers) with the source's link. Nothing is summarised or rewritten, so nothing can
be invented. Each fetcher fails independently.
"""

from __future__ import annotations

import datetime as dt
import html
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote_plus

from . import fetch

ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass(frozen=True)
class Item:
    text: str
    url: str | None = None


@dataclass(frozen=True)
class Row:
    left: str
    right: str


def _clean(s: str | None, limit: int = 240) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


# ------------------------------------------------------------------ news

def google_news(topic: str, region: str = "US", limit: int = 4) -> list[Item]:
    """Top stories for a search topic from the last day (Google News RSS)."""
    url = (f"https://news.google.com/rss/search?q={quote_plus(topic)}+when:1d"
           f"&hl=en-{region}&gl={region}&ceid={region}:en")
    root = ET.fromstring(fetch.get(url).content)
    out = []
    for it in root.iter("item"):
        title = _clean(it.findtext("title"))
        source = _clean(it.findtext("source"))
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3]
        if title:
            out.append(Item(f"{title} ({source})" if source else title, it.findtext("link")))
        if len(out) >= limit:
            break
    return out


def hacker_news(query: str | None = None, limit: int = 4) -> list[Item]:
    """Front-page-grade Hacker News stories from the last day, optionally matching a query."""
    since = int(time.time()) - 86400
    params = {"tags": "story", "numericFilters": f"created_at_i>{since},points>40", "hitsPerPage": "20"}
    if query:
        params["query"] = query
    data = fetch.get("https://hn.algolia.com/api/v1/search", params=params).json()
    hits = sorted(data.get("hits", []), key=lambda h: -(h.get("points") or 0))
    out = []
    for h in hits:
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"
        out.append(Item(f"{_clean(h.get('title'))} ({h.get('points', 0)} points)", url))
        if len(out) >= limit:
            break
    return out


def reddit(sub: str, limit: int = 3) -> list[Item]:
    """Top posts of the day in a subreddit (Atom feed; the JSON API blocks most servers)."""
    root = ET.fromstring(fetch.get(f"https://www.reddit.com/r/{sub}/top/.rss?t=day&limit={limit + 2}").content)
    out = []
    for e in root.iter(f"{ATOM}entry"):
        title = _clean(e.findtext(f"{ATOM}title"))
        link = e.find(f"{ATOM}link")
        if title and link is not None:
            out.append(Item(f"r/{sub}: {title}", link.get("href")))
        if len(out) >= limit:
            break
    return out


def arxiv(category: str, limit: int = 3) -> list[Item]:
    """Newest papers in an arXiv category."""
    root = ET.fromstring(fetch.get(
        "https://export.arxiv.org/api/query",
        params={"search_query": f"cat:{category}", "sortBy": "submittedDate", "sortOrder": "descending",
                "max_results": str(limit)}).content)
    out = []
    for e in root.iter(f"{ATOM}entry"):
        title = _clean(e.findtext(f"{ATOM}title"), 200)
        link = (e.findtext(f"{ATOM}id") or "").replace("http://", "https://")
        if title:
            out.append(Item(f"{title} [{category}]", link or None))
    return out


def feed(url: str, limit: int = 3) -> list[Item]:
    """Latest entries of any RSS/Atom feed the user added (fetched with SSRF checks)."""
    root = ET.fromstring(fetch.get(url, untrusted=True).content)
    channel = _clean(root.findtext("channel/title") or root.findtext(f"{ATOM}title"), 40)
    out = []
    for it in list(root.iter("item")) + list(root.iter(f"{ATOM}entry")):
        title = _clean(it.findtext("title") or it.findtext(f"{ATOM}title"))
        link = it.findtext("link")
        if not link:
            el = it.find(f"{ATOM}link")
            link = el.get("href") if el is not None else None
        if title:
            out.append(Item(f"{channel}: {title}" if channel else title, (link or "").strip() or None))
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ numbers

def quote(ticker: str) -> Row | None:
    """Last price and change vs previous close (Yahoo Finance chart API)."""
    data = fetch.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_plus(ticker)}",
                     params={"range": "5d", "interval": "1d"}).json()
    res = (data.get("chart") or {}).get("result") or []
    if not res:
        return None
    meta = res[0]["meta"]
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    closes = [c for c in ((res[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or [] if c]
    if len(closes) >= 2:
        prev = closes[-2]
    if price is None or not prev:
        return None
    change = (price - prev) / prev * 100
    cur = {"USD": "$", "EUR": "€", "GBP": "£", "NZD": "NZ$", "AUD": "A$", "JPY": "¥"}.get(meta.get("currency", ""), "")
    return Row(ticker, f"{cur}{price:,.2f}  {change:+.1f}%")


WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 80: "showers", 81: "showers", 82: "heavy showers",
       95: "thunderstorms", 96: "thunderstorms", 99: "thunderstorms"}


def weather(lat: float, lon: float, place: str) -> Row | None:
    """Today's forecast (Open-Meteo)."""
    d = fetch.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": f"{lat:.3f}", "longitude": f"{lon:.3f}", "timezone": "auto", "forecast_days": "1",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"}).json()["daily"]
    hi, lo = d["temperature_2m_max"][0], d["temperature_2m_min"][0]
    rain = d.get("precipitation_probability_max", [None])[0]
    desc = WMO.get(d["weather_code"][0], "")
    right = f"{hi:.0f}° / {lo:.0f}°, {desc}" + (f", {rain}% rain" if rain else "")
    return Row(f"Weather, {place}", right)


def geocode(name: str) -> tuple[str, float, float] | None:
    """Resolve a place name to (display name, lat, lon). Used once when interests are saved."""
    res = fetch.get("https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": name, "count": "1", "language": "en"}).json().get("results") or []
    if not res:
        return None
    r = res[0]
    return r["name"], float(r["latitude"]), float(r["longitude"])


def today_utc() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()
