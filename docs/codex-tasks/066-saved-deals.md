# Codex task 066 (roadmap brief 3): save deals locally and revisit them honestly

Source: docs/roadmap-beat-big-sites.md (task 063). Tasks 061 (spotlight, in flight) and 062 (price chart, shipped) are owned elsewhere; do not touch their code.


**Read first:** AGENTS.md, CLAUDE.md, docs/HANDOFF.md. Files:
`dealradar/publish/templates/engagement.js`,
`dealradar/publish/templates/index.html` (`dealKey`, `cardHtml`,
`filtered`, refresh/render paths), `dealradar/publish/site.py`,
`dealradar/publish/templates/privacy.html`, `dealradar/config.py`,
`config.yaml`, `dealradar/publish/tracking.py`,
`dealradar/growth/google.py`, `dealradar/growth/report.py`,
`tests/engagement_logic.cjs`, `tests/test_quality_trio.py`,
`tests/test_search_filters.py`, `tests/test_support.py`,
`tests/test_tracking.py`, `tests/test_growth_report.py`.

#### Why

A useful offer often appears before someone is ready to buy. Saving it
should be immediate and should not require email or an account.

#### Do

1. Add a configured `engagement.saved_deals` switch and bounded saved-list
   limit. In support mode add a 44px Save button with an accessible name,
   `aria-pressed`, and a Saved view with an exact local count. Hide the
   feature when disabled; keep dormant auth, locked stubs and free-8 rules.
2. Use a versioned, validated localStorage record containing only stable
   public `key`, safe display label/store and optional internal deal path.
   Do not save price, outbound URLs, photos, codes or post content. Home
   saving works before brief 4 adds paths; a missing path is not an error.
3. Reconcile with the current successfully loaded public payload. Present
   normal current cards for matches. A missing match says “Not currently
   listed; availability unknown,” with no cached price or Get deal button.
   A confirmed gone match says it ended and has no outgoing action. Do not
   equate a cap/rotation/missing page with expiration or bypass locked data.
4. Support remove, clear and cross-tab storage changes. A quota/permission
   failure keeps an in-memory shortlist for that tab and says it will not
   persist. Escape labels and validate any restored path as a same-origin
   `/deal/.../` path; corrupt or excessive records cannot break the wall.
   Explain “Saved on this device” and clearing site data on privacy.html.
5. Add GA4-only `deal_save`, `saved_view`, `saved_deal_click` events;
   counts only with permitted `method` labels. Extend `product` in the
   private report and add observed seven-day returning sessions using
   GA4's visitor classification with missing-data handling. Never send the
   shortlist or a key. This is a return-visit signal, not a save-to-purchase
   cohort or cross-device sync statistic.

#### Tests

Offline tests cover save/unsave/clear, duplicate saves, persistence, ordering
and limits, renamed items, live prices changing, a removed match, confirmed
gone versus absent, locked data, invalid paths/labels, malformed storage,
quota denial and cross-tab updates. Assert no saved prices or tagged URLs
are restored as live offers. Verify button events do not navigate, firing
counts once, unchanged ordinary deal/source links, feature-off output,
support/dormant variants and guarded analytics. Report tests include
missing visitor classification and meaningful denominator labels.

Run `.venv/bin/python -m pytest -q`. Own-site visual QA covers 360px,
iPhone/Pixel, keyboard, dark mode and reduced motion. Name blocked browser
checks. Add one HANDOFF bullet. Do NOT commit or push; end with what changed
and anything unsure.

Run `.venv/bin/python -m pytest -q`; all green except tests needing local
sockets your sandbox blocks (name them). One short HANDOFF bullet. Do NOT
commit or push. End with what changed and anything unsure.
