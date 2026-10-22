"""Turn "what are you into?" free text into concrete sources.

Default: a transparent keyword heuristic (no third party sees the text).
Optional: if the operator sets ANTHROPIC_API_KEY, Claude proposes the sources;
the sign-up page says so. Either way the user reviews and edits the result.
"""

from __future__ import annotations

import json
import logging
import os
import re

from pydantic import ValidationError

from .interests import Interests

log = logging.getLogger("dailytimes.suggest")

# keyword -> (arXiv categories, follow Hacker News)
CATALOG: list[tuple[str, list[str], bool]] = [
    (r"\b(ai|a\.i\.|artificial intelligence|machine learning|ml|deep learning|llms?|language models?)\b",
     ["cs.AI", "cs.LG"], True),
    (r"\b(hci|human[- ]computer|interaction design|ux|user experience|usability)\b", ["cs.HC"], False),
    (r"\b(xr|vr|ar|mr|augmented reality|virtual reality|mixed reality|spatial computing|metaverse)\b",
     ["cs.HC", "cs.GR"], False),
    (r"\b(computer vision|image recognition)\b", ["cs.CV"], False),
    (r"\b(nlp|natural language)\b", ["cs.CL"], False),
    (r"\b(robot\w*)\b", ["cs.RO"], False),
    (r"\b(security|privacy|cryptography|infosec)\b", ["cs.CR"], True),
    (r"\b(programming|software|developer|startups?|open source|tech)\b", [], True),
    (r"\b(quantum)\b", ["quant-ph"], False),
    (r"\b(astronomy|astrophysics|space)\b", ["astro-ph"], False),
    (r"\b(economics|economy)\b", ["econ.GN"], False),
    (r"\b(neuroscience|brain)\b", ["q-bio.NC"], False),
]
STOP = re.compile(r"^(i'?m|i am|i|my|me|really|also|mostly|mainly|into|interested in|like|love|follow|do|doing|"
                  r"work in|work on|working on|the|and|a|an|some|lots of|news about|news on|about)\s+", re.I)
# acronyms that are ambiguous as news searches ("HCI" is also an insurer)
ALIASES = {"hci": "human-computer interaction", "ar": "augmented reality", "vr": "virtual reality",
           "xr": "extended reality", "mr": "mixed reality", "ai": "artificial intelligence",
           "ml": "machine learning", "ux": "user experience design", "nlp": "natural language processing"}
VERBS = {"follow", "hold", "like", "love", "watch", "read", "enjoy", "work", "live", "track", "own", "study",
         "research", "build", "use", "do", "am", "is", "are", "have", "stuff", "things", "news", "everything"}
_TICKER = re.compile(r"\$([A-Za-z][A-Za-z.]{0,7})\b")
_SUB = re.compile(r"\br/([A-Za-z0-9_]{2,21})\b")
_PLACE = re.compile(r"\b(?:live|living|based|located)\s+in\s+([A-Z][\w\- ]{1,40}?)(?:[,.;\n]|$)")
# weaker: "a designer in Wellington." (capitalised word(s), not an acronym); the user reviews it
_IN_PLACE = re.compile(r"\bin\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)(?=[,.;\n]|$)")
_ROLE = re.compile(r"\b(researcher|engineer|designer|developer|scientist|student|manager|lecturer|professor|"
                   r"analyst|consultant)s?\b", re.I)


def heuristic(about: str) -> tuple[Interests, str | None]:
    text = about.strip()
    subs = _SUB.findall(text)
    tickers = [t.upper() for t in _TICKER.findall(text)]
    arxiv: list[str] = []
    hn = False
    for pattern, cats, follow_hn in CATALOG:
        if re.search(pattern, text, re.I):
            arxiv += [c for c in cats if c not in arxiv]
            hn = hn or follow_hn
    place = _PLACE.search(text) or _IN_PLACE.search(text)
    stripped = _SUB.sub(" ", _TICKER.sub(" ", text))
    if place:
        stripped = stripped.replace(place.group(0), " ")
    topics = []
    for part in re.split(r"[,;\n]|\band\b|\.\s", stripped):
        p = part.strip(" .!?-")
        for _ in range(3):
            p = STOP.sub("", p)
        p = _ROLE.sub("", p).strip()  # "UX researcher" -> "UX"
        p = re.sub(r"\b(" + "|".join(ALIASES) + r")\b", lambda m: ALIASES[m.group(1).lower()], p, flags=re.I)
        if 1 < len(p) <= 60 and len(p.split()) <= 5 and p.lower() not in VERBS:
            topics.append(p)
    data = {"about": text[:1000], "topics": topics[:6], "subreddits": subs[:6], "tickers": tickers[:10],
            "arxiv": arxiv[:5], "hacker_news": hn}
    return Interests.model_validate(data), (place.group(1).strip() if place else None)


SYSTEM = """You turn a person's description of their interests into a source plan for a daily printed news brief.
Return only a JSON object with these keys:
- topics: up to 6 short, unambiguous news search queries (2-4 words each) covering what they care about;
  spell out acronyms that could mean something else (HCI -> "human-computer interaction")
- subreddits: up to 4 existing subreddit names (no r/ prefix) that fit, only if you are confident they exist
- tickers: up to 8 stock/index tickers they mentioned or clearly hold an interest in (Yahoo Finance symbols); never guess
- arxiv: up to 3 arXiv categories (like cs.HC) only if they do research or follow papers
- hacker_news: true if they work in software or tech startups
- place: their city if they mentioned where they live, else null
- region: two-letter country code for their news edition, default "US"
Use only what the text supports. Do not add interests they didn't express."""


def llm(about: str) -> tuple[Interests, str | None] | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic(timeout=30.0)
        response = client.beta.messages.create(
            model=os.environ.get("DAILYTIMES_LLM_MODEL", "claude-opus-5-5"),
            max_tokens=2000,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            messages=[{"role": "user", "content": about[:1000]}],
        )
        if response.stop_reason == "refusal":
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        place = data.pop("place", None)
        data["about"] = about[:1000]
        allowed = set(Interests.model_fields)
        interests = Interests.model_validate({k: v for k, v in data.items() if k in allowed})
        return interests, (str(place)[:80] if place else None)
    except (anthropic.APIError, ValueError, ValidationError, KeyError, TypeError) as e:
        log.warning("LLM suggestion failed, using heuristic: %s", e)
        return None


def suggest(about: str) -> tuple[Interests, str | None, str]:
    """Returns (interests, place name to geocode or None, method: 'llm' | 'keywords')."""
    got = llm(about)
    if got is not None:
        return got[0], got[1], "llm"
    interests, place = heuristic(about)
    return interests, place, "keywords"
