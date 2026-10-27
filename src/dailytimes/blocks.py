"""Format-neutral brief blocks: the one schema shared by relay, renderers and agent.

A brief is a short list of layout blocks. Nothing here knows about paper,
pixels or printers; renderers decide how each block looks on a given profile.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import unicodedata
from typing import Annotated, Literal, Union
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

MAX_BLOCKS = 80
MAX_TOTAL_CHARS = 6000
LEAD_MAX = 40  # a few bold words run in before an item, newspaper style
MAX_URL_LEN = 2048  # news redirect links (Google News) routinely exceed 500

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_WS = re.compile(r"\s+")


def _clean_text(value: str, *, single_line: bool) -> str:
    if _CONTROL.search(value):
        raise ValueError("control characters are not allowed")
    if single_line and ("\n" in value or "\r" in value):
        raise ValueError("must be a single line; use separate blocks instead")
    value = _WS.sub(" ", value).strip()
    if not value:
        raise ValueError("must not be empty")
    return value


def _check_url(value: str) -> str:
    value = value.strip()
    if len(value) > MAX_URL_LEN:
        raise ValueError(f"url longer than {MAX_URL_LEN} characters")
    if any(c.isspace() for c in value):
        raise ValueError("url must not contain whitespace")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or "." not in (parts.hostname or ""):
        raise ValueError("url must be an absolute http(s) link to a real source")
    return value


class _Block(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Heading(_Block):
    """Section heading. level 1 = section, level 2 = headline inside a section."""

    type: Literal["heading"]
    text: str = Field(min_length=1, max_length=80)
    level: Literal[1, 2] = 1

    @field_validator("text")
    @classmethod
    def _text(cls, v: str) -> str:
        return _clean_text(v, single_line=True)


class Text(_Block):
    """A short paragraph. Optional `lead` runs in bold before it; optional `url` is its single source link."""

    type: Literal["text"]
    text: str = Field(min_length=1, max_length=600)
    lead: str | None = Field(default=None, max_length=LEAD_MAX)
    url: str | None = None

    @field_validator("text")
    @classmethod
    def _text(cls, v: str) -> str:
        return _clean_text(v, single_line=False)

    @field_validator("lead")
    @classmethod
    def _lead(cls, v: str | None) -> str | None:
        return None if v is None else _clean_text(v, single_line=True)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        return None if v is None else _check_url(v)


class Bullet(_Block):
    """One list item. Optional `lead` runs in bold before it; optional `url` is its single source link."""

    type: Literal["bullet"]
    text: str = Field(min_length=1, max_length=300)
    lead: str | None = Field(default=None, max_length=LEAD_MAX)
    url: str | None = None

    @field_validator("text")
    @classmethod
    def _text(cls, v: str) -> str:
        return _clean_text(v, single_line=False)

    @field_validator("lead")
    @classmethod
    def _lead(cls, v: str | None) -> str | None:
        return None if v is None else _clean_text(v, single_line=True)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        return None if v is None else _check_url(v)


class Row(_Block):
    """Left/right pair, e.g. a calendar slot or a stock price."""

    type: Literal["row"]
    left: str = Field(min_length=1, max_length=60)
    right: str = Field(min_length=1, max_length=40)

    @field_validator("left", "right")
    @classmethod
    def _text(cls, v: str) -> str:
        return _clean_text(v, single_line=True)


class Divider(_Block):
    type: Literal["divider"]


class QR(_Block):
    """A QR code for one link; the link is also printed as short text."""

    type: Literal["qr"]
    url: str
    caption: str | None = Field(default=None, max_length=80)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _check_url(v)

    @field_validator("caption")
    @classmethod
    def _caption(cls, v: str | None) -> str | None:
        return None if v is None else _clean_text(v, single_line=True)


class Small(_Block):
    """Small print: footnotes, sources, disclaimers."""

    type: Literal["small"]
    text: str = Field(min_length=1, max_length=300)

    @field_validator("text")
    @classmethod
    def _text(cls, v: str) -> str:
        return _clean_text(v, single_line=False)


Block = Annotated[Union[Heading, Text, Bullet, Row, Divider, QR, Small], Field(discriminator="type")]
CONTENT_TYPES = ("text", "bullet", "row", "qr", "small")
BlockList = TypeAdapter(list[Block])


class Brief(BaseModel):
    """A day's brief. `date` defaults to the device's local today at the relay."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    blocks: list[Block] = Field(min_length=1, max_length=MAX_BLOCKS)
    title: str | None = Field(default=None, max_length=60)
    date: dt.date | None = None

    @field_validator("title")
    @classmethod
    def _title(cls, v: str | None) -> str | None:
        return None if v is None else _clean_text(v, single_line=True)

    @model_validator(mode="after")
    def _limits(self) -> "Brief":
        if not any(b.type in CONTENT_TYPES for b in self.blocks):
            raise ValueError("brief has no content blocks (text, bullet, row, qr or small)")
        total = sum(len(t) for b in self.blocks for t in block_texts(b))
        if total > MAX_TOTAL_CHARS:
            raise ValueError(f"brief is {total} characters; keep it under {MAX_TOTAL_CHARS}")
        return self

    def blocks_json(self) -> list[dict]:
        return [b.model_dump(exclude_none=True) for b in self.blocks]

    def content_hash(self) -> str:
        payload = json.dumps({"title": self.title, "blocks": self.blocks_json()}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


def block_texts(b: _Block) -> list[str]:
    out = []
    for name in ("lead", "text", "left", "right", "caption"):
        v = getattr(b, name, None)
        if v:
            out.append(v)
    return out


# ---------------------------------------------------------------- links

_TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|mc_cid|mc_eid|ref|ref_src|igshid|si)$", re.I)


def canonical_url(url: str) -> str:
    """Normalise a URL for dedup: lowercase host, no www, no tracking params or fragment."""
    p = urlsplit(url.strip())
    host = (p.hostname or "").lower().removeprefix("www.")
    if host.startswith("old.reddit.") or host.startswith("m."):
        host = host.split(".", 1)[1]
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query) if not _TRACKING.match(k)))
    path = p.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, query, ""))


def short_link(url: str, limit: int = 40) -> str:
    """Human-typeable short form of a link for printing next to an item."""
    p = urlsplit(url)
    host = (p.hostname or "").lower().removeprefix("www.")
    if host == "news.google.com" and p.path.startswith(("/rss/articles/", "/articles/")):
        return "via Google News"  # opaque redirect id; the publisher is named in the headline
    path = p.path.rstrip("/")
    text = host + path
    if len(text) <= limit:
        return text
    if len(host) >= limit - 2:
        return host[: limit - 1] + "…"
    return text[: limit - 1] + "…"


def source_name(url: str) -> str:
    """The publisher's domain, for the source line printed under an item (the full link is noise on paper)."""
    p = urlsplit(url)
    host = (p.hostname or "").lower().removeprefix("www.")
    if host == "news.google.com" and p.path.startswith(("/rss/articles/", "/articles/")):
        return "via Google News"
    return host


def fingerprint(b: _Block) -> str | None:
    """Dedup key for a content item: its source link. Items without links aren't deduped."""
    url = getattr(b, "url", None)
    return canonical_url(url) if url else None


# ---------------------------------------------------------------- plain text input

_URL_TAIL = re.compile(r"\s*\(?(https?://\S+?)\)?\s*$")


def parse_plaintext(text: str, title: str | None = None) -> Brief:
    """Turn a markdown-ish note (email body, webhook text) into a Brief.

    `# H` heading, `## H` headline, `- item` bullet, `---` divider,
    `left | right` row, `qr: URL caption` QR, `small: text` small print,
    anything else is a paragraph. A trailing URL becomes the item's link.
    """
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n"))
    blocks: list[dict] = []
    para: list[str] = []

    def flush():
        if para:
            blocks.append(_with_url({"type": "text"}, " ".join(para)))
            para.clear()

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            flush()
            continue
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", line):
            flush()
            blocks.append({"type": "divider"})
        elif line.startswith("## "):
            flush()
            blocks.append({"type": "heading", "level": 2, "text": line[3:]})
        elif line.startswith("# "):
            flush()
            blocks.append({"type": "heading", "level": 1, "text": line[2:]})
        elif line[:2] in ("- ", "* ", "• "):
            flush()
            blocks.append(_with_url({"type": "bullet"}, line[2:]))
        elif line.lower().startswith("qr:"):
            flush()
            rest = line[3:].strip().split(None, 1)
            blocks.append({"type": "qr", "url": rest[0], **({"caption": rest[1]} if len(rest) > 1 else {})})
        elif line.lower().startswith("small:"):
            flush()
            blocks.append({"type": "small", "text": line[6:].strip()})
        elif line.count("|") == 1 and "://" not in line:
            flush()
            left, right = (s.strip() for s in line.split("|"))
            blocks.append({"type": "row", "left": left, "right": right})
        else:
            para.append(line)
    flush()
    return Brief.model_validate({"blocks": blocks, "title": title})


def _with_url(block: dict, text: str) -> dict:
    m = _URL_TAIL.search(text)
    if m and m.start() > 0:
        block["url"] = m.group(1)
        text = text[: m.start()]
    block["text"] = text.strip()
    return block
