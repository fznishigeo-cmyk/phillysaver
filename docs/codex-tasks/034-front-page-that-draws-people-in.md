# Codex task 034: a front page that draws people in

**Read first:** AGENTS.md, CLAUDE.md (knobs live in config.yaml). Files:
`dealradar/rank.py` (scores, `site_deals`), `dealradar/publish/site.py`
(the order deals.json carries), `dealradar/publish/templates/index.html`
(`ordered()`, the "fresh drops" default sort, the shuffle on each drop),
`dealradar/publish/seo.py` (static wall, best-today), tests.

Operator (2026-10-12): "people don't want to see a bunch of random used car
parts at the top. Those are useful, but they belong under Automotive.
Prioritize strong deals that draw people in, intelligently, even when it
shuffles." Most online shoppers browse everyday categories: home, kitchen,
beauty, fashion, health, kids & baby, toys, electronics.

## 1. "All" tab front page mix (the first 24 cards; deeper rows unchanged)
- Config `publish.front_page`:
  - `broad_categories: [home, kitchen, beauty, fashion, health, grocery,
    kids_baby, toys_games, electronics, computers, gaming, pets,
    outdoors, sports]` (everyday appeal),
  - `niche_categories: [auto, office, books_media, other]`,
  - `max_niche_top: 2` (at most 2 niche-category cards in the first 24),
  - `max_same_category_run: 2` (no more than 2 cards of the same category
    in a row in the first 24), `max_same_store_top: 8` (no single store
    takes more than 8 of the first 24).
  - `prefer_photos: true` (cards with a photo first within the top 24, as
    today).
- Applies only to the "All" tab, default sort ("fresh drops"), no search
  and no filters. Category tabs, other sorts, search and filters stay
  exactly as today. The free/locked rotation and the drop shuffle keep
  working (the mix is applied after the shuffle, deterministically from
  the same seed, so a drop never reshuffles twice).
- Niche deals are not hidden: they appear lower on All, and fully in
  their own tab.
- The static server-rendered wall (`seo.static_wall`) and
  `/best-deals-today/` use the same mix.

## 2. Tests
Top-24 caps (niche ≤ 2, runs ≤ 2, store ≤ 8, photos first), nothing
removed (same set of cards), untouched outside the All/default view,
deterministic for the same seed, config switches; static wall and
best-today follow the mix. Do not loosen other tests. One short bullet in
docs/HANDOFF.md under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
