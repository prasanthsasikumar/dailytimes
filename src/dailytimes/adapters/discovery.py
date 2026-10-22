"""Printer discovery helpers and profile suggestion."""

from __future__ import annotations

import locale
import os
import re

from .base import REGISTRY, DiscoveredPrinter

LETTER_REGIONS = {"US", "CA", "MX", "PH", "CL", "CO", "VE", "GT", "PR"}

_THERMAL_58 = re.compile(r"58|pos-?5|xp-?5|gb0\d|gt0\d|mx0\d|cat ?printer|peripage|paperang", re.I)
_THERMAL_80 = re.compile(r"80|tm-|tsp|pos-?8|xp-?8|receipt|thermal|rongta|bixolon|citizen ct", re.I)


def default_page_profile() -> str:
    lang = os.environ.get("LC_PAPER") or os.environ.get("LC_ALL") or os.environ.get("LANG") or ""
    if not lang:
        lang = (locale.getlocale()[0] or "")
    m = re.search(r"_([A-Z]{2})", lang)
    return "letter" if m and m.group(1) in LETTER_REGIONS else "a4"


def suggest_profile(*hints: str) -> str:
    text = " ".join(h for h in hints if h)
    if _THERMAL_58.search(text):
        return "thermal-58mm"
    if _THERMAL_80.search(text):
        return "thermal-80mm"
    return default_page_profile()


def discover_all(timeout: float = 2.0) -> list[DiscoveredPrinter]:
    found: list[DiscoveredPrinter] = []
    seen: set[tuple[str, str]] = set()
    for cls in REGISTRY.values():
        try:
            items = cls.discover(timeout=timeout)
        except Exception:
            continue
        for p in items:
            key = (p.adapter, str(sorted(p.options.items())))
            if key not in seen:
                seen.add(key)
                found.append(p)
    return found
