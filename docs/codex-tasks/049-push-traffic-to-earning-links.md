# Codex task 049: the traffic we push goes to links that earn

**Read first:** AGENTS.md, CLAUDE.md (politeness is law: robots.txt always,
1 req/s/host; knobs live in config.yaml; never fake a link). Files:
`dealradar/publish/site.py` (`link_mix`, `_serialize_deal`, how a deal's
public `url` is chosen, `rewrite_affiliate`, Sovrn wrapping),
`dealradar/rank.py` (`pick_score`, `top_picks`), `dealradar/publish/social.py`
(`_candidates`, used by Pinterest, Facebook/Instagram and Telegram),
`dealradar/pipeline.py` (`_resolve_short_links`, `_SHORT_LINK_HOSTS`,
`resolve_redirects` use, the `link_mix` summary), `dealradar/http_client.py`
(`resolve_redirects`), `config.yaml` (`publish.front_page.top_picks`,
`social`), tests.

Live numbers (fast run 2026-10-12 23:30 UTC, 2,808 published): Get deal goes
to Amazon with our tag 1,943, Sovrn 81, **the source post we found it on
529**, **other stores 34** of which 29 are competitors' click trackers
(`cc.dealsofamerica.com` 20, `go.dealmoon.com` 9) plus `slickdeals.net`,
`links.southernsavers.com`. A source-post or competitor-tracker link earns us
nothing (a tracker earns the competitor). We now push traffic (top picks on
the home page, Pinterest, Facebook, Instagram, Telegram), so that traffic
should land on deals that earn.

## 1. One definition: does this deal's Get deal link earn?
A helper in site.py (reuse `link_mix`'s bucket logic, one place, no copy):
earning = `amazon_tagged`, `amazon_short` with our tag if detectable else
not, `sovrn`, or a store link our affiliate rewrite tags; not earning =
`source_post`, a competitor tracker host (list in config:
`affiliate.competitor_tracker_hosts: [cc.dealsofamerica.com, go.dealmoon.com,
slickdeals.net, links.southernsavers.com]`), `none`, untagged Amazon. Expose
it on the serialized deal as `"earns": true|false` in deals.json (public:
fine, no secret).

## 2. Ranking and social
- `pick_score`: multiply by `top_picks.no_earn_factor: 0.5` (config) when
  the deal does not earn (keep them on the wall; rank them lower).
- `social._candidates`: skip deals that do not earn when
  `social.require_earning_link: true` (config, default true), so Pinterest,
  Facebook, Instagram and the Telegram channel only send people to deals
  that earn.

## 3. Try once to replace competitor trackers with the store link
In the existing `_resolve_short_links` pass (or alongside it, same per-run
cap and deadline), also try deals whose link host is in
`competitor_tracker_hosts`: `resolve_redirects` once per deal (it already
honors robots.txt, stops at bot walls and never fetches retailer pages it can
canonicalize). If it ends on a real store URL (not the tracker, not a bot
wall), store it as the product link (keep the original on the sighting for
credit) so the normal affiliate rewrite applies; otherwise flag
`link_resolve_tried` and leave the deal as is. Never bypass robots.txt; a
robots-blocked tracker simply stays unresolved.

## 4. Visibility
Run summary `link_mix` gains `earning` and `not_earning` totals and
`trackers_resolved` this run; one log line with counts only.

## 5. Tests
`earns` for each bucket (incl. tracker hosts from config); pick ordering
with the factor (an earning deal outranks an equal non-earning one); social
candidates skip non-earning deals and include them when the knob is false;
tracker resolution: resolved → store URL + affiliate rewrite, robots-blocked
→ unchanged and flagged, capped per run, idempotent across re-runs
(`test_rerun_is_idempotent` passes); byte-hash baselines updated from the
new output. One short HANDOFF bullet under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
