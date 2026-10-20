# 351 — "Get deal" never opens another deal site (credit by name only)

Repo: dealradar (wowpriced.com). Read CLAUDE.md first; every non-negotiable
applies (politeness/robots, evidence, facts only, idempotency, knobs in
config.yaml, full-suite-before-push is the operator's job — run targeted
tests only).

## Operator decision (2026-10-20 ~17:05Z)
Nishi found "Swisswell Men's Long Sleeve UPF 50+ Rash Guard" (via Deals of
America): Get deal opened dealsofamerica.com/swisswell-rash-guard-shirts/844877.htm
instead of Amazon, so she earns nothing. She re-read the permission emails:
"they said just reference them.. you don't need to link them ... so dont make
it go to their website". So the crawl-permission condition is CREDIT BY NAME
(the existing "via Deals of America" credit), not routing shoppers through
their trackers. ~140 of ~1,400 live deals currently send Get deal to another
deal site (site check by link host: cc.dealsofamerica.com 52, slickdeals.net
51, links.southernsavers.com 17, moneysavingmom.com 9, go.dealmoon.com 7,
hip2save.com 3, thekrazycouponlady.com 3, freestufffinder.com 2).

## Do
1. config.yaml affiliate.repair_competitor_trackers: true, with the operator
   quote; rewrite the comment above it (no longer "NEVER replace ... their
   tracker IS that credit") and the crawl_permission strings in sources.yaml
   for the affected sources to say the condition is credit by name on each
   deal (operator 2026-10-20). Verify the existing repair path really turns
   trackers into store links with OUR tag: pipeline._resolve_short_links
   (bounded per run, polite, robots-respecting, once per deal), the
   restore function (`_restore...`, "source credit: restored ...") must stop
   restoring, site.keeps_source_tracker returns False, link_facts protected
   logic. Fix anything that still forces the tracker when repair is on.
2. When a deal has no store link but has an ASIN (deal["asin"]), Get deal is
   the Amazon product link with our tag (https://www.amazon.com/dp/<ASIN>?tag=...)
   instead of the source post. No new network requests for this.
3. New publish rule, strict bool `hide_deal_site_links` (default false =
   previous bytes; config true): a live deal whose FINAL Get deal URL
   (LinkFacts.url) is a source post (any credit URL) or a competitor tracker /
   known deal-source host (affiliate.competitor_tracker_hosts plus
   site._deal_source_hosts()) is not published anywhere a shopper can click
   it: home, categories, search data (deals.json), deal pages, best-today,
   sitemap/IndexNow, social posting candidates. Do it in ONE place next to the
   existing photoless filter in dealradar/publish/site.py (the `visible = ...
   photoless_expired` line) so every consumer inherits it; dead/ended rows keep
   their current handling. Keep the stored rows (repair may fix them on a later
   run and they reappear). Summary: `hidden_deal_site_links` count plus by
   credit name, short.
4. Credits stay: the card still says "via Deals of America" (reference).
   Do not change credit rendering.
5. scripts/site_check_logic.cjs + site_check.js: the front page check flags any
   of the first 48 cards whose Get deal host is a deal-source/competitor host
   (one line per card like the car-part flags); add logic tests.
6. Tests: OFF = previous bytes via the history-pin pattern (tests/history_pins.py:
   add pre_hide_deal_site_links chained from the newest pre_ helper);
   repair-on routing for a DOA/DealMoon/Southern Savers tracker fixture (fake
   redirect, no network) ends at the store with our tag; ASIN-only deal → dp
   link with tag; post-only deal hidden when the flag is on and shown when
   off; idempotent rerun (tests/test_e2e_offline.py test_rerun_is_idempotent).

Reply with files changed, how many of today's ~140 you expect repaired vs
hidden (reason from code paths), and test commands + results.
