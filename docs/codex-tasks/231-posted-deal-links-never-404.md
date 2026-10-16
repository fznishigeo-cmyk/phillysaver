# 231 — Links we posted socially never land on a 404 (traffic plan B2, part 1)

## Why

Pins, Facebook posts and Telegram posts link to `/deal/<slug>-<key8>/` pages
(`dealradar/publish/pinterest.py:307`, `traffic.tagged_url`). A deal page lives
only while its deal row exists: `seo._deal_pages` (`dealradar/publish/seo.py:759`)
forgets the path once the row is pruned (`retention.deals_days`) and every
deployment starts from a fresh site folder, so the page disappears. Pinterest
pins keep getting clicks for months; today a pin older than a few weeks lands on
our 404 page. That wastes our best long-lived traffic and looks broken.

## Read first

CLAUDE.md, AGENTS.md, docs/HANDOFF.md. Then `seo._deal_pages`, the `seo_deal_paths`
meta record, `alerts_sent` in `dealradar/db.py` (one row per deal per channel when
a social post was sent), `publish/pinterest.py`, `publish/traffic.py`
(`tagged_url`), `seo.CATGROUPS`/`HUB_GROUPS`/`in_group`/`hub_href`, the `_headers`
writer in `seo.publish`, and `tests/test_e2e_offline.py::test_rerun_is_idempotent`.
Cloudflare Pages reads a `_redirects` file at the site root
(`/old/path/ /new/path/ 301`, one per line; at most 2,000 static rules).

## Task

1. When a `/deal/` path that was ever posted socially (any `alerts_sent` row for
   that deal on a social channel: pinterest, facebook, instagram, telegram — read
   the channel names actually used in the code) is no longer published, emit a
   permanent `301` from that exact path to the deal's category hub
   (`hub_href` of the first non-USED `HUB_GROUPS` match, the same rule the deal
   page's own hub link uses; `/` when no hub matches) in `site/_redirects`.
   Remember the rule in one private meta record (path → target, plus the date it
   was added) so it survives the deal row's deletion; never store post text,
   prices or images there.
2. Bound it: new config `seo.posted_deal_redirects` with `enabled: true` and
   `max_rules: 1500` (validated integer 1..1900, under Cloudflare's 2,000 cap).
   Over the cap, drop the oldest rules first. Disabled = no `_redirects` file and
   no meta writes, byte-identical to before.
3. A path that is published again (the deal came back) must not also redirect:
   live pages always win. Never redirect a path that is not a `/deal/` path with
   our `<slug>-<key8>` shape. Keep the file sorted so reruns are byte-identical.
4. Report counts in the publish summary (`posted_redirects`: rules, added today,
   dropped by cap). No public JSON field, no new request, no new dependency.
5. Leave `pinterest.py`'s links, captions and posting caps unchanged.

## Tests (offline)

- A posted deal whose row is pruned gets exactly one 301 to its hub; an unposted
  pruned deal gets none (it 404s as today); a revived deal gets no rule.
- Cap: with `max_rules: 2`, the oldest rule drops first; the order is stable.
- Disabled config: every published byte equals the enabled-but-nothing-posted
  output and no meta key is written.
- Rerun idempotency: same run key twice → identical `_redirects` bytes, no new
  rows, no sends (extend the existing idempotency coverage, do not weaken it).
- Hub targets: a kitchen deal → `/deals/home-kitchen/`, an auto deal →
  `/deals/automotive/`, an unknown category → `/deals/other/` (or `/` if that is
  what the deal page links), matching the deal page's own hub link.

## Rules

Ship it enabled (it changes nothing until a posted deal is pruned). Do not change
tests/test_workflows.py. Run tests with -n 1 and your own basetemp
/tmp/task231-*; delete it after each run. Do not run the whole suite. Do not commit.
Update docs/HANDOFF.md "In flight" with one short entry.
