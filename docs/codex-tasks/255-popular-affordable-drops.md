# 255 — Popular, affordable price drops lead the homepage (Keepa data we already pay for)

## Why

Operator 2026-10-16, relaying a marketing expert: on the homepage, favor
cheaper items that many people buy on Amazon ("1K+ bought in past month";
thresholds like 500 / 1,000 / 5,000) when their price has dropped a lot. Those
are the deals people actually buy. Keep the existing filters and the
women-shopper tilt (`publish.front_page.appeal`).

Today nothing in ranking measures popularity. The dollar term favors pricier
items: at 50% off, a $200 item scores about 11.5 points more than a $20 item
(`12 × log10(1 + savings)`: 24.0 vs 12.5). Sales rank is used only at intake
(Amazon `/deal` lane, rank 1–30,000, `keepa.py:83`).

**Amazon is never scraped.** Keepa's `/product` object carries `monthlySold`,
which is Amazon's own "bought in past month" figure, stored as the bracket's
lower bound: 50, 100 … 1000 for "1K+", 2000 … 10000 for "10K+". It is per
variation, `null` on many ASINs, and refreshed at `lastSoldUpdate` (Keepa
minutes). The product object also has the sales rank `stats.current[3]`. Our
price rechecks already ask for these records (`stats=1, history=0`,
`verify.py:515-536`), so reading them costs **0 extra tokens and 0 extra
requests**.

Associates approval amz378_998 (docs/HANDOFF.md "Operator decisions") covers
Keepa as the data source and price history. It does not cover the "bought"
figure. So this task uses the figure **for ordering only**. Never show it.

## Read first

CLAUDE.md, AGENTS.md, docs/HANDOFF.md ("Operator decisions": Amazon price
history amz378_998; "Broad-appeal top picks", task 114),
`dealradar/rank.py` (`pick_score` 187-265, the appeal block 260-264,
`top_picks` 268-331), `dealradar/publish/site.py` (picks 661-723; public
`meta.front_page` 901, which already omits `appeal`; site result 1139),
`dealradar/verify.py` (`_harvest_image` 345-355, `keepa_time` 445,
`keepa_products` 515-536, `judge_live_price` 710-768 with the harvest at 727,
cross-check harvest at 949), `dealradar/pipeline.py` (`apply` 1538+, which
persists every `_DEAL_FIELDS` key, and `summary["appeal"]` 2699),
`dealradar/models.py` 249-251, `dealradar/fetch/keepa.py` (constants 83 and
110-118, `_selection` 426-441, Amazon passes 631-685, `_run_focus` 833-858),
`config.yaml` `publish.front_page` (217-250) and `keepa`, the browser rotation
`DROP_WINDOW = 24` in `dealradar/publish/templates/index.html`, and
tests/test_appeal.py:196 (the byte-pin pattern to copy).

## Token math (state it in the HANDOFF entry)

- **Part A (capture): +0 tokens, +0 requests.** It reads only fields that are
  already in the `/product` responses we pay for: 1 token per ASIN with
  `stats=1`, plus today's existing extras (+1 live `update=0` from the surplus
  path, +2 `buybox` when on, up to 13 for used offer scans), none of which
  change. Do **not** add `rating`, `buybox`, `offers`, `history=1` or
  `update=0` for this task. Every Amazon top-pick candidate already has a read
  less than 3 h old, because `pick_max_unconfirmed_h: 3` is a `pick_score`
  gate. The top 200 are re-read every 0.75 h.
- **Part B (boost + shadow): 0 tokens, 0 LLM calls.** It is pure Python over
  stored facts.
- **Part C (optional feed page):** one `/deal` page costs 5 tokens. It counts
  against `keepa.amazon_pages_per_pull`, so the ceiling of Amazon `/deal`
  pages per pull stays the same (6). Actual spend can rise by at most 5 tokens
  in a pull where the general Amazon list would have ended early on a short
  page. With at most 144 pulls a day, that is ≤720 tokens/day as an absolute
  upper bound, and 0 in the normal case.
- Not in this task: a Product Finder lane with `monthlySold_gte`. It would
  cost 10 + ⌈n/100⌉ + n tokens per refresh, about 2,664 tokens/day for 100
  ASINs hourly. Build it only if the shadow shows too few qualifying deals.

## Task

### A. Store popularity facts from reads we already make (always on, private)

1. `models.Deal`: one new field `keepa_popularity: dict | None = None`, next to
   `keepa_price_seen_at` (models.py:249). Shape:
   `{"asin": "B0…", "monthly_sold": 1000 | None, "sold_at": "<ISO UTC from lastSoldUpdate>" | "", "sales_rank": 1234 | None}`.
   Deals are stored as a JSON payload, so no migration is needed.
2. `verify._harvest_popularity(deal, product)` goes next to `_harvest_image`
   and is called right after it at verify.py:727 (new and used rechecks) and
   at verify.py:949.
   - Write only when `product["asin"]` equals the deal's ASIN, because the
     figure is per variation.
   - `monthly_sold`: an int ≥ 0, else `None`.
   - `sold_at`: `keepa_time(lastSoldUpdate)` as ISO, else `""`.
   - `sales_rank`: `stats.current[3]` when it is an int > 0, else `None`.
   - A fresh record without `monthlySold` **replaces** the old value with
     `None`, so a stale "1K+" never lingers.
   - The value depends only on the product record, never on the clock, so
     reruns write identical payloads.
   - Do not harvest in photo_recovery's title-match path (that ASIN may
     differ).
3. The field stays private:
   - not in `_serialize_deal`, deal/product/checker pages, feeds or
     `deals.json`;
   - not in any LLM prompt (critic, AI take, newsletter);
   - not in change-detection hashes, `price_history`, `price_since` or alert
     dedupe.
   Do not add it to the Keepa `/deal` mapper. A candidate is read by
   `/product` before it can become a pick anyway.

### B. Config-driven homepage boost (ships OFF) and a 1-day shadow

4. Add `config.yaml` → `publish.front_page.popular_affordable`, placed after
   `appeal`. Leave `appeal` and `top_picks` untouched: tests pin them.

   ```yaml
       popular_affordable:        # task 255: Amazon "bought in past month" (via Keepa) + low price.
         enabled: false           # ranking only, never displayed; flip after the 1-day shadow
         shadow: true             # private run-summary comparison while enabled is false
         min_monthly_sold: 1000   # Amazon's bracket floor ("1K+ bought in past month")
         max_price: 50            # final_price at checkout, dollars
         pct_over_floor: 10       # needs pct_at_checkout >= publish.min_pct + this (50 today)
         max_age_h: 72            # Keepa's lastSoldUpdate must be this recent
         weight: 1.25             # pick_score multiplier
         max_share: 0.25          # boosted cards per 24-card drop window (floor: 6 of 24)
   ```

   No `config.py` change is needed: `front_page` is a raw dict
   (config.py:870). Missing keys mean the feature is off.
5. `rank.popular_affordable_factor(deal, cfg, now, options=None) -> float` is
   a pure function. It returns `weight` only when **all** of these hold, and
   otherwise returns 1.0:
   - `is_amazon_deal`;
   - not used, open-box or refurbished;
   - `final_price` is set and ≤ `max_price`;
   - `pct_at_checkout ≥ cfg.min_pct + pct_over_floor`. The threshold is
     derived from `min_pct`, with no absolute percent knob;
   - the popularity record's ASIN equals the deal's ASIN;
   - `monthly_sold ≥ min_monthly_sold`;
   - `sold_at` parses and is within `max_age_h` of `now`. A missing `sold_at`
     counts as stale.

   `pick_score` multiplies by the factor after the appeal block (rank.py:264),
   and only when `enabled`. The factor can only reorder deals that already
   passed every existing gate: unconfirmed prices, the 3 h Amazon
   confirmation, https photo, niche, used, and the evidence-backed
   `pct_at_checkout`. It never brings back a `None`. No new math goes into
   savings.py, and `verify_evidence` is unchanged.
6. Share cap in `top_picks`, enabled only:
   - Windows are the browser's 24-card rotation unit. Add `PICK_WINDOW = 24`
     in rank.py with a comment pointing at `DROP_WINDOW` in the template.
   - A boosted deal arriving when the current window
     (`len(picked) // PICK_WINDOW`) already holds `floor(max_share × 24)`
     boosted picks is re-queued at its unboosted score (score ÷ weight). It
     competes again and is never dropped.
   - The existing caps hold unchanged: 40 per store, 3 per brand, 24 per
     category, title twins, and the category run in the first 8.
   - With the flag off, `top_picks` returns the same list for every input.
7. Public bytes:
   - Omit `popular_affordable` from `meta.front_page` the way `appeal` is
     omitted (site.py:901).
   - Update the single assertion at tests/test_front_page.py:294 to exclude
     the key too. That is the only existing test edit expected.
   - With `enabled: false`, every published byte equals today's, with or
     without the section and with or without `keepa_popularity` on deals.
8. Shadow:
   - In `render_site`, after the live picks: when the section exists and
     `shadow` or `enabled` is set, compute the other ordering in memory.
     - Live off: with-boost = live candidate scores × forced factor, then
       `top_picks` with the cap.
     - Live on: without-boost = score ÷ factor.
     - Compare the two orderings. Nothing from this step is written to the
       site.
   - Return `site_res["popular_affordable"]` with:
     - `amazon_candidates`, `with_monthly_sold`, `fresh`;
     - `qualifying_at`: `{"500": n, "1000": n, "5000": n}`, with every other
       gate applied;
     - `boosted_in_picks`, `entered_picks`, `left_picks`;
     - `first_window_changed`;
     - `median_price_top24`: `{"without": x, "with": y}`;
     - `amazon_in_picks`: `{"without": a, "with": b}`;
     - `tilted_top24`: cards in appeal categories with factor > 1, for
       without and with;
     - `examples`: up to 3 item titles that entered.
   - In `pipeline.py`, set `summary["popular_affordable"]` next to
     `summary["appeal"]` (2699) and log one line.
   - Keep a 26 h ring in db meta `popular_affordable_shadow`, keyed by
     `run_key`. The same key replaces its entry; older entries drop.

### C. Optional: one popular, affordable `/deal` page (ships OFF)

9. Add `keepa.popular_affordable_page: false`. When true, and
   `front_page.popular_affordable.max_price` exists, and
   `amazon_pages_per_pull ≥ 2`:
   - Run one page of
     `{**amazon_sel, "currentRange": [0, round(max_price*100)], "sortType": SORT_SALES_RANK}`
     right after the focus pages. Add the new constant `SORT_SALES_RANK = 3`
     next to `SORT_PCT_DROP`.
   - Gate it like `_run_focus`: `_may_fetch(..., extra=True)` and never below
     the 300-token reserve.
   - It shares `amazon_seen`, `map_amazon` (the same evidence lines and hash)
     and `amazon_lane.admit` (the same new-post cap, so LLM work is unchanged).
   - When it ran, the later Amazon pass gets `amazon_pages_per_pull - 2`
     pages. Overflow resumes only the general passes, as today.
   - Count `popular_page_rows` and `popular_page_admitted` in the pull
     summary.
   - The meaning of `currentRange` and the sort direction come from Keepa's
     `DealRequest.java`. Claude confirms them with **one** live page after
     merge. Codex makes no live calls.

## Tests (offline, new file tests/test_popular_affordable.py unless noted)

- **Capture:**
  - A product fixture with `monthlySold` 1000, a `lastSoldUpdate` and
    `stats.current[3]` gives the exact dict.
  - Missing or negative values give `None` or `""`, and a fresh null clears an
    old value.
  - An ASIN mismatch is ignored.
  - Harvest runs on the new and used paths and on an answered no-opinion
    recheck (persisted by `apply`).
  - A fake fetcher proves the `/product` params are byte-identical to before
    (`stats=1, history=0`, no new keys) and that per-ASIN token accounting is
    unchanged.
- **Boost math:** two Amazon deals, otherwise equal, at 50% off.
  - With $30 + `monthly_sold` 1000, fresh, and the flag on: it outranks the
    $200 one. With the flag off: the $200 one leads, as today.
  - The factor is 1.0 for each of: 900 sold; `sold_at` older than 72 h;
    pct 49.9; price 50.01; a non-Amazon store; used; an ASIN mismatch.
  - Setting `publish.min_pct` to 45 moves the threshold to 55.
  - A qualifying deal that is `price_unconfirmed`, or whose Amazon
    confirmation is older than 3 h, still scores `None`.
  - The appeal factors still multiply.
- **Share cap:** 30 qualifying top scorers give ≤6 boosted picks in every
  24-window. Over-cap deals still appear at their unboosted rank, and the
  store, brand and category caps hold.
- **Byte pins, flag off:** copy tests/test_appeal.py:196 over the modes
  membership/open/support/shipped. Section omitted, `enabled: false`, and
  `enabled: false` with `shadow: true` produce identical site trees. Deals
  with and without `keepa_popularity` produce identical trees. No built file
  contains `monthly_sold`, `keepa_popularity` or `bought`.
- **Shadow:** the counts on a small fixture are exact. The meta ring keeps one
  entry per run key, a rerun replaces it, and 26 h-old entries drop.
- **Idempotency:** `tests/test_e2e_offline.py::test_rerun_is_idempotent` (both
  params) passes with the shipped config. Add a variant that flips
  `enabled: true` in the temp config, the way `audit_mode` is flipped, and
  gives one canned Amazon deal a qualifying `keepa_popularity`. Assert no new
  rows, no resent alerts and `second["popular_affordable"] == first[...]`. If
  the offline e2e has no confirmed Amazon pick, prove it instead with a
  same-run-key double render (identical bytes and meta ring), and say so.
- **Part C (fake transport):**
  - Flag off: the `/deal` request list is identical to before.
  - Flag on: the same number of Amazon `/deal` requests per pull; exactly one
    carries `currentRange [0, 5000]` and `sortType 3`, and the others are
    unchanged.
  - Rows share the lane cap.
  - The page is skipped below the reserve or when `amazon_pages_per_pull` is 1.
- Keep green: tests/test_appeal.py, tests/test_front_page.py,
  tests/test_keepa.py, tests/test_keepa_overflow.py,
  tests/test_price_accuracy.py, tests/test_photos_every_deal.py,
  tests/test_unique_asin_rechecks.py, tests/test_value_per_token_rechecks.py.

## Ship plan (for the HANDOFF entry)

1. Merge with `enabled: false`, `shadow: true` and
   `keepa.popular_affordable_page: false`. Capture starts on the first run at
   0 tokens.
2. After 24 h, Claude reads the ring and reports in plain words:
   - coverage, meaning the share of Amazon candidates with a fresh
     `monthly_sold`;
   - qualifying counts at 500 / 1K / 5K;
   - how many first-screen cards would change;
   - median top-24 price with and without the boost;
   - Amazon share and tilt share.
3. The operator flips `enabled: true` in one config line, and may move
   `min_monthly_sold`. Setting `enabled: false` restores today's scores and
   bytes. Part C is a separate call after the one-page live check.
4. Showing a "1K+ bought" badge needs its own Associates answer (same
   reference) and its own task.

## Rules

- No Amazon or retailer requests and no scraping. Keepa (`api.keepa.com`) is
  the only data source, and no new hosts.
- No live Keepa, LLM or retailer calls from Codex.
- Do not touch: savings.py math, `verify_evidence`, `min_pct` or other
  publish floors, `appeal`, `top_picks` knobs, `max_same_store`, LLM caps.
- Do not change tests/test_workflows.py.
- Run only targeted tests, with `-n 1 -p no:cacheprovider` and your own
  basetemp `/tmp/task255-*`. Delete it after each run. Do not run the full
  suite; report the exact pass/fail line of each run.
- Do not commit.
- Update docs/HANDOFF.md "In flight" with one short entry: what shipped OFF,
  the token math above, the shadow meta key, and the flip line.
