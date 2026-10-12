# Codex task 023: "Amazon deals today"-style store pages and "deals under $X" pages

**Read first:** AGENTS.md, CLAUDE.md (store facts only; knobs live in
config.yaml; idempotency). Files: `dealradar/publish/seo.py` (task 021 just
added deal pages, `/best-deals-today/`, `_landing_page`, `card_html(d,
deal_paths)`, the sitemap and IndexNow), `dealradar/publish/templates/hub.html`,
`hubs.yaml`, `scripts/site_check.js` (step 8), `tests/test_seo_deal_pages.py`.

## Why
People search "amazon deals today", "walmart deals today", "deals under 10
dollars". wowpriced has the live data for these; give each its own page.

## 1. Store pages: `/stores/<slug>/`
- One page per store with at least `seo.store_page_min_deals` (config,
  default 15) public live deals right now; at most `seo.store_pages_max`
  (default 12) stores, most deals first. Slug: the store name lowercased,
  ASCII, `-` joined ("Amazon" -> `amazon`, "Macy's" -> `macys`). Store names
  come from deals.json `store`; fold case and a trailing ".com".
- Title "{Store} deals today ({Month D, YYYY}) | wowpriced"; H1 "{Store}
  deals today"; one intro line with the store's live count rounded like the
  home page (exact under 100) and the median % off; the store's top 60 live
  deals in the wall's order as `card_html(d, deal_paths)` cards; links to the
  other store pages and the 12 hubs. Canonical to itself. Indexable.
- A store that drops under the floor: no page (folder removed), out of the
  sitemap.

## 2. Price pages: `/deals/under-<n>/` for n in `seo.price_pages`
(config, default [10, 25, 50])
- Public live deals whose sale price is ≤ n, in the
  wall's order, top 60. Title "Deals under ${n} today ({Month D, YYYY}) |
  wowpriced". Skip a page with fewer than 10 deals.
- Careful: `/deals/<slug>/` already holds the category hubs; `under-10`
  etc. must never collide with a hub slug (assert it) and the site check's
  hub picker (`/^\/deals\/[^/]+\/$/`) must skip `under-*`.

## 3. Wiring
- Sitemap + IndexNow: every store and price page (lastmod: the newest first
  sighting among its deals, so an unchanged page writes the same sitemap).
- Footer "More categories" area on hubs/deal pages/best-today: add a short
  "Stores" row and a "By price" row (links only).
- `scripts/site_check.js` step 8: also read the first `/stores/` page and
  `/deals/under-25/` when listed in the sitemap (200 + `<title>`); hub
  picker skips `under-*`.
- Deterministic bytes for the same data (no run timestamps beyond the
  date in the title).

## 4. Tests
Slugs (Macy's, Amazon.com folding into Amazon), the floor and cap, page
content (title, H1, count rounding, median, card links to deal pages,
canonical), the price filter and the 10-deal floor, no slug collision with
hubs, sitemap/IndexNow entries, folder removal when a store drops out,
byte-identical re-render, support-mode wording, the site_check picker.
tests/test_e2e_offline.py::test_rerun_is_idempotent stays green. Config
keys with one-line comments; one short bullet in docs/HANDOFF.md under
"In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
