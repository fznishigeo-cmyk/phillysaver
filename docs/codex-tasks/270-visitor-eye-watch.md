# 270 — Visitor's-eye watch every 10 minutes (real Chromium, our own site)

## Why
Operator 2026-10-17 ~03:25Z: "why can't you use a headless browser on the site
every 15 minutes and monitor things like these?" (the same Christmas tree sat
first for 45 minutes; nothing flagged it). The hourly site check
(.github/workflows/site-check.yml, scripts/site_check.js, :41 every hour)
opens wowpriced.com in Chromium but checks each run on its own and never looks
at what the first screen shows over time. The repo is public, so Actions
minutes are free. (CLAUDE.md's "no headless browsers" is about crawling
SOURCES; this is our own site.)

Operator decision 2026-10-17: run the watch every 10 minutes; this supersedes
the original 15-minute cadence requested above.

## Task
1. New workflow `.github/workflows/visitor-watch.yml`, cron `*/10 * * * *`
   (offset from site-check), concurrency group (never overlap), timeout 6 min,
   read-only permissions plus `actions: read` to fetch the previous run's
   snapshot artifact. Reuse site-check's Playwright install/caching.
2. `scripts/visitor_watch.js` (+ pure logic in `scripts/visitor_watch_logic.cjs`
   with node tests): Chromium at 390×844 (isMobile, hasTouch) and 1280×800,
   light and dark color scheme; load https://wowpriced.com/ like a visitor;
   record the first 24 cards (title, price, store, has photo, Get deal href
   host + target) and the drop countdown; save a JSON snapshot artifact.
3. Checks (each a line "name: ok" or "name: FLAG …" in the step summary):
   - top_rotation: first 6 cards identical to the previous snapshot taken
     after a drop mark has passed → FLAG (after task 269 ships; before it,
     report only);
   - price_mix: count ≤ $50 in the first 24 (goal ≥ 19) and > $100 (goal ≤ 1);
   - impulse: titles in the first 24 matching the not-impulse list from task
     267's config (`publish.front_page.impulse.utility_groups`, read from
     config.yaml) → FLAG with the titles;
   - photos: any of the first 24 without a visible loaded photo;
   - errors: console errors / page errors / failed same-origin requests;
   - freshness: deals.json publish time older than 40 minutes;
   - search: placeholder text and the Search Amazon button (no-match query
     "zzqx cupping test") has target _blank and the tagged href on click;
   - dark: no large bright area on the first screen in dark mode (reuse the
     glare measure from tests/dark_mode_browser.cjs).
4. Exit non-zero only when a FLAG repeats in 2 consecutive runs (one-off
   blips don't page anyone); write "### Visitor watch" with flags first.
5. Never click Get deal / affiliate links (no fake clicks or affiliate hits);
   set a UA marking the watcher and exclude it from analytics (the same way
   site-check avoids GA/Meta/Clarity — reuse its approach).

## Tests
Node logic tests for every check (fixtures of snapshots), the 2-in-a-row rule,
and summary formatting; a pytest that runs the script against a local static
build of the site (offline, route-fulfilled like tests/dark_mode_browser.cjs;
Chromium at /opt/pw-browsers/chromium, exit 77 = skip).

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task270-*` basetemp
(delete after each run), targeted tests only, never the full suite. No
network, no commit, no push. HANDOFF "In flight": one entry.
