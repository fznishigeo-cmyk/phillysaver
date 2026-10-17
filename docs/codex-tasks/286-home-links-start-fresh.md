# 286 — Home links start the homepage fresh (search, filters, tab, sort reset)

## Why
Operator 2026-10-17 (Facebook ad starts 9pm): "I search hijab, nothing there ...
when I click the W, wowpriced.com ... it doesn't take me back to the homepage. It
keeps taking me here until I click Clear search. That's confusing to people ...
if I click the W or LIVE DEALS at the top, it doesn't take me back. Next drop in
3:15 should be clickable and take you back to the home."
Cause: browse memory (templates/browse.js `initBrowse`) restores the last
remembered snapshot (`browseMemory.entries[browseMemory.last]`, query included)
on every load of the homepage, and the logo is a plain link to the same page.

## Task (config `site.home_link_resets`, bool, missing/false = today's bytes exactly; ship true)
1. On the homepage, a plain click (no modifier keys / middle button) on the logo
   link (`h1.brand a`), the ticker's "Live deals" label (`.tklabel`) or the drop
   timer (`#drop`) resets browsing in place: search box empty (and its ✕ hidden),
   store "", min % 0, price filters cleared (`clearEligibility()`), category tab 0,
   saved-deals view off, sort back to top picks (`top:-1`, stored like the sort
   select does), wall re-rendered from the top, page scrolled to the top, and a
   new browse history entry pushed (Back returns to the previous search). Before
   deals have loaded, the logo just follows its link. Model it on the existing
   `filterclear` handler and `selectAmazonDeals()`; reuse them, no new ordering.
   The label and timer get `cursor:pointer` and a `title` "Back to all deals";
   the logo stays the keyboard/screen-reader path (no new focus stops).
2. A homepage load whose navigation type is a plain "navigate" (typed URL, a
   home link from a deal/category/store page, "Back to deals") starts fresh
   instead of restoring `browseMemory.last`; back_forward and reload keep today's
   exact restore (task 237: Back lands on the same card). Use the existing
   `navigationMethod()`.
3. Analytics: one `home_reset` event (method: logo | ticker | timer) through the
   existing `track()`; no query text.

## Tests
- Real Chromium (exit 77 skip only if Chromium cannot launch): search "hijab"
  (no matches) → click logo → search box empty, Trending tab, cards visible,
  scrolled to top; same for the ticker label and the timer; browser Back returns
  to the "hijab" state; reload keeps the current state; navigating to "/" from a
  deal page link starts fresh; Back from a deal page still restores the exact card.
- OFF (missing knob) = today's bytes exactly (recorded pins; never re-pin).
- Phone budgets pass; idempotent rerun.

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task286-*` basetemp (delete
after each run), targeted tests only. No network, no commit, no push. HANDOFF
"In flight": one entry. Ship ON. Keep the diff small.
