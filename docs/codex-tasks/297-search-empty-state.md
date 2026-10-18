# 297 — No empty "Live Amazon prices" heading when there are no cards

## Why
Live check 2026-10-18 05:48Z (live-first-screens #6): /api/search answered
`reason: budget_exhausted` (the daily search token cap, config
search.tokens_per_day 3000, was used up by ~01:45 New York). Every browser then
showed the heading "Live Amazon prices for "cupping set"" with ZERO cards under
it, plus the big Amazon button. A heading that promises prices over nothing
looks broken to a visitor from the Facebook ad.

## Task (no new knob needed if OFF bytes are preserved: put the change in the
search_more ON path, dealradar/publish/search_more.py / templates/search-more*.js,
which already owns amazonResultsPaint when enabled; check search_results.py,
search_boost.py, search_circle.py for where the heading is painted)
1. When a no-match search ends with no Amazon cards for ANY reason
   (budget_exhausted, budget_unavailable, rate_limited after retries, timeout,
   price_error, disabled, empty items), do not show the "Live Amazon prices for
   <q>" heading or an empty grid. Show the big circling button (unchanged label,
   tag, rel) as the main action, with one short honest line above it, e.g.
   "See every <q> price on Amazon" — no claims of discounts or live prices.
2. When cards do arrive later (cache, retry), the heading and cards appear as
   today. Related pages (search-more) on scroll: if a later page hits the cap,
   stop quietly (no error text in the grid), keep the final button.
3. Never mention the price supplier's name anywhere (tests/test_search_more.py
   ::test_shipped_site_never_names_the_price_supplier must stay green).

## Tests
Node harness for each reason above (no heading, no empty grid, button present
with tag); cards-later path; existing search_more / search_boost / search_circle
/ analyze tests green; recorded pins unchanged (search_more OFF = previous
bytes). Browser test additions in tests/search_more_browser.cjs for a
budget_exhausted response (I run Chromium natively). Offline, -n 1
-p no:cacheprovider, own /tmp/task297-* basetemp (delete after), targeted tests
only. No commit/push. HANDOFF "In flight": one entry. Read CLAUDE.md first.
