# 256b — Best value finder: review fixes + "on sale now" (still ships OFF)

Task 256 is committed here as HEAD (`cx/256`). An independent review said:
OFF is safe once the test clock is pinned; ON is not ready. Fix everything
below. Repro scripts referenced live in
/tmp/claude-0/-home-user-phillysaver/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/rv256/
(read them if present; they are not part of the repo).

## Blocker
- **B1 test clock bomb:** tests build `KeepaBudget(available, 60, NOW)` with a
  fixed NOW (2026-10-17 12:00Z) while `available()` refills from the real
  clock, so `test_budget_gates[change3-2879-0]` and `[change6-660-0]` fail after
  12:00:01Z. Pin `util.now_utc` to NOW in the `offline` autouse fixture.

## Must fix before ON
- **S1 wrong unit prices / false banner.** (a) Per-term `unit_words` (no shared
  generic list containing pack/bags/rolls/sheets for count terms). (b) Reject a
  quantity when `numberOfItems` or `packageQuantity` > 1 unless the quote
  contains the pack multiplier. (c) Widen the multipack/conflict detection:
  "N count/bottles/rolls/boxes/pads", number-word packs ("Twin Pack", "Five
  Pack"), "x N" / "N x", "/pad", "12 fl oz, 2 Count", "3 bottles x 33.8 fl
  oz", "33.8 fl oz x 3", "100 Sheets/Pad", "75 ft, 3 Rolls", "10 Boxes".
  (d) `each` terms: a single item with no stated count is 1 each when
  `numberOfItems`/`packageQuantity` ≤ 1 and the title has no pack words;
  otherwise no unit price. (e) The banner only when every shown row has a unit
  price; else "Best value among items that state their size: …". (f) Pack
  multiplication moves into `savings.py` (math is code there). (g) Add all
  these titles as tests (positive and negative).
- **S2 liveness:** any non-429 finder/product failure or a 200 without
  `asinList` records a finder time/backoff for that term and moves on (no
  retry storm, no stalled queue). Log `totalResults` (count only) so the probe
  can settle finder billing.
- **S3 flapping pages:** a term is due when its oldest kept observation nears
  `max_price_age_h` (Keepa `lastUpdate` based), default `refresh_hours: 12`,
  and config validation requires `refresh_hours < max_price_age_h`.
- **S5 static "checked N min ago":** render `<time datetime>` with the
  `detail-checked` class (seo_audit skips it), compute the relative text and
  hide rows older than `max_price_age_h` in the browser; sitemap lastmod and
  IndexNow must not move when facts are unchanged.

## Should fix
- **S4 photos:** use `verify._keepa_image_name(product)` (the `images` field),
  not only `imagesCSV`; fixtures cover both.
- **S6 phantom debits/overrun:** only start a request when
  `monotonic() + request_timeout_s + min_host_interval_s < stop`; refund the
  reservation on `DeadlineExceeded` before send.

## Nits (do them)
Bucket gate uses `available − cost ≥ share × capacity`; promo-title check with
word boundaries (don't hide "Cartridges"/"Carton"/"Clips"; Subscribe & Save
alone must not hide a price); junk `unitValue` (≤ 0) = absent; missing
`lastUpdate` = no price shown; a terms-file load failure must not prune stored
facts; `round(max_price*100)`; price text "$1.50 per fl oz", "20¢ per load",
never "$19.995" or "per count" (use the term's display unit, e.g. "per pod",
"per roll"); ItemList JSON-LD uses same-site URLs (/p/ pages or the compare
page anchors), never affiliate URLs; no `/compare/` index or footer link when
no term has rows; call `keepa_products` without polluting the category-root
cache, planner latency samples or accuracy samples (flag or a thin wrapper);
wrap the pipeline compare step and `compare_pages.publish` so an error is
logged (type only) and never stops the run; node test skips when node is
missing.

## New: "On sale now"
When a compare row's ASIN is a live, price-confirmed published deal on
wowpriced, add a badge "On sale now — N% off" linking to our deal page (N is
the deal's published `pct_at_checkout`; no new math). Sort is unchanged.

## Rules
Ships `refresh: false`, `publish: "off"`. Offline only, `-n 1
-p no:cacheprovider`, own `/tmp/task256b-*` basetemp (delete after), targeted
tests (test_compare.py + the brief-256 regression list), never the full suite,
never re-pin an existing hash. No network, no commit, no push. Update the
HANDOFF 256 entry.
