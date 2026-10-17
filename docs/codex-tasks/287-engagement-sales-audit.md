# 287 — Audit every ranking/page algorithm for sales, dopamine and daily return (read-only research)

## Why
Operator 2026-10-17, on Facebook-ad launch night: "please review all code, all
algorithms to maximize our sales and consumer dopamine hits to keep them on our
website and keep shopping and keep coming back day to day." Long-term goal (see
docs/HANDOFF.md and the operator's notes in this brief): a TikTok-like learning
homepage — click learner, a "For you" row, explore slots, alerts — that is unique
and keeps people coming back, without ever faking a price, a scarcity claim or a
countdown (evidence rules in CLAUDE.md are law).

## Task (NO code changes; one research document)
Read the code that decides what a visitor sees and does: dealradar/rank.py,
appeal.py, appeal_score.py, publish/site.py, publish/seo.py, publish/drop_rotation.py,
the browser templates (index.html, browse.js, drop-rotation.js, search-results.js,
support/join/saved/ticker code), config.yaml's front_page/appeal/top_picks/
popular_affordable/everyday_price/spotlight/support/join blocks, the click
signal / click learner code, tracking (GA4 events) and the growth report.
Write `docs/research/engagement-audit.md` with:
1. **How a visit works today** (one page): what decides the first 24 cards, the
   rotation per drop, the ticker, spotlight, search, empty states, sign-up and
   support prompts, return triggers (drops, saved deals, alerts, Telegram/email).
2. **Findings, ranked by expected effect on clicks to stores (sales) and return
   visits**, each with: evidence (file:line and the current knob value), the
   mechanism, a concrete change (config knob vs code), effort (S/M/L), risk, and
   how we would measure it (which GA4 event / growth-report line, and the
   threshold that says it worked). Include at least: first-screen mix (gadgets,
   women's items, allure, kids, $10–20 impulse items, everyday staples), bad-deal
   filtering (e.g. "4 plastic shakers for $19.97"), novelty per drop/visit
   (repeat exposure), the empty-search experience, the "Next drop" habit loop,
   saved deals/alerts, speed on phones, and the gap between what the click
   signal measures and what the ranking uses.
3. **Bugs or contradictions** found on the way (two rules fighting, knobs that
   do nothing, server/browser disagreements), each with a failing-test sketch.
4. **The top 5 next builds** for the learning homepage in order, each as a
   one-paragraph brief we can hand to a builder, respecting: no fake urgency, no
   adult items boosted, Amazon rules (no price history/ratings/sales counts shown),
   private signals never published, money caps, phone budgets, idempotency.

## Rules
Read-only: no source edits, no commit, no push, no network. You may run
targeted tests/scripts offline with `-n 1 -p no:cacheprovider` and your own
`/tmp/task287-*` basetemp (delete it after). Final reply: at most 8 lines —
the top 5 findings in one line each and the document path.
