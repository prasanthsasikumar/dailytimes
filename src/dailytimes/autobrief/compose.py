"""Build an auto edition from a user's interests.

Sections run in a fixed order and each is skipped if its sources return nothing.
Items whose links the user already saw (60-day history) are skipped *before*
the edition is assembled, so the page still fills up.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from ..blocks import Brief, canonical_url
from . import sources
from .interests import Interests

log = logging.getLogger("dailytimes.autobrief")

# target number of linked items per edition, and the most any one source may contribute
TARGET = {"page": 32, "slip": 12}
CAP = {"topic": 4, "hn": 5, "reddit": 3, "arxiv": 3, "feed": 3}


def _per_source(interests: Interests, size: str) -> dict[str, int]:
    """Share the edition fairly between sources so one interest can't crowd out the rest."""
    n = (len(interests.topics) + len(interests.subreddits) + len(interests.arxiv) + len(interests.feeds)
         + int(interests.hacker_news))
    share = max(1, TARGET.get(size, 32) // max(n, 1))
    return {k: min(cap, share) for k, cap in CAP.items()}


def _run(jobs: dict[str, Callable[[], object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {k: pool.submit(fn) for k, fn in jobs.items()}
        for k, f in futures.items():
            try:
                out[k] = f.result(timeout=15)
            except Exception as e:  # one bad source never sinks the edition
                log.info("auto edition source %s failed: %s", k, e)
    return out


def build_auto_brief(interests: Interests, size: str = "page", seen: set[str] | None = None) -> Brief | None:
    seen = set(seen or ())
    per = _per_source(interests, size)
    jobs: dict[str, Callable[[], object]] = {}
    if interests.location:
        loc = interests.location
        jobs["weather"] = lambda: sources.weather(loc.lat, loc.lon, loc.name)
    for t in interests.tickers:
        jobs[f"quote:{t}"] = lambda t=t: sources.quote(t)
    for t in interests.topics:
        jobs[f"topic:{t}"] = lambda t=t: sources.google_news(t, interests.region, per["topic"] + 2)
    if interests.hacker_news:
        jobs["hn"] = lambda: sources.hacker_news(None, per["hn"] + 2)
    for s in interests.subreddits:
        jobs[f"reddit:{s}"] = lambda s=s: sources.reddit(s, per["reddit"] + 2)
    for c in interests.arxiv:
        jobs[f"arxiv:{c}"] = lambda c=c: sources.arxiv(c, per["arxiv"] + 2)
    for f in interests.feeds:
        jobs[f"feed:{f}"] = lambda f=f: sources.feed(f, per["feed"] + 2)
    if not jobs:
        return None
    got = _run(jobs)

    blocks: list[dict] = []
    used: set[str] = set()

    def fresh(items, n) -> list[sources.Item]:
        keep = []
        for it in items or []:
            key = canonical_url(it.url) if it.url else it.text
            if key in seen or key in used:
                continue
            used.add(key)
            keep.append(it)
            if len(keep) >= n:
                break
        return keep

    credited: list[str] = []

    def credit(name: str) -> None:
        if name not in credited:
            credited.append(name)

    def section(title: str, items: list[sources.Item], source: str) -> None:
        if not items:
            return
        credit(source)
        blocks.append({"type": "heading", "text": title[:1].upper() + title[1:]})
        for it in items:
            blocks.append({"type": "bullet", "text": it.text[:300], **({"url": it.url} if it.url else {})})

    rows = [r for r in [got.get("weather")] if r]
    if rows:
        credit("Open-Meteo")
        blocks.append({"type": "heading", "text": "Today"})
        blocks += [{"type": "row", "left": r.left[:60], "right": r.right[:40]} for r in rows]

    for t in interests.topics:
        section(t[:80], fresh(got.get(f"topic:{t}"), per["topic"]), "Google News")
    papers = [it for c in interests.arxiv for it in fresh(got.get(f"arxiv:{c}"), per["arxiv"])]
    section("New papers", papers, "arXiv")
    posts = [it for s in interests.subreddits for it in fresh(got.get(f"reddit:{s}"), per["reddit"])]
    section("Communities", posts, "Reddit")
    feeds = [it for f in interests.feeds for it in fresh(got.get(f"feed:{f}"), per["feed"])]
    section("Your feeds", feeds, "your feeds")
    if interests.hacker_news:
        section("Hacker News", fresh(got.get("hn"), per["hn"]), "Hacker News")

    quotes = [got.get(f"quote:{t}") for t in interests.tickers]
    quotes = [q for q in quotes if q]
    if quotes:
        credit("Yahoo Finance")
        blocks.append({"type": "heading", "text": "Markets"})
        blocks += [{"type": "row", "left": q.left, "right": q.right} for q in quotes]

    if not any(b["type"] in ("bullet", "row") for b in blocks):
        return None
    blocks.append({"type": "small", "text": "Auto edition: headlines and numbers exactly as published by "
                   + ", ".join(credited) + ". Connect an AI assistant for a personal edition with your calendar and mail."})
    return Brief.model_validate({"blocks": blocks, "title": "Auto edition"})
