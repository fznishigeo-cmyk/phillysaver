# Codex task 024: count where every "Get deal" link goes (who earns the click)

**Read first:** AGENTS.md, CLAUDE.md (counts only in public logs; never
weaken evidence or politeness). Files: `dealradar/publish/site.py`
(`_serialize_deal`: how `url` is chosen: tagged Amazon via
`rewrite_affiliate`, the store's canonical link, or the source post for
deals that need steps), `dealradar/pipeline.py` (the run summary),
`dealradar/growth/report.py` + `site_health.py` (the morning report reads
the live deals.json), tests.

Operator (2026-10-12): affiliate commission is the income; Amazon tag
`wowpriced-20` is live. Next decisions (join an all-store network such as
Sovrn/Skimlinks, direct Walmart/Target programs, sending code deals to the
store instead of the source post) need numbers: of the live public deals,
where does each Get deal link point?

## 1. One pure function `link_mix(deals) -> dict`
In `dealradar/publish/site.py` (or a small new module), over the
serialized public live deals:
- `amazon_tagged`: https amazon.com/www/smile host with our `tag`;
- `amazon_untagged`: Amazon host without it (should be 0 once the tag is
  set: flag it);
- `amazon_short`: amzn.to / a.co / link.amazon (cannot carry our tag);
- `source_post`: the deal links its source's post (needs steps);
- `other_store`: any other retailer link; plus `by_store`: top 10 store
  hosts (folded: www. stripped) with counts;
- `none`: no link.
Totals add up to the number of live public deals.

## 2. Where it shows
- Fast/hourly run summary: `link_mix` (counts only) next to `published`.
- Morning report "== Money ==" block: one line "Get deal links: N Amazon
  (tagged), N other stores, N source posts, N none" and the top 5 other
  store hosts, read from the live deals.json the report already fetches
  (site_health); a failure is one "not available" line.

## 3. Tests
Each bucket, host folding, totals, the untagged flag, the summary key, the
report lines (fake deals.json). Offline only. One short bullet in
docs/HANDOFF.md under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
