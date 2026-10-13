# Codex task 062: price-history chart on deal pages (trust, like camelcamelcamel)

**Read first:** AGENTS.md, CLAUDE.md (store facts only, math in code,
surgical changes). Files: `dealradar/db.py` (`price_history`,
`record_price`, `price_stats`), `dealradar/publish/seo.py` (deal pages,
`card_html`, structured data), `dealradar/publish/site.py`,
`dealradar/verify.py` (how history-checked tier uses history),
templates under `dealradar/publish/templates/`, tests for seo deal pages.

## Why
Shoppers trust a deal when they can see the price history. Big deal sites
show it; we already record our own observed prices (`price_history`) and do
not show them anywhere.

## Do
1. On each deal page, a small inline SVG chart (no JS library, no external
   requests) of OUR observed prices for that product over the last 90 days:
   line + dots, today's price highlighted, the typical (median) price as a
   dashed line, min/max labels, "Our price checks, last 90 days" caption and
   the number of checks. Only when there are >= 3 observations spanning >= 2
   days; otherwise nothing (no empty chart). All statistics computed in code
   (savings.py or a small helper) — never by an LLM.
2. Accessible: role="img", aria-label summarising ("Price checks: lowest
   $X on <date>, typical $Y, now $Z"), a visually hidden table of the points
   for screen readers. Colours from the existing tokens; works in dark mode;
   fits 360px phones without horizontal scroll; under 6 KB per page.
3. Do not add Keepa or any third-party history; do not change verification
   tiers, prices or publish rules. Page build time must stay within budget
   (one query per page or a single batched query for all pages).

## Tests
Chart only with enough points; stats correct (median, min, max, dates);
SVG well-formed and bounded size; aria-label text; dark-mode tokens; no
external URLs; build-time query count bounded. One short HANDOFF bullet.

Run `.venv/bin/python -m pytest -q`; all green except tests needing local
sockets your sandbox blocks (name them). Do NOT commit or push. End with what
changed and anything unsure.
