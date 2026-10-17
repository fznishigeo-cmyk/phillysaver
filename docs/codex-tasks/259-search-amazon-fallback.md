# 259 — "Search Amazon for …" so no search is a dead end (ships ON)

## Why

Operator 2026-10-17: a friend wanted "cupping" — a product we will never have
as a deal. Our search said "No deals match". A tagged Amazon search link
(opened in a new tab) would have earned the commission: "that would have
been my first customer". Amazon Associates allows tagged links to Amazon
search results pages; purchases in the 24 h cookie window earn.

## Task

1. Homepage search (templates/index.html, `searchRecoveryHtml` and the result
   count line): when the query is non-empty and `amazon_tag` is set,
   - **no results:** the recovery section shows a primary button-style link
     **Search Amazon for "<query>"** above the existing options;
   - **with results:** one quiet line after the last result card or under
     the count: `Not what you're looking for? Search Amazon for "<query>"`.
   Link: `https://www.amazon.com/s?k=<encodeURIComponent(query)>&tag=<tag>`,
   `target="_blank" rel="sponsored noopener"`, same new-tab behavior as Get
   deal. A small muted note next to it: "Amazon links may earn us a
   commission."
2. Query hygiene before building the URL: trim, collapse spaces, max 80
   characters; if the query looks like a URL, an email address or contains
   6+ consecutive digits, do not offer the link (privacy). Never send anything
   until the visitor clicks.
3. Analytics: fire one event on click through the existing tracking helper
   (`search_amazon`: GA4 event; Meta `trackCustom("SearchAmazon")`; the same
   GPC/opt-out rules as `deal_click`). No query text in any event.
4. Config: `site.search_amazon: true` (validated boolean). The tag comes from
   the existing `amazon_tag`; an empty tag or `search_amazon: false` renders
   exactly today's bytes. Respect any existing Amazon kill switch / store
   policy that hides Amazon links (if Amazon is off, no link).
5. Keep the home CSS/JS within the phone budgets (tests/phone_budget_fixture,
   scripts/publication_budget.py); do not raise a limit.

## Tests

- Python: config validation; `false`/empty tag → published bytes identical
  to today (extend an existing OFF-bytes test; never re-pin a hash — pin
  recorded fixtures to `search_amazon: false`).
- Browser (extend the existing homepage search browser test or add
  tests/search_amazon_browser.cjs with a pytest wrapper; Chromium at
  /opt/pw-browsers/chromium, exit 77 = skip): "cupping" with no matches →
  link visible, href exact (encoded, tagged), target _blank, rel sponsored;
  a matching query → the quiet line; URL/email/long-digit queries → no link;
  clicking fires the tracking stub without the query text; no network
  request until click (route-abort everything external).
- tests/test_public_copy.py passes.

## Rules

Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task259-*` basetemp
(delete after), targeted tests only (the files you touch + test_phone_budget
+ test_public_copy + test_e2e_offline -k rerun), never the full suite. No
network, no commit, no push. HANDOFF "In flight": one short entry.
