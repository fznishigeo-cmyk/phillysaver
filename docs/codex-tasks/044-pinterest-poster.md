# Codex task 044: Pinterest — connect once, then post our top deals

**Read first:** AGENTS.md, CLAUDE.md (idempotency; knobs in config.yaml;
never log or publish secrets; state.db lives encrypted on the data branch).
Files: `dealradar/cli.py`, `dealradar/pipeline.py` (where the hourly lane
ends), `dealradar/db.py` (meta get/set), `dealradar/publish/site.py`
(`pick`, deal page paths via `seo`), `.github/workflows/hourly.yml` (state
restore/commit steps, concurrency group `dealradar`), `config.yaml`,
`tests/test_workflows.py`.

Operator (2026-10-12): automated daily posts to Pinterest. The Pinterest
app "wowpriced deals" (App ID 1623198) has **Trial access**: every write
goes to the **sandbox** (`https://api-sandbox.pinterest.com/v5`), Pins are
visible only to us. That is the first rollout. Standard access later only
flips `environment` to production. GitHub secrets already exist:
`PINTEREST_APP_ID`, `PINTEREST_APP_SECRET`. Redirect URI registered:
`https://wowpriced.com/`.

## 1. Config (`config.yaml`, new `social.pinterest`)
```yaml
social:
  pinterest:
    enabled: true
    environment: sandbox          # sandbox (Trial access) | production
    redirect_uri: "https://wowpriced.com/"
    board_name: "Deals up to 90% off"
    pins_per_run: 2               # hourly lane
    daily_max: 20                 # per UTC day
    disclosure: "#ad"             # last word of every description
```
API base: `https://api-sandbox.pinterest.com/v5` for sandbox,
`https://api.pinterest.com/v5` for production; OAuth page always
`https://www.pinterest.com/oauth/`.

## 2. Connect once (`dealradar/publish/pinterest.py` + CLI + workflow)
- `python -m dealradar pinterest-auth-url` prints the authorize URL
  (client_id from env, redirect_uri, `response_type=code`, scopes
  `boards:read,boards:write,pins:read,pins:write,user_accounts:read`, a
  random `state`). No secret in it.
- `python -m dealradar pinterest-connect --code CODE`: POST
  `{api_base}/oauth/token` (Basic auth app id:secret, form body
  `grant_type=authorization_code`, `code`, `redirect_uri`,
  `continuous_refresh=true`). Store in db meta `pinterest_auth`:
  access_token, refresh_token, expires_at, refresh_expires_at,
  environment. Print only "connected (sandbox), access token valid N days".
- Before each use: if the access token expires within 3 days, POST
  `grant_type=refresh_token` and store what comes back (a new
  refresh_token too when Pinterest sends one). An `environment` mismatch
  between config and stored tokens = not connected (log one line).
- New workflow `.github/workflows/pinterest-connect.yml`
  (workflow_dispatch, input `code`): first step `::add-mask::` the code;
  restore state exactly like hourly.yml, run the connect CLI, commit state
  to the data branch exactly like hourly.yml; concurrency group
  `dealradar` (never runs beside the pipeline). Env: the two Pinterest
  secrets and STATE_DB_KEY only.

## 3. Post (hourly lane, after the site deploy step)
- New CLI `python -m dealradar pinterest-post` called by a new last step
  in hourly.yml (`continue-on-error: true`; env the two Pinterest secrets),
  after deploy, so every Pin links to a page that is live. It restores and
  commits state like the other steps (or runs inside the existing
  run/commit order; pick the simplest that keeps the posted log durable).
- Board: find `board_name` via GET /boards (paged); create it (PUBLIC)
  if missing; remember its id in meta.
- Candidates: the live published deals that carry `pick` (task 042), in
  pick order, with an https `image_url`, not already in meta
  `pinterest_posted` ({deal key: {pin_id, at}}). Post up to
  `pins_per_run`, never past `daily_max` per UTC day.
- Pin: `title` (max 100 chars) = "{pct}% off {item}" trimmed at a word;
  `description` (max 500) = "{item}. {pct}% off at {store}, verified by
  wowpriced. {disclosure}"; **no prices** (Amazon's rules for social
  posts); `link` = the deal's page `https://wowpriced.com/deal/<slug>/`;
  `alt_text` = item; `media_source` = `{"source_type": "image_url",
  "url": image_url}`.
- Record each success before the next call; a failed Pin is logged (status
  code only) and retried next run; a 401 stops the run (not connected).
- Not connected / disabled / no secrets: one log line, exit 0.

## 4. Safety
Tokens, code and secret never appear in logs, summaries, deals.json, the
site or the repo (test: render the site after connecting and grep every
file). Counts only in logs: "pinterest: posted N (today M/20), board ok".

## 5. Tests (httpx MockTransport, no network)
Auth URL params; code exchange stores tokens (sandbox base used); refresh
near expiry and keeps a returned new refresh token; environment mismatch;
board found / created; candidate order, image and pick filters; daily and
per-run caps; re-running posts nothing twice (idempotent); 401 stops;
title/description limits and no "$" in either; workflow masks the code,
uses group `dealradar`, restores and commits state; secrets never in
published files. One short HANDOFF bullet under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
