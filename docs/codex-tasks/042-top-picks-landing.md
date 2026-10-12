# Codex task 042: the landing page shows our best 120 deals ("top picks")

**Read first:** AGENTS.md, CLAUDE.md (knobs live in config.yaml; math is
code). Files: `dealradar/rank.py` (`score`, `front_page_mix`),
`dealradar/publish/site.py` (`_serialize_deal`, `render_site`, meta),
`dealradar/publish/seo.py` (`static_wall`, best-deals-today, the
front-page `rows` near line 360), `dealradar/publish/templates/index.html`
(`#sortsel`, `freshOrder`, `ordered`, `frontPageView`, `frontPageMix`,
the drop reshuffle near `state.seed = clock.lastSeed`, `AUTO_CARDS`),
`config.yaml` (`publish.front_page`, `rank`), tests
(`tests/front_page_logic.cjs`, `tests/test_front_page.py`).

Operator (2026-10-12): "under All, make sure those are the top deals ...
the top 120 deals you can muster ... that will suck people in and want
them to continue to explore." Today the All tab is newest-first with a
little jitter, so a mediocre fresh deal beats a great one from this
morning.

## 1. The pick score (Python, one place)
`rank.pick_score(deal, cfg, now) -> float | None`, from the deal row
(`verify_tier`, `brand`, `price_is_range`, `codes`, `stack_steps`, ...;
the serialized deal carries the tier as `badge`), knobs under `publish.front_page.top_picks` in config.yaml:

```yaml
top_picks:                    # the All tab's first cards (operator, 2026-10-12)
  count: 120
  pct_weight: 1.0             # pct_at_checkout counts as is (40-90)
  dollars_weight: 12.0        # + weight x log10(1 + abs_savings)
  corroborated_bonus: 8       # verify_tier corroborated (2+ sources)
  history_bonus: 6            # verify_tier history-checked
  known_store_bonus: 5        # store in known_stores (case-insensitive)
  brand_bonus: 3              # the deal names a brand
  fresh_half_life_h: 36       # quality x 0.5 ** (age_h / half_life)
  used_factor: 0.9            # used / open_box
  cheap_factor: 0.8           # final_price under cheap_below
  cheap_below: 5.0
  steps_factor: 0.8           # needs_steps, except a simple code deal (task 040)
  max_same_store: 40          # of the count
  max_same_brand: 3
  max_same_category: 24
  max_title_twins: 2          # same first 3 normalized title words
  known_stores: [amazon, walmart, target, best buy, home depot, lowe's,
                 costco, kohl's, macy's, nike, adidas, woot, ebay, sephora,
                 ulta, gap, old navy, wayfair, dell, lenovo, samsung, apple,
                 newegg, b&h photo, dick's sporting goods, rei, chewy, petco,
                 lego, walgreens, cvs, staples, nordstrom, zappos]
```

score = (pct_weight x pct + dollars_weight x log10(1 + abs_savings) +
bonuses) x factors x freshness. Not eligible (score None): no https
photo, dead, locked, price_is_range, a niche or "other" category
(`front_page.niche_categories`), or any of the conditions `targeted`,
`new_customer`, `in_store_only`, `ymmv`, `clearance_aisle`.

## 2. The 120 (Python), published
- In `render_site`, take eligible live deals by score, best first, and
  pick greedily up to `count` while honouring the max_same_* caps (a deal
  that would break a cap is skipped, not reordered later). The first 8
  must also respect the existing `front_page` run rule
  (`max_same_category_run`).
- Each picked deal gets `"pick": n` (1..count) in deals.json; others get
  no `pick` key. Every live deal also gets `"ps"`: its score rounded to
  one decimal (or no key when not eligible).
- The no-JS home wall (`static_wall`), the front-page rows near seo.py:360
  and /best-deals-today/ use the pick order (then today's order for the
  rest). Equal input gives equal output (no randomness in Python).

## 3. The All tab (index.html)
- `#sortsel`: first option `top:-1` "sort: top picks" (the default);
  "fresh drops" stays as the second option with today's behaviour.
- Default view on All (no search, no store, min % 0): the picked deals in
  `pick` order, re-dealt at every drop by shuffling only within blocks of
  12 with the existing drop seed (`jitter`), so the first screen changes
  each drop but always shows the top 12. Deals that arrived since the last
  drop and have a `pick` lead their block (the "NEW" tag stays). Then
  every other live deal in today's fresh order. Gone cards keep today's
  spacing rule. `AUTO_CARDS` (120) stays: auto-load stops after the picks.
- Category tabs, search and filters under "top picks": order by `ps`
  (deals without `ps` after, in fresh order), same block re-deal.
  Endless scroll on category tabs is unchanged.
- Everything that today keys on `sortKey === "fresh"` as the default
  (front-page view, drop reshuffle, "Fresh drop" toast, new-arrivals
  first) works the same for "top"; "fresh drops" keeps working as today.
- `frontPageMix` is not applied over the picks (Python already spread
  them); keep it for "fresh drops".

## 4. Tests
pick_score maths and every exclusion; caps (store, brand, category,
title twins) with a skewed catalogue; `pick`/`ps` in deals.json and
absent on locked stubs; static wall and best-today follow pick order;
JS: default sort is top picks, first 12 always picks 1-12 in some order,
block re-deal changes with the seed and is stable for one seed, new
arrivals lead their block, category tab orders by ps, "fresh drops"
unchanged, auto-load stops at 120 on All. Byte-hash baselines in
tests/test_support.py are updated from the new output. One short HANDOFF
bullet under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
