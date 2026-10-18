# 291 — The header search hooks people: "Analyze", paste any Amazon link, real progress

## Why
Operator 2026-10-17, launch night: about half of ad visitors will search, and "when
they search I need them hooked in, completely captured." References: Fakespot's
search box with an "Analyze" button, seekshop's "Worth it? Find out — paste a product
URL — Analyze" with a step list while it works, reviews.ai's "Try it free — paste any
review page link", nullfake's "free, no sign-up". Also: "they should be able to copy
and paste an Amazon URL. So you need to fix that if they can't." Today a pasted URL
is rejected by searchAmazonQuery() (it refuses "//", "www.", domains) and the deal
search finds nothing.

This worktree already contains the unpushed search_boost release (search on, big
"Compare all prices on Amazon" button, adult filter, phone retry): build on it.

## Task (config `search_analyze`, mapping, enabled bool; missing/false = today's bytes exactly; ship true)
1. Header search: placeholder "Search any product or paste an Amazon link"; a dark
   pill "Analyze" button inside the right end of the box (submits; Enter does the
   same; on phones a compact "Go"/icon if space is short; never wider than the box).
   A one-line hint under the box on focus/first visit: "Free price check · no
   sign-up" (no AI claim: the search is a live price lookup, not AI).
2. Paste an Amazon product link (amazon.com URLs with /dp/ASIN, /gp/product/ASIN,
   /gp/aw/d/ASIN, /exec/obidos/ASIN, or ?asin=; also smile./m./www.): extract the
   ASIN in the browser; only the ASIN ever leaves the browser (never the URL, never
   logged, never sent to analytics). amzn.to / a.co short links cannot be resolved
   client-side: say "Paste the full Amazon link (open it, then copy from the address
   bar)". Other stores' links: "We check Amazon links for now" + normal search.
   - If the ASIN is one of our live deals: show that deal first (our card, our link).
   - Else: look the product up live: add `asin=` to functions/api/search.js using
     Keepa /product (domain=1, asin, stats=1, history=0) under EXACTLY the same
     guards as q= (D1 reservation, minute window, lease, cooldown, Retry-After,
     daily token cap, cache by ASIN, fixed-vocabulary failures, gzip). Same item
     shape as q= results (currentItems). Never weaken any existing guard; keep
     tests/search_results_function.test.mjs green and add asin cases.
   - Result: one large "Live Amazon price" card (photo, title, price, "Amazon price
     as of hh:mm"), "View on Amazon" (dp/ASIN?tag=<amazon_tag>, rel sponsored),
     plus our closest live deals for the same words if any. Amazon rules: never
     show price history, ratings, review counts or sales counts.
3. Real progress while it works (seekshop-style list, but honest — each step is
   marked done only when it actually finishes): "Reading the link" → "Checking
   today's Amazon price" → "Comparing with N verified deals" (N = real live count)
   → result. Minimum display ~900 ms so it registers; reduced motion: no spinner
   animation, steps still update. Errors show the fixed-vocabulary reason in plain
   words and the "Search Amazon" fallback button (still tagged).
4. Adult filter (search_boost.exclude_words) applies to pasted products too.
5. GA4 events through the existing allowlist: search_analyze, paste_amazon_link,
   analyze_result (count/booleans only; never the URL, ASIN or query text).

## Tests
- OFF/missing = recorded bytes for every page (history pins; never re-pin; pin
  "current"-config fixtures with a tests/history_pins.py helper).
- Node harness: ASIN extraction table (valid/invalid/short links/other stores),
  only-ASIN-leaves, deal-first path, live path, progress steps honest (no step done
  before its promise resolves), error path keeps the tagged button, adult filtered.
- Function tests: asin= success, cache hit, every guard (lease, minute, 429
  Retry-After, budget, malformed), no secret in cache keys.
- Real Chromium at 390 and 1440: paste a link → result card; no console errors; no
  horizontal scroll; screenshots light/dark in docs/research/analyze-search/.
- Phone budgets pass; idempotent rerun.

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task291-*` basetemp (delete
after each run), targeted tests only. No network, no commit, no push. HANDOFF "In
flight": one entry.
