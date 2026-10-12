# Codex task 029: calm it down: a dignified casino, never a spam site

**Read first:** AGENTS.md, CLAUDE.md. Files:
`dealradar/publish/templates/index.html` (`cardHtml`, the phone `#subbar`,
`updatePaywall`), `dealradar/publish/templates/engagement.js` (`hotSignals`,
`hotHtml`), `dealradar/publish/templates/support.js` (`refresh`: the banner
and the phone bar), tests (`tests/engagement_logic.cjs`,
`tests/support_logic.cjs`, `tests/test_publish_site.py`).

Operator (2026-10-12): the site should feel like a dignified casino:
excitement from real wins, never a page of pop-ups and badges. Two things
are too loud today:

1. **Flames are everywhere.** One flame for ≥ 60% off alone lights up most
   of the wall (the floor is 40% and most deals sit at 50-80%), so "hot"
   means nothing. New rule: flames show only when a deal has **at least
   two** of the three signals (≥ 60% off, confirmed by 2+ sources, first
   seen < 2 h ago): 2 signals = 🔥🔥, 3 = 🔥🔥🔥; one signal = nothing. Same
   label/aria text; how-it-works sentence updated to say flames need two
   signals.
2. **Two "Join free" buttons on a phone at once.** At the top of the page
   on a phone the header button and the sticky bottom bar (`#subbar`) both
   show. The bottom bar appears only once the header's Join button has
   scrolled out of view (IntersectionObserver on `#subscribe`, falling back
   to a scroll check), and stays hidden while the support banner shows (as
   today). Desktop unchanged.

Also check, and fix only if true: nothing on the page animates in a loop
except the deal ticker (which stops under `prefers-reduced-motion`); no
element appears more than once per screen that asks the visitor for
something (Join/Support).

Tests: flame rule (0/1 signal → none, 2 → two, 3 → three); the phone bar
hidden while the header button is visible, shown after it scrolls away,
hidden while the banner shows; reduced motion stops the ticker. Do not
loosen other tests. One short bullet in docs/HANDOFF.md under "In
flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
