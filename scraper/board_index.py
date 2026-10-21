"""Our own aggregator: an index of company job boards, built from Common Crawl.

Common Crawl publishes a free index of every URL in its monthly web crawl (no account, no key).
Companies that host jobs on Greenhouse, Lever, Ashby, SmartRecruiters or Workday show up there
as URLs like boards.greenhouse.io/<board>/jobs/123, so collecting those URLs gives a list of
thousands of employers' job boards. run.py then checks a slice of the boards each day through
the same public job-board APIs the watchlist uses.

The harvest is incremental: each run reads a few dozen index pages, so the first full pass takes
a few days and a fresh pass starts every `reharvest_days`.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import re
import time

COLLINFO = "https://index.commoncrawl.org/collinfo.json"

# (Common Crawl URL pattern, regex that pulls the board out of a matching URL)
PROVIDERS = {
    "greenhouse": [
        ("boards.greenhouse.io/*", re.compile(r"^https?://boards\.greenhouse\.io/([A-Za-z0-9_-]+)(?=$|[/?#])", re.I)),
        ("job-boards.greenhouse.io/*", re.compile(r"^https?://job-boards\.greenhouse\.io/([A-Za-z0-9_-]+)(?=$|[/?#])", re.I)),
    ],
    "lever": [("jobs.lever.co/*", re.compile(r"^https?://jobs\.lever\.co/([A-Za-z0-9_.-]+)(?=$|[/?#])", re.I))],
    "ashby": [("jobs.ashbyhq.com/*", re.compile(r"^https?://jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)(?=$|[/?#])", re.I))],
    "smartrecruiters": [("jobs.smartrecruiters.com/*", re.compile(r"^https?://jobs\.smartrecruiters\.com/([A-Za-z0-9_-]+)(?=$|[/?#])", re.I))],
    "workday": [("*.myworkdayjobs.com/", re.compile(
        r"^https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)(/[^?#]*)?", re.I))],
}
SKIP = {"embed", "v1", "js", "api", "jobs", "job", "en-us", "oneclick-ui", "wday", "robots.txt", "favicon.ico",
        "sitemap.xml", "assets", "static", "careers", "login", "apply", "search", "privacy", "terms", "404"}
WORKDAY_SKIP = {"wday", "assets", "static", "robots.txt", "favicon.ico"}  # "Careers" is a common real Workday site


def board_key(ats: str, slug: str | None = None, workday: dict | None = None) -> str:
    if ats == "workday" and workday:
        return f"workday:{workday['host'].lower()}/{workday['site'].lower()}"
    return f"{ats}:{(slug or '').lower()}"


def days_since(d: str | None, today: dt.date) -> int:
    return 10**6 if not d else (today - dt.date.fromisoformat(d)).days


def page_plan(pages: int, cap: int) -> list[int]:
    """All pages, or `cap` pages spread evenly across the index when it is very large."""
    if pages <= cap:
        return list(range(pages))
    return sorted({min(pages - 1, round(i * pages / cap)) for i in range(cap)})


def _cdx(http, api: str, params: dict, tries: int = 3) -> str:
    """Common Crawl's index server answers 503 when busy; back off and retry."""
    for attempt in range(tries):
        try:
            return http.get_text(api, params, timeout=90)
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status and 400 <= status < 500 or attempt == tries - 1:
                raise
            time.sleep(15 * (attempt + 1))
    return ""


def _absorb(index: dict, ats: str, rx: re.Pattern, text: str, today: dt.date, wd_rel: re.Pattern, wd_reg: re.Pattern) -> int:
    added = 0
    boards = index["boards"]
    flags = index.setdefault("wd_flags", {})
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            url = json.loads(line).get("url", "")
        except json.JSONDecodeError:
            url = line.split(" ")[-1]
        m = rx.match(url)
        if not m:
            continue
        if ats == "workday":
            tenant, wd, site, path = m.group(1).lower(), m.group(2).lower(), m.group(3), m.group(4) or ""
            if site.lower() in WORKDAY_SKIP:
                continue
            wdcfg = {"host": f"{tenant}.{wd}.myworkdayjobs.com", "tenant": tenant, "site": site}
            key = board_key("workday", workday=wdcfg)
            if key in boards:
                continue
            f = flags.setdefault(key, [False, False])
            f[0] = f[0] or bool(wd_rel.search(path))
            f[1] = f[1] or bool(wd_reg.search(path))
            if f[0] and f[1]:  # has had relevant titles and postings in the region
                boards[key] = {"ats": "workday", "workday": wdcfg, "added": today.isoformat()}
                flags.pop(key, None)
                added += 1
        else:
            slug = m.group(1)
            if slug.lower() in SKIP or len(slug) < 2:
                continue
            key = board_key(ats, slug)
            if key not in boards:
                boards[key] = {"ats": ats, "slug": slug, "added": today.isoformat()}
                added += 1
    return added


def harvest_step(http, index: dict, cfg: dict, today: dt.date, wd_rel: re.Pattern, wd_reg: re.Pattern) -> str:
    """Read the next few index pages. Returns a one-line progress note."""
    index.setdefault("boards", {})
    h = index.setdefault("harvest", {})
    if h.get("finished") and days_since(h["finished"], today) < int(cfg.get("reharvest_days", 30)):
        return f"index from {h.get('crawl')} is current"
    if not h.get("queries") or h.get("finished"):
        latest = http.get_json(COLLINFO, timeout=60)[0]
        h.clear()
        h.update(crawl=latest["id"], api=latest["cdx-api"], started=today.isoformat(), finished=None, queries={})
        for ats in cfg.get("providers", list(PROVIDERS)):
            for pattern, _ in PROVIDERS.get(ats, []):
                h["queries"][f"{ats}|{pattern}"] = {"pages": None, "next": 0, "done": False}
    pending = [k for k, v in h["queries"].items() if not v["done"]]
    budget = int(cfg.get("harvest_pages_per_run", 60))
    per = max(1, math.ceil(budget / max(1, len(pending))))
    fetched = added = 0
    for key in pending:
        ats, pattern = key.split("|", 1)
        rx = dict(PROVIDERS[ats])[pattern]
        st = h["queries"][key]
        if st["pages"] is None:
            info = json.loads(_cdx(http, h["api"], {"url": pattern, "output": "json", "showNumPages": "true"}).strip().splitlines()[0])
            st["pages"] = int(info.get("pages", 0))
            st["plan"] = page_plan(st["pages"], int(cfg.get("max_pages_per_query", 400)))
        n = 0
        while st["next"] < len(st["plan"]) and n < per and fetched < budget:
            text = _cdx(http, h["api"], {"url": pattern, "output": "json", "fl": "url", "page": st["plan"][st["next"]]})
            added += _absorb(index, ats, rx, text, today, wd_rel, wd_reg)
            st["next"] += 1
            n += 1
            fetched += 1
        if st["next"] >= len(st["plan"]):
            st["done"] = True
    done = sum(len(v.get("plan") or []) if v["done"] else v["next"] for v in h["queries"].values())
    total = sum(len(v.get("plan") or []) for v in h["queries"].values() if v.get("plan") is not None)
    if all(v["done"] for v in h["queries"].values()):
        h["finished"] = today.isoformat()
        index.pop("wd_flags", None)
        return f"harvest of {h['crawl']} finished: {len(index['boards']):,} boards (+{added} today)"
    return f"harvesting {h['crawl']}: {done}/{total or '?'} index pages read, {len(index['boards']):,} boards so far (+{added} today)"


def pick_boards(index: dict, today: dt.date, cfg: dict, skip: set[str]) -> list[tuple[str, dict]]:
    """Boards with recent matches every day; everything else every `recheck_days`, oldest first."""
    hot_days = int(cfg.get("hot_days", 45))
    recheck = int(cfg.get("recheck_days", 5))
    budget = int(cfg.get("boards_per_run", 4000))
    hot, due = [], []
    for key, b in index.get("boards", {}).items():
        if key in skip or b.get("fails", 0) >= 3:
            continue
        if days_since(b.get("last_hit"), today) <= hot_days:
            hot.append((key, b))
        elif days_since(b.get("last_checked"), today) >= recheck:
            due.append((key, b))
    due.sort(key=lambda kb: kb[1].get("last_checked") or "")  # never checked first
    return hot + due[: max(0, budget - len(hot))]


_TITLE_JUNK = re.compile(r"^(jobs at|careers at|current openings at|join)\s+|\s*[-|–·:]\s*(jobs|careers|job board|current openings|open positions).*$|\s+(jobs|careers)$", re.I)


def prettify(slug: str) -> str:
    s = re.sub(r"[-_.]+", " ", slug).strip()
    return s.title() if s.islower() or s.isupper() else s


def clean_title(t: str) -> str:
    t = html.unescape(re.sub(r"\s+", " ", t or "")).strip()
    return _TITLE_JUNK.sub("", t).strip()


def board_name(http, b: dict, raws: list[dict]) -> str:
    """Best display name for an indexed board; falls back to a tidied board slug."""
    ats, slug = b["ats"], b.get("slug") or ""
    try:
        if ats == "greenhouse":
            name = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}").get("name")
        elif ats in ("lever", "ashby"):
            host = "jobs.lever.co" if ats == "lever" else "jobs.ashbyhq.com"
            m = re.search(r"<title[^>]*>(.*?)</title>", http.get_text(f"https://{host}/{slug}"), re.I | re.S)
            name = clean_title(m.group(1)) if m else None
        elif ats == "smartrecruiters":
            name = next((r["company"] for r in raws if r.get("company")), None)
        else:  # workday: the posting detail carries the employer name
            name = next((r.get("org_name") for r in raws if r.get("org_name")), None)
    except Exception:
        name = None
    if name:
        return name.strip()
    return prettify(slug or (b.get("workday") or {}).get("tenant", "Unknown"))
