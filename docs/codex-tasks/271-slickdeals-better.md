# 271 — Homepage ideas from Slickdeals, done better (each behind its own switch)

## Why
Operator 2026-10-17 ~04:00Z pasted Slickdeals' front page: "GET INSPIRATION
FROM THEM.. BUT DO IT BETTER!!!!" What their front page does: mostly cheap
everyday items ($2–$30: batteries, body wash, dish soap, Lysol, snacks, LEGO,
women's Walmart apparel, kids' items, chargers), "New · Today 10:43 PM"
timestamps, vote/comment counts, a "Just for you" carousel, "Personalized"
cards mixed into the feed, "Deals You May Have Missed", expired deals folded
away ("17 More Expired Deals"), trending stores/categories. What we do better
already: every price verified with a time, price history, photos, no paid
placements. Build these, phone-first, accessible, dark-mode-correct, within
the phone budgets (never raise a limit), each with its own switch in
`publish.front_page.sections` (validated; missing = off).

## Task
1. **New badge** (`new_badge`), YouTube-style (operator 2026-10-17: "subtly
   ... don't overdo it"): a small white "New" pill on the photo's bottom-left
   corner (the % badge owns top-left) for live cards first seen < 3 h,
   computed in the browser from first_seen_at; no relative time, no motion,
   no layout shift; never on more than 1 in 3 of the first 24 cards (oldest
   of the new lose it first); hidden once this visitor has clicked the card.
2. **Rows between the first grids** (`rows`): after card 12 and after card 36
   on the All view, one horizontal, swipeable, keyboard-reachable row each:
   "Under $10" (new, price-confirmed, ≤ $10, impulse rules from 267 apply),
   "Everyday essentials" (staple groups from 260 when present), "For the
   kids" (kids_baby/toys categories, ≤ $40). Max 10 cards per row, no
   duplicates of cards already shown above, hidden when < 4 qualify. Rows
   are not shown in search, tabs or Saved.
3. **Deals you may have missed** (`missed`): a row near the end of the
   picks: yesterday's top picks that are still live and re-confirmed today
   (same freshness rules as picks).
4. **Ended deals folded** (`ended_fold`): cards that ended today never sit
   among live cards; one line at the bottom "12 deals ended today — show"
   expands them (today they linger inline for `show_dead_hours`).
5. **Real popularity, not votes** (`grabbed`): "🔥 12 grabbed today" on a
   card only when first-party Get-deal clicks today ≥ 5 (from the existing
   click signal, dealradar/clicks.py; aggregated per deal, rounded down to a
   multiple of 5 above 20, never invented, never from Keepa monthlySold).
   Publish only the rounded count in deals.json; tests prove no counts below
   the threshold and no per-visitor data leave the server.
6. **Just for you, on the device** (`for_you`): after a visitor clicks Get
   deal on ≥ 2 cards in a category, 1 of every 6 cards after the first 12 is
   drawn from their top categories, labeled "For you". Stored only in
   localStorage (category counts, capped, 30-day expiry; cleared with the
   existing saved-deals/privacy controls); nothing sent anywhere; respects
   Global Privacy Control by staying off. Deterministic per drop.

## Tests
Logic tests per feature (*_logic.cjs style) incl. thresholds, no duplicates,
hidden rows when supply is thin, privacy (no new network requests, GPC),
dark mode; one browser test at 390×844 and 1280×800 driving a fixture site
through a drop: rows render, swipe/keyboard reachable, no layout shift, no
console errors; OFF switches = today's bytes (recorded pins pin them off,
"recorded before task 271"; never re-pin); phone budgets.

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task271-*` basetemp
(delete after each run), targeted tests only, never the full suite. No
network, no commit, no push. HANDOFF "In flight": one entry. Ship every
switch ON in config except `grabbed` and `for_you` (ON after review).
