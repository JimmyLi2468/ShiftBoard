"""Text, location, salary and employment-type helpers shared by every source."""
from __future__ import annotations

import html
import re

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
# Longest names first so "west virginia" wins over "virginia".
_NAMES = sorted(((v.lower(), k) for k, v in STATES.items()), key=lambda t: -len(t[0]))
# Two-letter codes that are also common words or abbreviations; only trusted after a comma.
_AMBIGUOUS = {"IN", "OR", "ME", "OK", "HI", "DE", "ID", "AL", "LA", "MS", "CO"}

CITY_HINTS = {
    "washington, d.c.": "DC", "washington dc": "DC", "nyc": "NY", "new york city": "NY",
    "manhattan": "NY", "brooklyn": "NY", "queens": "NY", "long island": "NY",
    "westchester": "NY", "white plains": "NY", "tarrytown": "NY", "stony brook": "NY",
    "melville": "NY", "hauppauge": "NY", "boston": "MA", "somerville": "MA", "waltham": "MA",
    "cambridge, ma": "MA", "philadelphia": "PA", "philly": "PA", "allentown": "PA",
    "lehigh valley": "PA", "baltimore": "MD", "new haven": "CT", "stamford": "CT",
    "greenwich": "CT", "hartford": "CT", "jersey city": "NJ", "newark": "NJ",
    "princeton": "NJ", "hoboken": "NJ", "secaucus": "NJ",
}

CITY_ALIASES = {"nyc": "New York", "new york city": "New York", "manhattan": "New York", "philly": "Philadelphia"}

NON_US = re.compile(
    r"\b(canada|united kingdom|uk|england|london|ireland|dublin|india|bangalore|bengaluru|"
    r"hyderabad|pune|germany|berlin|munich|france|paris|netherlands|amsterdam|spain|poland|"
    r"israel|tel aviv|mexico|brazil|singapore|japan|china|australia|europe|emea|apac|latam|"
    r"toronto|vancouver|montreal)\b",
    re.I,
)
_REMOTE = re.compile(r"\bremote\b|work from home|\bwfh\b|\banywhere\b", re.I)
_NOISE = re.compile(
    r"^(remote|hybrid|on-?site|onsite|in[- ]office|united states( of america)?|usa?|"
    r"\d+ locations?|multiple locations|north america|americas)$",
    re.I,
)


# ---------------------------------------------------------------- text

def clean_html(s: str | None) -> str:
    """HTML (possibly entity-escaped, as Greenhouse sends it) to readable plain text."""
    if not s:
        return ""
    s = html.unescape(str(s))
    s = re.sub(r"(?i)<\s*(br|/p|/li|/h\d|/div|/tr|/ul|/ol)\s*/?>", "\n", s)
    s = re.sub(r"(?i)<p[^>]*>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "\n• ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = s.replace("\u00a0", " ")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()


_BOILER = re.compile(
    r"equal (employment )?opportunity|\beeo\b|without regard to|reasonable accommodation|"
    r"e-verify|affirmative action|benefits (include|package)|401\(?k\)?|privacy (notice|policy)|"
    r"pay transparency|salary range|compensation range|base pay|we are proud to be",
    re.I,
)
_HEAD_LINE = re.compile(
    r"(?im)^\s*(about the (role|job|position|opportunity)|the role|role (overview|summary)|"
    r"position (summary|overview)|job (summary|overview|description)|the opportunity|your role|"
    r"what you(?:'|’)ll do|overview)\s*:?\s*$"
)
_HEAD_INLINE = re.compile(
    r"(?i)\b(about the role|the role|position summary|job summary|role overview|the opportunity)\s*:\s*"
)


def summarize(text: str, limit: int = 300, company: str = "") -> str:
    """Two or three sentences from the part of the posting that describes the role."""
    if not text:
        return ""
    # Drop header lines like "Acme | Engineer | NYC | Full-time" (HN posts).
    text = "\n".join(l for l in text.split("\n") if l.count("|") < 2)
    body = text
    m = _HEAD_LINE.search(text) or _HEAD_INLINE.search(text)
    if m and m.start() < len(text) * 0.7:
        body = text[m.end():]
    parts = [s.strip(" •-\t") for s in re.split(r"(?<=[.!?])\s+|\n+", body)]
    parts = [s for s in parts if len(s) >= 25 and not _BOILER.search(s) and not _HEAD_LINE.match(s)]
    # Skip a leading "Acme is a ..." company intro when the posting says more after it.
    first_word = (company or "").split(" ")[0].lower()
    if len(parts) > 1 and first_word and parts[0].lower().startswith(first_word):
        parts = parts[1:]
    out = ""
    for s in parts:
        if len(out) + len(s) + 1 > limit:
            if not out:
                out = s[: limit - 1].rsplit(" ", 1)[0] + "…"
            break
        out = f"{out} {s}".strip()
    return out


# ---------------------------------------------------------------- locations

def _state_from(p: str, low: str) -> str | None:
    m = re.search(r",\s*([A-Z]{2})\b", p)  # "Allentown, PA"
    if m and m.group(1) in STATES:
        return m.group(1)
    m = re.search(r"(?:^|\b)US[A]?\s*-\s*([A-Z]{2})\s*-", p)  # "US-NY-New York"
    if m and m.group(1) in STATES:
        return m.group(1)
    for hint, code in CITY_HINTS.items():  # city hints before names: "Washington, DC"
        if hint in low:
            return code
    for name, code in _NAMES:
        if re.search(r"\b" + re.escape(name) + r"\b", low):
            return code
    if len(p) <= 60:  # short location strings only, never prose
        for m in re.finditer(r"(?:^|[\s(\-–/])([A-Z]{2})(?=$|[\s,)\-–/]|\d)", p):
            code = m.group(1)
            if code in STATES and code not in _AMBIGUOUS:
                return code
    return None


def _city_from(p: str, state: str | None) -> str | None:
    m = re.match(r"^USA?\s*-\s*[A-Z]{2}\s*-\s*(.+)$", p)
    if m:
        return m.group(1).strip() or None
    parts = [t.strip() for t in p.split(",")]
    if len(parts) == 1 and " - " in p:
        parts = [t.strip() for t in p.split(" - ")][::-1]
    keep = []
    for i, t in enumerate(parts):
        tl = t.lower()
        if not t or _NOISE.match(t) or NON_US.fullmatch(tl) or re.fullmatch(r"\d{5}(-\d{4})?", t):
            continue
        if t.upper() in STATES and len(t) == 2:
            continue
        if state and tl == STATES[state].lower() and (i > 0 or len(parts) == 1):
            continue
        t = re.sub(r"\s*\((remote|hybrid|on-?site)\)\s*", "", t, flags=re.I).strip()
        t = re.sub(r"^(remote|hybrid)\s*[-–:]\s*", "", t, flags=re.I).strip()
        if t and not _NOISE.match(t):
            keep.append(t)
    if not keep:
        return None
    city = keep[0]
    if state:
        city = re.sub(r"\s+" + state + r"$", "", city)
    if city.isupper() and len(city) > 3:
        city = city.title()
    return CITY_ALIASES.get(city.lower(), city)


def parse_location(part: str) -> dict | None:
    p = re.sub(r"\s+", " ", str(part or "")).strip(" -–,;")
    if not p:
        return None
    low = p.lower()
    remote = bool(_REMOTE.search(low))
    us = not NON_US.search(low)
    state = _state_from(p, low)
    if not state and not remote:
        if re.fullmatch(r"(us|usa|united states( of america)?)", low):
            return {"label": "United States", "city": None, "state": None, "remote": False, "us": True}
        return {"label": p, "city": None, "state": None, "remote": False, "us": us}
    city = None if (remote and not state) else _city_from(p, state)
    if city and state:
        label = f"{city}, {state}"
    elif state and not remote:
        label = STATES[state]
    elif remote:
        label = f"Remote ({state})" if state else ("Remote (US)" if us else "Remote (outside US)")
    else:
        label = p
    return {"label": label, "city": city, "state": state, "remote": remote and not city, "us": us}


_SPLIT = re.compile(r"\s*(?:;|\||/(?!\d)| or |\n|•)\s*")


def parse_locations(raws) -> list[dict]:
    out, seen = [], set()
    for raw in raws or []:
        if not raw:
            continue
        for part in _SPLIT.split(str(raw)):
            loc = parse_location(part)
            if loc and loc["label"] not in seen:
                seen.add(loc["label"])
                out.append(loc)
    return out


def in_region(locs: list[dict], states: set[str], include_remote_us: bool, exclude_cities: set[str]) -> bool:
    for loc in locs:
        city = (loc.get("city") or "").lower()
        if loc.get("state") in states and city not in exclude_cities:
            return True
        if include_remote_us and loc.get("remote") and loc.get("us") and loc.get("state") is None:
            return True
    return False


# ---------------------------------------------------------------- salary

_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*([kK]\b)?"
_RANGE = re.compile(r"\$\s?" + _NUM + r"\s*(?:USD)?\s*(?:-|–|—|to)\s*(?:USD\s*)?\$?\s?" + _NUM, re.I)
_UPTO = re.compile(r"up to \$\s?" + _NUM, re.I)
_PAY_WORDS = re.compile(r"salary|base|compensation|pay|range|annual|per year|/yr|\bote\b", re.I)


def _num(n: str, k: str | None) -> float:
    v = float(n.replace(",", ""))
    return v * 1000 if k else v


def parse_salary(text: str) -> tuple[int, int] | None:
    """Best annual base range found in free text, or None."""
    if not text:
        return None
    found = []
    for m in _RANGE.finditer(text):
        lo, hi = _num(m.group(1), m.group(2)), _num(m.group(3), m.group(4))
        if hi < 1000 and m.group(4) is None and m.group(2):  # "$150k-180"
            hi *= 1000
        if lo < 1000 and hi >= 1000:  # "$150-180k"
            lo *= 1000
        window = text[max(0, m.start() - 80): m.end() + 40]
        if re.search(r"per hour|/\s?h(ou)?r\b|hourly|an hour", window, re.I):
            lo, hi = lo * 2080, hi * 2080
        if 30_000 <= lo <= hi <= 2_000_000:
            found.append((bool(_PAY_WORDS.search(window)), int(lo), int(hi)))
    if found:
        found.sort(key=lambda t: not t[0])  # prefer ranges next to pay words
        return found[0][1], found[0][2]
    m = _UPTO.search(text)
    if m:
        v = _num(m.group(1), m.group(2))
        if 30_000 <= v <= 2_000_000:
            return int(v), int(v)
    return None


# ---------------------------------------------------------------- employment type

_TITLE_NON_FT = re.compile(
    r"\b(intern|internship|co-?op|part[- ]time|temporary|temp|seasonal|per diem|freelance|interim)\b|"
    r"\(contract\)|\bcontractor\b|\bcontract\s*(role|position|to[- ]hire|w2|basis|only)\b|"
    r"[-–|]\s*contract\s*$|\b\d+[- ]month\b",
    re.I,
)
_TEXT_NON_FT = re.compile(
    r"\bthis is a (temporary|contract|part[- ]time|fixed[- ]term)\b|\bcontract[- ]to[- ]hire\b|"
    r"\b\d+[- ]month (contract|assignment)\b|\b(w2|1099|c2c|corp[- ]to[- ]corp) (contract|only|role)\b",
    re.I,
)


def is_full_time(employment_type: str | None, title: str, text: str = "") -> bool:
    et = (employment_type or "").lower().replace("_", " ")
    if et:
        if re.search(r"part|contract|temp|intern|seasonal|freelance|fixed", et):
            return False
        if re.search(r"full|permanent|regular", et):
            return not _TITLE_NON_FT.search(title or "")
    if _TITLE_NON_FT.search(title or ""):
        return False
    if _TEXT_NON_FT.search(text or ""):
        return False
    return True
