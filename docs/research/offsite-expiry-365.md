# Optional off-site expiry (365, 2026-10-21)

`retention.offsite_unseen_days: null` ships OFF. Missing/null preserves previous
writers, maintenance options, database bytes and publication bytes. The only
accepted ON values are integers 1–3650 (booleans, floats and strings fail).
The operator decides whether to enable it. No production state was accessed.

## Protection and deletion

Expiry runs **after a successful current render**, inside the existing `deals`
maintenance sweep. A non-dead row must have a valid `last_seen_at` strictly older
than N days and be absent from the render's protected set. The cutoff boundary,
recent/future observations, invalid clocks and every protected row survive.
The existing 14-day dead-deal policy remains separate.

The renderer supplies its actual selections; expiry does not reimplement ranking,
photo, price-confirmation, used-offer, link, access or admission rules:

| Surface | Protection source |
| --- | --- |
| Wall, public/member JSON, locked stubs, static HTML mirror, category/store/price/event/family pages | Full serialized inventory, including entries beyond the first visible wall |
| Spotlight | Actual serialized spotlight selections, including price-only offers |
| Saved deals / watches | The same public inventory; `engagement.js` uses `savedPublicMatch`. Missing saved entries display device-local labels and “Not currently listed; availability unknown”, with no DB lookup |
| Deal pages | `seo._deal_pages` rows, including remembered suppressed Amazon used offers rendered as ended pages |
| Product pages | Every offer in the actual `product_pages.prepare` models, including retained offers absent from the wall |
| Public weekly hub statistics / optional lowest-seen comparison | Contributor keys collected inside the same `seo.week_stats` / `product_offers.evaluate` pass used by the renderer, without copying their filters |
| Checker, finder, tool pages, comparisons, PWA | Subsets of the above public inventory, deal rows and product models; no independent inventory source |
| IndexNow | Every non-removed deal receipt's hash maps back to matching deal keys; non-removed product receipts protect their remembered offers, even if this render no longer emits that URL |

An unknown live product-receipt mapping, unreadable surface mapping, or a caller
without a completed render proof retains **all** non-dead rows. Receipts remain
intact. Over-protection is intentional. Expiry does not mark an offer dead or
change publication filters.

Deletion matches dead pruning's dependency boundaries:

| Data | Treatment |
| --- | --- |
| `deals`, `sightings` | Delete inventory; foreign-key cascade deletes sightings in the same transaction |
| `keepa_recheck_history` | Delete the expired key's samples in that same transaction; the existing orphan sweep still runs |
| `products`, `product_keys`, `product_offers`, `price_history` | Retain independent product identity, original store/condition, evidence and unique observation receipts, as dead pruning already does. Product-page/history readers explicitly support pruned offers |
| Click/accuracy details, aggregates and watermarks | Retain their existing independent retention/archival policy and replay guards; expiry does not delete them |
| `alerts_sent`, `outgoing`, delivery batches, run claims, post receipts, social/meta guards, IndexNow receipts | Never cascade or delete for off-site expiry; established rejected-attempt/detail retention is unchanged |
| Lifetime `deals.ai_public_at` / `ai_public_price` | Move to lazy permanent `offsite_deal_anchors`; restore atomically when that key is re-created, even if expiry has since been switched OFF |

The inventory sweep examines fresh and protected rows against the same per-table,
per-run `state_maintenance_max_rows` allowance. It uses transactional chunks of at
most 64, the existing frozen-high-water cursor and shared cooperative
`state_maintenance_budget_s` / tail deadline. An interrupted chunk rolls back
inventory, cascades, anchors, counters and cursor together. Replaying a run key
cannot renew its allowance. Both values of `state_retention_337` are covered.

One additional line per maintenance invocation reports `expired`, `protected`,
`left_by_bound` and `unproven`, grouped into fixed `alive` / `other_non_dead`
labels. No deal key, status text, title or URL is logged. Expired counts are the
durable run totals; protected counts cover all current non-dead protected rows;
left counts cover eligible rows still awaiting the bounded sweep. Diagnostic SQL
counts do not load payloads or delete rows; a temporary key table is absent from
the encrypted database. Bounds are cooperative SQLite bounds, as in task 337,
not a hard wall-clock guarantee including bookkeeping/counts/compaction.

## Offline measurement

Reproduce with `.venv/bin/python -m scripts.offsite_expiry_365`. The script uses
only an automatically removed temporary database and deterministic invented
facts (seed 365), with no network. SQLite here was **3.45.1**. Both cases start
from the same database and fixed **2026-10-21 08:58 UTC** clock.

The fixture has **33,919 deals**: **30,692 alive**, **3,227 dead**, and **52,700
sightings**. Alive age cohorts exactly match the brief: **4,736 <1 day**, **18,699
1–7 days**, **7,257 7–12 days**; none older. Within cohorts ages are uniform and
shuffled. There are **1,613 protected public keys**, sampled across cohorts.
Dead rows died within 12 days, so neither case changes dead retention.

Additional modeled ratios: **20,352 recheck histories** (60% of deals, 12 samples
each); **113,756 price observations** (one per sighting plus three checks for
each Amazon offer); **30,528 product offers** (90% of deals), with exact product
anchors on about 78% of those. Compact alive payloads total **32,862,518 bytes**, near
the brief's 31.7 MB. Facts include prices, dates, conditions, identifiers, shipping
and source credit; there is no random incompressible ballast or stored post text.

All raw sizes below are **after VACUUM and WAL checkpoint**. Gzip is level 9 with
mtime 0; bytes are decimal, without encryption overhead.

| Case | Alive rows expired | Raw bytes | Gzip bytes | Raw reduction | Gzip reduction | Runs to drain |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Before | 0 | 151,748,608 | 24,597,112 | — | — | — |
| N=7 | 6,885 | 132,132,864 | 22,042,769 | 19,615,744 (12.93%) | 2,554,343 (10.38%) | 8 |
| N=3 | 18,751 | 95,977,472 | 17,717,126 | 55,771,136 (36.75%) | 6,879,986 (27.97%) | 8 |

| Table | Before | N=7 after | N=3 after |
| --- | ---: | ---: | ---: |
| Deals | 33,919 | 27,034 | 15,168 |
| Sightings | 52,700 | 42,029 | 23,573 |
| Recheck histories | 20,352 | 16,224 | 9,165 |
| Price observations (independent retained facts/receipts) | 113,756 | 113,756 | 113,756 |
| Product offers (independent retained identities) | 30,528 | 30,528 | 30,528 |

The actual shared maintenance bounds were **4,096 examined rows/table/run,
1.0 seconds**, with task 337 ON. Each measured inventory pass examined 4,096
rows. N=7 expiry counts by run: **904, 885, 930, 921, 938, 905, 917, 485**.
N=3: **2,433, 2,486, 2,561, 2,498, 2,528, 2,520, 2,485, 1,240**.
The last 1,151 inventory rows were recent dead rows, so the expiry backlog
drained before the ninth pass needed to finish the entire inventory sweep.
Measured maintenance times were **0.449–0.791 seconds/run** for N=7 and
**0.411–0.998 seconds/run** for N=3. Slower hosts or other maintenance backlogs
can require more runs; these are observed counts, not a production guarantee.
Integrity and foreign-key checks passed after both experiments.

This models the specified tables and ratios, **not** the complete 307 MB raw /
76.4 MB encrypted production snapshot. Other tables, real payload entropy,
actual public/receipt overlap and host speed were not measured. Do not apply
these percentages to the encrypted production DB or infer a guaranteed deadline
extension. Shipping OFF saves zero bytes until the operator opts in.

## Verification

A direct comparison against pre-change HEAD
`ed9b045e87cb8ea2c703b1dec7e417d144c7d962` used identical fixed-clock inputs and
the original changed Python/config files in an isolated temporary package.
OFF matched **every SQLite byte** and **every published file byte**:

| Configuration | Matching DB bytes | Matching published files |
| --- | ---: | ---: |
| Legacy maintenance | 454,656 | 27 |
| Bounded task-337 maintenance | 491,520 | 27 |
| Shipped settings | 548,864 | 55 |

Tests exercise strict parsing, OFF byte equality, current wall/spotlight/deal /
retained-product/checker protection, remembered suppressed deal pages, live and
removed receipts, conservative unknown mappings, age boundaries, other statuses,
dead policy, dependent cascades, permanent guards, resighting/member sends,
transaction rollback, durable 64-row chunks, reopen/resume, and actual pipeline
same-run replay following expiry with task 337 OFF and ON. Relevant regression
suites passed locally: **667 passed, 1 browser skip**, then a final publication
selection after the aggregate-protection additions: **392 passed, 1 browser
skip**. The expanded four-case guard matrix also passed, including switching
expiry OFF before re-sighting. The full suite remains with Claude. No commit or push.
