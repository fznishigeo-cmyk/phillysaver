# Codex task 069 (roadmap #9): a lightweight installable wowpriced (home-screen app)

Source: docs/roadmap-beat-big-sites.md item 9 (task 063). Running in parallel
and owned elsewhere: 065 (phone filters + Back state in index.html controls,
`filtered`, URL/sessionStorage state) and 068 (deal-page links from cards,
tap-to-copy codes, seo.py `_deal_page`/`card_html`, outbound click handler).
Do not touch those areas. Shipped and owned elsewhere: 061 spotlight, 062
price chart, 064 static fallback, 066 saved deals (heart in the photo corner,
"♥ Saved (n)" button). Keep them working.

**Read first:** AGENTS.md, CLAUDE.md, docs/HANDOFF.md. Files:
`dealradar/publish/site.py` (write order, `_headers`, site_version),
`dealradar/publish/seo.py` (shared `<head>` for hub/deal pages),
`dealradar/publish/templates/index.html` (head, footer),
`dealradar/publish/templates/hub.html`, `dealradar/publish/tracking.py`,
`dealradar/growth/google.py`, `dealradar/growth/report.py`, `config.yaml`,
`dealradar/config.py`, existing brand/logo assets (find them; the header
logo mark already exists), `tests/test_publish_site.py`,
`tests/test_tracking.py`, `tests/test_growth_report.py`,
`tests/test_support.py` (byte-hash baselines: regenerate only for the
variants your change alters, and say which).

## Why
70-80% of visitors are on phones. A home-screen icon brings people back
without email. Big deal sites have apps; this is the zero-install version.

## Do
1. **Manifest + icons.** Generate `manifest.webmanifest` from config
   (`pwa.enabled: true`, name "wowpriced", short_name "wowpriced",
   theme/background colors taken from the existing CSS tokens, `display:
   standalone`, `start_url: "/"`, `id: "/"`, `scope: "/"`). Icons derived
   from the existing logo mark (192, 512, 512 maskable with safe-zone
   padding, 180 apple-touch-icon) produced at build time or committed as
   small PNGs; no store logos, no new brand. Link the manifest,
   `theme-color` (light and dark media variants) and apple-touch-icon in
   the home page AND every generated page head. Serve the manifest with
   `application/manifest+json` via `_headers` and a sensible cache rule.
   `pwa.enabled: false` removes every trace (byte-identical to today).
2. **No offline offer caching.** Chromium no longer requires a service
   worker for installability; do NOT add one in this task. If you find a
   hard requirement, stop and say so instead of adding one.
3. **Quiet install option.** Chromium: capture `beforeinstallprompt`
   (preventDefault), and only then show an "Install app" text button in
   the footer (44px target, existing tokens). Never a modal, banner,
   overlay or timer; never on first paint. iOS Safari (no prompt API):
   a footer link "Add to Home Screen" opening a short own-site help
   section (Share → Add to Home Screen), shown only on iOS Safari and
   not in standalone mode. Hide both when already installed
   (`display-mode: standalone` or `navigator.standalone`).
4. **Standalone polish.** In standalone mode, outbound Get deal links
   must still open the retailer (keep target/rel exactly as today; do not
   change affiliate URLs). Status-bar color matches the header in light
   and dark. No layout shift: measure the first card's top on a 390x844
   phone before/after in normal browser mode and report both numbers.
5. **Measure (GA4-only, counts only).** `install_offer_shown`,
   `install_accepted` (from `userChoice` / `appinstalled`, once each),
   and a once-per-session `standalone_session`. Extend the private
   growth report's Product section with these counts and label them as
   actions, not retained users. No ids, no URLs in parameters.

## Tests
Manifest JSON validity and fields; icons exist with the right pixel
sizes; head tags on home, hub, store/price and deal pages; `_headers`
content type; `pwa.enabled: false` byte-identical output; install button
hidden until `beforeinstallprompt`, shown once, hidden after
`appinstalled` and in standalone; iOS help only on iOS Safari; events fire
once with allowed params only; report lines with zero/unavailable data.

Run `.venv/bin/python -m pytest -q`; all green except tests needing local
sockets your sandbox blocks (name them). One short HANDOFF bullet. Do NOT
commit or push. End with what changed, the before/after first-card top,
and anything unsure.
