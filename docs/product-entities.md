# Product entities

Task 089 adds durable identities alongside existing deals. It does not replace
deal dedupe, ranking, evidence checks, price history, alerts or product-family
pages. There are no new requests, LLM calls, services or dependencies.

| Storage | Purpose |
|---|---|
| `products` | Integer `product_id`, exact variant signature, first/last observation times. |
| `product_keys` | Indexed `(kind, value, product_id)` aliases; multiple identifiers can support one product. |
| `product_offers` | One row per existing `dedupe_key`: product, store, physical condition, link reason, compact identity fingerprint, first/last seen. |
| `deals.product_id` | Nullable internal reference, exposed by all DB deal readers and the upsert result; never serialized publicly or duplicated in the payload. |

An offer is still an independent deal, with its own price, source credits,
checkout requirements and availability. Nothing combines or deletes deal rows.
The two mapping tables use `WITHOUT ROWID`; identity fingerprints are 16 bytes.
Only facts and identifiers are stored, never source text or images.

## Resolution and invariants

Resolution tries ASIN, GTIN, store SKU and then explicit brand/model. The
`product_offers.match` value records the link basis (`asin`, `gtin`, `sku`,
`brand_model`), or `unresolved`/`conflict`. New unidentified deals get their own
product; identical titles alone never join them.

- ASINs come from the stored `asin` or a US Amazon product URL, with exact
  ten-character validation. Conflicting stored/link ASINs cannot resolve.
- `upc`, `ean` and `gtin`, when present in the stored payload, need a valid
  8/12/13/14-digit checksum. Equivalent leading-zero representations normalize
  to 14 digits. All-zero codes and contradictory identifiers cannot resolve.
- `sku` is namespaced by store. Explicit `bestbuy_sku`, `ebay_legacy_id` and
  `woot_id` also work. Recognized Best Buy/eBay/Walmart product URLs and Woot
  offer URLs supply exact local keys; tracking parameters supply no identity.
  Woot URL slugs have their own namespace, distinct from Woot API IDs.
  A product can have multiple eBay/Woot listing IDs when another exact key
  proves the re-listing; a changed listing ID alone never links products.
- Brand/model needs explicit stored `brand` and `model` or `model_number`.
  The model must contain letters and digits, at least four alphanumeric
  characters; bare years, names and capacity/resolution tokens do not qualify.
  The extraction prompt is unchanged; models and barcodes are never guessed
  from titles or obtained through a new call.
- **Precision takes priority over recall:** every match also requires the
  same full item text (case/Unicode/whitespace normalized, punctuation and
  word order preserved) and the same explicit quantity, pack, capacity,
  size, color, variant, model, grade, currency and region facts. The title
  is only a veto, never a matching key. This protects even unrecognized
  color/size words in titles. Different titles and missing-vs-known variant
  facts deliberately leave some genuine cross-store matches separate.
- New, used, open-box and refurbished inventory have separate product
  identities in this conservative first version. Conditions/grades are
  never mixed in comparisons or histories. A broader physical-product
  family, if needed later, would be another layer.
- Conflicting identifiers or keys pointing to multiple established products
  never union those products. A changed variant or contradictory identifier
  on an already linked deal quarantines that offer (`product_id=NULL`,
  `match=conflict`). It stays quarantined for explicit future repair: old
  price observations are not reassigned to a different variant. All original
  deal and price rows remain intact.

## Lifecycle and history

Migration only creates tables and nullable/reference/checkpoint columns. It
is idempotent and does not bulk-link existing rows at database open. Inserts
and incoming sightings attach locally in the same transaction; other fact
updates maintain existing links without starting a second backfill. Each fast/hourly
run backfills at most `product_entities.backfill_per_run` untouched rows
(default 200; 0 pauses). Offer rows are durable checkpoints, and the
`run_stats.products_backfilled` marker makes a retry of a retained run key
skip the next batch. A failed batch rolls back both links and its marker.

Product/offer anchors survive deletion of an ended deal. This retains links
to the existing `price_history` rows and lets a later exact re-listing find
the product. Anchors grow with distinct historical deals, not with checks
or run count. They are intentionally not garbage-collected yet.

`DB.product_price_history(product_id, days, now=...)` returns observations
in chronological order, within an inclusive UTC window, with `price`,
`observed_at`, `dedupe_key`, `store` and `condition`. It reads the same
`price_history` facts as `price_observations`; sightings fill gaps only when
that deal/content hash has no price-history record. Future, negative,
nonfinite and missing prices are excluded. No prices, lowest-price claims,
savings or interpolated checks are computed. Existing deal charts are unchanged.

## Optional comparison

`product_entities.also_at_enabled: false` is the default in code and YAML.
Off preserves published bytes; the private shadow calculation still runs. On adds
“Also at &lt;store&gt; for &lt;price&gt;” to live deal pages only, using already-public
live offers with the same product/condition and an emitted detail page.
It links to that page so the existing price steps and credits remain available;
the public checkout requirement summary accompanies conditional prices.
One offer per other store is selected by price, then stable key. Locked,
ended, unpublished, price-unconfirmed, range/per-unit and non-USD offers never
supply comparisons. Neither block appears on a price-unconfirmed page.
Product IDs stay internal; public JSON, social rules and ranking are unchanged.

Task 096 adds a second publication guard: both offers must share an exact
identifier, with no disagreement in title/stored pack or quantity, capacity,
size/color/variant, physical condition/grade, or model. Unexplained title
differences also fail closed. The price ratio must be within 0.4–2.5 in both
directions (zero only compares with zero); the YAML limits can be tightened.
A rejected cheapest offer is omitted, with no fallback to a more expensive
offer from that store. These checks apply identically to shadow and display.

## Lowest we've seen

`product_entities.lowest_seen_enabled: false` separately gates one detail-page
line: “Lowest we've seen: $7.00 on 2026-10-07 (UTC, last 30 days)”. The minimum
comes from `DB.product_price_history` for that exact product and condition,
within the inclusive preceding 30 days, with observations on at least three
distinct UTC dates. Duplicate checks on one date do not satisfy the floor;
equal minima use the earliest date. Thin history is hidden. Computation is
pure code in `savings.py`, with no LLM or requests.

These are our evidence-checked ingestion/repricing observations, never raw
Keepa history or inferred prices. “Verified” means the ingestion evidence
gate, not the independent-source verification badge. The existing product
resolver quarantines changed variants rather than relabeling old history;
the minimum also explicitly filters each observation's condition. Existing
90-day deal charts are unchanged.

## Private shadow report

Every publish evaluates its actual emitted deal pages, regardless of either
display switch. The single encrypted
state meta key `product_entities_shadow` retains one record per run key for
`shadow_retention_days` (7 by default). Standalone publishes use the publish
timestamp as their key. Retries replace that run's facts without adding runs
or sample sets; the original timestamp stays. Retention runs on each publish.

Each record holds page counts (`published_pages`, `eligible_pages`,
`also_at_pages`, `lowest_seen_pages`), `also_at_pages_by_match` (ASIN, GTIN,
SKU, brand/model), directed candidate/blocked pair counts and rejection
reasons. A page can count under more than one match type. Up to 30 sampled
pairs contain both titles, stores, conditions, already-public prices, the
exact match type and rejection reasons. Rejected pairs take priority;
deterministic run-key sampling rotates the remainder between runs.

Only the private growth report reads this meta: latest-publish counts and
samples, plus retained UTC daily publish/candidate/blocked counts. Repeated
pairs count once per publish, so daily totals are not unique-product counts.
No retained data is reported as unavailable, never zero observed errors.
Diagnostics never enter public files, JSON, structured data, publish/run
summaries, Actions summaries or logs. The feature makes no external requests.

## Switch-on readiness

- Collect at least three complete UTC days of real publish samples. Zero
  candidate pairs means insufficient coverage, not proof of precision.
- Review sampled pairs across the match types and stores actually present:
  same model, pack/quantity, capacity, variant and condition. Require zero
  flagged pairs over those three days and zero reviewer-found wrong matches;
  investigate any flags and restart the clean observation window after fixes.
- Spot-check the minimum/date against the stored evidence-checked observations:
  same product/condition, three distinct UTC days and a 30-day window.
- Check labeled, locked, ended and capped-out pages never contribute; run the
  full suite and review disabled byte equality before activation.

Only after that review, flip either or both exact config lines independently:

```yaml
product_entities:
  also_at_enabled: true
  lowest_seen_enabled: true
```

Both ship **false**. This task supplies the observation mechanism; it does not
claim that the required real-data review has happened.
