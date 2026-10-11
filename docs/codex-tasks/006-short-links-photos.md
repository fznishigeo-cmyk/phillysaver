# Codex task 006: resolve leftover short links so photo-less deals get photos

**Read first:** AGENTS.md and CLAUDE.md. Politeness rules are law: every
request goes through the PoliteFetcher (robots.txt, 1 req/s/host, our
User-Agent); never bypass a bot wall.

## The problem
Some live deals still store a short link as their product link, because the
ingest-time resolver (`pipeline._resolve_product_urls`) has a 60 s budget per
run and busy runs leave links unresolved. Live examples:

    https://on.ltk.com/+zGxPtUgZsMvunvxgSJBWEg      (Walmart, via hip2save)
    https://goto.target.com/B5okL1                   (Target, via southernsavers)

Such a deal has no ASIN and no retailer page to read, so both photo passes
(`_recheck_prices` photo batches for ASINs, `_backfill_product_images` for
retailer og:image) skip it and the card shows a store-name tile.

## What to build
1. In `dealradar/pipeline.py`, a pass `_resolve_short_links(db, fetcher, deadline, cfg)`:
   - Short-link hosts table: `_SHORT_LINK_HOSTS = ("on.ltk.com", "goto.target.com",
     "amzn.to", "a.co")` (exact host match, lowercased).
   - Candidates: deals with no `image_url`, no `link_shared`, no
     `link_resolve_tried` flag, alive (or dead inside the same "Just missed"
     window `_backfill_product_images` uses), whose `canonical_url` host is in
     the table.
   - For each, one `http_client.resolve_redirects(fetcher, url,
     cfg.redirect_max_hops, deadline=deadline)` call; at most
     `_SHORT_LINK_PER_RUN = 20` per run; stop at the deadline
     (`DeadlineExceeded` = stop, leave the deal unflagged for next run).
   - Always add the `link_resolve_tried` flag (one attempt per deal, ever).
   - When the resolved URL differs from the stored one, is http(s), and is
     not a bot wall (`is_challenge_url`): set `canonical_url` to it (the column
     via `db.update_deal(..., canonical_url=...)` AND the payload field), set
     `asin` from it when the deal has none (`extract_asin`), and remove the
     `product_image_tried` / `image_backfill_tried` flags.
   - NEVER change `dedupe_key`. Log one INFO line with counts.
2. Call it in `run()` on the fast lane only, right before the photo passes
   (next to `_backfill_blog_images`), with `deadline - _RESERVE_FOR_TAIL_S`,
   and put its count in the summary as `short_links`.
3. Tests (offline, `httpx.MockTransport`; see tests/test_tracker_links.py and
   tests/test_pipeline_sources.py for fixtures):
   - an on.ltk.com deal whose link 301s to `https://www.walmart.com/ip/123`
     gets that canonical_url, keeps its dedupe_key, loses the photo-tried
     flags, gains `link_resolve_tried`;
   - an amzn.to link resolving to an Amazon /dp/ URL gets the ASIN;
   - a link resolving to a bot wall keeps its stored link (flag still set);
   - a deal with a photo, or already tried, or a non-short host is never fetched;
   - the per-run cap holds; a second run fetches nothing again;
   - a full re-run of the pipeline adds no rows (idempotency contract).

## Rules
- Run `.venv/bin/python -m pytest -q` from the repository root (use
  `/home/user/phillysaver/.venv/bin/python` if `.venv` is missing here). All
  tests must pass, including the new ones.
- Touch only `dealradar/pipeline.py` and new/changed tests.
- Commit on this branch (`codex/task-006`) with a clear message. Do not push.
