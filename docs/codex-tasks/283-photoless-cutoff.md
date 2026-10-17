# 283 — Photo-less deals leave the public site after 3 hours (back when a photo arrives)

## Why
Operator: "we need 99.99% of images showing up" / "aim for 99 percent". Live: 85%
of live deals show a photo (2,923/3,449); the site check is red every hour on
its 90% floor. Facebook ad lands at 9pm New York today. Task 265 (photo identity
recovery) is parked (politeness blocker), so this is the small, safe half on its
own.

## Task (config `publish.photos.max_photoless_h`, validated number ≥ 1; missing = off)
- A live deal whose public card has no https photo `max_photoless_h` hours (ship 3)
  after `first_seen_at` is not published anywhere public: homepage wall, category
  and store/hub pages, deals.json/manifest, deal pages, best-today, sitemap,
  IndexNow, ticker, Telegram/Pinterest/Facebook selection. It stays in the DB
  unchanged; deals younger than the cutoff are unaffected.
- When a photo arrives later, the deal is published again on the next run with
  no special handling (it is a pure publish-time filter, no new state).
- Deal pages that disappear follow the existing removed-page path (whatever the
  site does today for a deal that stops being published: noindex/redirect/
  IndexNow removal); do not invent a new mechanism.
- Run summary adds `unpublished_photoless` (count only).
- Site check floor: `site_check.min_photo_pct` 99 (config only).
- Same photo definition the site check uses (a card that shows a photo), so the
  floor and the filter agree; store-tile placeholders count as no photo.

## Tests
- Boundary: 2h59 published, 3h00 not; photo arrives → republished next run.
- Removed deal page follows the existing removal path; sitemap/IndexNow agree.
- OFF (missing knob) = today's bytes exactly (recorded pins; never re-pin).
- Idempotent rerun; phone budgets pass.

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task283-*` basetemp (delete
after each run), targeted tests only. No network, no commit, no push. HANDOFF
"In flight": one entry. Ship ON.
