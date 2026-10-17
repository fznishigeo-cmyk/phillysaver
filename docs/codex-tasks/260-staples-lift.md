# 260 — Homepage: a soft lift for everyday staples on real sale (ships ON)

## Why

Operator 2026-10-17: the first screens should subtly favor "no-brainer"
products people always need when they are on a real sale — laundry
detergent, trash bags, toilet paper, paper towels, dish soap, dishwasher pods,
toothpaste, hand soap, foil, zip bags, body wash, shampoo, conditioner,
deodorant, tissues, disinfecting wipes, all-purpose cleaner, sponges, toilet
bowl cleaner, plastic wrap, parchment paper, stain remover, razors, floss,
pads/tampons, AA/AAA batteries, coffee, cooking oil, rice/pasta, canned
goods — plus kids' staples (diapers, baby wipes, formula, kids' toothpaste,
kids' snacks, pull-ups). "It's all about subtlety": never a filter, never
the whole page.

## Task

1. `dealradar/staples.py`: a curated keyword table (data, not code: put it in
   `config.yaml` under `publish.front_page.staples.groups`, each group a list
   of whole-word/phrase patterns, e.g. laundry: ["laundry detergent",
   "detergent pods", "tide pods", "gain flings", ...]; exclusions per group to
   avoid false hits ("trash can" is not "trash bags", "coffee maker"/"coffee
   table" are not coffee, "toilet paper holder" is not toilet paper, "rice
   cooker" is not rice). Matching on the normalized item title (+ brand) with
   word boundaries; pure function `staple_group(deal) -> str | ""`.
2. `rank.staples_factor(deal, options)` → `weight` (default 1.12) when the
   deal matches a group, is NOT used/open-box/refurbished, and its
   `final_price` ≤ `max_price` (default 60); else 1.0. `pick_score`
   multiplies by it after the appeal/everyday-price blocks when
   `staples.enabled`. Public `ps` may include it (titles are public).
3. Share cap in `top_picks` like the popular_affordable cap: at most
   `max_per_window` (default 4) staple-boosted picks per 24-card window; an
   overflow staple is re-queued at its unboosted score (never dropped), and at
   most `max_per_group` (default 1) per group per window so the page doesn't
   show three detergents. Existing caps unchanged.
4. Private summary (meta ring + growth-report section, NEVER public logs —
   see task 255b): staples matched among candidates, in the first 24
   without/with, groups entered.
5. Config (`publish.front_page.staples`): `enabled: true`, `weight`,
   `max_price`, `max_per_window`, `max_per_group`, `groups`. Validated;
   missing section = off; omitted from public `meta.front_page`.

## Tests (offline, tests/test_staples.py)

Positive/negative matching table (≥ 40 titles incl. the exclusions above);
factor gates; caps per window and per group; overflow re-queued; OFF (enabled
false) = today's bytes and order; recorded byte-pin fixtures pin
`staples.enabled: false` ("recorded before task 260") — never re-pin a hash;
no staple data in public logs; rerun idempotency.

## Rules

Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task260-*` basetemp
(delete after), targeted tests (test_staples, test_front_page, test_appeal,
test_popular_affordable, test_e2e_offline -k rerun, plus every file you pin),
never the full suite. No network, no commit, no push. HANDOFF "In flight":
one short entry. Tasks 255/255b (popular boost, everyday-price tilt) are
merged in this tree; compose with them, don't change their behavior.
