"""Daily run: python -m scraper.run  (see README for options)."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import yaml

from . import sources
from .client import FixtureHttp, Http, scrub
from .news import google_news
from .normalize import in_region, is_full_time, parse_locations, parse_salary, summarize
from .tagger import score_job, tag_job

ROOT = Path(__file__).resolve().parent.parent
AGGREGATORS = {"adzuna", "hn"}
_REMOTE_TITLE = re.compile(r"\bremote\b", re.I)  # partial views: absence from one run doesn't mean closed


def norm(s: str) -> str:
    s = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|the)\b\.?", "", (s or "").lower())
    return re.sub(r"[^a-z0-9]+", "", s)


def job_id(company: str, title: str, state: str | None) -> str:
    return hashlib.sha1(f"{norm(company)}|{norm(title)}|{state or ''}".encode()).hexdigest()[:12]


def load_yaml(p: Path, default):
    return yaml.safe_load(p.read_text()) if p.exists() else default


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")


class Run:
    def __init__(self, cfg: dict, http, today: dt.date):
        self.cfg, self.http, self.today = cfg, http, today
        r = cfg.get("regions", {})
        self.states = [s.upper() for s in r.get("states", [])]
        self.state_set = set(self.states)
        self.remote_ok = bool(r.get("include_remote_us", True))
        self.exclude_cities = {c.lower() for c in r.get("exclude_cities", [])}
        rel = cfg.get("relevance", {})
        self.title_include = re.compile("|".join(rel.get("title_include", [".*"])), re.I)
        self.title_exclude = re.compile("|".join(rel.get("title_exclude", ["$^"])), re.I)
        self.detail_budget = int(cfg.get("detail_budget", 150))
        self.health: list[dict] = []
        self.ok_feeds: set[str] = set()

    def note(self, name: str, kind: str, status: str, jobs: int = 0, note: str = "") -> None:
        self.health.append({"name": name, "kind": kind, "status": status, "jobs": jobs, "note": scrub(note)[:300]})

    # ------------------------------------------------------------ processing

    def process(self, r: dict) -> dict | None:
        title = r["title"]
        if not title or self.title_exclude.search(title) or not self.title_include.search(title):
            return None
        locs = parse_locations(r["locations"])
        if not any(l.get("state") or l.get("remote") for l in locs) and r.get("detail") and self.detail_budget > 0:
            self._enrich(r)  # e.g. Workday "2 Locations"
            locs = parse_locations(r["locations"])
        if self.remote_ok and _REMOTE_TITLE.search(title) and not any(l.get("state") for l in locs) and all(l.get("us") for l in locs):
            locs.append({"label": "Remote (US)", "city": None, "state": None, "remote": True, "us": True})
        if not in_region(locs, self.state_set, self.remote_ok, self.exclude_cities):
            return None
        max_age = int(self.cfg.get("max_age_days", 45))
        if r["posted"] and (self.today - dt.date.fromisoformat(r["posted"])).days > max_age:
            return None
        if r.get("detail") and self.detail_budget > 0:
            self._enrich(r)
        if self.cfg.get("full_time_only", True) and not is_full_time(r["employment_type"], title, r["text"]):
            return None
        salary, est = r["salary"], r["salary_est"]
        if not salary:
            salary, est = parse_salary(r["text"]), False
        tags, mode, travel = tag_job(title, r["text"], locs, r["company_cfg"], r["workplace"], r["extra_tags"])
        smax = salary[1] if salary else None
        keep = [l for l in locs if l.get("state") in self.state_set or (l.get("remote") and l.get("us") and not l.get("state"))]
        first_state = next((l["state"] for l in keep if l.get("state")), None)
        return {
            "id": job_id(r["company"], title, first_state),
            "title": title,
            "company": r["company"],
            "locations": [{"label": l["label"], "state": l["state"], "remote": l["remote"]} for l in keep],
            "salary_min": salary[0] if salary else None,
            "salary_max": smax,
            "salary_est": bool(est) if salary else False,
            "summary": summarize(r["text"], company=r["company"]) or None,
            "url": r["url"],
            "posted": r["posted"] or self.today.isoformat(),
            "sources": [r["source"]],
            "feed": r.get("feed", r["source"]),
            "tags": tags,
            "mode": mode,
            "travel": travel,
            "score": score_job(title, tags, smax, self.cfg.get("scoring", {})),
        }

    def _enrich(self, r: dict) -> None:
        self.detail_budget -= 1
        try:
            sources.enrich(self.http, r)
        except Exception as e:  # keep the listing even if the detail call fails
            r["detail"] = None
            print("detail failed:", scrub(e), file=sys.stderr)

    # ------------------------------------------------------------ collection

    def collect(self, companies: list[dict], discovered: dict, offline: bool) -> list[dict]:
        out: list[dict] = []

        a = self.cfg.get("adzuna", {})
        if a.get("enabled"):
            app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
            if offline:
                app_id, app_key = app_id or "fixture", app_key or "fixture"
            if not (app_id and app_key):
                self.note("Adzuna", "aggregator", "skipped", note="Add ADZUNA_APP_ID and ADZUNA_APP_KEY secrets to enable.")
            else:
                n = self._drain("Adzuna", "aggregator", "adzuna",
                                sources.adzuna(self.http, a, app_id, app_key, self.states, self.today), out)
                if n is not None:
                    self.ok_feeds.add("adzuna")

        for c in companies:
            self._company(c, discovered, out)

        h = self.cfg.get("hn", {})
        if h.get("enabled"):
            if self._drain("HN Who’s Hiring", "aggregator", "hn", sources.hn_whoishiring(self.http, h), out) is not None:
                self.ok_feeds.add("hn")
        return out

    def _company(self, c: dict, discovered: dict, out: list, retried: bool = False) -> None:
        resolved = self._resolve(c, discovered)
        if not resolved:
            return
        fetchers = {
            "greenhouse": lambda: sources.greenhouse(self.http, resolved),
            "lever": lambda: sources.lever(self.http, resolved),
            "ashby": lambda: sources.ashby(self.http, resolved),
            "smartrecruiters": lambda: sources.smartrecruiters(self.http, resolved),
            "workday": lambda: sources.workday(self.http, resolved, self.today),
        }
        fetch = fetchers.get(resolved["ats"])
        if not fetch:
            self.note(c["name"], "company", "error", note=f"Unknown ats '{resolved['ats']}'.")
            return
        feed = f"{resolved['ats']}:{c['name']}"
        n = self._drain(c["name"], resolved["ats"], feed, fetch(), out)
        if n is not None:
            self.ok_feeds.add(feed)
        elif not retried and c.get("careers_url") and (c.get("slug") or c.get("workday")):
            # The slug in companies.yaml failed; try discovering it from the careers page.
            self.health.pop()
            discovered.pop(c["careers_url"], None)
            bare = {k: v for k, v in c.items() if k not in ("ats", "slug", "workday")}
            self._company(bare, discovered, out, retried=True)

    def _resolve(self, c: dict, discovered: dict) -> dict | None:
        if c.get("ats") and (c.get("slug") or c.get("workday")):
            return c
        url = c.get("careers_url")
        if not url:
            self.note(c["name"], "company", "error", note="Needs ats + slug, or a careers_url to discover them.")
            return None
        cached = discovered.get(url)
        stale = cached and cached.get("error") and (self.today - dt.date.fromisoformat(cached["checked"])).days >= 7
        if not cached or stale:
            try:
                found = sources.discover(self.http, url)
                cached = (found or {"error": "No Greenhouse, Lever, Ashby, Workday or SmartRecruiters link on the page."})
            except Exception as e:
                cached = {"error": f"Couldn’t load careers page: {scrub(e)[:160]}"}
            cached["checked"] = self.today.isoformat()
            discovered[url] = cached
        if cached.get("error"):
            self.note(c["name"], "company", "needs setup", note=cached["error"])
            return None
        return c | {k: v for k, v in cached.items() if k in ("ats", "slug", "workday")}

    def _drain(self, name: str, kind: str, feed: str, gen, out: list) -> int | None:
        n = 0
        try:
            for r in gen:
                r["feed"] = feed
                job = self.process(r)
                if job:
                    out.append(job)
                    n += 1
        except Exception as e:
            self.note(name, kind, "error", n, str(e))
            return None
        self.note(name, kind, "ok", n)
        return n


def dedupe(jobs: list[dict]) -> list[dict]:
    """Same company + title + state from several feeds: keep the richest record."""
    best: dict[str, dict] = {}
    for j in jobs:
        cur = best.get(j["id"])
        if not cur:
            best[j["id"]] = j
            continue
        a, b = (cur, j) if cur["sources"][0] not in AGGREGATORS else (j, cur)
        merged = dict(a)
        merged["sources"] = sorted(set(cur["sources"]) | set(j["sources"]))
        merged["feeds"] = sorted({cur.get("feed", ""), j.get("feed", "")} - {""})
        if merged["salary_max"] is None or (merged["salary_est"] and b["salary_max"] and not b["salary_est"]):
            merged.update(salary_min=b["salary_min"], salary_max=b["salary_max"], salary_est=b["salary_est"])
        merged["tags"] = list(dict.fromkeys(a["tags"] + b["tags"]))
        merged["summary"] = a["summary"] or b["summary"]
        merged["posted"] = min(a["posted"], b["posted"])
        merged["score"] = max(a["score"], b["score"])
        best[j["id"]] = merged
    return list(best.values())


def merge_history(prev: list[dict], fresh: list[dict], today: dt.date, ok_feeds: set[str], cfg: dict) -> list[dict]:
    t = today.isoformat()
    old = {j["id"]: j for j in prev}
    out = []
    for j in fresh:
        p = old.pop(j["id"], None)
        j["first_seen"] = p["first_seen"] if p else t
        j["last_seen"] = t
        j["active"] = True
        out.append(j)
    retention = int(cfg.get("retention_days", 30))
    for j in old.values():
        unseen = (today - dt.date.fromisoformat(j["last_seen"])).days
        if unseen > retention:
            continue
        feeds = set(j.get("feeds") or [j.get("feed", "")])
        if any(f.split(":")[0] in AGGREGATORS or f in AGGREGATORS for f in feeds):
            j["active"] = unseen <= 14  # aggregator queries rotate, so give them two weeks
        elif feeds & ok_feeds:
            j["active"] = False  # the company's own board loaded and the job is gone
        out.append(j)
    out.sort(key=lambda j: (j["posted"], j["score"]), reverse=True)
    return out


def merge_signals(prev: list[dict], fresh: list[dict], today: dt.date, keep_days: int) -> list[dict]:
    by_id = {s["id"]: s for s in prev}
    for s in fresh:
        by_id.setdefault(s["id"], s | {"first_seen": today.isoformat()})
    cutoff = (today - dt.timedelta(days=keep_days)).isoformat()
    items = [s for s in by_id.values() if (s.get("date") or s.get("first_seen", "")) >= cutoff]
    return sorted(items, key=lambda s: s.get("date") or "", reverse=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fetch, tag and publish job postings for Shift Board.")
    ap.add_argument("--config", default=str(ROOT / "config"), help="folder with settings.yaml and companies.yaml")
    ap.add_argument("--out", default=str(ROOT / "web" / "data"), help="where jobs.json, signals.json, health.json go")
    ap.add_argument("--state", default=str(ROOT / "data"), help="where the ATS discovery cache lives")
    ap.add_argument("--companies", help="watchlist file (default: <config>/companies.yaml)")
    ap.add_argument("--offline", help="replay fixtures from this folder instead of calling the network")
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD), for tests")
    args = ap.parse_args(argv)

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    cfg_dir, out_dir, state_dir = Path(args.config), Path(args.out), Path(args.state)
    cfg = load_yaml(cfg_dir / "settings.yaml", {})
    companies = load_yaml(Path(args.companies) if args.companies else cfg_dir / "companies.yaml", []) or []
    http = FixtureHttp(args.offline) if args.offline else Http()

    run = Run(cfg, http, today)
    discovered = load_json(state_dir / "discovered.json", {})
    jobs = dedupe(run.collect(companies, discovered, bool(args.offline)))
    prev = load_json(out_dir / "jobs.json", {}).get("jobs", [])
    jobs = merge_history(prev, jobs, today, run.ok_feeds, cfg)

    signals_prev = load_json(out_dir / "signals.json", {}).get("items", [])
    fresh_signals = []
    n = cfg.get("news", {})
    if n.get("enabled"):
        for q in n.get("queries", []):
            try:
                got = google_news(http, q)
                fresh_signals += got
                run.note(f"News: {q[:48]}", "signals", "ok", len(got))
            except Exception as e:
                run.note(f"News: {q[:48]}", "signals", "error", 0, str(e))
    signals = merge_signals(signals_prev, fresh_signals, today, int(n.get("keep_days", 60)))

    stamp = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat() if not args.today else f"{args.today}T11:15:00+00:00"
    write_json(out_dir / "jobs.json", {"generated": stamp, "count": len(jobs), "jobs": jobs})
    write_json(out_dir / "signals.json", {"generated": stamp, "items": signals})
    write_json(out_dir / "health.json", {"generated": stamp, "calls": http.calls, "sources": run.health})
    write_json(state_dir / "discovered.json", discovered)
    active = sum(1 for j in jobs if j["active"])
    print(f"{active} active jobs ({len(jobs)} incl. recently closed), {len(signals)} signals, {http.calls} requests")
    for h in run.health:
        if h["status"] != "ok":
            print(f"  {h['status']}: {h['name']} - {h['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
