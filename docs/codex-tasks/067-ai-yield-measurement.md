# Codex task 067 (roadmap brief 5): audit intake against the real site floor and persist useful AI yield

Source: docs/roadmap-beat-big-sites.md (task 063). Tasks 061 (spotlight, in flight) and 062 (price chart, shipped) are owned elsewhere; do not touch their code.


**Read first:** AGENTS.md, CLAUDE.md, docs/HANDOFF.md,
docs/codex-tasks/059-more-deals-per-ai-dollar.md. Files:
`dealradar/parse/extractor.py` (`_below_floor_post`, `_can_clear_floor`,
`parse_posts`), `dealradar/pipeline.py` (post-critic audit and summary),
`dealradar/db.py` (`run_stats`, source audit, spend ledger),
`dealradar/config.py`, `config.yaml`, `dealradar/growth/report.py`,
`tests/test_parse_value.py`, `tests/test_ai_spend.py`,
`tests/test_spend_pacing_shadow.py`, `tests/test_money_math_correctness.py`,
`tests/test_e2e_offline.py`, `tests/test_growth_report.py`.

#### Why

Task 059 already ships cost controls, but a safe rollout needs persistent,
deduplicated useful yield. The prefilter/second-read threshold must respect
the lower site tier if configured; current headline and site floors happen
to be equal, which hides that edge case.

#### Do

1. Derive the conservative prefilter and second-opinion eligibility floor
   from `min(cfg.min_pct, cfg.site_show_from_pct)`, matching the site's
   effective floor. Keep extra-savings/range/ambiguous/structured exemptions,
   the existing conservative buffer, evidence verification and final
   per-condition publication gates. All discount arithmetic stays in
   `savings.py`; this does not lower any publication floor.
2. Keep `parse.prefilter_below_floor: shadow` in shipped config. Count
   candidate posts and post-critic publishable misses by source, plus
   candidates not fully evaluable because extraction/critic/caps failed.
   Zero judged misses with incomplete evaluation is not a clean result.
3. Persist compact, idempotent yield facts in encrypted state, sufficient
   for private daily/seven-day aggregates. Count distinct newly public
   deal identities once and useful evidence-backed price changes
   separately. Multiple sightings, feed/email copies, critic rejects,
   below-floor items, old inventory and reruns do not inflate new supply.
   Mark pre-instrumentation history unavailable and record coverage dates.
   Use bounded retention consistent with run_stats; store no post bodies,
   rejected extracts or full summary blobs.
4. Add `ai_yield.unique_new_public_deals`, `useful_price_updates`,
   `new_deals_per_usd`, `prefilter_unevaluated` to the run summary and
   private growth report. Denominator includes all actual model-priced
   paid work, category/critic/shadow calls and conservative uncertain
   spend, with zero-spend ratios shown as unavailable. Preserve real
   additional spend when two executions share a run key; yield accounting
   must not overwrite or bypass the reservation ledger.
5. Report readiness for a later prefilter activation, without switching
   automatically. Proposed minimum evidence: seven complete UTC days,
   at least 200 fully evaluated candidate posts across at least three
   independent source sites, zero publishable misses, no unexplained
   evaluation gaps, and preserved source/category supply in the offline
   replay. Fewer candidates means insufficient evidence. Any miss keeps
   shadow mode and prompts a narrower rule. These are rollout criteria,
   not a claim that the present checkout already satisfies them.
6. Keep critic model, critic reservation share, Keepa checks, pacing,
   per-run/day caps, parse-reuse evidence checks, retries and outgoing
   idempotency intact. Add no new live LLM calls solely for this audit;
   use existing shadow observations and offline synthetic replay fixtures.

#### Tests

Exercise equal/lower site floors, per-condition floors, near-boundary
discounts, extra coupons/rebates, ranges, roundups, malformed inputs and
free structured posts. Shadow output must match off at equal floors;
lower-tier useful posts are never skipped. Test publishable-miss and
unevaluated counts after critic, deduped cross-source/feed/email yield,
price updates versus new identities, zero cost, unknown models/uncertain
cost, cap/deadline interruptions, UTC boundaries, old read-only snapshots,
bounded retention, shared-run-key additional spend and idempotent reruns.
Replay synthetic ambiguous and eligible fixtures without network/paid
calls. Report readiness tests must fail closed for incomplete or too-small
samples. Do not weaken existing evidence or Keepa critic tests.

Run `.venv/bin/python -m pytest -q`, including the offline rerun contract;
name sandbox-blocked checks and never describe a blocked suite as green.
Add one HANDOFF bullet recording the observation start and what activation
evidence remains. Do NOT commit or push; end with what changed and anything
unsure.

Run `.venv/bin/python -m pytest -q`; all green except tests needing local
sockets your sandbox blocks (name them). One short HANDOFF bullet. Do NOT
commit or push. End with what changed and anything unsure.
