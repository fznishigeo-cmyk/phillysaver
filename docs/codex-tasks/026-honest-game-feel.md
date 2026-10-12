# Codex task 026: subtle, honest "game feel" on the home page

**Read first:** AGENTS.md, CLAUDE.md. Page: `dealradar/publish/templates/index.html`
(cards `cardHtml`, the tagline, the ticker, `track()`, `state`, the drop
clock), `support.js`, tests in `tests/test_publish_site.py`,
`tests/support_logic.cjs` (how the page's JS is tested offline).

Operator (2026-10-12): more visitors should click "Get deal" and come back
daily: light gamification, subtle. **Hard rule: everything shown is true.**
No fake counts, no fake scarcity, no fake timers, no "X people are viewing".
Every number comes from deals.json or from this visitor's own browser.
Each feature below has its own switch in `config.yaml` (`engagement:` block,
all true by default) passed through deals.json `meta.engagement`, so one
can be turned off without a deploy of code.

1. **New since your last visit.** localStorage `wp.lastVisit` (try/catch;
   no storage = feature silently off). On load: deals first seen after the
   last visit get a small "NEW" tag and the tagline gains
   "· N new since your last visit" (N > 0 only). Update the stamp when the
   visitor leaves (pagehide) so the count stays stable during the visit.
2. **Your finds this week.** Each Get deal click adds that deal's
   `abs_savings` to a private weekly tally in localStorage (dedupe by deal
   key; resets each Monday local time). A small header chip shows
   "You've found $X in savings this week" once X ≥ $10. Wording says
   "found", never "saved" (we don't know they bought). Never sent anywhere.
3. **Visit streak.** Consecutive local days with a visit; from day 2 a tiny
   "🔥 N-day streak" chip beside the clock. Missing a day resets to 1.
4. **Surprise me.** A small button by the sort control: scrolls to and
   flashes a random deal among the top 50 currently shown (renders more
   cards if needed, like the ticker does). `track("surprise_click")`.
5. **Hot meter.** On cards, 1-3 small flames from real signals only:
   +1 for ≥ 60% off, +1 for "✓ 2+ sources" (corroborated), +1 for first
   seen < 2 hours ago. 0 = no flames. A `title`/aria-label explains
   ("Hot: 70% off, confirmed by 2 sources"), and how-it-works.html gets one
   sentence explaining the flames.
All chips: muted styling, no animation loops, `prefers-reduced-motion`
respected, keyboard reachable where interactive, readable by screen
readers, dark mode. Nothing shifts layout after load (reserve space or add
in the same render). Mobile 390px: chips wrap, never overflow.

Tests: the switches (meta + config), each feature's logic in an offline
node harness (fake localStorage including a throwing one, fixed clock):
new-since counts, weekly reset on Monday, dedupe, streak reset, surprise
picks only from the top 50, flame scoring for each signal; the wording
("found", never "saved"); nothing rendered when the switch is off; the
`track` events. Do not loosen existing tests. One short bullet in
docs/HANDOFF.md under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
