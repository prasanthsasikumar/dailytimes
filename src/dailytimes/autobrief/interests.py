"""What a user wants in their auto edition. Stored as JSON on the user row."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SUB = re.compile(r"^[A-Za-z0-9_]{2,21}$")
_TICKER = re.compile(r"^[A-Z0-9.^=\-]{1,12}$")
_ARXIV = re.compile(r"^[a-z\-]+(\.[A-Za-z\-]+)?$")


class Location(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=80)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class Interests(BaseModel):
    model_config = ConfigDict(extra="forbid")

    about: str = Field(default="", max_length=1000)  # free text, as the user wrote it
    topics: list[str] = Field(default_factory=list, max_length=8)
    subreddits: list[str] = Field(default_factory=list, max_length=6)
    tickers: list[str] = Field(default_factory=list, max_length=10)
    arxiv: list[str] = Field(default_factory=list, max_length=5)
    feeds: list[str] = Field(default_factory=list, max_length=6)
    hacker_news: bool = False
    location: Location | None = None
    region: str = Field(default="US", pattern=r"^[A-Z]{2}$")  # news edition (Google News gl=)

    @field_validator("topics")
    @classmethod
    def _topics(cls, v):
        out = []
        for t in v:
            t = " ".join(t.split())[:60]
            if t and t.lower() not in (o.lower() for o in out):
                out.append(t)
        return out

    @field_validator("subreddits")
    @classmethod
    def _subs(cls, v):
        out = []
        for s in v:
            s = s.strip().removeprefix("/").removeprefix("r/")
            if not _SUB.match(s):
                raise ValueError(f"not a subreddit name: {s!r}")
            out.append(s)
        return list(dict.fromkeys(out))

    @field_validator("tickers")
    @classmethod
    def _tickers(cls, v):
        out = []
        for t in v:
            t = t.strip().upper().removeprefix("$")
            if not _TICKER.match(t):
                raise ValueError(f"not a ticker symbol: {t!r}")
            out.append(t)
        return list(dict.fromkeys(out))

    @field_validator("arxiv")
    @classmethod
    def _arxiv(cls, v):
        for c in v:
            if not _ARXIV.match(c):
                raise ValueError(f"not an arXiv category: {c!r} (e.g. cs.HC)")
        return list(dict.fromkeys(v))

    @field_validator("feeds")
    @classmethod
    def _feeds(cls, v):
        for f in v:
            if not re.match(r"^https?://[^\s/]+\.[^\s]+$", f):
                raise ValueError(f"not a feed URL: {f!r}")
        return list(dict.fromkeys(v))

    def is_empty(self) -> bool:
        return not (self.topics or self.subreddits or self.tickers or self.arxiv or self.feeds
                    or self.hacker_news or self.location)
