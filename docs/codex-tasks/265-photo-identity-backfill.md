# 265 — Photo floor back above 90%: recover Amazon product identity for old deals

## Evidence (live, fast lane #491, 2026-10-17 02:47Z)
`photo_coverage: live_public 2611, with_photo 2274, without_photo 337, by_cause:
amazon_no_asin 245, woot_no_photo 11, other_store_no_source 38, other 43`.
`photos: recovered_identity 0` — task 253/253b (product_identity_url kept
separate from click routing) recovered nothing on live data. Photo gaps by
canonical host: slickdeals.net 146, zdcs.link 48, go.dealmoon.com 17,
on.ltk.com 15, clicks.trx-hub.com 14, unknown 12. Site check: photos showing
87.5% < 90% floor (first 24 cards all have photos).

## Task
1. Find out from the code why `recovered_identity` is 0 in production (likely:
   identity is only extracted when a post is (re)parsed, and the 245 live
   Amazon deals were parsed before 253 / come from cached or structured
   paths that never set it). Write the finding in HANDOFF.
2. Bounded, polite backfill pass (`photos.identity_backfill`, ON, cap
   `per_run: 15`, after price rechecks and photo search, inside the run's
   time budget): for live, public, Amazon-store deals with no ASIN and no
   `product_identity_tried` marker, take the deal's stored source post URL
   (or its canonical link when that is a known short-link host: zdcs.link,
   go.dealmoon.com, clicks.trx-hub.com, on.ltk.com) and
   - short links: resolve with the existing redirect resolver (HEAD/GET,
     robots honored, 1 req/s/host, descriptive UA, conditional GET where
     possible) — never follow into login walls;
   - source posts: fetch the post page through the existing polite fetcher
     (robots honored; no new hosts beyond the source's own), extract Amazon
     product links deterministically from the HTML (dp/ASIN, gp/product/ASIN,
     amzn.to resolved by the resolver). No LLM call, no post text stored.
   Accept an ASIN only when exactly one distinct product ASIN is found (or
   the resolved link itself is a dp URL); else record "ambiguous". Store
   only `product_identity_url` (canonical https://www.amazon.com/dp/ASIN) and
   `product_identity_tried` (timestamp + outcome); never change the click
   link, dedupe key, price or evidence. Existing Keepa photo lookup then fills
   the photo on the normal path (no extra Keepa spend beyond the existing
   photo budget/caps).
3. Count outcomes in the existing `photos` summary (recovered_identity,
   ambiguous, no_link, fetch_failed) — counts only, no URLs in logs.

## Tests (offline, recorded HTML fixtures; transport recorder proves request
count, hosts, robots consultation, 1 req/s spacing and the per-run cap)
Single-ASIN post → identity set, click link unchanged; multi-ASIN roundup →
ambiguous, nothing set; short link → dp URL; robots disallow → no fetch;
failures marked tried with backoff (retry after 7 days max 3 times);
idempotent rerun; OFF (enabled false) = today's requests and bytes; recorded
byte pins keep hashes (pin the switch off; never re-pin).

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task265-*` basetemp
(delete after each run; disk is tight), targeted tests only, never the full
suite. No network, no commit, no push. HANDOFF "In flight": one entry.
