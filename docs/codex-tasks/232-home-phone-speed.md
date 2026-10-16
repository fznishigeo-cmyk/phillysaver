# 232 — Home page phone speed: Lighthouse performance 70 → 90+, same page

## Why

The daily Lighthouse run (mobile, 2026-10-16) scored: home **70** (weakest audits:
Total Blocking Time, Largest Contentful Paint, First Contentful Paint), category
hub 99, deal page 98, best-deals-today 99. Accessibility and SEO are 100. The home
page is where visitors land; slow phones bounce and Google uses these vitals.
The other pages share the same CSS/fonts and score 98–99, so the gap is the
home page's own work: about 2,300 deals in `deals.json`, a 48-card static wall
(`seo._STATIC_WALL_MAX`), then the browser renders `PAGE_CARDS = 60` and grows to
`AUTO_CARDS = 300` on scroll (`dealradar/publish/templates/index.html` ~911-2152),
plus the ticker, drop clock and engagement scripts.

## Read first

CLAUDE.md, AGENTS.md, docs/HANDOFF.md, docs/codex-tasks/035-lighthouse-polish.md
(earlier polish and what it measured), .github/workflows/lighthouse.yml,
scripts/lighthouse_summary.js, scripts/publication_budget.py and
tests/test_phone_budget.py (task 206: gzip budgets), templates/index.html.

## Task

1. **Measure first, locally.** Build a realistic home page offline: ~2,300 public
   deals made with `tests.test_publish_site.make_deal` (mixed stores/categories,
   photos on ~90%, the shipped `config.yaml`), served by `python3 -m http.server`.
   Run Lighthouse 12.6.1 mobile (the workflow's flags; Chromium at
   `/opt/pw-browsers` or Playwright's `chromium.executablePath()`), 3 runs, median.
   Record performance, TBT, LCP, FCP, CLS and the top main-thread tasks
   (Lighthouse `mainthread-work-breakdown` / `bootup-time`). Write the numbers in
   docs/HANDOFF.md before changing anything.
2. **Fix the biggest causes, smallest change first.** Typical suspects, in the
   order the measurement says, not this list's order: parsing/sorting all deals
   before the first paint; rendering 60 cards in one task (render the first
   screen, then the rest in idle-time chunks that keep each task < 50 ms);
   synchronous work in the ticker/clock/engagement start-up; layout thrash
   (reading layout inside loops); the LCP image (fetchpriority/preload of the
   first card photo only); render-blocking CSS/JS in `<head>`.
3. **The visitor sees the same page.** Same cards in the same order, same 48-card
   static wall, same 60 then up to 300 on scroll, same filters, search, tabs,
   clock and ticker behaviour, same niche-last and photos-first ordering. No
   change to deals.json shape, prices, links, evidence or tracking. If a fix
   would change anything visible, do not ship it: describe it in HANDOFF with
   before/after numbers so the operator can decide.
4. Re-measure (3 runs, median) and record after-numbers. Target: home
   performance ≥ 90 with TBT < 200 ms on the Lighthouse mobile profile; stop at
   the first change set that gets there.

## Tests

Existing browser harnesses (front page order, search/filters, load more, ticker,
clock, engagement, lighthouse polish, phone budget) must pass unchanged in
meaning. Add focused tests for any new chunked rendering: the final DOM after
idle equals the old one-shot render for the same seed; first-screen cards appear
in the first task; no card is duplicated or skipped across chunks; reruns are
byte-identical. Re-pin published-byte tests only where the home template bytes
changed, and say which.

## Rules

Do not change tests/test_workflows.py. Run tests with -n 1 only (shared
machine), your own basetemp /tmp/task232-*, delete it after each run; do NOT run
the whole suite. No headless browser against retailer sites (local build only).
Do not commit. Update docs/HANDOFF.md "In flight" with before/after numbers.
