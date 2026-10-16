# 237 — Back lands exactly; then phones skip drawing off-screen cards

## Why

Home page phone speed (Lighthouse mobile, live) is 79; every other page scores
98–100. On 2026-10-16 a local experiment put `content-visibility: auto` on the
wall's deal cards: Lighthouse performance median 95 vs 85 and Total Blocking
Time 151 ms vs 408 ms (5 alternating runs). It was held because Back/reload
position restore then landed 207 px off on a phone (exact without it), and
placeholder heights were 382 px vs the real 375 px.

There is also a pre-existing desktop bug on the live code: on reload/Back
without the back-forward cache, `restoreBrowsePosition`
(`dealradar/publish/templates/browse.js` ~line 172) asks `scrollTo(3905)`, the
page lands at 3457 (−448 px) and stays; the anchor card ends 494 px down. A
later `repaintSaved` re-render (`engagement.js` ~line 495) runs ~100 ms later
and moves layout under the restored position. Phones are exact today.

Study (Redis `dictRehashMicroseconds`): do the work in steps and re-check
between steps. Here: after scrolling, re-measure the anchor on the next frames
and correct, instead of trusting one measurement taken before layout settles.

## Read first

CLAUDE.md, AGENTS.md, docs/HANDOFF.md. `templates/browse.js` (snapshot,
anchor key/index/offset, `restoreBrowsePosition`, `restoreInitialBrowse`,
`restoreBrowseOnReturn`, popstate), `templates/engagement.js`
(`repaintSaved`), `templates/support.js` (`refresh` → `refreshNow`, one
requestAnimationFrame), `templates/index.html` (wall render, card markup and
CSS, the first 4 photos in the static wall), `tests/browse_logic.cjs` and the
Python tests that run it, the mobile-audit and Lighthouse workflows/scripts
(`scripts/lighthouse_summary.js`, LH_MIN 85, phone budgets from task 206).

## Task

1. Exact restore: after the scroll in `restoreBrowsePosition`, re-check the
   anchor card's top against the saved offset on the next animation frames
   (at most ~6 frames or ~500 ms, and once more after a pending
   `repaintSaved`/refresh redraw), correcting by the difference when it is
   more than 2 px. Stop early when stable; stop immediately on any user
   scroll, touch, wheel or key input (never fight the user). No change to what
   is saved, the history API use, or `back_restore` tracking (one event).
2. Then enable `content-visibility: auto` on wall deal cards only (not the
   first screen's cards: leave the first 8 cards, i.e. the static-wall photos
   and above-the-fold rows, fully rendered), with `contain-intrinsic-size`
   using `auto` plus a size measured from the real card at each breakpoint
   (so the remembered size replaces the placeholder after first render). One
   config switch `site.offscreen_cards: true|false` (validated bool; shipped
   true only if the checks below pass, otherwise false) that adds or omits
   the CSS rule; false = byte-identical to today.
3. No other visual change: same card markup, spacing, photos, order, filters,
   saved deals, search, tap targets.

## Tests and checks

- `tests/browse_logic.cjs` harness: restore converges to the anchor within
  the frame budget when layout shifts after the first scroll (simulate a
  −448 px shift and a +7 px per-card height change); a user scroll during
  correction cancels it; exactly one `back_restore` event.
- Byte pins: switch false → identical published bytes; switch true → only the
  CSS rule differs (update pins deliberately, list them).
- If Chromium is usable in your sandbox (`/opt/pw-browsers`, Playwright), add
  an offline check that serves the built site, scrolls to card 120 at 390×844
  and 1280×800, reloads, and asserts the anchor is within 4 px. Do not
  download browsers. If not usable, say so; I will run the browser check.

## Rules

Do not change tests/test_workflows.py. Run tests with -n 1 and your own
basetemp /tmp/task237-*; delete it after each run. Do not run the whole suite.
Do not commit. Update docs/HANDOFF.md "In flight" with one short entry.
