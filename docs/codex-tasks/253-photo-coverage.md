# 253 — Photo coverage back above 90%: recover product identity for "link to the post" deals (ships ON)

## Why

The hourly site check now fails its photo floor (`site_check.min_photo_pct: 90`):
21:07Z 89.97%, 22:06Z 89.5% (2171 of 2427 live deals show a photo; 256 don't).
Visitors still see photos on all top-24 cards, but the operator wants photos on
every card ("we need 99.99% of images showing up!"). The no-photo breakdown at
22:06Z:

- by store: 186 Amazon, 16 Walmart, 6 Woot, 6 CandyinBulk, 4 Giftory, 4 Kroger, 3 amazon.com, 3 Shokz.com
- by source: 129 Slickdeals, 60 Deals of America, 37 Hip2Save, 9 DealMoon, 5 Brad's Deals
- link host of the deal's own link (45 direct): 38 redirect.viglink.com, 5 go.dealmoon.com
- 211 of the 256 "link to their post" (codes/steps deals whose Get-deal link is the source post)
- examples: "Walmart | Time and Tru Women's Eyelet Midi Skirt | via Hip2Save | redirect.viglink.com";
  "Amazon | Dewjom Dual-Probe Wireless Meat Thermometer | via Slickdeals | slickdeals.net";
  "Amazon | Rigwarl Waterproof Touchscreen Winter Gloves | via Deals of America | dealsofamerica.com";
  "Amazon | Brawny TearASquare 3-Ply Paper Towels | via DealMoon | go.dealmoon.com"

Wrapped tracker links already decode (`dedupe.asin_from_links` handles viglink
`u=`, slickdeals `u2=`, dealmoon `url=`, DOA `url=`). So the misses are deals
whose stored links never carried the product URL: the product link existed only
in the post body at parse time and was not kept as a fact.

## Read first

CLAUDE.md (store facts only — an ASIN, a Walmart item id or a product URL is a
fact; never store post text), AGENTS.md, docs/HANDOFF.md,
dealradar/photo_recovery.py (`resolve_posts`, `backfill_asins`, `title_match`),
dealradar/product_images.py, dealradar/dedupe.py (`asin_from_links`, upsert of
facts), dealradar/parse/extractor.py (what links reach the model / the deal),
dealradar/fetch/rss.py + the Slickdeals/DOA/Hip2Save/DealMoon adapters (post
`links`/`photos.links`), dealradar/http_client.py (`extract_asin`,
`tracker_target`), config.yaml `images:` and `site_check:`, the site check
scripts/site_check.js + site_check_logic.cjs (how "photo showing" is counted),
tests for photos (tests/test_photos_every_deal.py and friends).

## Task

1. Find, with tests on realistic fixtures for each source above, where the
   product link of a "link to post" deal is available during parse (post body
   links, `post.photos.links`, `post.resolved_links`) and why it is not kept.
   Keep the product identity as a fact on the deal at upsert time:
   Amazon ASIN, Walmart `/ip/<id>` URL, other retailer product URL — only when
   exactly one product link in the post matches the deal (ambiguous roundups get
   nothing; never guess). The Get-deal link stays the post when the deal needs
   codes/steps; only the identity/photo fields change.
2. Backfill: a bounded one-time pass for existing live deals that lack identity
   uses data we already have (stored links, sightings) — no new fetches of
   retailer pages beyond the existing photo paths and their caps; robots and
   1 req/s/host rules unchanged.
3. Photos: once an Amazon ASIN is known, the existing Keepa image path supplies
   the photo (within its caps). For Walmart/other retailers, use the photo the
   source post already supplied for that exact product if the source allows
   images (`source_image_allowed`), else the existing og:image path. Report what
   share of the 256 examples each path can recover (offline estimate from the
   fixtures/logic).
4. Private run summary: `photos: {recovered_identity, by_source, still_missing}`.

## Tests (offline)

Per-source fixtures (Slickdeals, Deals of America, Hip2Save→Walmart via viglink,
DealMoon): identity recovered from post links; roundup with several products →
no identity; idempotent rerun (no new rows, no extra Keepa calls);
test_rerun_is_idempotent unchanged; evidence rules untouched.

## Rules

Facts only, no post text stored. No headless browsers, robots honored. Keepa
calls only through existing capped paths. Do not change tests/test_workflows.py
or `site_check.min_photo_pct`. Run the tests you touch plus photo-related tests
and tests/test_e2e_offline.py with -n 1 and your own basetemp /tmp/task253-*;
delete it after. Do not run the whole suite. Do not commit. Update
docs/HANDOFF.md "In flight" with one entry.
