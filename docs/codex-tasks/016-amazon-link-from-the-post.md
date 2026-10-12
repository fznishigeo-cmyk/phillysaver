# Codex task 016: find the Amazon link in the post for photo-less Amazon deals

**Read first:** AGENTS.md, CLAUDE.md (politeness is law: robots.txt honored,
1 req/s/host via PoliteFetcher, never bypass bot walls; store facts only:
a product link, never post text or images; idempotency).

The fast run's "photo gaps" line (2026-10-12) shows 178 live site deals
without a photo; the biggest group is 52 with NO canonical URL at all.
On the live page these are Amazon deals like "M&M'S Peanut Butter
Chocolate Candy Singles 24-Count" (via Jungle Deals), "Kettle Brand Potato
Chips Variety Pack 20 Ct", "Nature Made Vitamin D3 100-Count": the
extraction kept the facts but no product link, so there is no ASIN and the
Keepa photo pass (which works from the ASIN) never runs for them.

1. **New fast-lane pass `_links_from_posts`** in `dealradar/pipeline.py`,
   run right before the Keepa image backfill. For each live SITE deal
   (`rank.site_deals`, alive) with no `image_url`, no `canonical_url` (or no
   ASIN while the store is Amazon), and not flagged `post_link_tried`:
   - take its first credited sighting's post URL (`db.sightings_for`);
   - fetch that page with the run's PoliteFetcher (robots, rate limit,
     deadline as every other pass; a robots block / 4xx / 5xx / timeout just
     flags the deal tried);
   - collect the Amazon product links in the page's `<a href>`s: amazon.*
     `/dp/ASIN`, `/gp/product/ASIN`, and `amzn.to` / `a.co` short links
     (resolve those with the existing `resolve_redirects`); use the existing
     `extract_asin` / canonicalize helpers;
   - one distinct ASIN on the page: that is the deal's product. Several
     (a roundup): take the link whose anchor text best matches the deal's
     item name (normalized word overlap), only when that best match is
     unique and covers at least 60% of the item's words; otherwise none.
   - On a match set `canonical_url` (canonical Amazon /dp/ URL, no tags) and
     `asin`, and lift `image_backfill_tried` / `product_image_tried` so the
     photo passes look again. Always add `post_link_tried` (one attempt per
     deal, ever). Store nothing else from the page.
   Cap 40 fetches per run, newest deals first; one fetch per post URL per
   run (several deals from one post share it).
2. **Rakuten links:** add `click.linksynergy.com` with param `murl` to
   `http_client._TRACKER_PARAMS` (tracker_target already decodes those).
3. **Summary:** add `post_links: {"tried": N, "found": M}` to the run
   summary next to `redirects`.
4. **Tests** (offline, fake transport): a single-product post gives the
   ASIN and the photo flags are lifted; a roundup picks the matching link
   only when it is clearly the best; an ambiguous roundup sets nothing; a
   robots-disallowed post is never fetched and the deal is flagged tried;
   the cap and newest-first hold; a deal that already has a photo or a
   canonical with an ASIN is skipped; tracker_target decodes a linksynergy
   murl. Re-run safety: tests/test_e2e_offline.py::test_rerun_is_idempotent
   stays green.

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push.
End your reply with a short note of what changed and anything unsure.
