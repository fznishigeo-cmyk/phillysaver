# Codex task 072 (roadmap #18): reliable IndexNow receipt and retry accounting

Source: docs/roadmap-beat-big-sites.md item 18. Shipped on this base (keep working): 061 spotlight, 062 price chart, 064 static fallback, 065 phone filters + Back state, 066 saved deals, 067 AI yield measurement (`dealradar/ai_yield.py`, `ai_yield.*` summary), 068 deal-page links, 069 installable app. Running in parallel and owned elsewhere: 070 (SEO audit: `seo.py` audit, sitemap `lastmod`, `seo_audit` summary) and 071 (price watches in `engagement.js`/`index.html`). Do not touch their areas beyond what your task strictly needs; if you must touch `seo.py`, keep the change small and say exactly where.


- **Benefit:** changed and ended search pages reach participating engines
  sooner. HANDOFF identifies the current publish-before-POST accounting,
  which can defer recovery from a failed send for a week.
- **Impact:** medium / medium / medium / medium.
- **Effort / risk:** M / medium; separate content discovery from receipt,
  acknowledge only the submitted batch after a valid response, and retry
  with backoff. Preserve serialization with the existing encrypted-state
  lifecycle; deployment failure must never acknowledge unpublished pages.
- **Files:** `dealradar/publish/seo.py`, `dealradar/cli.py`,
  `dealradar/db.py`, `.github/workflows/fast.yml`,
  `.github/workflows/hourly.yml`, `tests/test_seo.py`,
  `tests/test_workflows.py`, `dealradar/growth/report.py`.
- **Measure:** New run-summary/growth-report `indexnow.pending`,
  `indexnow.received`, `indexnow.key_pending`, `indexnow.failed`,
  `indexnow.oldest_pending_hours`. A 200 acknowledges receipt, and 202
  still has key validation pending; neither promises indexing, per
  [the protocol](https://www.indexnow.org/documentation). Do not call it
  Google instant indexing or add a new external sending channel.


## Do
1. Read the current IndexNow flow end to end (due-list in `seo.py`, the POST
   in `cli.py`/workflows, what state marks a URL as sent) and write the
   failure modes you find in the HANDOFF bullet.
2. Separate "page changed and is due" from "engine received it". Only a
   valid response for the exact submitted batch acknowledges those URLs;
   200 = received, 202 = received with key validation pending (keep a
   pending-key state and re-check the key file is published), 4xx/5xx or
   transport error = failed with bounded exponential retry (config knobs;
   never more than the protocol's batch limits; at most one POST batch per
   run per engine endpoint).
3. Never acknowledge a URL whose page was not actually deployed in that
   run (deploy failure, skipped publish). Keep the encrypted-state
   lifecycle and idempotent re-runs: re-running a run key never re-POSTs an
   acknowledged batch and never double-counts.
4. Summary + private growth report: `indexnow.pending`, `received`,
   `key_pending`, `failed`, `oldest_pending_hours`. Wording: receipt, not
   indexing; never "Google instant indexing". No new external channel.

## Tests
Due vs received separation; 200 / 202 / 403 / 422 / 429 / 5xx / timeout;
retry backoff and cap; deploy failure leaves URLs pending; batch size cap;
rerun idempotency (tests/test_e2e_offline.py::test_rerun_is_idempotent);
report rows with zero and unavailable data; real-git workflow tests still
pass.

Run `.venv/bin/python -m pytest -q`; all green except tests needing local
sockets your sandbox blocks (name them). One short HANDOFF bullet. Do NOT
commit or push. End with what changed and anything unsure.
