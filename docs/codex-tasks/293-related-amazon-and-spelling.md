# 293 — Keep scrolling into Amazon products; fix misspelled searches

## Why
Operator 2026-10-17 (launch night): "I'm searching for a mouse ... five, ten, and
then it stops. I want to keep going by putting products related to this item,
directly from Amazon ... they can keep scrolling down." And: "I put 'hair tire'
... images didn't show up ... most people can't spell. Can you write the code
where if they spell it wrong, you fix that spelling?" This worktree holds the
unpushed release (search_boost, search_circle, search_analyze, install button):
build on it.

## Task (config `search_more`, mapping {enabled, pages, min_query_chars}; missing/false = today's bytes exactly; ship enabled true, pages 3)
1. More from Amazon below our matches: when a search HAS matching deals, after our
   cards render a section "More from Amazon for "<q>"" using /api/search results
   (same item shape, same card markup as the existing #amazon-results cards, same
   adult filter, tag, rel="sponsored nofollow noopener", "Amazon price as of").
   Load it lazily when the visitor scrolls near the end of our results
   (IntersectionObserver; no fetch if they never scroll there). Infinite scroll:
   next pages load as they keep scrolling, up to `pages` pages per query, then a
   final "See all "<q>" on Amazon →" button (same circling style). Never repeat an
   ASIN already shown on the page.
2. Paging: add `page` (0..pages-1) to /api/search q= requests; Keepa search page
   param; page is part of the cache key; EVERY existing guard applies per call
   (reservation, minute window, lease, cooldown, Retry-After, daily cap, cache).
   Keep tests/search_results_function.test.mjs green and add page cases. When the
   function answers rate_limited/budget_exhausted, stop paging quietly (the
   button stays).
3. Spelling: when a query returns no deal matches AND Amazon returns 0 items, try
   ONE corrected query before giving up: correct each word against a vocabulary
   built in the browser from live deal titles plus a small built-in list of common
   shopping words (bundled, < 3 KB gz): edit distance 1 for words of 4+ letters,
   prefer the candidate that forms a bigram seen in deal titles (so "hair tire" →
   "hair tie"). Show "Showing results for <b>hair tie</b> · Search instead for
   "hair tire"" (the second is a link that searches the original). The corrected
   request is a normal guarded request (counts against the same caps). Never
   correct model numbers/brands with digits.
4. GA4 via the existing allowlist: search_more_page (page number), search_spelling
   (corrected: bool) — never the query text.

## Tests
- OFF/missing = recorded bytes (history pins; never re-pin; pin current-config
  fixtures with a tests/history_pins.py helper).
- Node harness: lazy load only on intersection, page sequence 0..pages-1, dedupe,
  stop on rate_limited, final button tagged; spelling table ("hair tire"→"hair
  tie", "mouce"→"mouse", "iphone 15"→ unchanged, "xbox360"→ unchanged), one
  corrected request at most.
- Function tests for page param + guards.
- Real Chromium script (I run it outside the sandbox) at 390 and 1440.

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task293-*` basetemp (delete after
each run), targeted tests only. No network, no commit, no push. HANDOFF "In flight":
one entry.
