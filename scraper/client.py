"""HTTP client used by every source, plus an offline client that replays fixtures."""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from pathlib import Path

import requests

USER_AGENT = "ShiftBoard/1.0 (personal job search; daily, low volume)"


def scrub(msg: str) -> str:
    """Remove API credentials from anything that might be logged or committed."""
    return re.sub(r"(app_(?:id|key)=)[^&\s'\"]+", r"\1***", str(msg))


class Http:
    def __init__(self, timeout: int = 25, pause: float = 0.5):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.timeout = timeout
        self.pause = pause
        self.calls = 0

    def _request(self, method: str, url: str, **kw):
        time.sleep(self.pause)
        self.calls += 1
        r = self.session.request(method, url, timeout=self.timeout, **kw)
        r.raise_for_status()
        return r

    def get_json(self, url: str, params: dict | None = None):
        return self._request("GET", url, params=params).json()

    def post_json(self, url: str, body: dict):
        return self._request("POST", url, json=body, headers={"Accept": "application/json"}).json()

    def get_text(self, url: str, params: dict | None = None) -> str:
        return self._request("GET", url, params=params).text


class FixtureHttp(Http):
    """Maps requests to files listed in <root>/manifest.json.

    Each manifest rule is {"match": [substrings...], "file": "name.json"}; the first rule whose
    substrings all occur in the full request (URL, query string and POST body) wins. No match
    behaves like an HTTP 404.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.rules = json.loads((self.root / "manifest.json").read_text())
        self.calls = 0

    def _file(self, url: str, params: dict | None = None, body: dict | None = None) -> Path:
        full = url + ("?" + urllib.parse.urlencode(params) if params else "") + (json.dumps(body) if body else "")
        for rule in self.rules:
            if all(m in full for m in rule["match"]):
                return self.root / rule["file"]
        raise requests.HTTPError(f"404 (no fixture) for {full}")

    def get_json(self, url, params=None):
        self.calls += 1
        return json.loads(self._file(url, params).read_text())

    def post_json(self, url, body):
        self.calls += 1
        return json.loads(self._file(url, None, body).read_text())

    def get_text(self, url, params=None):
        self.calls += 1
        return self._file(url, params).read_text()
