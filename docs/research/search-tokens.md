# Search tokens — task 298 (2026-10-18)

The function still reserves **110 tokens** before every uncached Keepa call:
10 search tokens plus up to 100 product tokens. Previously it kept the whole
reservation, even when the upstream receipt reported a smaller cost.

A successful response with a nonnegative safe-integer `tokensConsumed` at most
110 now settles the original UTC day's `search_budget` row to the real cost.
The debit creates a pending reservation in the same D1 transaction. Settlement
subtracts the difference and consumes that reservation in another atomic batch;
replaying it cannot refund twice. Settlement preserves minute counts, dispatch
spacing and other requests' debits. KV receives the settled row as an advisory
mirror; stale/failed KV writes can conservatively deny capacity, but D1 remains
the authority. Unsettled reservations expire with the existing two-day ledger
cleanup. Existing ledger rows need no column migration.

Missing, negative, fractional, string, boolean, nonfinite and otherwise malformed
receipts never refund. Upstream failures/timeouts retain the reservation. Finite
receipts above 110 still halt the original UTC day; a late ordinary settlement
cannot lower that halt. Cache hits make no upstream call or reservation.

## Capacity from the offline harness

The text/page fixtures in `tests/search_results_function.test.mjs` report
**20 tokens** per successful call; the ASIN fixture reports **1 token**.
These are test inputs, not measurements of production costs.

For a daily budget B and constant positive receipt C, successful uncached calls
are limited to `floor((B - 110) / C) + 1` when B >= 110: the next call must still
fit its full reservation. Before settlement the limit was `floor(B / 110)`.

| Daily budget | Before (110 retained) | After (20-token search receipt) | After (1-token ASIN receipt) |
| --- | ---: | ---: | ---: |
| 3,000 (previous) | 27 | 145 | 2,891 |
| 5,000 (current) | 45 | 245 | 4,891 |

The current budget therefore supports about **5.4 times** as many uncached
20-token search pages. With three uncached pages per search, 245 calls cover
81 complete three-page searches, plus two pages. Actual capacity depends on
receipts, failures, cache hits, rate limits and conservative stale KV reads.
A zero-token receipt returns the full reservation; time/rate limits still apply.

## Robot isolation

All five Playwright scripts that open the live site install `**/api/search*`
interception before navigation, with service workers blocked:

- `site_check.js`, `visitor_watch.js`, `mobile_audit.cjs`, and `browser_probe.js`
  return `{items:[], reason:"robot"}`. Inventory search, empty-state buttons,
  affiliate destination checks and other existing checks remain in place.
- `live_first_screens.js` uses an empty fixture for its empty search and a single
  synthetic product plus a locally fulfilled image for its product-card check.
  It asserts the fixture title, card count and loaded photo. All later pages and
  ASIN requests are intercepted too.
- Visitor watch also intercepts search endpoints reached through its manually
  followed redirects, before pacing or fetching them.

The only explicit live search probe is `live_first_screens.js`'s single fixed
`q=cupping` request (page zero, no changing query or cache buster). The deployed
configuration caches it for six hours; this does not guarantee a shared hit
across separate edge cache locations. No live probes were run for this task.

## Offline validation

Targeted pytest wrappers exercise the existing SQLite-backed function harness,
all five robot route installations, the visitor-watch logic harness, mobile
audit logic, site-check logic and screenshot/syntax checks. Function regressions
cover settlement, duplicate/concurrent settlement, rollback, failed KV mirrors,
UTC rollover, untrusted receipts, oversized halt, concurrent reservations,
refunded capacity, caches, ASINs and pagination. Robot harnesses execute the
scripts' route setup against fake targets and verify local fulfillment without
network access or launching a browser.

Final targeted run: **6 pytest wrappers passed in 20.25s**; the earlier four-wrapper
run also passed.

All pytest invocations use `-n 1 -p no:cacheprovider` and an owned
`/tmp/task298-*` basetemp, deleted after each run. Full-suite testing and live
browser verification remain for review; no commit or push.
