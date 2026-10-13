# Codex task 068 (roadmap brief 4): connect the live wall to useful phone deal pages

Source: docs/roadmap-beat-big-sites.md (task 063). Already shipped on this base and owned elsewhere: 061 (Today-only spotlight: `activeSpotlight`, `spotlight-grid`, price-only basis), 062 (price-history chart on deal pages: `publish/price_history.py`), 064 (static wall fallback: `loadedOnce`, `#load-status`, per-deal `goodDeal` validation in `fetchPublish`), 066 (saved deals: `savedButtonHtml`, `deal_path` is already read by `saveDeal` when present). Keep all of them working; do not restyle or move them. The 062 chart IS on the deal page now: keep it, do not add another.

Phone is 70-80% of traffic. Every layout claim must hold at 360px.


**Read first:** AGENTS.md, CLAUDE.md, docs/HANDOFF.md; coordinate with task
062's owner. Files: `dealradar/publish/site.py` (shared serialization and
write order), `dealradar/publish/seo.py` (`_deal_pages`, `_deal_page`,
`card_html`, `_basis_label`), `dealradar/publish/templates/index.html`
(`cardHtml`, outbound click handling),
`dealradar/publish/templates/hub.html`,
`dealradar/publish/tracking.py`, `dealradar/growth/google.py`,
`dealradar/growth/report.py`, `tests/test_seo_deal_pages.py`,
`tests/test_rich_results.py`, `tests/test_publish_site.py`,
`tests/test_search_filters.py`, `tests/test_tracking.py`,
`tests/test_growth_report.py`, `tests/test_support.py`.

#### Why

We already publish useful stable URLs, but live card titles stop linking to
them after JavaScript renders. Search visitors also lack tap-to-copy codes,
and their Get deal clicks are not covered by the home-wall listener.

#### Do

1. Expose an optional `deal_path` only for pages actually emitted for
   that public deal. Reuse the stored `seo_deal_paths` mapping and current
   cap/visibility selection. Do not regenerate a path from a renamed
   title. Arrange one page-selection pass before the final payload write;
   maintain shared public/member values and unchanged opaque locked stubs.
2. Link dynamic titles to that path just like static titles. Deals without
   an emitted page retain the existing fallback. Get deal continues using
   the exact `d.url`; do not add an interstitial or change affiliate,
   step-based, Amazon, EPN or protected partner behavior. Preserve brief
   2's return state; integrate local Save if brief 3 is available.
3. Make the existing deal facts scan well on 360px: current price, price
   basis, condition/requirements, steps, sources, disclosure and a 44px
   Get deal action. Convert known codes into accessible tap-to-copy
   buttons, with visible selectable text when JS/clipboard is unavailable.
   Keep image-free static pages, related offers, ended-page noindex and
   the current absence of Product markup for ended deals. Do not add a
   chart, invented stock check or global sticky sales panel in this brief.
4. Give generated static outbound anchors explicit safe store/category
   metadata and add one scoped delegated handler. It covers detail and
   listing/related-card sponsored outbound links, while internal titles
   and credit links are not `deal_click`. The dynamic home listener and
   shared handler must never count the same action twice. Handle modified
   clicks without preventing normal browser navigation.
5. Add GA4-only detail-view and code-copy counts. For Get deal retain
   `deal_click` with store/category and a bounded page-kind `method`.
   Extend the private report with `product.deal_detail_views` and
   `product.deal_detail_outbound_clicks` by device/page family; label
   click/view ratio as actions, not purchases. No code, product ID, price
   or outbound URL reaches tags. Measure incremental payload gzip bytes
   to account for the added path field.

#### Tests

Check stable paths after rename, missing/capped pages, public/member
equality, no locked leakage, first load and refresh title destinations,
unchanged Get deal URLs for Amazon/EPN/Sovrn/code/multistep/protected
partners, and inert ended pages. Test copy success/failure/no clipboard,
escaping and safe metadata. Offline Node tests exercise static listing,
detail/related and dynamic clicks, modified clicks and no duplicate counts;
credits/internal navigation never count as outbound. Validate HTML/JSON-LD
prices and conditions still agree, no invented review/expiry, correct
canonical and noindex, measured payload size and unchanged no-JS use.

Run `.venv/bin/python -m pytest -q`; verify phone/desktop/light/dark and
keyboard detail journeys on generated own-site pages, never opening
retailer links. Name blocked visual checks. Add one HANDOFF bullet. Do NOT
commit or push; end with what changed and anything unsure.

