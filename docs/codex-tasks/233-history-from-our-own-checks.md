# 233 — Price-history pages grow from our own verified price checks

## Why

The price-history (/p/) pilot is approved for the 200 best non-Amazon pages,
admitted best-first at 50/day (traffic plan B3, shipped 2026-10-16). Live runs
today report `product_pages: eligible 0, indexable 0, noindex 314`. A page needs
`seo.product_pages.min_checks` (6) observations spanning `min_span_days` (7)
within 90 days (`dealradar/publish/product_pages.py` prepare, ~line 165).
Observations come from `db.product_price_history` (`dealradar/db.py:786`):
`price_history` rows plus unduplicated sightings. `price_history` is written
only by `dedupe.py:538` and `:626`, i.e. when a deal post is seen again. Our
own successful price checks never become observations:
- Woot listings re-read every run (`woot_listed_at`, Woot adapter / Woot API),
- store API readings (`dealradar/store_prices.py` `recheck_store_prices`,
  Best Buy and eBay readings, ~line 395-480),
- Keepa Amazon readings (`dealradar/keepa_rechecks.py`); Amazon histories stay
  hidden by `amazon_history: hide`, but recording privately is harmless.
So a non-Amazon product almost never reaches 6 observations over 7 days.

## Read first

CLAUDE.md, AGENTS.md, docs/HANDOFF.md, docs/product-entities.md,
`publish/product_pages.py` (prepare, gates, `_admission_order`),
`db.py` (`record_price`, `product_price_history`, price_history schema:
UNIQUE(canonical_key, content_hash)), `store_prices.py`, the Woot adapter and
where `woot_listed_at`/`price_rechecked_at` are set, `keepa_rechecks.py`,
`non_amazon_freshness.evidence_at` (what counts as successful evidence),
retention/pruning of price_history (Codex 060), tests/test_product_price_pages.py,
tests/test_e2e_offline.py::test_rerun_is_idempotent.

## Task

1. Config `seo.product_pages.record_own_checks` (`enabled: true`,
   `max_per_offer_per_day: 24`, validated int 1..96). When enabled, every
   SUCCESSFUL price reading that already updates a live deal's current price
   evidence (Woot listing re-read, store API reading, Keepa reading) also
   records one `price_history` observation via `db.record_price` with:
   the deal's dedupe_key, its store, the price actually read (the same value
   the code uses as the deal's current price; never a computed or LLM number),
   `observed_at` = the reading's own time, and
   `content_hash = "check:<source>:<UTC YYYY-MM-DDTHH>"` so one observation per
   offer per source per hour, and a rerun of the same run adds no rows (UNIQUE).
   Failed, skipped, suspect, gone, unconfirmed or mismatched readings record
   nothing. Respect `max_per_offer_per_day` (count existing check: rows).
2. Do NOT change the gates (min_checks, min_span_days, recency), caps, admission
   order, Amazon hiding, or any published page by itself; pages change only
   because more real observations exist. Disabled = byte-identical state and
   output.
3. Add to the product_pages run summary a small private breakdown of why
   pages are not eligible: `not_eligible: {few_checks, short_span, not_recent}`
   (counts), and `own_check_rows_today`. No public field.
4. Retention: make sure the existing price_history retention bounds these rows
   (state size). Report the expected rows/day from a fixture (e.g. 300 Woot
   + store readings) in HANDOFF.

## Tests (offline)

- A Woot deal re-read at 6 different hours over 7 days becomes eligible; the
  same with readings failing or suspect stays ineligible.
- Same hour twice / same run key twice → one row; rerun idempotency test extended.
- `max_per_offer_per_day` respected.
- Disabled → identical DB rows and published bytes.
- The not_eligible breakdown counts each page once.
- Amazon pages still hidden with `amazon_history: hide` even with recorded rows.

## Rules

Evidence or it doesn't exist: only record prices the code already read and
trusts for the live deal. Math stays in savings.py. Do not change
tests/test_workflows.py. Run tests with -n 1 only, basetemp /tmp/task233-*,
delete it after each run; do NOT run the whole suite. Do not commit. Update
docs/HANDOFF.md "In flight" with one short entry.
