"""Signals: news about new plants, S/4HANA programs and carve-outs, from Google News RSS.

These aren't job postings. They point at companies that will be hiring for plant ramp-ups
or ERP programs, so you can add them to your watchlist or plant list.
"""
from __future__ import annotations

import email.utils
import hashlib
import re
import xml.etree.ElementTree as ET

from .normalize import STATES, _NAMES

SIGNAL_RULES = [
    ("plant announcement", r"new (manufacturing )?(plant|facility|factory)|groundbreak|break(s|ing)? ground|to build|"
                           r"expansion|invest\w* \$|\$\d[\d.,]* ?(million|billion|m\b|b\b)|headquarters and plant"),
    ("plant opening", r"\bopens?\b|opening|ribbon[- ]cutting|begins? production|starts? production|first shipments?"),
    ("s4 migration", r"s/?4\s?hana|\becc\b|\bsap\b|erp (migration|implementation|go-live)"),
    ("carve-out", r"carve[- ]?out|divest|spin[- ]?off|to acquire|acquires|acquisition"),
]
_RX = [(t, re.compile(p, re.I)) for t, p in SIGNAL_RULES]


def _state(title: str) -> str | None:
    low = title.lower()
    for name, code in _NAMES:
        if re.search(r"\b" + re.escape(name) + r"\b", low):
            return code
    m = re.search(r",\s*([A-Z]{2})\b", title)
    return m.group(1) if m and m.group(1) in STATES else None


def google_news(http, query: str) -> list[dict]:
    xml = http.get_text("https://news.google.com/rss/search", {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    root = ET.fromstring(xml)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        if source and title.endswith(" - " + source):
            title = title[: -(len(source) + 3)]
        pub = item.findtext("pubDate")
        try:
            date = email.utils.parsedate_to_datetime(pub).date().isoformat() if pub else None
        except (TypeError, ValueError):
            date = None
        tags = [t for t, rx in _RX if rx.search(title)]
        out.append({
            "id": hashlib.sha1(title.lower().encode()).hexdigest()[:12],
            "title": title, "url": (item.findtext("link") or "").strip(), "source": source,
            "date": date, "tags": tags, "state": _state(title), "query": query,
        })
    return out
