# 269 — The top of the homepage changes at every drop again (ships ON)

## Why
Operator 2026-10-17 ~03:20Z: "I've seen this Christmas tree for almost 45
minutes, what's going on?" Root cause (templates/index.html topOrder): with
`checkout_price_deals.enabled: true` the gated block
`/*checkout-price*/ return picks.concat(rest); /*/checkout-price*/` returns
the server's pick order unchanged ("keep the server's sliding-window spacing
through browser adoption and drops"), so `rotatePicks` (operator 2026-10-13:
"a drop must look like new deals landing, not a reshuffle") never runs. The
first card only changes when the server's ranking changes.

## Task
1. Restore per-drop rotation on the checkout-price path WITHOUT breaking the
   server's spacing guarantees: read how the server spaces picks (site.py /
   rank.top_picks: per-24-card-window caps — same store/brand, boosted shares
   of tasks 255/260/267, checkout-price labels) and rotate only by whole
   aligned windows of 24 so every window keeps its server-built composition.
   Bound the rotation to the strongest windows: `site.drop_rotation_windows`
   (default 3 → the first screen cycles through picks 1–24, 25–48, 49–72 one
   drop at a time), so the top always shows strong impulse picks and never
   page 5. If the server spacing is a sliding window (any 24 consecutive),
   make sure the junction after rotation still satisfies it (re-check in JS
   with the same rule, or have the server emit window ids and rotate by them).
2. Picks that are new since the last drop still lead — but only by moving
   them to the front WITHIN the first displayed window, never in a way that
   breaks the window's caps.
3. Same visitor, same drop → same order (refresh shows the same top within a
   drop window; seeded by the drop mark as today). Back restore (237),
   saved deals, search, category tabs and sort options unchanged.
4. OFF path: `drop_rotation_windows: 0` = today's bytes and behavior.

## Tests
Logic tests (existing *_logic.cjs style): rotation per drop seed across the
first N windows, caps preserved in every displayed window (store/brand/boost
counts), new picks lead within window 1, same seed same order, 0 = current
order. Browser test (pytest wrapper, Chromium at /opt/pw-browsers/chromium,
exit 77 = skip): advance the fake clock across two drops at 390×844 — the first
card changes at each drop and returns after N drops; no layout shift; no
console errors. Recorded byte pins keep their hashes (pin the knob to 0,
"recorded before task 269"; never re-pin).

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task269-*` basetemp
(delete after each run), targeted tests only, never the full suite. No
network, no commit, no push. HANDOFF "In flight": one entry.
