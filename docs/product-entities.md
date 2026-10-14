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
Off performs no comparison work and preserves published bytes. On adds
“Also at &lt;store&gt; for &lt;price&gt;” to live deal pages only, using already-public
live offers with the same product/condition and an emitted detail page.
It links to that page so the existing price steps and credits remain available;
the public checkout requirement summary accompanies conditional prices.
One offer per other store is selected by price, then stable key. Locked,
ended, unpublished, range/per-unit and non-USD offers never supply comparisons.
Product IDs stay internal; public JSON, social rules and ranking are unchanged.
