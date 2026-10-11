# Codex task 013: photos for the deals that still show a store tile

**Read first:** AGENTS.md, CLAUDE.md (politeness is law: robots.txt, 1
req/s/host via PoliteFetcher, never bypass bot walls; store the image URL
only, never bytes).

Live examples still showing a store-name tile: "RM Gold NYC" (via Brad's
Deals), "Clinique official" (via DealMoon), "Revolve Clothing", "Adidas via
eBay", several "Woot" cards.

1. **Retailer photo pass beyond the allow-list.** `pipeline._backfill_product_images`
   only fetches hosts in `_PRODUCT_IMAGE_RETAILERS`. Let it fetch ANY https
   product page link a photo-less deal holds, except: hosts matching
   `_PRODUCT_IMAGE_SKIP_HOSTS` (blogs, trackers, affiliate redirectors,
   Amazon), known marketplaces' bot walls (`is_challenge_url`), and listing
   pages (`_names_no_product`). Keep: one attempt per deal ever
   (`product_image_tried`), the per-run cap (raise `_PRODUCT_IMAGE_PER_RUN`
   to 40), the run deadline, og:image/twitter:image only, the junk filter.
   Keep the allow-list as a priority order (allow-listed hosts first), not
   a gate. Add tests for an unlisted retailer (e.g. revolve.com) getting a
   photo and a blog/tracker host never being fetched.
2. **Woot.** `dealradar/fetch/woot.py` takes `item["Photo"]`. Find why some
   Woot deals end up with no image (other Woot item shapes, e.g. `Photos`
   lists or offer-level photos; deals that reached the DB from a blog post
   that links woot.com). Fix the adapter for the shapes the Woot API
   returns, with tests, and make sure woot.com product links are eligible
   for step 1.
3. **Broken photos repair themselves.** In the fast lane, check up to 30
   stored `image_url`s per run (oldest-checked first, meta-tracked), HEAD
   with a GET fallback, 1 req/s/host via the fetcher. A non-2xx or non-image
   answer clears `image_url` and lifts the photo `*_tried` flags so the passes
   look again. Never check the same URL twice within 24 h. Tests.
4. Re-run safety: a re-run of the same run key adds no rows
   (tests/test_e2e_offline.py::test_rerun_is_idempotent stays green).

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push.
