# 262 — The homepage search box answers with best value (ships OFF)

## Why
Operator 2026-10-17: the main search box now reads "Search deals or any
Amazon product" (task 259) and should become the camelcamelcamel-beating
entry point. Task 256/256b built the best value finder (`/compare/` pages from
`data/compare_terms.yaml`, unit prices, "On sale now" badges; refresh/publish
still off). Connect the two.

## Task
1. When `compare.publish` is "on" AND `site.search_best_value: true`, publish
   a small static index (`/compare/index.json`: term slug, display name,
   synonyms, best row's unit price text, as-of time; no Amazon-restricted
   fields, same allowlist as 256) — size-capped (≤ 30 KB), no-cache headers.
2. Homepage search (templates/index.html, inside a new gated block like
   `/*search-best-value*/…/*/search-best-value*/` so OFF bytes are identical):
   when the typed query matches a term name or synonym (normalized, whole-word,
   same matcher style as the homepage search), show ONE card above the
   results: "Best value: <term> — <best unit price text>, checked <relative
   time>" linking to `/compare/<slug>/`. Then our matching deals, then the
   Search Amazon line (259) as today. Index is fetched lazily on the first
   keystroke (one request, cached in page memory only), never before typing.
3. Hide the card when the as-of time is older than `max_price_age_h`
   (browser-side, like 256b S5).
4. Analytics: `best_value_click` event, no query text (same rules as 259).
5. Phone budgets unchanged (do not raise a limit); dark mode looks right.

## Tests
Python: OFF bytes identical (pin recorded fixtures to the switch off,
"recorded before task 262"; never re-pin); index allowlist and size cap.
Browser (pytest wrapper, Chromium at /opt/pw-browsers/chromium, exit 77 =
skip): "tide pods" → card visible with link; "cupping" → no card, Amazon
button; stale index → no card; no network request before typing; one
index request max; no query in analytics.

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task262-*` basetemp
(delete after each run; disk is tight), targeted tests only, never the full
suite. No network, no commit, no push. HANDOFF "In flight": one entry.
Base: this tree has 256/256b and 259 merged.
