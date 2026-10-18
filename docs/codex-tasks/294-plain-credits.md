# 294 — Source credit stays, but never as a link to their site

## Why
Operator 2026-10-17 (launch night): "Stop giving hyperlinks to other people's
websites, then they're going to just go over there ... you can give its source but
I don't want them to be able to click on that." (e.g. "via The Krazy Coupon Lady",
"via Money Saving Mom", "via Slickdeals" are links today.) "Via Amazon price drop is
fine." Project rule (CLAUDE.md "Store facts only"): credit to the source stays on
every card — as plain text. Also on "Just missed" cards: "there should be like one
just missed for every 9 or 10 active deals (or more)". This worktree holds the
unpushed release: build on it.

## Task (config `site.credit_links`, bool; missing/true = today's bytes exactly; ship false)
1. With credit_links false, every visitor-facing credit renders as plain text
   "via <name>" (no <a>, same position and styling, muted): homepage JS cards
   (index.html credits map), static cards (seo.card_html), product pages
   (product_pages.py credits), deal pages, best-deals/compare/store pages, the
   ticker/spotlight if they carry credits, Deal Check if it shows credits. Search
   the publish code for every credit/source link. Leave RSS, email, Telegram and
   social posts unchanged.
2. Do NOT change where "Get deal" goes (store/affiliate links stay as they are).
3. "Just missed" (dead) cards: at most 1 in every 10 cards anywhere in a list (and
   never two in a row); extra ones move further down (or out of the first 60), never
   reorder live deals among themselves. If a cap like this already exists, report
   its value and enforce 1 in 10.
4. Keep OFF bytes unchanged; no new requests.

## Tests
- OFF/missing = recorded bytes (history pins; never re-pin).
- ON: no <a> inside any credit across every page type in a full publish; the text
  "via <name>" still present on every card; Get deal hrefs unchanged; dead-card
  spacing (1 in 10, never adjacent) on the homepage JS order and the static order.
- Phone budgets pass; idempotent rerun.

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task294-*` basetemp (delete after
each run), targeted tests only. No network, no commit, no push. HANDOFF "In flight":
one entry.
