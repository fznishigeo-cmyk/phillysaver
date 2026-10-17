# 258 — Pinterest: evergreen product pins fill slow hours (ships OFF)

## Why

Pinterest posting reached 10/10 on 2026-10-16, but only after 18:30 UTC: from
00:00 to 16:35 UTC every hourly run said "posted 0 (today 4/10)" because few
live deals pass the social rules (plenty of stock, earning link, no used/open
box/clearance, require_stock_evidence…). Pins compound on Pinterest, so empty
hours are lost reach. Our ~2,700 Amazon product price-history pages (/p/…) are
evergreen: they stay valid whatever today's price is.

## Task

1. In `dealradar/publish/pinterest.py`, when a run's deal candidates (the
   existing `_pin_candidates` → `_spread` result) leave room under the run
   and daily limits, fill **at most** `pinterest.evergreen.per_run` (1) of the
   remaining slots with evergreen product pins, only when the day's posted
   count is behind an even pace (`today < daily_max × hours_elapsed_utc / 24`,
   rounded down) — deals always come first; evergreen never takes a slot a
   deal could use.
2. Evergreen candidates: published /p/ product pages (use the existing
   product_pages state/paths — never invent a URL; the page file must exist in
   the deployed set the same way deal pins require a deployed deal), Amazon or
   Walmart products with an https photo, a stored price history of ≥ 10 checks,
   not pinned in the last `evergreen.repeat_days` (30), and never a product
   whose page is pruned. Prefer the appeal categories (publish.front_page.appeal
   category_factors > 1) and most-recent activity; deterministic order.
3. Pin text: **no prices, no percentages, no "deal ends"** (prices go stale on
   Pinterest). Title: "<product title> — price history"; description:
   "See how the price of <short title> moved over the last 90 days on
   wowpriced." + the existing disclosure + "#ad". Link: the /p/ page via
   `tagged_url(..., "pinterest", "social")` with campaign "evergreen". Board:
   the category board (same `board_name`/streak rules). Alt text: the title.
4. Ledger + idempotency: record evergreen pins in the same posted state with
   a kind marker; a rerun of the same run key posts nothing new; daily totals
   count both kinds; the log line stays "pinterest: posted N (today X/10)"
   plus ", evergreen K" when K > 0 (no numbers beyond these counts).
5. Config: `pinterest.evergreen: {enabled: false, per_run: 1, repeat_days: 30,
   min_checks: 10}` validated like the other pinterest keys.

## Tests (offline, tests/test_pinterest_evergreen.py)

Deal candidates win; pace gate; repeat_days; never a pruned/missing page; no
price/percent characters in title/description/alt (reuse `_without_prices`
checks); board streak; disabled = byte-identical requests to today
(transport recorder); rerun idempotency; daily cap shared with deal pins.

## Rules

Ships `enabled: false`. Offline only, `-n 1 -p no:cacheprovider`, own
`/tmp/task258-*` basetemp (delete after), run tests/test_pinterest*.py and
test_e2e_offline.py -k rerun, never the full suite, never re-pin an existing
hash. No network, no commit, no push. HANDOFF "In flight": one short entry.
