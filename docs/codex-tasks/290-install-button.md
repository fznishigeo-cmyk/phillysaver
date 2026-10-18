# 290 — "Install app" button, top right (like Tesla's Install)

## Why
Operator 2026-10-17, launch night: "when I went to Tesla, right at the top right, it
said Install. I want our website to do the same thing so it can install like that,
the app — you already made the app." wowpriced already ships a web manifest
(dealradar/publish/_pwa_web.py, config `pwa.enabled: true`, display standalone).

## Task (config `pwa.install_button`, bool; missing/false = today's bytes exactly; ship true)
1. Browser's own Install: verify in real Chromium that the published homepage is
   installable (CDP `Page.getInstallabilityErrors` returns none, or the closest
   check this Chromium offers) — this is what makes Chrome show "Install" in the
   address bar like Tesla. If anything is missing (icon sizes/purpose, start_url,
   id, screenshots, a minimal service worker), fix it behind the same knob with the
   smallest change. A service worker, if needed, must not cache HTML or deals.json
   (network only / pass-through) and must not change any existing request.
2. Our own button in the header, right side next to "Join free" (phones: compact
   icon + "App"): "Install app".
   - Chrome/Edge/Android: capture `beforeinstallprompt`, show the button only then;
     click → `prompt()`; hide after `appinstalled` or a dismissed prompt for 7 days.
   - iPhone/iPad Safari (no prompt API): show the button; click opens a small,
     accessible sheet: "Add wowpriced to your Home Screen: tap Share, then Add to
     Home Screen" with the Share glyph (inline SVG); closes with X/Escape/backdrop.
   - Never shown when already running installed (display-mode standalone /
     navigator.standalone), never in Facebook/Instagram in-app browsers (they cannot
     install; detect FBAN|FBAV|Instagram in UA), never in the Android app.
   - Style: matches the header (pill, same height as Join free), visible focus,
     WCAG AA, reduced motion respected, no layout shift (space reserved or appears
     without moving the search box).
   - GA4 events via the existing allowlist: install_click, install_accepted,
     install_dismissed, installed (no personal data).
3. Keep the existing footer "Install app" link working.

## Tests
- OFF/missing = recorded bytes for every page (history pins; never re-pin; pin
  "current"-config fixtures with a tests/history_pins.py helper like pre_task_288).
- ON: button markup/logic in a node harness (prompt path, iOS sheet path, standalone
  hidden, in-app browser hidden, 7-day dismissal); real Chromium: installability
  check passes, button appears after a synthetic beforeinstallprompt, no console
  errors, no horizontal scroll at 390 px; screenshots 390×844 and 1440×900 light/dark
  in docs/research/install-button/.
- Phone budgets pass; idempotent rerun.

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task290-*` basetemp (delete after
each run), targeted tests only. No network, no commit, no push. HANDOFF "In flight":
one entry.
