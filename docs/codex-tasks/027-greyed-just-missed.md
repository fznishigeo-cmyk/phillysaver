# Codex task 027: a deal is "gone" only when confirmed; gone deals grey out in place

**Read first:** AGENTS.md, CLAUDE.md (politeness is law: no headless
browsers against retailers, no bot-wall bypass; Keepa/Woot APIs are the
allowed live checks; idempotency). Files: `dealradar/pipeline.py`
(the price recheck: `gone_suspect_at`, `_CONFIRM_GONE_H`, `death =
"confirmed"`; the upsert loop's `text_marks_dead` → `death =
"post_says_over"`), `dealradar/verify.py` (`mark_expired_dead`:
`api_ended` / `stated_expiry`), `dealradar/rank.py` (dead deals on the
site, `publish.show_dead_hours`), `dealradar/publish/site.py`
(`_serialize_deal`, the dead fields in deals.json),
`dealradar/publish/templates/index.html` (`render()`, `cardHtml`, the
"Just missed" section, the ticker), `dealradar/publish/seo.py`
(`static_wall` "Just missed", `card_html` dead cards), tests.

Operator (2026-10-12): never tell visitors a deal is over unless we are
sure; and show a confirmed-gone deal greyed out where it was ("ooo, just
missed it"), not clickable, instead of a section at the bottom.

## 1. Only confirmed deaths are shown as gone
- **Confirmed** = `death` in {"confirmed" (two Keepa/Woot API readings
  `_CONFIRM_GONE_H` apart, as today), "api_ended" (Woot's own end date)}.
- **Unconfirmed** = "post_says_over" and "stated_expiry". When such a deal
  has an Amazon ASIN or is a Woot offer (the live recheck can read it), do
  NOT kill it: set `gone_suspect_at` and let the price recheck decide
  (first reading due on the next run; it kills only on the second gone
  reading, revives on a live offer at the price, exactly the existing
  machinery). Other stores (no API we may use): mark dead as today, but
  they are never shown as gone: the wall, hubs and deal pages simply stop
  showing them (an ended deal page for them keeps the noindex "ended"
  page; that is fine).
- deals.json: a dead deal is published only when confirmed, with
  `gone: true` and `died_at`; unconfirmed dead deals are not published.

## 2. Greyed in place
- The wall keeps confirmed-gone deals (within `publish.show_dead_hours`,
  set it to 24 in config.yaml) in their own rank position, greyed:
  grayscale photo, ~55% opacity, a small "Just missed · gone 3h ago" label
  (relative time from `died_at`, rounded to hours, "gone just now" under
  1 h), no Get deal button, no link anywhere in the card (title not a
  link, no "via" links), `aria-disabled="true"`, not focusable, cursor
  default. Clicking it does nothing (no tracking, no navigation).
- Placement, so they never crowd out live deals: never among the first 8
  cards of the wall; at most 1 greyed card in any run of 8 cards (push the
  extra ones later); they count toward PAGE_CARDS like any card; filters
  and search apply to them; sort orders by their rank as if alive.
- Remove the bottom "Just missed" section from the JS wall and from
  `seo.static_wall` (static: greyed cards inline with the same placement
  rules, no links). The ticker shows live deals only.
- Category hubs, store/price pages, best-deals-today: live deals only.

## 3. Tests
Each death kind → published as gone or not; ASIN/Woot unconfirmed deaths
become suspects (not dead) and the recheck confirms or revives them; other
stores' unconfirmed deaths are hidden; greyed card markup has no `<a`,
no Get deal, `aria-disabled`; placement (none in the first 8, ≤ 1 per 8);
show_dead_hours 24; no "Just missed" section anywhere; ticker live-only;
tests/test_e2e_offline.py::test_rerun_is_idempotent green. Update the
tests that assumed the bottom section, and say which. One short bullet
in docs/HANDOFF.md under "In flight"; also update the "Deals stay until
genuinely over" standing rule text to say unconfirmed deaths are re-checked
or hidden, never shown as gone.

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
