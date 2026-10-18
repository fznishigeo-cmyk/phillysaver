# 298 — Our robots must not spend Amazon search lookups; settle reservations to real cost

## Why
search.tokens_per_day was 3,000 (now 5,000) and ran out at 05:45Z on 2026-10-18
WITHOUT ad traffic. functions/api/search.js reserves RESERVATION = 110 tokens per
Keepa call (10 search + up to 100 product) and never gives any back, so 3,000 =
about 27 searches a day. Our own robots search live: scripts/site_check.js
(hourly, a fresh title word each run → cache miss; with search_more the related
pages call /api/search too), scripts/visitor_watch.js (every 10 minutes, QUERY),
scripts/live_first_screens.js and scripts/mobile_audit.cjs.

## Task
1. Robots: in every Playwright script that opens wowpriced.com, route
   `**/api/search*` to a canned local response (items from a small fixture, or
   `{items:[], reason:"robot"}` where the check only needs the empty state) so no
   robot ever reaches Keepa — EXCEPT live_first_screens' explicit /api/search
   checks, which must use one fixed query (cached for 6 h) at most. Keep every
   existing check meaningful (assert against the fixture). Update the logic
   harnesses/tests (visitor_watch_logic.cjs etc.) accordingly.
2. Function: when Keepa returns a trusted, finite `tokensConsumed` <= RESERVATION,
   settle the day's ledger to the real cost (refund the difference) atomically in
   the same D1 row (no double refund on retries; never refund on absent or
   untrusted receipts; keep the oversized-receipt halt). Test with the existing
   function harness (tests/search_results_function.test.mjs).
3. Report in docs/research/search-tokens.md: searches per day possible before
   and after (from RESERVATION and typical tokensConsumed in tests).

## Tests
Function tests (settle, no double refund, untrusted receipt, oversized halt,
concurrency); each robot script's harness proves /api/search is intercepted.
Offline, -n 1 -p no:cacheprovider, own /tmp/task298-* basetemp (delete after),
targeted tests only. No commit/push. HANDOFF "In flight": one entry. Read CLAUDE.md first.
