# Shift Board

A daily job board for one person's search: SAP/ERP roles at new and migrating plants, manufacturing startups, PE carve-outs and quant roles in NY, NJ, CT, eastern PA, MA and MD. A GitHub Action collects postings every morning, tags them, and publishes a small web app on GitHub Pages.

## What's in the box

| Path | What it does |
|---|---|
| `scraper/` | Python pipeline: fetch → keep full-time, in-region, relevant titles → tag → score → dedupe → merge with yesterday |
| `config/settings.yaml` | Regions, title filters, Adzuna queries, news searches, scoring weights |
| `config/companies.yaml` | Company watchlist (their own job boards) |
| `web/index.html` | The app. Reads `web/data/*.json`; shows sample data if those don't exist yet |
| `.github/workflows/daily.yml` | Runs the pipeline at 11:15 UTC, commits the data, deploys Pages |
| `tests/` | Fixture-based tests for every source (fictional companies) |

Sources: Adzuna's API (broad discovery, free key), each watchlist company's public Greenhouse / Lever / Ashby / SmartRecruiters / Workday board, the monthly HN "Who is hiring?" thread (manufacturing-related posts only), and Google News RSS for the Signals tab. Nothing logs in anywhere and nothing touches LinkedIn or Indeed.

## Setup (about 15 minutes)

1. **Make a GitHub repo** and push this folder to it. GitHub Pages is free for public repos; a private repo needs a paid plan for Pages (or run locally, below). The repo only holds job postings and config. Your tracker and notes never leave your browser.
2. **Get an Adzuna key** at https://developer.adzuna.com (free). In the repo: Settings → Secrets and variables → Actions → add `ADZUNA_APP_ID` and `ADZUNA_APP_KEY`. Without them, everything else still runs.
3. **Turn on Pages**: Settings → Pages → Source: **GitHub Actions**.
4. **Run it once**: Actions → Daily job scan → Run workflow. When it finishes, the page URL is on the run summary.
5. **Check Settings → Sources in the app.** Any company marked "needs setup" didn't reveal its job board; fix it in `companies.yaml` (below).

## Running locally

```bash
pip install -r requirements.txt
export ADZUNA_APP_ID=... ADZUNA_APP_KEY=...   # optional
python -m scraper.run
python -m http.server -d web 8000             # open http://localhost:8000
```

Opening `web/index.html` straight from disk shows the sample data, because browsers block `fetch` on `file://`.

## Adding companies

Add a line to `config/companies.yaml` with a `careers_url`. On the next run the scraper looks for a Greenhouse, Lever, Ashby, Workday or SmartRecruiters link on that page and caches what it finds in `data/discovered.json`. To check a page right away:

```bash
python -m scraper.sources https://www.example.com/careers
```

If discovery fails (some careers sites load the board with JavaScript), open a job on their site, copy the board URL, and set it by hand:

```yaml
- {name: Acme, kind: startup, stage: seed, ats: ashby, slug: acme}         # jobs.ashbyhq.com/acme
- name: Big Manufacturer
  kind: manufacturer
  ats: workday                                                              # acme.wd5.myworkdayjobs.com/External
  workday: {host: acme.wd5.myworkdayjobs.com, tenant: acme, site: External}
  search: [SAP, ERP, S/4HANA]
```

The starter list hasn't been checked against the live sites, so expect a few to need this.

## Tags

Tags come from rules in `scraper/tagger.py` (title and description patterns) plus the company's `kind` and `stage`. The app adds four of its own: plant tags from your plant list, `stale`, `closed`, and `manual`.

| Tag | Meaning |
|---|---|
| new plant | Posting mentions a new plant/facility, greenfield site, start-up or ramp-up; or the company is on your plant list and go-live hasn't happened yet |
| post-go-live | Hypercare, post-go-live stabilization; or within N months (default 6) after a listed plant's go-live |
| s4 migration, ecc | S/4HANA conversion/migration/transformation, brownfield, RISE; ECC or R/3 mentioned |
| sap, erp, architect | SAP stack; any ERP or business systems; architect in the title |
| consulting / si, pe portfolio, carve-out | Integrator or consulting role; PE firm or portfolio company; carve-out, divestiture, TSA |
| startup, seed stage, series a/b, growth stage | From the watchlist or the posting text (YC, founding team, Series A) |
| fde / solutions | Forward-deployed, solutions or implementation engineer |
| quant | Quant research, development or trading |
| edi / integration, ewm / wms, mes, data / bi, ai | Skill areas |
| manufacturing, regulated, itar / us person | Industry, FDA/GMP/USDA/SOX, ITAR or clearance |
| manager, director+, pm role | Level; pm role = project/program manager titles without an engineering component |
| remote, hybrid, on-site, travel 25%+, travel 50%+ | Work setup |

The plant list lives in the app (Settings → Plant projects, or "Track as a plant project" on a Signals item). A plant is tagged `new plant` from its announcement until go-live, then `post-go-live` for the window you set.

## The match score

0–100: a base of 30, plus the tag weights and title phrases in `settings.yaml`, plus or minus points for the top of the pay range. It's for sorting, not a verdict. Tune the weights to taste.

## Limits worth knowing

- Adzuna's free tier has a monthly call limit. The default config rotates (query, state) pairs over three days and caps each run at 25 calls. Check your limit on the Adzuna dashboard and change `max_calls` or `rotate_days` to fit.
- Adzuna estimates pay when a posting doesn't list it. Those show as "est." and can be hidden with a filter.
- Pay-transparency laws mean most NY, NJ, MA and MD postings include a range; CT and PA ones often don't, so keep "Include jobs with no posted pay" on unless you want to drop them.
- A posting from a company board is marked closed the first day it's missing from a board that loaded fine. Adzuna and HN postings get two weeks, since their queries rotate.
- Tracker data is per browser. Use Settings → Export backup to move it.

## Tests

```bash
pip install pytest
python -m pytest -q
python scripts/refresh_sample.py   # rebuilds the sample data embedded in web/index.html
```
