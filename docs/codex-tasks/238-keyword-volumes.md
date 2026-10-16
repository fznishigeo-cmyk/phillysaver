# 238 — Keyword volumes from Google Keyword Planner (read-only, manual run)

## Why

Operator plan 2026-10-16: build a multi-store "Is this a good deal?" checker (/check/) plus store pages. Which pages and phrases come first depends on real US search volumes. Today we have only low-confidence estimates, for example:
- "best buy price history" 1K–3K;
- "price drop alert" 0.5K–2K;
- "costco price adjustment" 10K–40K.

Google Ads API access is on Cloud project `wowpriced-511321` at Explorer level (docs/HANDOFF.md ~line 60). Third-party write-ups say Explorer blocks KeywordPlanIdeaService and Basic is needed. We could not open Google's access-level page from the sandbox, so the first run is also the access test: under Explorer, expect an error code, log it and stop.

The same OAuth identity can pause campaigns (docs/ads-guard.md), so read-only must be enforced in code and tests. phillysaver is public: volumes, CPCs and per-keyword results never go to Actions logs.

## Read first

- Project rules and state: CLAUDE.md, AGENTS.md, docs/HANDOFF.md (Google Ads lines ~58-66), docs/ads-guard.md.
- `dealradar/growth/google_ads.py`: `TOKEN_URL`, `access_token`, `_check`/`_codes` with `codes_only=True`, `GoogleAdsError`.
- `dealradar/growth/report.py` `save_copy` (~line 1161): private reports repo, 1 request/second, `clock.sleep`.
- `dealradar/config.py`: growth settings ~line 941 (`growth_google_ads_api_version`, `growth_google_ads_customer_id` normalized to digits, `growth_google_ads_login_customer_id`), `resolved_user_agent`.
- `dealradar/cli.py`: the `growth-report` and `ads-guard` subcommands and `common(sp)`.
- Workflows: `.github/workflows/growth-report.yml` and `ads-guard.yml` (secrets as env-map values only).
- Fake-transport patterns: tests/test_growth_report.py and tests/test_ads_guard.py.

## Task

### 1. Config

Add `growth.keyword_volumes` in config.yaml with three keys:
- `keywords`: a list;
- `geo_target: 2840` (US);
- `language: 1000` (English).

Validate in config.py:
- 1..100 keywords, each stripped, lowercase and unique;
- each keyword at most 80 chars, from `[a-z0-9 '-]` only;
- geo_target and language are positive ints.

Ship this starter list (40):

best buy price history, best buy price tracker, target price history, target price tracker, woot price history, price drop alert, price drop alerts, price drop alert email, price tracker, price history chart, is this a good deal, is this a good price, price checker, was price checker, is this sale real, fake discount, fake discount checker, walmart was price, walmart rollback meaning, is walmart rollback a good deal, walmart price history, walmart price tracker, costco price adjustment, target price adjustment, target price match, best buy price match, best buy price match after purchase, home depot price adjustment, lowes price adjustment, kohls price adjustment, macys price adjustment, price adjustment after purchase, black friday price adjustment, amazon price checker, amazon price tracker, amazon price history, amazon typical price, ps5 price history, airpods price history, camelcamelcamel walmart.

### 2. New module `dealradar/growth/keyword_volumes.py`

Entry point: `run(cfg, *, http_client=None, now=None) -> int`.

**Credentials**
- Read `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET` and `GOOGLE_ADS_REFRESH_TOKEN` via `get_secret`.
- If any is missing: log `MISSING_CREDENTIALS`, exit 1, send no HTTP.
- Get the token with `google_ads.access_token(..., codes_only=True)`.

**The request**
- Exactly one metrics POST to `https://googleads.googleapis.com/{version}/customers/{customer}:generateKeywordHistoricalMetrics`.
- Version and customer come from the cfg properties above.
- Headers:
  - `Authorization: Bearer`;
  - `login-customer-id` only when set;
  - User-Agent `cfg.resolved_user_agent()`.
- Body:
  - `keywords`;
  - `geoTargetConstants: ["geoTargetConstants/<geo>"]`;
  - `language: "languageConstants/<lang>"`;
  - `keywordPlanNetwork: "GOOGLE_SEARCH"`;
  - `historicalMetricsOptions: {"includeAverageCpc": true}`.
- Do not send `yearMonthRange`; Google's default is the last 12 months.

**Read-only by construction**
- Every Google request goes through one helper.
- The helper checks the URL against a module allowlist (the token URL plus that single endpoint, built from cfg) and raises before sending anything else.
- The text "mutate" must not appear anywhere in the module.
- Do not import or reference `ads_guard`, `ads_setup` or `GuardAPI`.

**Pacing**
- At least 1 s between any two requests, via `clock.sleep` so tests can fake it.
- On HTTP 429 or `RESOURCE_EXHAUSTED`: one retry after 30 s, then stop.
- At most 3 Google requests per run (token, metrics, one retry).

**Errors**
- Any other 4xx/5xx goes through `_check(resp, codes_only=True)`.
- Log Google's codes only, exit 1, and write no private file.
- Exit 1 for an Explorer-level refusal too. Record the codes seen in HANDOFF after the first real run.

**Parsing**

For each result, read:
- `text` and `closeVariants`;
- `keywordMetrics.avgMonthlySearches`;
- the lowest and highest month in `monthlySearchVolumes`;
- `competition`;
- `lowTopOfPageBidMicros`, `highTopOfPageBidMicros` and `averageCpcMicros`, shown as dollars.

Int64 values arrive as strings; a missing value gives a blank cell. A requested keyword absent from the results is listed as "merged or no data". A wrong shape gives `INVALID_RESPONSE` and exit 1.

**Output**
- A markdown table sorted by avg monthly searches (descending, ties by keyword).
- A header line with: UTC date, geo, language, API version, and "Google rounds these; accounts with little spend may get coarse values".
- Save via `report.save_copy` to `keywords/<UTC date>.md` in `growth.reports_repo`.
- Add optional `path` and `message` parameters to `save_copy`. Their defaults must keep the growth report's path (`reports/<day>.md`), commit message and bytes unchanged.
- If the private copy is not saved (no repo, no `REPORTS_REPO_TOKEN`, or an HTTP error): exit 1, and drop the results without logging them.

**Public log** contains only:
- the number of keywords sent and results received;
- saved or not saved;
- error codes.

It never contains a volume, CPC, competition value or per-keyword line.

**Not touched:** no state.db, LLM, Keepa or Telegram.

### 3. CLI

Add `python -m dealradar keyword-volumes` in cli.py, using the `common(sp)` pattern with a lazy import.

### 4. Workflow `.github/workflows/keyword-volumes.yml`

- Trigger: `workflow_dispatch` only, with no schedule.
- `permissions: contents: read`, `timeout-minutes: 5`.
- `concurrency: {group: keyword-volumes, cancel-in-progress: false}`.
- Steps: checkout, Python 3.12, `pip install -e .`, then one step that runs the command.
- That step's env: the three `GOOGLE_ADS_*` secrets and `REPORTS_REPO_TOKEN`, each `${{ secrets.X }}` written as an env-map value only.
- No restore_state or commit_state, and no git push.

### 5. Docs

In docs/HANDOFF.md:
- Add a short "Keyword volumes" note: how to dispatch the workflow, the expected Explorer refusal, and the owner-side Basic access steps (brand verification on the OAuth consent screen, then apply from the Google Ads API page in Cloud Console).
- Fix the stale "API version pinned in config (`v26`)" line to `v25`, matching config.yaml.

## Tests (offline, new file tests/test_keyword_volumes.py)

**Happy path** (fake transport and fake clock):
- Google sees exactly the token POST and then the metrics POST, followed by save_copy's GET and PUT to `api.github.com/.../contents/keywords/<day>.md`.
- The body fields and headers are as specified.
- Requests are at least 1 s apart.
- The table is sorted, string int64 values are parsed, and a missing keyword shows as "merged or no data".

**Retry and errors:**
- 429 once, then 200: exactly one retry, after 30 s.
- 429 twice: exit 1, no file written.
- 403 with Google error details: exit 1, codes-only log (no message text with digits), no retry, no file.
- Missing secrets: exit 1 and zero requests.
- Missing reports token, or `reports_repo: ""`: exit 1, and no results in the log.

**Read-only guard:**
- The allowlist helper refuses any other URL, including a `campaigns:mutate` URL, before the transport is called.
- An AST and text check of keyword_volumes.py finds no "mutate", "ads_guard", "ads_setup" or "GuardAPI".

**Log hygiene:** with planted values such as 98765 searches and 4321000 micros, `caplog.text` contains none of those values and no keyword text.

**Config validation:** an empty list, duplicates, uppercase, more than 100 entries, an over-long keyword and bad characters are all rejected.

**save_copy defaults:** a new test asserts the default path `reports/<day>.md` and the default commit message are unchanged. The existing tests/test_growth_report.py must pass unmodified.

**Workflow shape** (in this file, not test_workflows.py):
- dispatch only, no schedule;
- contents: read;
- timeout 5;
- own concurrency group;
- every `secrets.` line is an env-map value;
- no restore_state, commit_state or git push.

## Rules

- Read-only: no Google Ads writes of any kind. Do not touch ads_guard.py, ads_setup.py, GuardAPI or their workflows.
- No numbers in public logs.
- Measuring "amazon" phrases is fine; never bid on them.
- Do not dispatch the workflow; Claude runs it after review.
- Do not change tests/test_workflows.py.
- Run only the touched tests, with `-n 1` and your own basetemp: `.venv/bin/pytest -q -n 1 --basetemp=/tmp/task238-a tests/test_keyword_volumes.py tests/test_growth_report.py tests/test_ads_guard.py`. Delete the basetemp after each run, and do not run the whole suite.
- Do not commit.
- Add one short entry to docs/HANDOFF.md "In flight".
