# 256 — Best value finder: compare similar items by real cost per unit (ships OFF)

## Why

Operator 2026-10-17: build a free tool that drives traffic and beats
camelcamelcamel. "Anyone can search any product … consumers are emotional and
want to buy the item now … so they could compare similar items and the
different cost. I search Tide detergent and it gives me 10 similar and the
cheapest price … a USB cable: the best cable at the cheapest price … they
don't care 'wait three weeks', I want my product now."

camelcamelcamel answers "when to buy one exact product". We answer "which one
to buy today": for an everyday product type, the options a shopper can buy now
(Prime, in stock, well rated), ranked by **real cost per unit** (per load, per
count, per fl oz) with a price checked minutes ago. Pack sizes are where
shoppers overpay without noticing (the 81-count often beats the 42-count), and
Amazon's own pages do not compare across brands and sizes.

v1 is static pages for a curated list of product types, refreshed daily from
Keepa inside a token budget. Live "search anything" comes later (v2: a request
queue; v3: Amazon's own Creators API once the Associates account unlocks it).

## Policy (hard rules)

- **Amazon data only from Keepa**, the approved source (Associates call
  amz378_998, docs/HANDOFF.md "Operator decisions": prices, 90-day history,
  lowest recent price, "checked X min ago"). Never fetch amazon.com or any
  retailer page for this feature.
- **Never display** star ratings, review counts, "bought in past month"
  (`monthlySold`), sales rank or Keepa's name. Ratings/reviews may only be
  shown when obtained through PA API/Creators API (Associates policy); the
  bought figure is not approved yet (task 255 uses it for ordering only). All
  of them may be used as **filters** in the finder query and for ordering ties.
- Prices carry the existing "as of" text and `amazon_as_of.DISCLAIMER`, are
  hidden when older than `compare.max_price_age_h` (24), and link through the
  normal tagged Amazon link path.
- Math is code: the unit-price division lives in `dealradar/savings.py`
  (CLAUDE.md "Math is code"). A unit price is published only when its quantity
  is backed by a verbatim quote from the product title (CLAUDE.md "Evidence or
  it doesn't exist").
- Public copy: tests/test_public_copy.py rules apply (no Keepa/Claude/Codex/LLM;
  "AI" is allowed but do not claim AI picks anything in v1 — the ranking is
  arithmetic).

## Token math (measured 2026-10-16, see HANDOFF entry you write)

- Plan: 60 tokens/min (86,400/day). Recent use ≈ 68–74k/day; ≈ 12–19k/day is
  lost because the bucket sits at its 3,600 cap. Rechecks are not starved
  (no 429s, `rate_limited` false).
- Product Finder `/query`: 10 tokens + 1 per 100 ASINs (our `_finder_cost`,
  fetch/keepa.py:140-144; min page size 50). Returns ASINs only.
- `/product` with stats, history=0: 1 token per ASIN (no `offers`, no
  `rating`, no `buybox`, no `update` for this feature).
- Per term: finder 11 (weekly) + 20 product reads (daily) ≈ 21/day amortized.
  120 terms ≈ 2.5k tokens/day. Budget knob 6,000/day leaves room to grow.
- The refresh spends only from a near-full bucket (see B.3), i.e. tokens that
  would otherwise be wasted, and never below the recheck reserves.

## Read first

CLAUDE.md, AGENTS.md, docs/HANDOFF.md; `dealradar/photo_recovery.py`
`search_photos` (the paid-step pattern to copy: run_key ledger in db meta,
reserve floor, `budget.available()` before every call, receipts, stop on 429,
`rate_limited`); `dealradar/fetch/keepa.py` (`QUERY_URL`, `_finder_cost`,
`_run_sweep` finder selection 1065-1077, `_regular_basis`); `dealradar/verify.py`
(`keepa_products`, `live_price_reading`, `_live_new_price`, `keepa_observed_at`,
`keepa_time`); `dealradar/keepa_budget.py`; `dealradar/savings.py` +
SAVINGS_RULES.md; `dealradar/publish/product_pages.py` (`prepare`/`publish`, the
static-page pattern, caps, STYLE) and `amazon_as_of.py`; `publish/site.py`
(where product pages and the checker are published; sitemap); `config.py`
`images.*` validation (the pattern for a new validated block); `pipeline.py`
fast-lane step order around `photo_recovery.search_photos` (~2587).

Keepa field names (official `keepacom/api_backend` structs):
- Finder selection: `title` (all keywords must appear; whole words),
  `rootCategory`, `categories_include`, `productType` ([0] = standard),
  `singleVariation`, `current_RATING_gte` (0–50, 40 = 4.0★),
  `current_COUNT_REVIEWS_gte`, `buyBoxIsPrimeEligible`, `availabilityAmazon`
  ([0] = in stock), `monthlySold_gte`, `current_NEW_gte/_lte` (cents),
  `sort` ([["current_SALES","asc"]]), `perPage` (≥ 50), `page`.
- Product: `title`, `imagesCSV`, `stats.current`, `stats.avg90`,
  `unitCount` {`unitValue`, `unitType`, `eachUnitCount`}, `numberOfItems`
  (-1 = n/a), `packageQuantity` (0/-1 = n/a), `size` (free text),
  `availabilityAmazon`, `lastUpdate`.
- Coverage of `unitCount` is unknown; v1 must work when it is missing.

## Task

### A. Terms (data, not code)

1. `data/compare_terms.yaml`: about 60 everyday product types to start. Each:
   `slug`, `name` (display, e.g. "Laundry detergent pods"), `keywords` (finder
   title words; keep them few and whole-word, e.g. ["laundry", "pods"]),
   optional `root_category` (leave empty when unsure), `unit` (the unit we
   compare by: `count`, `load`, `fl_oz`, `oz`, `lb`, `ft` or `each`),
   `unit_words` (title words that evidence that unit, e.g. count: ["count",
   "ct", "pods", "pacs", "pack"]; load: ["loads"]; fl_oz: ["fl oz", "fl. oz",
   "fluid ounce", "fluid ounces"]), optional `exclude` (title words that drop a
   row, e.g. "sample", "refill" where it changes the comparison), optional
   `max_price`.
   Cover: laundry (pods, liquid, dryer sheets), dishwasher pods, dish soap,
   paper towels, toilet paper, trash bags, tissues, disinfecting wipes, hand
   soap, foil, zip bags, AA and AAA batteries, coffee pods, ground coffee,
   protein powder, protein bars, multivitamins, vitamin D3, fish oil,
   toothpaste, floss, mouthwash, razor blades, deodorant, shampoo, body wash,
   sunscreen, cotton swabs, diapers, baby wipes, dog poop bags, cat litter,
   printer paper, sticky notes, USB-C cable, Lightning cable, HDMI cable,
   power strip, USB-C wall charger, power bank, microSD card, LED light bulbs,
   smart plug, water filter pitcher cartridges, paper plates, granola bars,
   almonds, electrolyte packets, tea bags, olive oil, peanut butter, bleach,
   all-purpose cleaner, magic eraser, bandages, hand sanitizer, nitrile gloves.
   Validate the file at load (unique slugs, known units, non-empty keywords);
   a bad file fails loudly in tests, and at runtime skips the bad term with a
   warning.

### B. Refresh (paid, lowest priority, ships OFF)

2. New `dealradar/compare.py` with `refresh(db, cfg, now, fetcher, deadline,
   run_key, *, budget, rate_limited=False) -> dict` called in the fast lane
   right after `photo_recovery.search_photos`, only when `compare.refresh` is
   true. Per run, take due terms oldest-first (`refresh_hours`, default 24):
   - **Finder** when the term's ASIN list is missing or older than
     `finder_days` (7): one `/query` page, `perPage` 50, selection = title
     keywords (+ root category if set), `productType` [0],
     `singleVariation` true, `current_RATING_gte` = `min_rating`,
     `current_COUNT_REVIEWS_gte` = `min_reviews`, in stock
     (`availabilityAmazon` [0]) or `buyBoxIsPrimeEligible` per
     `prime_only`, `current_NEW_gte` 1, `current_NEW_lte` from `max_price`
     when set, sort by `current_SALES` asc. Keep the first
     `candidates_per_term` (20) ASINs, in finder order.
   - **Product reads**: those ASINs via `verify.keepa_products` (stats, no
     history, no offers/rating/buybox/update), batched across terms and
     deduplicated per run.
   - Record receipts (`tokensConsumed`) and the finder's actual cost in the
     run summary: the first live run settles whether "+1 per 100 ASINs" counts
     the page or the whole result set.
3. **Budget gates** (copy `search_photos`): `compare.tokens_per_day` (6,000)
   and `compare.tokens_per_run` (300) in a run_key-keyed db-meta ledger (a
   rerun of the same run key makes no calls); spend only while
   `budget.available()` ≥ `max(reserve_tokens, recheck_surplus_reserve) +
   cost` **and** ≥ `compare.min_bucket_share` × capacity (0.8, so it uses
   tokens that would hit the cap); `budget.available()` before every call;
   stop the step on a 429 or `rate_limited`; respect `deadline` and
   `compare.max_seconds_per_run` (20).
4. **Facts stored privately** in a new table (or one bounded db-meta JSON if
   simpler): per term the ASIN list + finder time; per ASIN the read time and
   only these facts: title, first image name, the shopper-visible new price
   (reuse `live_price_reading` / `_live_new_price` semantics; a price hidden
   behind a coupon/promo is "no price"), avg90 new price, `unitCount`,
   `numberOfItems`, `packageQuantity`, `size`, `availabilityAmazon`, Keepa
   `lastUpdate`. Never store ratings, reviews, monthlySold or sales rank.
   Bounded: drop terms no longer in the file and ASINs no term references.

### C. Unit price (evidence + math)

5. `savings.unit_price(price, quantity) -> float | None` (round to 4 dp;
   None for non-positive inputs) plus a SAVINGS_RULES.md line.
6. Quantity with evidence (`compare.py`): prefer `unitCount.unitValue` when
   its `unitType` maps to the term's `unit`; else a title parse. Either way the
   quantity is accepted only when the title contains that number next to one
   of the term's `unit_words` (e.g. "81 Count", "81-Count", "81 ct",
   "100 fl oz", "6 ft"), and the quote is kept as the evidence string. Handle
   "Pack of N" × per-item quantity only when both appear verbatim. Anything
   ambiguous (two different quantities for the same unit) → no unit price.

### D. Pages

7. `dealradar/publish/compare_pages.py`, modeled on product_pages:
   - `/compare/` — the list of terms with a search box that filters it on the
     device (a small inline JSON, no network). A query with no match shows the
     5 closest terms by shared words and "Not covered yet".
   - `/compare/<slug>/` — "Best value right now": up to 10 rows with a fresh
     price, those with a unit price first (ascending), then the rest by price.
     Each row: photo, title, price, "checked X min ago", unit price ("27¢ per
     load") with the title quote on hover/tap, "Lowest in 90 days" when the
     price ≤ the stored 90-day low (or avg90 basis per `_regular_basis` if no
     low is stored — say which in HANDOFF), a tagged Amazon link, and our /p/
     page link when one exists. A short top line: the best unit price vs the
     most expensive per unit among the shown rows ("Best value: X at 27¢ per
     load; others shown go up to 41¢"). The disclaimer. No ratings, reviews,
     bought counts, ranks.
   - Rows whose price is older than `max_price_age_h` show no price and are
     not ranked; a term with no fresh rows is not published.
   - `compare.publish`: `off` (default, publishes nothing — every byte equals
     today), `preview` (pages published with `noindex`, not in the sitemap,
     not linked from any other page), `on` (indexed, in the sitemap,
     ItemList JSON-LD, and one footer link "Compare prices" — only in `on`).
   - Dark mode: use the page tokens; works with task 254's dark stylesheet.
   - Cap: `compare.max_terms` (120) pages.

### E. Config

8. `config.yaml` → new `compare:` block, validated in config.py like
   `images.*` (wrong types raise with a clear message):

   ```yaml
   compare:                   # task 256: best value finder (Keepa only; nothing shown is a rating or bought count)
     refresh: false           # paid step; turn on after review + one live probe
     publish: "off"           # off | preview | on
     terms_file: data/compare_terms.yaml
     candidates_per_term: 20
     refresh_hours: 24
     finder_days: 7
     min_rating: 40           # finder filter only (4.0 stars); never displayed
     min_reviews: 50          # finder filter only; never displayed
     prime_only: true
     tokens_per_day: 6000
     tokens_per_run: 300
     min_bucket_share: 0.8
     max_seconds_per_run: 20
     max_price_age_h: 24
     max_terms: 120
   ```

## Tests (offline; new tests/test_compare.py; Keepa responses are fixtures)

- Terms file validation (good file, duplicate slug, unknown unit).
- Unit evidence: positives ("Tide PODS … 81 Count", "100 fl oz", "Pack of 3,
  20 Count Each" only when both verbatim) and negatives (number without unit
  word, two conflicting quantities, unitCount type not matching the term).
- `savings.unit_price` cases.
- Ranking and page rows: unit-priced rows first, stale prices hidden and
  unranked, "Lowest in 90 days" rule, top line numbers come from code.
- Never displayed: build pages from fixtures that include rating, reviews,
  monthlySold and sales rank in the Keepa product; assert none of those values
  or words ("stars", "reviews", "bought") appear in any output byte.
- Budget: per-run and per-day caps, bucket-share gate, reserve floor, 429 stop,
  deadline stop; a rerun of the same run_key makes zero calls and writes
  identical pages; tests/test_e2e_offline.py::test_rerun_is_idempotent still
  passes (both modes).
- `publish: off` → the published tree hash equals today's for the shipped
  config (copy the OFF-bytes pattern from tests/test_offscreen_cards.py);
  `preview` → noindex, absent from the sitemap and from every other page;
  `on` → footer link + sitemap + JSON-LD.
- Public copy rules on the new pages.

Targeted regression (`-n 1`): tests/test_compare.py, test_public_copy.py,
test_product_price_pages.py, test_seo.py, test_photos_every_deal.py,
test_e2e_offline.py -k rerun, test_phone_budget.py, plus any file whose pins
you had to keep with `compare.publish = "off"`.

## Rules

- Ships with `refresh: false` and `publish: "off"`: no token spend and no
  published byte changes until the operator's reviewer flips it.
- Never re-pin an existing hash; pin recorded fixtures to the OFF value
  instead (the task 237/254 pattern) and list them in HANDOFF.
- No network calls (fixtures only), no commit, no push. Use `-n 1`,
  `-p no:cacheprovider` and your own `/tmp/task256-*` basetemp (delete it
  after), never the full suite.
- Tasks 253b, 254 and 255 are being merged in parallel (photo identity,
  dark-mode stylesheet, homepage popularity boost). Touch only what this brief
  needs.
- Update docs/HANDOFF.md "In flight" with one short entry: the switches, the
  files, token math, what the first live probe must confirm (finder cost
  semantics, unitCount coverage per term, rows per term), and open policy
  questions for the operator's next Amazon call (showing "bought in past
  month"; comparison pages themselves under amz378_998).
