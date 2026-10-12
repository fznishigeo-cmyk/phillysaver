# Codex task 035: Lighthouse polish (subtle, measurable)

**Read first:** AGENTS.md, CLAUDE.md. Files: `dealradar/publish/templates/index.html`,
`hub.html`, `join.html`, the info pages, `dealradar/publish/seo.py`
(deal pages, best-today, store/price pages), tests.

The daily Lighthouse scan (mobile, 2026-10-12) scored: home performance 84 /
accessibility 98; hubs 99 / 90; deal pages 99 / 95; best-today 100 / 92;
best-practices and SEO 100 everywhere. Failing audits: home Largest
Contentful Paint, Cumulative Layout Shift, First Contentful Paint, heading
order; hubs/deal/best-today colour contrast; hubs "links rely on colour";
heading order on best-today. Goal: every page ≥ 95 accessibility, home
performance ≥ 90, nothing visibly redesigned.

1. **Contrast:** every text/background pair ≥ 4.5:1 (≥ 3:1 for ≥ 18.66px
   bold / 24px): darken the muted grey in light mode and lighten it in dark
   mode just enough; chips, badges, the "via" credit line, the small print,
   placeholders. List the before/after ratios in your note.
2. **Links not by colour alone:** in body text (hub intro, deal page facts,
   footers, small print) links get an underline (offset 2px, thickness
   1px); buttons and nav pills stay as they are.
3. **Heading order:** one `h1` per page, no skipped levels (h1 → h2 → h3),
   card titles stay `h3` under an `h2` section heading (add visually
   hidden `h2`s where a section has none).
4. **Home performance:** reserve the static wall's card media space so the
   live wall replacing it does not shift (same aspect ratio box);
   `fetchpriority="high"` + `loading="eager"` on the first 4 card images
   only, `decoding="async"` on the rest; no render-blocking script before
   the header (inline critical CSS stays); the ticker starts after first
   paint (`requestIdleCallback` / timeout fallback). Do not change what the
   page shows.
5. Tests for each (contrast helper over the CSS custom properties, heading
   order in every rendered page, underline rule, the image attributes).
   One short bullet in docs/HANDOFF.md under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
