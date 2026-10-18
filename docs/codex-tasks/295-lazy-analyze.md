# 295 — Lazy-load Analyze so the homepage gets its phone headroom back

## Why
Task 291 (search_analyze) inlines ~1.7 KB gzip of script+CSS into index.html.
tests/test_home_comment_strip.py's headroom test had to pin `search_analyze`
off (see the comment there) to stay under publish.phone_budgets
home_html_gzip_max_bytes (77360). Release M also added search_more's inline
loader and button CSS. Goal: Analyze costs the first paint nothing until the
visitor uses the search box, like search_more's lazy `/search-more.js`
(dealradar/publish/search_more.py + templates/search-more-loader.js — copy
that pattern).

## Task (config `search_analyze.lazy`, bool, missing/false = today's bytes exactly; ship true)
1. When lazy: index.html keeps only a tiny loader (placeholder text, the
   Analyze/Go button markup, the hint element, and a stub that dynamic-imports
   `/search-analyze.js?v=<version>` on first focus, input, paste, or Analyze
   click). Everything else (ASIN extraction, progress list, lookups, related
   deals, CSS) moves into that ON-only asset. A paste or click that happens
   before the import resolves must be replayed once it loads (no lost paste,
   no double lookup). Import failure: the plain search keeps working and the
   tagged Search Amazon button still shows.
2. Every existing hook (search_results / search_boost / search_circle /
   search_more swaps into the analyze code, e.g. search_more's
   `analyzeLookup` / `analyzeCancel` / `keepa`-free `price_error` key / removed
   blur line) must keep working: run the search_more and analyze browser tests.
3. Remove the headroom test's `search_analyze` pin (and its comment) once the
   shipped config passes the budget with lazy on; keep the budget at 77360.

## Tests
- OFF/missing lazy = recorded bytes for every existing pin (use tests/history_pins.py:
  add `pre_task_295` = lazy False, chain it from pre_task_293 so every older
  pin turns it off; never re-pin a recorded hash).
- Node harness: no analyze network before first interaction; paste-before-load
  replays exactly once; failure fallback.
- Real Chromium (tests/search_analyze_browser.cjs and search_more_browser.cjs,
  390 and 1440, light/dark): same results as non-lazy. Exit 77 skip only if
  Chromium cannot launch (I will run them natively if you cannot).
- Phone budgets pass with the shipped config; the site never contains "keepa"
  (tests/test_search_more.py::test_shipped_site_never_names_the_price_supplier).

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task295-*` basetemp (delete
after each run), targeted tests only. No network, no commit, no push. HANDOFF
"In flight": one entry. Read CLAUDE.md first.
