"""Job sources. Each fetcher yields "raw" dicts; run.py filters, tags and merges them.

All endpoints here are public job-board APIs that the boards themselves expose for career
pages. No logins, no scraping of LinkedIn or Indeed.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Iterator

from .normalize import clean_html, parse_salary


def raw(source: str, native_id, company: str, title: str, locations: list, url: str, posted,
        employment_type: str | None = None, salary: tuple | None = None, salary_est: bool = False,
        text: str = "", workplace: str | None = None, extra_tags: list[str] | None = None,
        detail: dict | None = None, company_cfg: dict | None = None) -> dict:
    return {
        "source": source, "native_id": str(native_id), "company": (company or "").strip(),
        "title": clean_html(title), "locations": [l for l in locations if l], "url": url,
        "posted": to_date(posted), "employment_type": employment_type, "salary": salary,
        "salary_est": salary_est, "text": text or "", "workplace": workplace,
        "extra_tags": extra_tags or [], "detail": detail, "company_cfg": company_cfg or {},
    }


def to_date(v) -> str | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):  # epoch milliseconds (Lever)
        return dt.datetime.fromtimestamp(v / 1000, dt.timezone.utc).date().isoformat()
    s = str(v)
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else None


# ---------------------------------------------------------------- Adzuna (aggregator)

def adzuna_pairs(cfg: dict, states: list[str], today: dt.date) -> list[tuple[str, str | None]]:
    """All (query, where) pairs, then the slice for today when rotating to save API calls."""
    names = {"NY": "New York", "NJ": "New Jersey", "CT": "Connecticut", "PA": "Pennsylvania",
             "MA": "Massachusetts", "MD": "Maryland", "RI": "Rhode Island", "DE": "Delaware"}
    pairs = []
    for q in cfg.get("queries", []):
        if q.get("scope", "states") == "national":
            pairs.append((q["what"], None))
        else:
            pairs += [(q["what"], names.get(s, s)) for s in states]
    rotate = max(1, int(cfg.get("rotate_days", 1)))
    slot = today.toordinal() % rotate
    return [p for i, p in enumerate(pairs) if i % rotate == slot]


def adzuna(http, cfg: dict, app_id: str, app_key: str, states: list[str], today: dt.date) -> Iterator[dict]:
    budget = int(cfg.get("max_calls", 25))
    used, failed, last_error = 0, 0, None
    for what, where in adzuna_pairs(cfg, states, today):
        for page in range(1, int(cfg.get("pages", 2)) + 1):
            if used >= budget:
                break
            params = {"app_id": app_id, "app_key": app_key, "what": what, "results_per_page": 50,
                      "max_days_old": cfg.get("max_days_old", 4), "full_time": 1, "sort_by": "date"}
            if where:
                params["where"] = where
            used += 1
            try:
                data = http.get_json(f"https://api.adzuna.com/v1/api/jobs/{cfg.get('country', 'us')}/search/{page}", params)
            except Exception as e:  # one bad query shouldn't stop the rest
                failed, last_error = failed + 1, e
                break
            results = data.get("results", [])
            for r in results:
                loc = r.get("location") or {}
                area = loc.get("area") or []
                locs = [loc.get("display_name", "")]
                if len(area) >= 2:
                    locs.append(f"{area[-1]}, {area[1]}" if len(area) > 2 else area[1])
                sal = (r["salary_min"], r.get("salary_max") or r["salary_min"]) if r.get("salary_min") else None
                yield raw("adzuna", r.get("id"), (r.get("company") or {}).get("display_name", "Unknown"),
                          r.get("title", ""), locs, r.get("redirect_url", ""), r.get("created"),
                          employment_type=r.get("contract_time") or r.get("contract_type"),
                          salary=tuple(int(x) for x in sal) if sal else None,
                          salary_est=str(r.get("salary_is_predicted", "0")) in ("1", "true", "True"),
                          text=clean_html(r.get("description", "")))
            if len(results) < 50:
                break
    if used and failed == used:
        raise RuntimeError(f"All {used} Adzuna requests failed; last error: {last_error}")


# ---------------------------------------------------------------- company job boards

def greenhouse(http, c: dict) -> Iterator[dict]:
    data = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs", {"content": "true"})
    for j in data.get("jobs", []):
        text = clean_html(j.get("content", ""))
        locs = [(j.get("location") or {}).get("name", "")]
        locs += [o.get("location") for o in j.get("offices", []) if o.get("location")]
        yield raw("greenhouse", j.get("id"), c["name"], j.get("title", ""), locs, j.get("absolute_url", ""),
                  j.get("first_published") or j.get("updated_at"), text=text, company_cfg=c)


def lever(http, c: dict) -> Iterator[dict]:
    data = http.get_json(f"https://api.lever.co/v0/postings/{c['slug']}", {"mode": "json"})
    for j in data:
        cat = j.get("categories") or {}
        locs = cat.get("allLocations") or [cat.get("location", "")]
        sr = j.get("salaryRange") or {}
        sal = None
        if sr.get("min") and "hour" not in (sr.get("interval") or ""):
            sal = (int(sr["min"]), int(sr.get("max") or sr["min"]))
        parts = [j.get("descriptionPlain", "")]
        for lst in j.get("lists", []) or []:
            parts.append(lst.get("text", ""))
            parts.append(clean_html(lst.get("content", "")))
        parts.append(j.get("additionalPlain", ""))
        yield raw("lever", j.get("id"), c["name"], j.get("text", ""), locs, j.get("hostedUrl", ""),
                  j.get("createdAt"), employment_type=cat.get("commitment"), salary=sal,
                  text="\n".join(p for p in parts if p), workplace=j.get("workplaceType"), company_cfg=c)


def ashby(http, c: dict) -> Iterator[dict]:
    data = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{c['slug']}", {"includeCompensation": "true"})
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations", []) or []]
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        if addr.get("addressLocality") or addr.get("addressRegion"):
            locs.append(", ".join(x for x in (addr.get("addressLocality"), addr.get("addressRegion")) if x))
        if j.get("isRemote") and not any("remote" in (l or "").lower() for l in locs):
            locs.append("Remote" if (addr.get("addressCountry") or "United States") in ("United States", "USA", "US") else f"Remote ({addr.get('addressCountry')})")
        comp = j.get("compensation") or {}
        sal = None
        for part in comp.get("summaryComponents") or []:
            if part.get("compensationType") == "Salary" and part.get("minValue"):
                sal = (int(part["minValue"]), int(part.get("maxValue") or part["minValue"]))
                break
        if not sal:
            sal = parse_salary(comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary") or "")
        text = j.get("descriptionPlain") or clean_html(j.get("descriptionHtml", ""))
        yield raw("ashby", j.get("id"), c["name"], j.get("title", ""), locs, j.get("jobUrl", ""),
                  j.get("publishedAt"), employment_type=j.get("employmentType"), salary=sal, text=text,
                  workplace=j.get("workplaceType"), company_cfg=c)


def smartrecruiters(http, c: dict) -> Iterator[dict]:
    offset = 0
    while True:
        data = http.get_json(f"https://api.smartrecruiters.com/v1/companies/{c['slug']}/postings",
                             {"limit": 100, "offset": offset})
        for j in data.get("content", []):
            loc = j.get("location") or {}
            locs = [", ".join(x for x in (loc.get("city"), loc.get("region")) if x)]
            if loc.get("remote"):
                locs.append("Remote" if (loc.get("country") or "us").lower() == "us" else f"Remote ({loc.get('country')})")
            yield raw("smartrecruiters", j.get("id"), c["name"], j.get("name", ""), locs,
                      f"https://jobs.smartrecruiters.com/{c['slug']}/{j.get('id')}", j.get("releasedDate"),
                      employment_type=(j.get("typeOfEmployment") or {}).get("label"),
                      detail={"kind": "smartrecruiters", "url": j.get("ref")}, company_cfg=c)
        offset += 100
        if offset >= int(data.get("totalFound", 0)) or offset >= 500:
            break


def _workday_posted(s: str | None, today: dt.date) -> str | None:
    s = (s or "").lower()
    if "today" in s:
        return today.isoformat()
    if "yesterday" in s:
        return (today - dt.timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*days?", s)
    if m:
        return (today - dt.timedelta(days=int(m.group(1)) + (1 if "+" in s else 0))).isoformat()
    return None


def workday(http, c: dict, today: dt.date) -> Iterator[dict]:
    w = c["workday"]
    base = f"https://{w['host']}/wday/cxs/{w['tenant']}/{w['site']}"
    seen: set[str] = set()
    for term in c.get("search") or ["SAP", "ERP", "business systems"]:
        offset = 0
        while offset < int(c.get("max_per_term", 60)):
            data = http.post_json(base + "/jobs", {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term})
            posts = data.get("jobPostings", []) or []
            for p in posts:
                path = p.get("externalPath")
                if not path or path in seen:
                    continue
                seen.add(path)
                yield raw("workday", path, c["name"], p.get("title", ""), [p.get("locationsText", "")],
                          f"https://{w['host']}/{w['site']}{path}", _workday_posted(p.get("postedOn"), today),
                          detail={"kind": "workday", "url": base + path}, company_cfg=c)
            offset += 20
            if len(posts) < 20:
                break


def enrich(http, r: dict) -> None:
    """Fetch the full posting for sources whose list endpoint is thin (Workday, SmartRecruiters)."""
    d = r.get("detail")
    if not d or not d.get("url"):
        return
    r["detail"] = None
    data = http.get_json(d["url"])
    if d["kind"] == "workday":
        info = data.get("jobPostingInfo") or {}
        r["text"] = clean_html(info.get("jobDescription", ""))
        r["employment_type"] = info.get("timeType") or r.get("employment_type")
        locs = [x for x in [info.get("location", "")] + list(info.get("additionalLocations") or []) if x]
        r["locations"] = locs or r["locations"]
        r["url"] = info.get("externalUrl") or r["url"]
        r["posted"] = to_date(info.get("startDate")) or r["posted"]
        r["workplace"] = info.get("remoteType") or r.get("workplace")
    elif d["kind"] == "smartrecruiters":
        sections = ((data.get("jobAd") or {}).get("sections") or {})
        r["text"] = "\n".join(clean_html((sections.get(k) or {}).get("text", ""))
                              for k in ("jobDescription", "qualifications", "additionalInformation"))


# ---------------------------------------------------------------- HN Who's Hiring (startups)

_ROLE_WORDS = re.compile(r"engineer|developer|manager|analyst|architect|scientist|lead|head of|founding|designer|operations|ops\b", re.I)
_LOC_WORDS = re.compile(r"remote|on-?site|onsite|hybrid|nyc|new york|boston|\b[A-Z]{2}\b", re.I)


def hn_whoishiring(http, cfg: dict) -> Iterator[dict]:
    hits = http.get_json("https://hn.algolia.com/api/v1/search_by_date",
                         {"tags": "story,author_whoishiring", "hitsPerPage": 10}).get("hits", [])
    story = next((h for h in hits if (h.get("title") or "").lower().startswith("ask hn: who is hiring")), None)
    if not story:
        return
    item = http.get_json(f"https://hn.algolia.com/api/v1/items/{story['objectID']}")
    must = [re.compile(p, re.I) for p in cfg.get("require_patterns", [])]
    for ch in item.get("children", []) or []:
        text = clean_html(ch.get("text") or "")
        if not text or (must and not any(p.search(text) for p in must)):
            continue
        first = text.split("\n", 1)[0]
        parts = [p.strip() for p in re.split(r"\s*\|\s*", first) if p.strip()]
        if len(parts) < 2:
            continue
        low = text.lower()
        if re.search(r"full[- ]time|\bft\b|permanent", low):
            emp = "full-time"
        elif re.search(r"\bcontract\b|part[- ]time|freelance", low):
            emp = "contract"
        else:
            continue  # the board is full-time only; skip posts that don't say
        title = next((p for p in parts[1:] if _ROLE_WORDS.search(p) and not _LOC_WORDS.fullmatch(p)), "Several roles (see post)")
        locs = [p for p in parts[1:] if _LOC_WORDS.search(p) and p != title] or [first]
        yield raw("hn", ch.get("id"), parts[0][:80], title[:140], locs,
                  f"https://news.ycombinator.com/item?id={ch.get('id')}", ch.get("created_at"),
                  employment_type=emp, salary=parse_salary(text), text=text, extra_tags=["startup"])


# ---------------------------------------------------------------- ATS discovery

_DISCOVER = [
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)")),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([A-Za-z0-9_-]+)")),
    ("greenhouse", re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_.-]+)")),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.%-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)")),
    ("workday", re.compile(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)")),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)")),
]
_BAD_SLUGS = {"embed", "v1", "js", "api", "jobs", "job", "en-us", "oneclick-ui", "wday"}


def detect_ats(page: str) -> dict | None:
    for ats, rx in _DISCOVER:
        for m in rx.finditer(page or ""):
            if ats == "workday":
                tenant, wd, site = m.groups()
                if site.lower() in _BAD_SLUGS:
                    continue
                return {"ats": "workday", "workday": {"host": f"{tenant}.{wd}.myworkdayjobs.com", "tenant": tenant, "site": site}}
            slug = m.group(1)
            if slug.lower() not in _BAD_SLUGS:
                return {"ats": ats, "slug": slug}
    return None


def discover(http, careers_url: str) -> dict | None:
    return detect_ats(http.get_text(careers_url))


if __name__ == "__main__":  # python -m scraper.sources https://example.com/careers
    import json
    import sys

    from .client import Http

    for u in sys.argv[1:]:
        print(u, json.dumps(discover(Http(), u)))
