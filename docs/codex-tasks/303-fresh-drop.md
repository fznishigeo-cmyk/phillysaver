# Task 303 — a drop must bring NEW deals to the top (operator 2026-10-18, during the ad)

## What the operator saw (live, desktop, after "Next drop" hit 0)
The top row barely changed: the same products, "moved 2 squares to the left". Quote: "the price drop when it reaches 0 should be more dramatic... all top 15-30 tiles need to change! ... people waited 15 minutes for the price drop. they should see 15-30 or more new items on the home page! they cannot be the same items at the top... it needs to look like something is happening or people will lose interest feel like its fake".

Root cause (read dealradar/publish/templates/drop-shuffle.js `shuffleDropPicks`): slots are grouped by 24-card window (`Math.floor(i / 24)`), so cards only rotate WITHIN the first 24 — the same products stay on top.

## Goal (Trending / homepage top-picks view, sort "top picks", no search)
1. At every clock mark, the first 30 cards must contain at least 20 deals that were NOT in the first 30 of the previous mark (and ideally not of the two previous marks). Draw them from the eligible ranked pool (first ~300 eligible rows: has photo, live, not locked/niche/used/open-box/price-unconfirmed/aged — the current eligibility filter), cycling deterministically by mark index so every visitor sees the same drop and a page reload within a window shows the same order. Deals pushed out of the top 30 keep their relative order below it (nothing is removed).
2. Keep the existing protections: per-24-window store/brand/boost shares as far as possible, clothing caps (max_clothing_per_window), checkout-price cap, Allure spacing, max_same_category_run, and task 302 same-product spacing (`spaceSameProducts` runs after; families never within 8, max 2 per 48). Price mix of the first 24 stays mostly <= $50 (the operator's impulse rule: >= 19 of 24 at <= $50 when the pool allows).
3. The drop moment (only when the clock actually crosses a mark while the page is open, not on first load): the new top cards appear with a short staggered fade/slide-in (<= 600 ms total), each card that is new since the previous mark shows a small "Just dropped" badge until the next mark, and a toast/banner near the timer says "{N} new deals just dropped" for ~4 s. prefers-reduced-motion: no animation, badge and toast stay. No layout shift outside the grid; the Back-button restore and scrolled-reader anchor keep working (see refreshDropPicks).
4. Behind config: keep `drop_shuffle` as the switch for the reshuffle; add `drop_fresh` (strict bool, AppConfig default False = previous bytes; config.yaml true) for the new selection + moment. Chain a `pre_drop_fresh` helper from `pre_task_295` in tests/history_pins.py (like pre_card_tidy / pre_same_product_spacing); never re-pin.

## Tests
- Node logic test with a realistic pool (300 eligible + 100 ineligible rows, mixed categories, prices, clothing, a few product families): for 8 consecutive marks, >= 20 of the top 30 are new vs the previous mark; all rows kept (same multiset); constraints above hold; deterministic for the same mark; < 50 ms per mark on 4,000 rows.
- Update tests/drop_shuffle_browser.cjs (real Chromium, I run it) to assert at the mark: >= 20 of the first 30 data-keys changed, "Just dropped" badges present on the new ones, the toast text, reduced-motion has no animation; Back/anchor behavior unchanged.
- OFF/missing bytes test; strict config test.
- Run only the targeted files you touch: `.venv/bin/pytest -q -p no:cacheprovider --basetemp=/tmp/b303 <files>`. No full suite, no commit, no push. Update docs/HANDOFF.md. Reply <= 8 lines.
