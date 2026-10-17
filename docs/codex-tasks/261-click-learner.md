# 261 — Learn from clicks: bounded, slow ranking adjustments (shadow first)

## Why
Operator 2026-10-17: "as people click, learn what they click and constantly
adjust". The first-party click signal already exists (dealradar/clicks.py:
D1 ingestion, `click_signal_cursor`, `click_signal_shadow`). Build the
learner that turns it into ranking factors — safely.

## Task
1. Read clicks.py and how its shadow is computed today; reuse its data.
2. A learner (`dealradar/click_learning.py`) that, once per day (UTC), fits
   per-feature click-through multipliers for: category, appeal group, staple
   group (task 260's `staple_group`, if present; else skip), price band
   (≤$25, $25–60, $60–150, >$150), store. Use impressions you can derive
   honestly (cards published in the first 24/48 positions per run, stored
   privately and bounded) vs clicks; empirical-Bayes shrinkage toward 1.0
   with a minimum evidence threshold (e.g. ≥ 200 impressions and ≥ 10
   clicks per feature) — below it the factor is exactly 1.0.
3. Guardrails: each factor clamped to [0.90, 1.15]; at most ±0.03 change
   per day per feature; the product of all learned factors clamped to
   [0.85, 1.20]; never applied to used/open-box; never brings back a None
   score; deterministic for the same inputs; rerun-idempotent per day.
4. `publish.front_page.click_learning: {enabled: false, shadow: true, …}`.
   Shadow: compute the would-be order in memory and record a private summary
   (meta ring + growth-report section; NEVER public logs/bytes — see 255b).
   Enabled: multiply in `pick_score` after the other tilts. Public `ps`
   excludes learned factors (they are private).
5. Bounded private storage (≤ 30 days, size-capped).

## Tests (offline, tests/test_click_learning.py)
Shrinkage below threshold = 1.0; clamps; daily step limit; determinism;
rerun idempotency; shadow-only leaves public bytes identical; no learned data
in public logs/bytes; recorded byte pins keep their hashes with the switch off
(never re-pin).

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task261-*` basetemp
(delete after), targeted tests only, never the full suite. No network, no
commit, no push. HANDOFF "In flight": one short entry.
