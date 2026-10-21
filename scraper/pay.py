"""Pay estimates for postings that don't list pay.

Every posting that does list pay is saved to data/pay_history.json (a year's worth). For a posting
without pay, the estimate is a weighted median of the most similar saved postings: same kind of
role, similar level, same state or nearby, same ERP context and overlapping title words. Each
neighbour's range is nudged up or down for any difference in level (about 10% per step).

Because NY, NJ, MA and MD require pay ranges in postings, the history fills up quickly, and the
estimates get better every day.
"""
from __future__ import annotations

import datetime as dt
import re

# Checked in order; the first match wins.
FAMILIES = [
    ("quant developer", r"quant\w*\s+(developer|engineer|dev)\b"),
    ("quant", r"\bquant"),
    ("pe operations", r"portfolio operations|value creation|operating (partner|associate|principal)"),
    ("solutions", r"forward[- ]deployed|solutions? engineer|implementation (engineer|consultant|specialist|manager)|customer engineer|deployment engineer"),
    ("architect", r"architect"),
    ("leader", r"\b(director|vp|vice president|head of|cio|cto|chief)\b"),
    ("manager", r"\bmanager\b"),
    ("lead", r"\blead\b|\bprincipal\b|\bstaff\b"),
    ("data", r"\bdata\b|analytics|\bbi\b|business intelligence"),
    ("engineer", r"engineer|developer"),
    ("analyst", r"analyst|consultant|specialist|administrator|coordinator"),
]
_FAM = [(name, re.compile(rx, re.I)) for name, rx in FAMILIES]
_LEVELS = [
    (r"\b(vp|vice president|chief|cio|cto)\b", 7.0),
    (r"\b(senior|sr\.?) director\b|\bhead of\b", 6.0),
    (r"\bassociate director\b", 4.5),
    (r"\bdirector\b", 5.0),
    (r"\b(senior|sr\.?) manager\b", 4.0),
    (r"\bmanager\b", 3.5),
    (r"\b(lead|principal|staff)\b", 3.0),
    (r"\b(senior|sr\.?)\b", 2.0),
    (r"\b(junior|jr\.?|entry|associate)\b", 0.5),
]
_LEVELS = [(re.compile(rx, re.I), v) for rx, v in _LEVELS]
_STOP = {"and", "of", "the", "for", "to", "in", "at", "with", "a", "an", "sr", "senior", "jr", "ii", "iii", "iv", "i"}
_TRISTATE = {"NY", "NJ", "CT"}
ERP_TAGS = {"sap", "erp", "s4 migration", "ecc"}


def family(title: str) -> str:
    return next((name for name, rx in _FAM if rx.search(title or "")), "other")


def level(title: str) -> float:
    return next((v for rx, v in _LEVELS if rx.search(title or "")), 1.5)


def tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9/]+", (title or "").lower()) if w not in _STOP and len(w) > 1}


def _profile(j: dict) -> dict:
    states = [l.get("state") for l in j.get("locations", []) if l.get("state")]
    return {
        "family": family(j["title"]), "level": level(j["title"]), "tokens": tokens(j["title"]),
        "state": states[0] if states else ("remote" if j.get("locations") else None),
        "erp": bool(ERP_TAGS & set(j.get("tags", []))), "company": (j.get("company") or "").lower(),
    }


def similarity(a: dict, b: dict) -> float:
    s = 3.0 if a["family"] == b["family"] else 0.0
    s += 2.0 * max(0.0, 1 - abs(a["level"] - b["level"]) / 2.5)
    if a["state"] and a["state"] == b["state"]:
        s += 1.0
    elif a["state"] in _TRISTATE and b["state"] in _TRISTATE:
        s += 0.5
    s += 1.0 if a["erp"] == b["erp"] else 0.0
    union = a["tokens"] | b["tokens"]
    s += 2.0 * (len(a["tokens"] & b["tokens"]) / len(union) if union else 0)
    if a["company"] and a["company"] == b["company"]:
        s += 0.5
    return s


def _wmedian(pairs: list[tuple[float, float]]) -> float:
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return v
    return pairs[-1][0]


def _round5k(v: float) -> int:
    return int(round(v / 5000.0) * 5000)


def update_history(history: list[dict], jobs: list[dict], today: dt.date, keep_days: int = 365, cap: int = 6000) -> list[dict]:
    """Add today's postings that list real pay (not estimates); drop old entries."""
    by_id = {h["id"]: h for h in history}
    for j in jobs:
        if j.get("salary_max") and not j.get("salary_est") and j.get("active", True):
            by_id[j["id"]] = {
                "id": j["id"], "title": j["title"], "company": j["company"], "locations": j.get("locations", []),
                "tags": [t for t in j.get("tags", []) if t in ERP_TAGS], "min": j.get("salary_min") or j["salary_max"],
                "max": j["salary_max"], "seen": today.isoformat(),
            }
    cutoff = (today - dt.timedelta(days=keep_days)).isoformat()
    kept = [h for h in by_id.values() if h["seen"] >= cutoff]
    kept.sort(key=lambda h: h["seen"], reverse=True)
    return kept[:cap]


def estimate(job: dict, history: list[dict], k: int = 7, min_sim: float = 4.0) -> dict | None:
    target = _profile(job)
    scored = []
    for h in history:
        if h["id"] == job.get("id"):
            continue
        p = _profile(h)
        sim = similarity(target, p)
        if sim >= min_sim:
            scored.append((sim, h, p))
    if not scored:
        return None
    scored.sort(key=lambda t: t[0], reverse=True)
    top = scored[:k]
    lows, highs = [], []
    for sim, h, p in top:
        adj = min(1.4, max(0.75, 1.10 ** (target["level"] - p["level"])))  # ~10% per level step
        w = sim * sim
        lows.append((h["min"] * adj, w))
        highs.append((h["max"] * adj, w))
    lo, hi = _round5k(_wmedian(lows)), _round5k(_wmedian(highs))
    if lo > hi:
        lo, hi = hi, lo
    n = len(top)
    avg = sum(t[0] for t in top) / n
    confidence = "close" if n >= 4 and avg >= 6 else "partial" if n >= 2 else "rough"
    return {
        "min": lo, "max": hi, "n": n, "confidence": confidence,
        "examples": [{"title": h["title"], "company": h["company"], "min": h["min"], "max": h["max"]} for _, h, _ in top[:3]],
    }
