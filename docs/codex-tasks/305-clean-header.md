# Task 305 — a clean, aligned header like Slickdeals / Groupon / Amazon (operator 2026-10-18)

Operator: "can you fix the formatting it looks a little jumbled." She showed our desktop header next to Slickdeals, Groupon and Amazon.

## What she sees now (desktop ~1440–2000 px)
Teal header: logo + "wowpriced" at left; the rainbow search field (task 301) with the purple "Analyze" pill (card_tidy styles) in the middle with "Free price check · no sign-up" hanging under it; a dark "Next drop in 9:26" pill to the right that is vertically misaligned with the search field; Install app / Join free buttons further right; a separate row "VERIFIED DEALS · ● 1,000+ LIVE DEALS" under everything; then the category tabs further down the page as pill buttons. Items sit at different heights and the rows don't line up.

## References (describe, do not copy branding)
- Slickdeals: one row — logo | wide rounded search | four icon+label actions (Deal Alerts, Post a Deal, Giveaways, Sign Up) evenly spaced and vertically centred; a second row of nav links (Categories, Coupons, Forums) aligned under the search.
- Groupon: one row — logo | search with a round search button | App, Sell, heart, bell, cart, account icons; a second row of category links with icons, horizontally scrollable with a ">" button.
- Amazon: dark bar — logo | location | big search | language, account, orders, cart; second darker row of category links ("All", Prime, Groceries, Today's Deals ...).

## Goal (desktop >= 900 px)
1. Row 1, everything vertically centred on one line, consistent 16–24 px gaps: logo | search field (flex 1, max ~720 px; keep the rainbow ring and the purple Analyze pill exactly as styled) | right group: the Next-drop timer (compact, same height as the search field, e.g. 48–56 px), Install app, Join free (keep "Join free · price alerts" on >= 1500 px). Nothing wraps between 900 and 2560 px; at narrower desktop widths the right group may drop labels to icons before anything wraps.
2. Row 2: the category tabs (Trending, All deals, Used & Open-Box, Home & Kitchen, ...) become a clean nav row inside the header bar (text links with the active one highlighted, horizontally scrollable with a fade/">" control when they overflow — no wrapping to two lines), and the "VERIFIED DEALS · ● 1,000+ LIVE DEALS" status moves to the right end of that row in small caps (or under the logo), not its own row. "Free price check · no sign-up" becomes the search placeholder's companion: either inside the field as helper text on wide screens or removed from desktop (keep it on phones if it fits the existing phone layout).
3. Phones (< 900 px): keep today's phone layout (logo + Join free, full-width search, timer) but align it neatly; the mobile audit must stay green with headerH <= 196 and firstCardTop <= 402 at 390 and 360 widths (scripts/mobile_audit.cjs metrics), no overflow, tap targets >= 44 px.
4. Behind a strict-bool flag `header_polish` (AppConfig default False = previous bytes; config.yaml true); chain `pre_header_polish` from `pre_task_295` in tests/history_pins.py; never re-pin. Homepage gzip budget: tests/test_home_comment_strip.py headroom must stay green (CSS only, compact; reuse existing classes).
5. Dark mode and the event/deal pages that share the header must not break.

## Tests
- OFF/missing bytes + strict config.
- A Chromium test tests/header_polish_browser.cjs (I run it; model it on tests/search_box_glow_browser.cjs): at 1024, 1280, 1440, 1920 light/dark — every row-1 item's vertical centre within 2 px of the search field's centre, no wrapping, no horizontal scroll; tabs row single line; at 390/360 phone metrics as above; screenshots to docs/research/header/.
- Targeted pytest only: `.venv/bin/pytest -q -p no:cacheprovider --basetemp=/tmp/b305 <files>`. No full suite, commit or push. Update docs/HANDOFF.md. Reply <= 8 lines.
