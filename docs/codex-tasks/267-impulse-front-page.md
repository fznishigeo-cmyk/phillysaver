# 267 — Front page = impulse buys: price share cap + "not an impulse buy" demotion

## Why
Operator 2026-10-17 ~03:00Z: "nothing over 50 bucks for the majority … who
the hell is going to buy a water reducing valve as an impulse buy? That can
be something people search." The everyday-price tilt (full ≤ $40 → 0.55 at
$100+) is live; appeal favors home/kitchen/beauty/fashion; staples lift (260)
is being fixed. Two gaps remain:

1. **Price share cap** in `top_picks` (same overflow mechanism as 260 after
   its review fix: overflow re-queued at its exact baseline score AND original
   ordinal, never dropped): per 24-card window at most `max_over_50: 5` picks
   with final_price > 50 and at most `max_over_100: 1` with final_price > 100.
2. **Not-impulse demotion**: `publish.front_page.impulse.utility_groups`
   (data in config.yaml, like 260's groups): whole-word phrases for
   plumbing/hardware/utility/replacement parts — pressure reducing valve,
   valve, pipe fitting, PVC, ball valve, shutoff, check valve, faucet cartridge,
   water heater element/anode, toilet fill valve/flapper, breaker, outlet
   receptacle, conduit, wire nut, gasket, o-ring, HVAC/furnace filter (by
   size, e.g. 16x25x1), refrigerator water filter replacement, replacement
   blade/belt/brush roll for a named model, sump pump, well pump, thermostat
   wire, drain snake, caulk, grout, drywall, lumber, hinge, drawer slide, etc.
   — with exclusions so impulse-friendly home items keep their appeal
   (shower head, faucet = keep; "water bottle" ≠ water valve). Factor
   `utility_factor: 0.5` on the landing pick score only; the wall, search,
   category tabs and deal pages are unchanged.
3. Private summary (meta ring + growth report; never public logs) of how the
   first 24 changed: count over $50, over $100, utility items.

Config: `publish.front_page.impulse: {enabled: true, max_over_50: 5,
max_over_100: 1, utility_factor: 0.5, utility_groups: …}` validated.

## Tests (tests/test_impulse_front_page.py)
Matching table ≥ 40 titles (positives + exclusions); caps per window with
equal-score ties (no hidden boost; cap 0 = unboosted order); composes with
popular_affordable, staples and everyday_price caps; OFF = today's order and
bytes; recorded byte pins keep their hashes (pin the switch off, "recorded
before task 267"; never re-pin); rerun idempotency; private summary not in
public logs.

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task267-*` basetemp
(delete after each run), targeted tests only, never the full suite. No
network, no commit, no push. HANDOFF "In flight": one entry. Base includes
255/255b and the 260 fix if present; if 260 is absent, implement the overflow
helper so 260 can reuse it.

## Addendum (operator 2026-10-17 ~03:45Z): "WHO THE HELL IS GOING TO BUY THIS — WHY IS THIS ON THE FRONT PAGE?"
An OPEN BOX "Amerock Champagne Bronze Drawer Pull" $9.19 showed on the homepage.
Picks exclude used/open-box/refurbished, but after the 120 picks the homepage
wall (templates/index.html topOrder: `rest` sorted by first_seen_at, newest
first) shows everything unfiltered. Fix as part of this task:
4. Below the picks, the front-page view orders `rest` by the same landing
   score (server-provided rank or a public, privacy-safe order field — never
   the private popularity factor) with the impulse rules applied, not newest
   first. Used / open-box / refurbished / renewed items and utility-group
   matches go after all new impulse-eligible items on the All view (they stay
   fully visible in the Used tab, category tabs and search). Keep the
   phone budgets; keep Back restore exact (237).
5. Extend the impulse utility groups with home hardware: drawer pull, cabinet
   pull, knob (cabinet/drawer), hinge, drawer slide, door stop, wall plate,
   switch plate, outlet cover, anchor, screw, bolt, bracket, shelf bracket,
   furniture leg, caster wheel — with exclusions for decor items people do
   impulse-buy (e.g. "wall art", "picture frame", "mirror", "lamp").
Tests: the open-box drawer pull lands after every new impulse item on the All
view; still visible in the Used tab and search.
6. (operator ~03:50Z: "1/400 people have this phone — why would this be on the
   home page?") Named-device accessories (rank.niche_reason: cases, screen
   protectors, bands for one phone/watch/tablet model, camera-system and
   vehicle-specific parts) join the same "after all impulse items" group on the
   All view as used/open-box and utility items. Test: "OtterBox Samsung Galaxy
   S24 FE React Series Case" and "OtterBox Profile Series Case for Samsung
   Galaxy S25 Edge" land after every impulse item on the All view.
