# Shift Board

A daily job board for one person's search: SAP/ERP roles at new and migrating plants, manufacturing startups, PE carve-outs and quant roles in NY, NJ, CT, eastern PA, MA and MD. A GitHub Action collects postings every morning, tags them, and publishes a small web app on GitHub Pages.

## What's in the box

| Path | What it does |
|---|---|
| `scraper/` | Python pipeline: fetch → keep full-time, in-region, relevant titles → tag → score → dedupe → merge with yesterday |
| `scraper/board_index.py` | Our own aggregator: finds thousands of employers' job boards in Common Crawl and checks a slice each day |
| `scraper/pay.py` | Estimates pay for postings that don't list it, from similar postings that do |
| `config/settings.yaml` | Regions, title filters, Adzuna queries, news searches, scoring weights |
| `config/companies.yaml` | Company watchlist (their own job boards) |
| `web/index.html` | The app. Reads `web/data/*.json`; shows sample data if those don't exist yet |
| `.github/workflows/daily.yml` | Runs the pipeline at 11:15 UTC, commits the data, deploys Pages |
| `tests/` | Fixture-based tests for every source (fictional companies) |

Sources, none of which need an account or key:

- **Board index.** Common Crawl publishes a free index of every URL in its monthly web crawl. Every employer that hosts jobs on Greenhouse, Lever, Ashby, SmartRecruiters or Workday shows up there, so the scraper collects those job boards (thousands of them) and checks a slice each day through the boards' public APIs. Boards with recent matches are checked daily; the rest every 5 days.
- **Watchlist.** Companies you name in `config/companies.yaml`, checked every day.
- **HN "Who is hiring?"**, manufacturing-related posts only.
- **Google News RSS** for the Signals tab.

Adzuna is supported but off by default (see below). Nothing logs in anywhere and nothing touches LinkedIn or Indeed.

## Setup (about 15 minutes)

1. **Make a GitHub repo** and push this folder to it. GitHub Pages is free for public repos; a private repo needs a paid plan for Pages (or run locally, below). The repo only holds job postings and config. Your tracker and notes never leave your browser.
2. **Turn on Pages**: Settings → Pages → Source: **GitHub Actions**.
3. **Run it once**: Actions → Daily job scan → Run workflow. When it finishes, the page URL is on the run summary.
4. **Give the index a few days.** The first Common Crawl harvest reads 60 index pages per run, so the list of boards fills in over the first few days and job counts climb with it. Settings → Sources in the app shows its progress.
5. **Check the watchlist rows in Settings → Sources.** Any company marked "needs setup" didn't reveal its job board; fix it in `companies.yaml` (below).

## Running locally

```bash
pip install -r requirements.txt
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

## The board index

`board_index` in `settings.yaml` controls it. What happens each run:

1. **Harvest** (until a pass is complete, then again every 30 days): read the next 60 pages of Common Crawl's URL index for `boards.greenhouse.io`, `job-boards.greenhouse.io`, `jobs.lever.co`, `jobs.ashbyhq.com`, `jobs.smartrecruiters.com` and `*.myworkdayjobs.com`, and add any new boards. Workday boards are kept only if their job URLs have shown a relevant title and a location in your states, because Workday hosts most large employers of every kind.
2. **Check** up to 4,000 boards, 3 requests at a time: every board with a match in the last 45 days, then the boards checked longest ago. A board that fails 3 times in a row is dropped.
3. **Name** each board the first time it has a match (Greenhouse's API, the board page's title, or Workday's posting data).

The index lives in the GitHub Actions cache, not in the repo. If the cache is ever evicted, the next runs simply rebuild it.

What it can't see: employers whose careers sites run on SAP SuccessFactors, iCIMS, Taleo, UKG, ADP and similar systems, because those don't show up as tidy board URLs. Many SAP-heavy manufacturers recruit through SuccessFactors, so that's the biggest gap. It can be closed later with a reader for SuccessFactors career sites, or by turning on Adzuna, which does see them.

## Adzuna (optional)

Adzuna is a commercial job aggregator with a free API key. It sees postings on career systems the board index can't. To use it, register at https://developer.adzuna.com, add `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` as repository secrets, and set `adzuna.enabled: true` in `settings.yaml`. Its free tier has a monthly call limit; the default config rotates (query, state) pairs over three days and caps each run at 25 calls.

## Sync between devices

The tracker, notes, added jobs and settings are saved in each browser. To keep your phone and laptop in step, connect both to a private GitHub repo of your own (Settings → Sync between devices in the app has the same steps):

1. On GitHub, create a new **private** repository named `ShiftBoard-data`. It can stay empty.
2. GitHub Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token. Repository access: **Only select repositories** → `ShiftBoard-data`. Permissions → Repository permissions → **Contents: Read and write**. Pick an expiration and copy the token.
3. In Shift Board on each device: Settings → Sync between devices → paste the token → Connect.

How it works: the app keeps one file, `shift-board-sync.json`, in that repo. It syncs when the app opens, when you come back to it, and a few seconds after each change. Every tracker entry and added job carries a timestamp, so if both devices changed things, the newer edit of each item wins and removals carry over. The app refuses to connect to a public repo. The token is stored only in that browser and can only touch that one repo; disconnecting removes it. Sync doesn't work inside the Claude preview, only on your GitHub Pages site.

Without sync: Settings → Export backup (on a phone this opens the share sheet, so you can AirDrop the file to your laptop), then Import backup on the other device. Imports merge by the same timestamps rather than overwrite.

## Pay estimates

When a posting doesn't list pay, the app shows an estimate in italics, like *est. $145K–$175K*. Tap it to see what it's based on.

Every posting that does list pay is saved to `data/pay_history.json` for a year. NY, NJ, MA and MD require ranges in postings, so this fills up quickly. For a posting without pay, `scraper/pay.py` finds the most similar saved postings and takes a weighted median of their ranges. Similarity counts the kind of role (architect, manager, lead, analyst, quant and so on), the level (senior, manager, director and so on), the state, whether it's an SAP/ERP role, and shared title words. Each neighbour's range is nudged about 10% per level of difference.

Each estimate is labelled by how good the match is: close (4 or more strong matches), partial (2 or 3), or rough (1). It isn't shown at all when nothing similar has been seen yet. Expect estimates to be thin for the first week and to improve as the history grows. They're a guide for deciding what to apply to, not a negotiation number.

The filter "Use estimated pay when a posting has none" decides whether estimates count toward the pay floor and the Highest pay sort.

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

- Common Crawl's index server is a free shared service and is sometimes slow or busy. The harvest retries and, if it still can't get through, pauses until the next run; checking the boards already in the index carries on regardless.
- Each daily check is one request per board (plus a few for Workday postings), similar to a person opening the career page. Keep `workers` low.
- If Adzuna is on, its own pay estimates are used for its postings instead of ours.
- Pay-transparency laws mean most NY, NJ, MA and MD postings include a range; CT and PA ones often don't, which is where the estimates matter most.
- A posting from a company board is marked closed the first day it's missing from a board that loaded fine. Adzuna and HN postings get two weeks, since their queries rotate.
- The job list starts small and grows over the first week as the index fills in.
- Tracker data is per browser unless sync is on. A home screen app on iPhone has its own storage, separate from Safari.

## Tests

```bash
pip install pytest
python -m pytest -q
python scripts/refresh_sample.py   # rebuilds the sample data embedded in web/index.html
```
