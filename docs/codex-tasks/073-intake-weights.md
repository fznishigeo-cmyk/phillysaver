# Codex task 073 (roadmap #19): useful, diverse intake per AI dollar (shadow first)

Source: docs/roadmap-beat-big-sites.md item 19. Shipped on this base (keep working): 061 spotlight, 062 price chart, 064 static fallback, 065 phone filters + Back state, 066 saved deals, 067 AI yield measurement (`dealradar/ai_yield.py`, `ai_yield.*` summary), 068 deal-page links, 069 installable app. Running in parallel and owned elsewhere: 070 (SEO audit: `seo.py` audit, sitemap `lastmod`, `seo_audit` summary) and 071 (price watches in `engagement.js`/`index.html`). Do not touch their areas beyond what your task strictly needs; if you must touch `seo.py`, keep the change small and say exactly where.


- **Benefit:** more relevant nonduplicate offers across everyday stores
  and departments throughout the day, with the same AI budget.
- **Impact:** high / medium / high / high.
- **Effort / risk:** M / medium; depends on rank 5 measurement. Existing
  source rotation/interleaving, audit counters and Keepa reserve already
  exist. Add bounded per-source intake weights with exploration and a
  maximum wait, rather than pausing a low-yield source permanently or
  filling the wall with one earning store.
- **Files:** `dealradar/pipeline.py`, `dealradar/source_audit.py`,
  `dealradar/config.py`, `config.yaml`, `dealradar/keepa_budget.py`,
  `dealradar/growth/report.py`, `tests/test_pipeline_sources.py`,
  `tests/test_source_audit.py`, `tests/test_spend_pacing_shadow.py`,
  `tests/test_front_page.py`.
- **Measure:** Existing `extract_by_source`, `spend_by_kind`,
  `price_recheck.tokens_short`; New run-summary
  `intake.unique_public_by_source`, `intake.oldest_deferred_hours`,
  `intake.public_category_mix`. Judge unique eligible yield and coverage,
  not raw extracted counts or a source's affiliate earnings alone.


## Do
1. Read how sources are chosen and ordered each run today (rotation,
   interleaving, fast/hourly lanes, per-source budgets, Keepa reserve,
   pacing, `max_posts_per_run`) and the 067 yield facts. Summarize it in the
   HANDOFF bullet before changing anything.
2. Compute bounded per-source intake weights from 067's deduplicated useful
   yield (unique new public deals + useful price updates per dollar) over a
   rolling window, with: a floor so no source is starved, an exploration
   share for new/low-sample sources, a maximum wait (every source is read
   at least once per configured hours), and a cap so one source or one
   earning store cannot dominate. Knobs in `config.yaml` under
   `intake:` with a `mode: shadow | on` (ship `shadow`).
3. Shadow mode changes NOTHING about which posts are read; it records what
   the weighted order would have been and reports
   `intake.unique_public_by_source`, `intake.oldest_deferred_hours`,
   `intake.public_category_mix`, plus a would-have-changed count. `on`
   reorders/limits intake within today's existing caps (tested, not
   enabled).
4. Keep caps, pacing, ledger, evidence checks, robots/politeness, crawl
   permissions, circuit breakers and idempotent re-runs exactly as they
   are. Tripped/blocked sources stay as they are; weights never retry a
   tripped source early.
5. Private growth report: a short "Intake (shadow)" section with the
   top/bottom sources by useful yield per dollar and the oldest deferred
   source, with sample sizes; unavailable when 067 data is too young.

## Tests
Weights with zero/low samples, exploration share, floor, max wait, cap;
shadow output identical intake to off; `on` respects caps and max wait;
no change to tripped sources; summary keys present; rerun idempotency.

Run `.venv/bin/python -m pytest -q`; all green except tests needing local
sockets your sandbox blocks (name them). One short HANDOFF bullet. Do NOT
commit or push. End with what changed and anything unsure.
