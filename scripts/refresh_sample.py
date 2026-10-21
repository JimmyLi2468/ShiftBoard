"""Rebuild the sample data embedded in web/index.html from tests/fixtures.

The page shows this sample only when data/jobs.json can't be loaded (for example when you
open the file straight from disk). Run: python scripts/refresh_sample.py
"""
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scraper import run  # noqa: E402

TODAY = "2026-10-20"

with tempfile.TemporaryDirectory() as tmp:
    run.main(["--offline", str(ROOT / "tests/fixtures"), "--companies", str(ROOT / "tests/fixtures/companies.yaml"),
              "--out", f"{tmp}/web", "--state", f"{tmp}/state", "--today", TODAY])
    load = lambda n: json.loads(Path(f"{tmp}/web/{n}").read_text())
    jobs, signals, health = load("jobs.json"), load("signals.json"), load("health.json")

# Pretend the board has been running for a while: first seen on the posting date,
# with the last two days counting as new, and one posting already closed.
for j in jobs["jobs"]:
    j["first_seen"] = max(j["posted"], "2026-10-06")
    if j["title"] == "Operating Associate, Value Creation":
        j["active"] = False
        j["last_seen"] = "2026-10-17"

blob = json.dumps({"jobs": jobs, "signals": signals, "health": health}, ensure_ascii=False, separators=(",", ":"))
blob = blob.replace("</", "<\\/")
page = ROOT / "web/index.html"
html = page.read_text()
html, n = re.subn(r'(<script id="sample-data" type="application/json">)(.*?)(</script>)',
                  lambda m: m.group(1) + blob + m.group(3), html, count=1, flags=re.S)
assert n == 1, "sample-data script tag not found"
page.write_text(html)
print(f"Embedded {len(jobs['jobs'])} sample jobs and {len(signals['items'])} signals in {page.relative_to(ROOT)}")
