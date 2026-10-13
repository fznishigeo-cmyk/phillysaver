# Codex task 052: source coverage audit (are we getting every deal our partners post?)

**Read first:** AGENTS.md, CLAUDE.md (politeness is law; permission bypass is
host-only; never publish source text or images beyond the grant). Files:
`sources.yaml`, `dealradar/fetch/` (adapters, `registry.py`, `validate.py`),
`dealradar/db.py` (`source_health`, posts, sightings, deals),
`dealradar/pipeline.py` (where items are fetched, parsed, dropped),
`dealradar/cli.py`, `.github/workflows/` (state restore/commit pattern in
hourly.yml; `growth-report.yml` for a read-only job that restores state),
`dealradar/publish/admin_alerts.py` (`send_admin_text`).

Operator (2026-10-13): ~20 sites gave written permission to pull their deals.
Make sure we really get them: for each source, what it lists versus what
reaches the site, and why the rest does not. The fast lane currently warns
"11 sources tripped" (circuit breaker open).

## 1. CLI `python -m dealradar source-audit` (read-only on state)
For every enabled source (permission sources first), using the existing
adapters and the same polite fetcher (no LLM calls, no writes to state):
- fetch once: HTTP status, items found, newest item date, robots/permission
  used, tripped state and reason from `source_health` (last error, since,
  trips);
- from the database, last 24 h and 7 days: posts seen, posts parsed, deals
  extracted, deals dropped by reason (evidence, below floor, critic suspect,
  duplicate, dead), deals live on the site now, share of live deals whose
  Get deal link carries the source's credit (permission condition);
- verdict per source: OK / LOW YIELD (items but few deals) / STALE (newest
  item older than 3 days) / BROKEN (fetch fails or 0 items) / TRIPPED, with
  the most likely cause from the data (e.g. adapter selector no longer
  matches, feed moved, 403).
Output: a Markdown table to stdout and to `$GITHUB_STEP_SUMMARY`, plus a
short Telegram admin message (counts per verdict and the worst 5) via
`send_admin_text` when the bot secrets exist. No deal text or images in any
output; source names, counts, statuses only.

## 2. Workflow `.github/workflows/source-audit.yml`
Manual (`workflow_dispatch`) and weekly (Monday 13:17 UTC). Restores state
read-only like growth-report.yml (never commits state), runs the CLI with
the secrets the adapters need (same names as fast.yml) and the Telegram
bot secrets. Concurrency group `source-audit`.

## 3. Tests (offline; httpx MockTransport and a seeded DB)
Each verdict; drop-reason counts; credit-share; tripped reason surfaced;
permission host-only bypass respected (a non-granted host blocked by robots
stays blocked); no deal text or image URL in outputs; workflow shape (never
commits state, secrets as env-map lines). One short HANDOFF bullet.

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
