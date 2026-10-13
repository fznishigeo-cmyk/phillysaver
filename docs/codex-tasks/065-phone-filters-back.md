# Codex task 065 (roadmap brief 2): price and eligibility filters, with preserved browsing state

Source: docs/roadmap-beat-big-sites.md (task 063). Task 068 (deal pages linked from cards, tap-to-copy codes) runs in parallel and owns `cardHtml` title links and outbound click counting: do not change those. Already shipped on this base and owned elsewhere: 061 (Today-only spotlight above the grid; it hides while a search/store/min% filter excludes it, see tests/spotlight_logic.cjs), 064 (static wall fallback: `loadedOnce`, `#load-status`; `render()` is a no-op until the first publish loads), 066 (saved deals: Saved view disables store/sort/min% controls while open). New filters must follow the same rules as the existing ones in each of those (spotlight hides when a filter excludes it; disabled in the Saved view; no-op before first load).

Phone is 70-80% of traffic. No new sticky bar; the filter sheet must not push the first card further down on a 390x844 phone when closed (measure the first card's top before/after and report both numbers).


**Read first:** AGENTS.md, CLAUDE.md, docs/HANDOFF.md. Files:
`dealradar/publish/templates/index.html` (`filtered`, `applyPublish`,
`populateStores`, `resetWall`, controls and phone CSS),
`dealradar/publish/templates/engagement.js`, `dealradar/config.py`,
`config.yaml`, `dealradar/publish/tracking.py`,
`dealradar/growth/google.py`, `dealradar/growth/report.py`,
`tests/test_search_filters.py`, `tests/search_filters_logic.cjs`,
`tests/test_front_page.py`, `tests/front_page_logic.cjs`,
`tests/test_support.py`, `tests/test_tracking.py`,
`tests/test_growth_report.py`.

#### Why

Phone visitors can choose a store, but cannot express “new, under my budget,
no extra steps.” Returning from an offer should not require rebuilding the
search or finding the old scroll position.

#### Do

1. Add a small accessible filter sheet on phones and equivalent compact
   desktop controls. Include configured price presets (reuse
   `seo.price_pages`), an optional maximum price, “New condition only,”
   “No extra steps,” Apply and Clear. Use 44px hit areas, 16px input text,
   existing tokens and focus/escape/return behavior; no extra sticky bar.
2. Filter existing public facts only. A price ceiling requires a finite
   known `final_price`. New condition excludes normalized used/open-box/
   refurbished markers; do not label it a seller/warranty guarantee.
   No-extra-steps uses `needs_steps === false`. Unknown fields must not
   silently pass those explicit filters. Keep source credit, ranking,
   gone-card spacing and publication floors intact.
3. Make allow-listed store/category/sort and fixed preset selections
   representable in a shareable browse URL; extend the existing Amazon
   route without breaking it. Keep typed search and custom numeric input
   in guarded sessionStorage, outside URLs and analytics. Canonical
   remains `/`; create no crawlable parameter-page sitemap entries.
4. Restore query, filters, shown count and a stable visible-card anchor
   when returning in the same tab. URL selections take precedence over
   remembered defaults. Handle `popstate`, reload, missing stores and
   storage denial; do not push a history entry per typed character.
   After refresh use current facts: a missing anchor falls back to a
   nearby position without reviving a removed offer.
5. Add GA4-only `filter_apply`, `filter_empty`, `back_restore` events,
   once per completed action, with a bounded `method` value only. Extend
   the private `product` report with these counts and a device-segmented
   sessions-with-Get-deal rate. No query, price, threshold or item key is
   an event parameter; mask new inputs for Clarity.

#### Tests

Use mixed stores/conditions, null/missing prices, step-based/plain offers,
gone cards and locked stubs. Verify all filter intersections, clear/reset,
no floor change, valid/invalid/unknown URL values, preserved Amazon route,
URL-vs-default precedence, Back/Forward, reload, refresh removing an anchor,
blocked storage, current price above a saved ceiling and no duplicate
events. Check the existing 300-card Trending behavior, other-view loading,
search/category restore and support/dormant modes. Tracking tests prove
raw input cannot reach any event; report mocks cover zero sessions and
unavailable data. Check focus, keyboard, dark mode and reduced motion.

Run `.venv/bin/python -m pytest -q`; perform own-site phone/desktop Back
navigation QA without opening retailer links. Name sandbox-blocked checks
and leave their verification pending. Add one HANDOFF bullet. Do NOT commit
or push; end with what changed and anything unsure.

