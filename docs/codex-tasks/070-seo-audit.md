# Codex task 070 (roadmap #6): audit and improve SEO pages, freshness and structured data

Source: docs/roadmap-beat-big-sites.md item 6 (task 063). Shipped on this base and owned elsewhere (keep working, do not restyle or move): 061 Today-only spotlight, 062 price chart on deal pages, 064 static wall fallback (`loadedOnce`, `#load-status`, per-deal `goodDeal`), 066 saved deals (heart in the photo corner from `cardHtml`, "♥ Saved (n)" first in `#controls` only once something is saved, `engagement.js` saved-deals block), 067 AI yield, 068 deal pages linked from cards (`deal_path`, `deal-actions.js`, tap-to-copy codes), 069 installable app (`pwa.py`, manifest, footer install link). Task 065 (phone filters + Back state: `#controls`, `filtered`, URL/sessionStorage state) runs in parallel: do not touch those areas. Phone is 70-80% of traffic: every layout claim must hold at 360px, and report the first card's top on a 390x844 phone before/after if the home page changes.

**Read first:** AGENTS.md, CLAUDE.md, docs/HANDOFF.md, the roadmap item 6
text. Files: `dealradar/publish/seo.py`, `dealradar/pipeline.py`,
`dealradar/publish/templates/hub.html`, `dealradar/publish/templates/hubs.yaml`,
`dealradar/db.py`, `dealradar/growth/google.py`, `dealradar/growth/report.py`,
`config.yaml`, `tests/test_seo.py`, `tests/test_rich_results.py`,
`tests/test_seo_store_price_pages.py`, `tests/test_seo_deal_pages.py`.

## Why
People searching a store, category or product should land on a useful,
current page whose visible facts match its markup. The pages and markup
exist; audit them before adding more page combinations.

## Do
1. **Audit in code, every publish (no network).** Add a run-summary
   `seo_audit` with `indexable_by_family` (home/category/store/price/
   best_today/deal), `empty_indexable` (an indexable page whose visible
   live-offer list is empty or below the family's minimum),
   `schema_mismatches` (JSON-LD price/currency/availability/condition/
   name that differs from the visible facts on the same page),
   `broken_internal_links` (internal hrefs to pages this publish did not
   emit), and `sitemap_urls` vs emitted indexable pages. Log the first few
   offending paths (paths only).
2. **Fix what the audit finds, conservatively.** An indexable hub/category
   page with too few CURRENT offers becomes `noindex,follow` and leaves the
   sitemap until it has enough again (minimum per family in config, derived
   from today's behavior; do not lower operator floors `min_pct` /
   `site_show_from_pct`). Ended deal pages stay noindex with no Product
   markup. Fix every broken internal link at its source.
3. **Honest `lastmod`.** A sitemap `lastmod` changes only when the page's
   substantive facts change (price, availability/ended, offer set on a hub,
   title), not on every build; keep a compact per-URL fact fingerprint in
   state with bounded retention. See Google's guidance on lastmod
   (developers.google.com/search/blog/2023/06/sitemaps-lastmod-ping).
4. **Product snippets, factual only.** For live single-deal pages keep
   Product + Offer markup that matches visible text; no invented reviews,
   ratings, expiry, shipping or return policy; never claim wowpriced sells
   the item (seller is the store). Validate with structural checks in tests.
5. **Search Console by page family.** Extend the private growth report's
   search section with clicks, impressions and CTR by page family and
   device (existing Search Console access; paginate; missing data says
   unavailable, never zero).

## Keep exactly
Evidence-backed prices only; store credit and source credit; canonical
URLs; the 068 `deal_path` stability (never regenerate a path from a
renamed title); idempotent re-runs; page and sitemap output for unchanged
facts byte-stable across two publishes.

## Tests
Audit counts on fixtures with a sparse hub, a schema/visible mismatch, a
broken link and an ended deal; noindex/sitemap removal and return; lastmod
stable across two publishes with unchanged facts and moving on a price
change; Product/Offer fields equal visible text; no review/rating/expiry
keys; report rows with zero and unavailable data.

Run `.venv/bin/python -m pytest -q`; all green except tests needing local
sockets your sandbox blocks (name them). One short HANDOFF bullet. Do NOT
commit or push. End with what changed, the audit numbers on the test
fixture, and anything unsure.
