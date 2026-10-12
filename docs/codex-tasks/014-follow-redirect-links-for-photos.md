# Codex task 014: follow redirect links so photo-less deals get a photo

**Read first:** AGENTS.md, CLAUDE.md (politeness is law: robots.txt honored,
1 req/s/host via PoliteFetcher, never bypass bot walls; store the image URL
only, never bytes; idempotency).

The hourly site check (`scripts/site_check.js`, 2026-10-12) found 184 of
1,436 live deals with no photo, 0 broken. Of the 93 that link straight to a
product, most link through a redirect the pipeline never follows, so it
never learns the ASIN (Keepa photo) or the retailer page (og:image):

    23 walmart.com (bot wall: leave alone), 15 dealnews.com, 9 zdcs.link,
    7 creator.anchor.store, 6 go.magik.ly, 5 macys.com, 4 thekrazycouponlady.com,
    4 slickdeals.net, 4 shoplowes.me, 2 bradsdeals.com, 2 thefreebieguy.com,
    2 link.amazon, 2 freestufffinder.com, 2 go.dealmoon.com, 2 clicks.trx-hub.com

`pipeline._resolve_short_links` only follows `_SHORT_LINK_HOSTS`
(`on.ltk.com`, `goto.target.com`, `amzn.to`, `a.co`), 20 per run.

1. **Follow every redirect host, not just four.** A photo-less deal's
   `canonical_url` is worth resolving when its host is
   - in `_SHORT_LINK_HOSTS`, extended with: `zdcs.link`, `creator.anchor.store`,
     `go.magik.ly`, `shoplowes.me`, `link.amazon`, `go.dealmoon.com`,
     `clicks.trx-hub.com`; or
   - the site of one of the deal's own credit sources (a deal site linking
     through itself: dealnews.com, slickdeals.net, bradsdeals.com,
     thekrazycouponlady.com, thefreebieguy.com, freestufffinder.com, ...).
     Use the deal's sightings / credits to know its sources' hosts; compare
     registrable domains (strip `www.`). Do NOT hardcode that second list.
   Never resolve a retailer (amazon.com, walmart.com, target.com ...) or a
   bot-wall host (`is_challenge_url`). Keep `resolve_redirects` as is
   (robots honored per hop, bot walls never fetched, hop cap, deadline).
2. **Include deals that link to their post.** Deals that need steps publish
   the post URL, but their `canonical_url` is still the product link: the
   selection already reads `canonical_url`, so make sure nothing excludes
   them (they are 91 of the 184).
3. **Raise `_SHORT_LINK_PER_RUN` to 60**, newest deals first (by
   `first_seen_at`), still one attempt per deal ever (`link_resolve_tried`).
   On a resolve that lands on Amazon, set the ASIN (already done) so the
   Keepa photo pass picks it up; on any other retailer, lift
   `product_image_tried` (already done) so the og:image pass tries it.
4. **Diagnostics in the run summary** (public log: hosts and counts only,
   never item names or URLs): one line "photo gaps: N alive deals without a
   photo; by canonical host: ..." (top 10), and "redirects: resolved X of Y".
   Find where the fast run writes its Actions summary and add it there.
5. **Tests:** a dealnews.com click link credited to DealNews resolves to an
   amazon.com/dp/ASIN (ASIN set, flags lifted); a zdcs.link resolves to a
   retailer; a walmart.com canonical and an amazon.com canonical are never
   fetched; a robots-blocked redirect host stops and the deal is flagged
   tried; the cap holds; newest first. Re-run safety:
   tests/test_e2e_offline.py::test_rerun_is_idempotent stays green.

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push.
Write a short note of what you changed at the end of your reply.
