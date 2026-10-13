# Codex task 063: roadmap to beat the big deal sites with code (analysis only)

**Read first:** AGENTS.md, CLAUDE.md, docs/HANDOFF.md, README.md, then skim
`dealradar/` and `dealradar/publish/templates/index.html`.

Operator (2026-10-13): make wowpriced.com not only compete with
million-dollar deal sites (Slickdeals, DealNews, Brad's Deals, Hip2Save,
Krazy Coupon Lady, DealsPlus, Woot/Meh) but exceed them, using code instead
of spending. 70-80% of visitors are on phones. Constraints that never bend:
the CLAUDE.md non-negotiables (robots, evidence, facts only, math in code,
money caps ~$6-10/day LLM), no store logos, customer emails stay off for now,
no paid ads yet.

## Deliverable (no code changes)
Write `docs/roadmap-beat-big-sites.md`:
1. What those sites do that we do not (from your knowledge; say so where you
   are unsure), and where we already win (verification, fake-MSRP removal,
   speed, no clutter).
2. A ranked list of 15-25 code-only features/fixes, each with: user benefit,
   expected impact (traffic, trust, conversion, earnings), effort (S/M/L),
   risk, files it touches in THIS repo, and how to measure it (a run-summary
   or growth-report field). Phone experience, SEO (deal/store/category
   pages, freshness, structured data), retention without email (PWA
   install, saved deals, price-drop watch in localStorage), trust signals,
   page speed, and more deals per AI dollar should all be considered.
3. The first 5 to build, each written as a ready-to-run Codex brief in the
   style of docs/codex-tasks/058-*.md (Read first / Why / Do / Tests).

Do NOT commit or push. End with a 10-line summary.
