# 350 — Spend idle Keepa tokens on the best NEW-item price drops in the operator's focus groups

Repo: dealradar (wowpriced.com). Read CLAUDE.md first; every non-negotiable
applies (evidence verified in code, math only in savings.py, AI caps, knobs in
config.yaml, idempotency, politeness, Amazon rules: no price history, averages,
stars or reviews ever DISPLAYED; internal filters are fine).

## Why (operator 2026-10-20)
"are you utilizing [Keepa]? 1 token per second" → "build it ... and sexy items..
not adult items just sexy stuff for girlfriends to buy for their boyfriends to
look sexy in or wives to wear".
The plan grants 60 tokens/min (86,400/day). Live runs use ~10/min: hourly
#271 token_plan needed_per_minute 10.3 of 60 granted, bucket 3,565 → 3,490 of
3,600, tokens_short 0, "overflow_discovery": {pages 0, tokens 0,
new_candidates 0}. Amazon NEW discovery today = fixed page counts
(keepa.amazon_pages_per_pull 12, focus_categories 6 roots ×
focus_pages_per_category 2, popular page, overflow_extra_pages_max 8) in
dealradar/fetch/keepa.py (_run_pass/_run_focus/_run_popular_page, ~line 660+),
budgeted by dealradar/volatility.py (~line 320). Amazon used/Warehouse is off
(amazon_used.enabled false), so most of the old overflow logic finds nothing.

## Build (behind ONE config switch, strict bool, default false = previous bytes/behaviour)
`keepa_focus_finder:` block in config.yaml (enabled + knobs). With it on:
1. Budget: after the existing deadlines/reserves (top/wall rechecks,
   keepa.reserve_tokens, visitor_reserve_tokens, search budget), spend the
   PREDICTED idle tokens of this run (reuse volatility's surplus/overflow
   forecast — do not invent a second forecaster) on extra Keepa /deal pages,
   up to a configured share (e.g. 0.7) and a per-run page cap. Never let it
   delay due price rechecks; never run under the reserves; respect the run
   deadline and 1 req/s pacing.
2. Targeting: NEW condition only (Amazon's own or Buy Box new price), current
   price ≤ max_price (config 50), genuine drop vs the 90-day average ≥ the
   existing min_pct logic (keepa.genuine_drops_only), sane sales rank, and an
   internal rating/review floor (filter only, never displayed). Target the
   operator's groups through Keepa category ids (resolve sub-category ids via
   Keepa's category lookup/search once and cache them in meta like the
   existing ROOTS/NAMES caches; config lists names). Groups (config, ordered,
   rotate pages fairly across runs):
   - women: women's clothing, dresses, tops, handbags, jewelry, beauty/makeup,
     skincare, fragrance;
   - sexy (NOT adult): women's lingerie, bras, panties, bodysuits, sleepwear &
     loungewear (satin/silk pajamas, nightgowns, babydolls), robes,
     swimwear; men's underwear/boxer briefs, men's sleepwear/robes, men's
     swim trunks, cologne;
   - kids & baby: toys, kids' clothing/shoes, baby essentials;
   - everyday essentials: household supplies, paper/cleaning, personal care,
     grocery staples;
   - gadgets: small electronics/accessories (headphones, chargers, smart
     home, phone accessories).
3. Adult exclusion is absolute: drop Keepa products flagged adult
   (isAdultProduct or equivalent) AND any title matching the existing
   front_page appeal exclude_words (reuse the existing matcher/regex — same
   list the site uses). Add the sexy-group test cases: "Women's Satin Pajama
   Set" kept; "Men's Boxer Briefs 5-Pack" kept; "... vibrator ..." /
   "adult toy" dropped.
4. Publish through the EXISTING Keepa-deal path (same evidence, critic,
   Buy Box/click-price rules, savings.py math, photos from Keepa images,
   link with our Amazon tag). No new LLM calls per deal beyond what Keepa
   deals already cost; add a per-run cap on new deals from this lane
   (config) so AI spend stays inside the existing caps.
5. Dedupe against live deals/ASINs and against each run's other passes.
6. Run summary: one `keepa_focus_finder` block with pages, tokens, candidates,
   kept/dropped by reason (adult, price, drop, rank, dup), published by group,
   idle-before/idle-after estimate. Keep it short (the hourly log tail is
   capped at 5,000 lines).

## Tests
Unit tests with recorded/fake Keepa responses (no network): budget never
dips under reserves or delays due rechecks; pages scale with predicted idle
and stop at caps/deadline; group rotation; ≤$50 + genuine-drop + adult
filters (cases above); dedupe; OFF = previous behaviour/bytes (history pin
pattern in tests/history_pins.py — add a pre_ helper chained from the newest
existing pre_ helper); idempotent rerun (tests/test_e2e_offline.py
test_rerun_is_idempotent must stay green). Run targeted tests; do NOT run the
full suite (the operator does).

Reply with: files changed, the budget formula, expected extra tokens/hour at
today's numbers, test commands + results.
