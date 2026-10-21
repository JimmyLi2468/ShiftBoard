"""Rule-based tags and a 0–100 match score.

Each rule is (tag, title_pattern, text_pattern). A title pattern is tested on the
title only; a text pattern on title + description. Edit freely: the web app shows
whatever tags appear in the data.
"""
from __future__ import annotations

import re

S4 = r"(s/?4\s?hana|\bs/4\b|\bs4\b)"
S4_MIGRATION = (
    S4 + r".{0,80}(migrat|conver|transform|journey|program|brownfield|greenfield|implementation|upgrad|rollout|deploy)"
    r"|(migrat|conver|transform|transition|move|moving|upgrad)\w*.{0,80}" + S4 +
    r"|\becc\b.{0,40}" + S4 + r"|rise with sap|brownfield|bluefield|selective data transition"
)
NEW_PLANT = (
    r"new (manufacturing |production |greenfield )?(plant|facility|factory)\b|greenfield (plant|site|facility)"
    r"|plant (start[- ]?up|launch|ramp)|(start[- ]?up|launch|ramp[- ]?up) of (a|our|the) new"
    r"|brand[- ]new (plant|facility|factory)|new production (line|facility)|commissioning of"
    r"|facility (start[- ]?up|ramp)"
)
POST_GO_LIVE = (
    r"hypercare|post[- ]go[- ]live|after go[- ]live|stabiliz\w*.{0,40}(go[- ]live|new (system|plant|erp|sap))"
    r"|sustain(ment|ing)? (team|phase|support).{0,40}(go[- ]live|implementation)"
)

RULES: list[tuple[str, str | None, str | None]] = [
    ("sap", None, r"\bsap\b|" + S4 + r"|\babap\b|\bfiori\b|\bbw/?4\s?hana\b"),
    ("erp", None, r"\berp\b|\bsap\b|" + S4 + r"|oracle (ebs|e-business|cloud erp|fusion)|netsuite|dynamics 365|\bd365\b"
                  r"|\bepicor\b|\binfor (ln|m3|cloudsuite)|jd ?edwards|\bsyteline\b|\bqad\b|business systems?\b"),
    ("s4 migration", None, S4_MIGRATION),
    ("ecc", None, r"\becc\s?6?(\.0)?\b|\br/3\b|\bsap r3\b"),
    ("new plant", None, NEW_PLANT),
    ("post-go-live", None, POST_GO_LIVE),
    ("architect", r"architect", None),
    ("manager", r"\bmanager\b|\blead\b", None),
    ("director+", r"\bdirector\b|\bvp\b|vice president|\bcio\b|\bcto\b|\bhead of\b|\bchief\b", None),
    ("edi / integration", None, r"\bedi\b|\bx12\b|edifact|\bidocs?\b|sap po(/pi)?\b|\bpi/po\b|\bcpi\b|integration suite"
                                r"|mulesoft|\bboomi\b|ibm sterling|b2b integration"),
    ("ewm / wms", None, r"\bewm\b|warehouse management|\bwms\b|\bsap wm\b"),
    ("mes", None, r"\bmes\b|manufacturing execution|opcenter|\bignition\b|factorytalk|aveva"),
    ("data / bi", None, r"power ?bi|tableau|data warehouse|\bbw/?4|\bsap bw\b|datasphere|analytics engineer|\bdbt\b"
                        r"|snowflake|databricks"),
    ("ai", None, r"\bllms?\b|generative ai|\bgenai\b|\brag\b|retrieval[- ]augmented|machine learning|\bai (strategy|agents?|engineer)"),
    ("quant", r"\bquant", r"\bquantitative (research|analyst|developer|trading|finance|strateg)|stochastic|derivatives? pricing"
                          r"|alpha (research|generation)|systematic (trading|strategies|investing)|portfolio optimi[sz]ation"
                          r"|market microstructure"),
    ("pe portfolio", None, r"private equity|\bpe[- ]backed\b|portfolio compan(y|ies)|operating partner|value creation"),
    ("carve-out", None, r"carve[- ]?outs?|divestitures?|transition services? agreement|\btsas?\b|separation from (its|the) parent"),
    ("consulting / si", r"\bconsult(ant|ing)\b", r"client engagements?|billable|systems? integrator|implementation partner firm"),
    ("fde / solutions", r"forward[- ]deployed|solutions? (engineer|consultant)|implementation (engineer|consultant|specialist|manager)"
                        r"|deployment (engineer|strategist)|customer engineer", None),
    ("manufacturing", None, r"manufactur|\bplants?\b|production (line|floor|planning)|factory|shop floor|discrete|process industr"
                            r"|\bcpg\b|food (processing|production)|pharmaceutical|aerospace|automotive|semiconductor|industrial"),
    ("regulated", None, r"\bfda\b|\bc?gmp\b|\bgxp\b|computer system validation|\bcsv\b|21 cfr|\busda\b|haccp|iso 13485|as9100|\bsox\b"),
    ("itar / us person", None, r"\bitar\b|export[- ]controlled|security clearance|\bu\.?s\.? (citizen(ship)?|persons?)\b|\bcmmc\b"
                               r"|nist (sp )?800-171"),
    ("seed stage", None, r"\bpre-?seed\b|\bseed[- ](stage|round|funded)\b|\by ?combinator\b|\byc [swfx]?\d{2}\b"
                         r"|\bfounding (engineer|team|member|hire)|first \d+ (employees|hires|engineers)"),
    ("series a/b", None, r"\bseries [ab]\b"),
]
_COMPILED = [(t, re.compile(a, re.I) if a else None, re.compile(b, re.I | re.S) if b else None) for t, a, b in RULES]

_PM_TITLE = re.compile(r"\b(project|program|portfolio) manager\b|\bscrum master\b|\bpmo\b|\bdelivery manager\b", re.I)
_NOT_PM = re.compile(r"architect|engineer|analyst|systems manager|erp manager|sap manager|product manager", re.I)
_TRAVEL = re.compile(r"travel[^.%\n]{0,60}?(\d{1,3})\s?%|(\d{1,3})\s?%[^.\n]{0,30}travel", re.I)
_HYBRID = re.compile(r"\bhybrid\b", re.I)
_REMOTE_TXT = re.compile(r"fully remote|100% remote|remote[- ]first|work from anywhere", re.I)
_ONSITE = re.compile(r"\bon[- ]?site\b|\bin[- ]office\b|\bin person\b|\b5 days (a|per) week\b", re.I)


def travel_percent(text: str) -> int | None:
    vals = [int(a or b) for a, b in _TRAVEL.findall(text or "") if int(a or b) <= 100]
    return max(vals) if vals else None


def work_mode(workplace: str | None, locs: list[dict], text: str) -> str | None:
    w = (workplace or "").lower().replace("_", "").replace("-", "")
    if w in ("remote",):
        return "remote"
    if w in ("hybrid",):
        return "hybrid"
    if w in ("onsite", "inoffice", "inperson"):
        return "on-site"
    if locs and all(l.get("remote") for l in locs) or _REMOTE_TXT.search(text or ""):
        return "remote"
    if _HYBRID.search(text or "") or any("hybrid" in (l.get("label") or "").lower() for l in locs):
        return "hybrid"
    if _ONSITE.search(text or ""):
        return "on-site"
    return None


def tag_job(title: str, text: str, locs: list[dict], company: dict | None, workplace: str | None,
            extra: list[str] | None = None) -> tuple[list[str], str | None, int | None]:
    blob = f"{title}\n{text}"
    tags: list[str] = []
    for tag, tpat, xpat in _COMPILED:
        if (tpat and tpat.search(title)) or (xpat and xpat.search(blob)):
            tags.append(tag)
    if _PM_TITLE.search(title) and not _NOT_PM.search(title):
        tags.append("pm role")
    travel = travel_percent(text)
    if travel is not None and travel >= 50:
        tags.append("travel 50%+")
    elif travel is not None and travel >= 25:
        tags.append("travel 25%+")
    mode = work_mode(workplace, locs, text)
    if mode:
        tags.append(mode)
    company = company or {}
    kind, stage = company.get("kind"), company.get("stage")
    if kind == "startup":
        tags.append("startup")
    if kind == "si":
        tags.append("consulting / si")
    if kind == "pe":
        tags.append("pe portfolio")
    if kind == "quant":
        tags.append("quant")
    if stage == "seed":
        tags.append("seed stage")
    elif stage in ("series-a", "series-b"):
        tags.append("series a/b")
    elif stage == "growth":
        tags.append("growth stage")
    for t in extra or []:
        tags.append(t)
    if "seed stage" in tags or "series a/b" in tags:
        tags.append("startup")
    seen, out = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out, mode, travel


def score_job(title: str, tags: list[str], salary_max: int | None, scoring: dict) -> int:
    s = float(scoring.get("base", 30))
    weights = scoring.get("tags", {})
    for t in tags:
        s += weights.get(t, 0)
    tl = title.lower()
    for phrase, w in (scoring.get("title_boost") or {}).items():
        if phrase.lower() in tl:
            s += w
    if salary_max:
        for floor, w in sorted(scoring.get("salary_top", []), key=lambda x: -x[0]):
            if salary_max >= floor:
                s += w
                break
    return int(max(0, min(100, round(s))))
