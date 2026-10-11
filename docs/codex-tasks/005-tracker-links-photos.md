# Codex task 005: see through Deals of America tracker links (photos + price checks)

**Read first:** AGENTS.md and CLAUDE.md. Politeness, evidence and idempotency
rules apply. This task adds NO network requests.

## The problem
Deals from `dealsofamerica` store a tracker link as their product link, e.g.

    https://cc.dealsofamerica.com/v1/otc/05SnKFVIkpfXAecBz6C7qZl?short_url=00GH66w9im2QK4R3vcnmY1c&url=https%3A%2F%2Fwww.amazon.com%2Fgp%2Fproduct%2FB0GLP6BWD9%3Ftag%3Dxyz

The real product URL is in the `url` query parameter, but nothing reads it.
So these deals have no `asin`: no Keepa photo, no live price check. Their
cards show a store-name tile. The same applies to the Walmart links behind
these trackers (`walmart.com/ip/...`): the retailer-photo pass skips them
because the link's host is cc.dealsofamerica.com.

## What to build
1. **`http_client.tracker_target(url: str) -> str`**, a pure function with no
   network access, next to `challenge_target`.
   - Table-driven: `_TRACKER_PARAMS = {"cc.dealsofamerica.com": "url"}` (host →
     query parameter that holds the destination).
   - Return the decoded destination when the link's host is in the table and
     the parameter is an `https://` URL with a host. Otherwise return `''`.
   - Reject anything `challenge_target` rejects: control characters,
     backslashes, a bot-wall target (`is_challenge_url`).
   - Never raise on malformed input.
2. **ASIN at ingest** (`dedupe.py`, where `asin = extract_asin(extracted.product_url)`):
   - When that is empty, also try `extract_asin(tracker_target(extracted.product_url))`.
   - Do NOT change `canonical_url`, `link_as_posted` or any dedupe key. The
     tracker stays the card's link: it carries the source's affiliate credit,
     which is the condition of its crawl permission.
3. **Retailer-photo pass** (`pipeline._backfill_product_images`): when a deal's
   link is a tracker with a target, use the target for the host checks
   (`_PRODUCT_IMAGE_RETAILERS`, skip hosts, `_names_no_product`,
   `is_challenge_url`) and for the one polite fetch. Change nothing else
   there.
4. **One-time repair**, modelled on `pipeline._repair_botwall_urls`:
   - Gate it on a db meta key such as `tracker_asins_2026_10_11`.
   - For every deal (any status) whose `canonical_url` is a tracker with a
     target: set `asin` from the target when the deal has none, and remove
     the `product_image_tried` / `image_backfill_tried` flags so both photo
     passes try again.
   - Never change `dedupe_key` or `canonical_url`.
   - Call it next to `_repair_botwall_urls(db)` in `run()`.
5. **Tests** (offline, in the style of tests/test_links.py and the botwall
   repair tests). They must cover:
   - `tracker_target`: Amazon target, Walmart target, missing param, `http://`
     or `javascript:` target, a non-table host, garbage input.
   - Ingest gets the ASIN while `dedupe_key` and `canonical_url` are unchanged.
   - Repair sets the ASIN, lifts the flags, runs once, and leaves keys alone.
   - The photo pass fetches the Walmart target, never the tracker host.
   - A re-run adds no rows (the idempotency contract).

## Rules
- Run `python -m pytest -q`. All tests must pass, including the new ones.
- Touch only `dealradar/http_client.py`, `dealradar/dedupe.py`,
  `dealradar/pipeline.py` and tests.

## Deliver
Commits on this branch (`codex/task-005`). Never push to
`claude/dealradar-aggregator-pyqirm`.
