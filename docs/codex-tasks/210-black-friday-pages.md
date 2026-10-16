# 210 — /black-friday/ and /cyber-monday/ pages (traffic plan B4; live by Nov 1)

## Why
Black Friday is Fri Nov 27, 2026; Cyber Monday Mon Nov 30, 2026: the biggest deal-search week of the
year. Google needs the pages weeks ahead to index them. The operator wants wowpriced to look established;
these pages must look like a confident, permanent part of the site (live all year, updated every run).

## Task (read CLAUDE.md, AGENTS.md, docs/HANDOFF.md first; follow the existing hub pages' patterns)
1. Config (`config.yaml`, validated in `dealradar/config.py`): `seo.event_pages: {enabled: true, events:
   [{slug: black-friday, name: "Black Friday", date: 2026-11-27, window_days: [-28, 3], keywords:
   ["black friday"]}, {slug: cyber-monday, name: "Cyber Monday", date: 2026-11-30, window_days: [-28, 3],
   keywords: ["cyber monday"]}], min_items: 10, max_items: 60, intro: {black-friday: "", cyber-monday: ""}}`.
   Code default: disabled (every published byte unchanged when off; prior byte pins add the usual
   "predates" line for the new switch).
2. A live deal qualifies for an event page when (a) its verified evidence/title/conditions name the event
   (case-insensitive keyword match on fields we already store), or (b) the event window is open and it is a
   top pick (`pick` set). Never the whole wall. Same publish rules as hubs (alive, not locked, not
   price-unconfirmed, photo preferred), niche categories (front_page.niche_categories) excluded.
3. Pages at `/black-friday/` and `/cyber-monday/`, built like the existing category hubs (same header,
   cards, footer, structured data, canonical, breadcrumbs). Copy computed in code: H1 "Black Friday Deals
   2026", a one-line dek with the live count and "updated every 15 minutes", the event date, and the
   operator intro from config (empty = omitted). Before the window opens, show the top picks with a calm
   line "Early Black Friday deals" (no countdown gimmicks). After the event (window end) keep the page,
   retitled "Black Friday 2026 deals (ended)"-style is NOT wanted: keep "Black Friday Deals" and simply show
   the current best picks; never 404.
4. noindex until the page has `min_items` deals; indexable once it does. Add both to the sitemap and
   IndexNow submissions only while indexable; link them from home (one small chip/link near the category
   tabs, styled like existing links), from every hub's related-links block, and from the footer.
5. Amazon rules: `amazon_history: hide` is on — no price-history wording, charts or "lowest since" for
   Amazon items. For non-Amazon items you may show "lowest we've seen since <first check>" ONLY via
   `savings.product_price_facts` (math lives in savings.py).
6. Tests (offline): qualification (keyword vs pick vs excluded), floors (noindex below min_items, sitemap
   only when indexable), Amazon hide honored, off = every byte unchanged, idempotent rerun
   (`tests/test_e2e_offline.py::test_rerun_is_idempotent` still passes), page passes the existing SEO/hub
   audits (`seo_audit`), mobile layout same as hubs.
7. Full suite: `.venv/bin/python -m pytest -q -n 2 -p no:cacheprovider --basetemp=/tmp/task210-full`.
   Re-pin published-byte hashes only for intended changes. Do not commit. Short HANDOFF note.

## Constraints
CLAUDE.md non-negotiables; no new network calls, scripts or LLM calls; WCAG 2.2 AA; phone-first; keep
pages light (no new images). Use your own basetemp under /tmp/task210-*; never delete other /tmp dirs.
