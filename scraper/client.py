"""HTTP client used by every source, plus an offline client that replays fixtures."""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
from pathlib import Path

import requests

USER_AGENT = "ShiftBoard/1.0 (personal job search; daily, low volume)"


def scrub(msg) -> str:
    """Remove API credentials from anything that might be logged or committed."""
    return re.sub(r"(app_(?:id|key)=)[^&\s'\"]+", r"\1***", str(msg))


class Http:
    """requests with a polite pause, a call counter, and one session per thread."""

    def __init__(self, timeout: int = 25, pause: float = 0.3):
        self.timeout = timeout
        self.pause = pause
        self.calls = 0
        self._lock = threading.Lock()
        self._local = threading.local()

    @property
    def session(self) -> requests.Session:
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers["User-Agent"] = USER_AGENT
            self._local.session = s
        return s

    def _request(self, method: str, url: str, timeout: int | None = None, **kw):
        time.sleep(self.pause)
        with self._lock:
            self.calls += 1
        r = self.session.request(method, url, timeout=timeout or self.timeout, **kw)
        r.raise_for_status()
        return r

    def get_json(self, url: str, params: dict | None = None, timeout: int | None = None):
        return self._request("GET", url, timeout=timeout, params=params).json()

    def post_json(self, url: str, body: dict):
        return self._request("POST", url, json=body, headers={"Accept": "application/json"}).json()

    def get_text(self, url: str, params: dict | None = None, timeout: int | None = None) -> str:
        return self._request("GET", url, timeout=timeout, params=params).text


class FixtureHttp(Http):
    """Maps requests to files listed in <root>/manifest.json.

    Each manifest rule is {"match": [substrings...], "file": "name.json"}; the first rule whose
    substrings all occur in the full request (URL, query string and POST body) wins. No match
    behaves like an HTTP 404.
    """

    def __init__(self, root: str | Path):
        super().__init__()
        self.root = Path(root)
        self.rules = json.loads((self.root / "manifest.json").read_text())

    def _file(self, url: str, params: dict | None = None, body: dict | None = None) -> Path:
        with self._lock:
            self.calls += 1
        full = url + ("?" + urllib.parse.urlencode(params) if params else "") + (json.dumps(body) if body else "")
        for rule in self.rules:
            if all(m in full for m in rule["match"]):
                return self.root / rule["file"]
        err = requests.HTTPError(f"404 (no fixture) for {full}")
        err.response = type("R", (), {"status_code": 404})()
        raise err

    def get_json(self, url, params=None, timeout=None):
        return json.loads(self._file(url, params).read_text())

    def post_json(self, url, body):
        return json.loads(self._file(url, None, body).read_text())

    def get_text(self, url, params=None, timeout=None):
        return self._file(url, params).read_text()
