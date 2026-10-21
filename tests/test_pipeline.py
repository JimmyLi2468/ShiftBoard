"""Run with: python -m pytest -q"""
import json
import shutil
from pathlib import Path

import pytest

from scraper import run
from scraper.client import scrub
from scraper.normalize import is_full_time, parse_location, parse_salary, summarize

FIX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parent.parent


@pytest.fixture()
def out(tmp_path):
    args = ["--offline", str(FIX), "--companies", str(FIX / "companies.yaml"), "--config", str(ROOT / "config"),
            "--out", str(tmp_path / "web"), "--state", str(tmp_path / "state"), "--today", "2026-10-20"]
    assert run.main(args) == 0
    load = lambda name: json.loads((tmp_path / "web" / name).read_text())
    return {"jobs": load("jobs.json")["jobs"], "signals": load("signals.json")["items"],
            "health": load("health.json"), "args": args, "tmp": tmp_path}


def by_title(jobs, title):
    return next(j for j in jobs if j["title"] == title)


def test_full_time_and_region_filters(out):
    titles = {j["title"] for j in out["jobs"]}
    assert "Plant Systems Analyst (Contract)" not in titles          # contract
    assert "Quantitative Research Intern (Summer 2027)" not in titles  # intern
    assert "SAP Analyst, Temporary" not in titles                      # Workday part time
    assert "SAP S/4HANA Functional Lead" not in titles                 # Adzuna contract
    assert "Senior Software Engineer" not in titles                    # San Francisco
    assert "SAP Analyst" not in titles                                 # Dallas
    assert "Line Operator, 2nd Shift" not in titles                    # title excluded
    assert "Frontend Engineer" not in titles                           # HN, not manufacturing


def test_dedupe_prefers_company_board(out):
    j = by_title(out["jobs"], "SAP S/4HANA Solution Architect")
    assert j["sources"] == ["adzuna", "lever"]
    assert (j["salary_min"], j["salary_max"], j["salary_est"]) == (170000, 205000, False)
    assert j["url"].startswith("https://jobs.lever.co/")


def test_tags(out):
    jobs = out["jobs"]
    assert {"new plant", "post-go-live", "sap"} <= set(by_title(jobs, "SAP Business Systems Manager, Allentown Plant")["tags"])
    assert {"s4 migration", "ecc", "architect"} <= set(by_title(jobs, "SAP S/4HANA Solution Architect")["tags"])
    assert {"carve-out", "pe portfolio", "travel 25%+"} <= set(by_title(jobs, "Director, Portfolio Operations – ERP & Carve-outs")["tags"])
    assert {"seed stage", "startup", "itar / us person"} <= set(by_title(jobs, "Founding Forward Deployed Engineer, ERP Integrations")["tags"])
    assert "pm role" in by_title(jobs, "ERP Program Manager")["tags"]
    assert "quant" in by_title(jobs, "Quantitative Researcher, Industrials")["tags"]


def test_workday_multi_location_uses_detail(out):
    j = by_title(out["jobs"], "SAP EWM Lead")
    assert [l["label"] for l in j["locations"]] == ["Tarrytown, NY"]  # Raleigh, NC is out of region
    assert j["salary_max"] == 175000


def test_discovery_and_health(out, tmp_path):
    disc = json.loads((out["tmp"] / "state" / "discovered.json").read_text())
    assert disc["https://kettlebrook.example/careers"]["slug"] == "kettlebrookfoods"
    status = {h["name"]: h["status"] for h in out["health"]["sources"]}
    assert status["Kettlebrook Foods"] == "ok"
    assert status["Copperline Tools"] == "needs setup"


def test_history_marks_closed_and_keeps_first_seen(out):
    web = out["tmp"] / "web" / "jobs.json"
    data = json.loads(web.read_text())
    gone = by_title(data["jobs"], "ERP Program Manager")
    gone["first_seen"] = gone["last_seen"] = "2026-10-01"
    data["jobs"] = [j for j in data["jobs"] if j["title"] != "ERP Program Manager"] + [gone]
    # Pretend the Lever board no longer lists it: drop it from the fixture by renaming the job.
    lever = json.loads((FIX / "lever_saucon.json").read_text())
    tmp_fix = out["tmp"] / "fix"
    shutil.copytree(FIX, tmp_fix)
    (tmp_fix / "lever_saucon.json").write_text(json.dumps([p for p in lever if p["id"] != "sp-2"]))
    web.write_text(json.dumps(data))
    args = list(out["args"])
    args[1] = str(tmp_fix)
    args[args.index("--today") + 1] = "2026-10-21"
    run.main(args)
    jobs = json.loads(web.read_text())["jobs"]
    assert by_title(jobs, "ERP Program Manager")["active"] is False
    arch = by_title(jobs, "SAP S/4HANA Solution Architect")
    assert arch["first_seen"] == "2026-10-20" and arch["active"] is True


def test_signals(out):
    tags = {s["title"][:20]: s["tags"] for s in out["signals"]}
    assert len(out["signals"]) == 4
    assert "plant announcement" in tags["Kettlebrook Foods to"]
    assert "s4 migration" in tags["Palisade Ridge Pharm"]


def test_helpers():
    assert parse_location("US-NY-New York")["label"] == "New York, NY"
    assert parse_location("Remote (Canada)")["us"] is False
    assert parse_salary("$150k-$185k + equity") == (150000, 185000)
    assert parse_salary("Pay: $65–$80 per hour") == (135200, 166400)
    assert is_full_time(None, "Contract Manufacturing Manager") is True
    assert is_full_time("Full-time", "SAP Analyst (Contract)") is False
    assert "first person" in summarize("Acme is a company that makes widgets for people. You will be the first person to own it.", company="Acme")
    assert scrub("https://api.adzuna.com/x?app_id=123&app_key=secret") == "https://api.adzuna.com/x?app_id=***&app_key=***"
