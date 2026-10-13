# Codex task 061: "Today only" spotlight for daily-deal sites (Meh first)

**Read first:** AGENTS.md, CLAUDE.md (evidence, math in code, knobs in
config.yaml, surgical changes). Files: `dealradar/rank.py` (`pick_score`,
`top_picks`), `dealradar/publish/site.py` (`_serialize_deal`, front-page
payload), `dealradar/publish/templates/index.html` (front page JS/CSS, phone
blocks), `dealradar/publish/seo.py` (`card_html`), `config.yaml`
(`front_page:`), `sources.yaml` (meh, sidedeal, morningsave),
`dealradar/fetch/meh.py`, tests `tests/test_front_page.py`,
`tests/test_support.py` (byte-hash baselines).

## Why
Operator (2026-10-13): "Meh deals are extremely good. Prioritize it to really
catch people's attention, subtly; it shouldn't be lost in the 1000s of deals."
Meh, SideDeal and MorningSave post one deal a day that ends at a known time.
Today such a deal competes with ~3,000 others and its link earns nothing
(`no_earn_factor` halves its pick score), so it never surfaces.

## Do
1. Config knob `front_page.spotlight` with `sources: [meh, sidedeal,
   morningsave]` and `max_cards: 1`. Live fact (2026-10-13): Meh's API gives
   the price but NO regular price, so its deal ("2-Pack: Aukey MixFlex Sling
   30W PD 10,000mAh Power Banks", final price evidence-verified) fails the
   publish floor and is never shown. A spotlight deal qualifies when it is
   alive, from a listed source, seen within its day, has a photo, has an
   evidence-verified final price, and passes every `rank.is_publishable`
   gate EXCEPT the % floor (add a narrow, named exception for spotlight
   sources only; do not loosen anything else). When pct_at_checkout is
   known and >= `publish.min_pct`, show it as today; when it is unknown,
   the card shows the price and "Today only" with NO percent, NO "save $",
   NO strikethrough, and NO invented regular price. If several qualify,
   known-pct deals rank first (highest pct), then newest.
2. Front page: render the spotlight card ABOVE the normal grid on every tab
   where it qualifies (All/Trending), visually a normal card plus a subtle
   accent: a small "Today only" ribbon, the source name ("Meh"), and a
   countdown to the deal's end when the source gives one (Meh: the deal ends
   at the next midnight US Central; compute the end in code, show
   "ends in 5h 12m", hide when unknown). It must not be duplicated in the
   grid below. On phones it spans the full row (2 columns) but keeps the
   same card height rules as today. Respect prefers-reduced-motion (no
   animation), focus rings, ARIA (role/label "Today only deal").
3. Static HTML (seo.card_html / STATIC_WALL) gets the same card first so
   no-JS readers and search engines see it.
4. Never change prices, evidence, tiers or link rewriting; the spotlight is
   placement only. A spotlight deal without a known pct appears ONLY in the
   spotlight (not in the grid, alerts, social posts, deal pages' "% off"
   copy or structured data offers with a discount). Keep the drop rotation
   (DROP_WINDOW) for the grid.

## Tests
Selection (qualifies / not alive / no photo / two candidates / unknown pct shows no % / known pct below floor still excluded / non-spotlight source without pct still excluded everywhere),
end-time computation for Meh (DST boundary), not duplicated in the grid,
phone CSS spans 2 columns, reduced-motion rule, static HTML has it first,
desktop unchanged when no spotlight. Byte-hash baselines updated from new
output only. One short HANDOFF bullet.

Run `.venv/bin/python -m pytest -q`; all green except tests needing local
sockets your sandbox blocks (name them). Do NOT commit or push. End with what
changed and anything unsure.
