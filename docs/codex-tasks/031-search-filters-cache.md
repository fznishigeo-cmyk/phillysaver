# Codex task 031: a real search box, clear filters, always-fresh pages

**Read first:** AGENTS.md, CLAUDE.md. Files: `dealradar/publish/templates/index.html`
(the toolbar: `#q`, `#storesel`, sort, `#surprise`, `#minpct`, `#srcsel`;
`filtered()`; the code that fills the store and source selects; the
`site_version` reload), `dealradar/publish/seo.py` (`_HEADERS`), tests.

Operator (2026-10-12): "the search box is tiny: give it its own row on top,
filters underneath"; "I searched best office and the BestOffice deal did not
come up"; "I don't see Walmart under all sources"; "without an incognito
window the site doesn't update". Subtle, polished, tested.

## 1. Search on its own row
- `#q` moves to its own full-width row above the filters (desktop and
  phone): ~48px tall, 16px text (no iOS zoom), a magnifier icon inside on
  the left, a clear (✕) button on the right when it has text (keyboard
  reachable, `aria-label="Clear search"`), placeholder "Search 1,600+ deals
  (brand, product, store)" with the live rounded count. The filters (stores,
  sort, Surprise me, min %, found by) sit in one row under it; on phones
  they wrap as today. Same look as task 030's buttons (calm outline).
- No layout shift: the row exists in the static HTML.

## 2. Search that finds things
- Normalize both sides: lowercase, strip accents, `&` → "and", every
  non-alphanumeric run → one space. A deal matches when EVERY query word is
  found in its haystack (item, brand, store, category label, conditions),
  where a word matches either a haystack word prefix or a substring of the
  haystack with spaces removed (so "best office", "bestoffice" and "best
  offi" all find "BestOffice 4-Shelf …"; "ninja blender" finds "Ninja
  Professional Plus Blender").
- While the search box has text, search covers ALL categories (the category
  tab is ignored and visually shows "All"); store, min % and found-by
  filters still apply. Clearing the box restores the tab the visitor was
  on. A one-line muted result count under the search row: "12 deals match
  "best office"" (or "No deals match … : try fewer words").
- Typing is debounced (~150 ms); Enter does nothing extra; Escape clears.

## 3. Clear filter labels, complete lists
- Store select: first option "All stores"; options are every store present
  in the live deals, sorted by count (desc) then name, with counts
  ("Walmart (37)"); store names folded like task 023 (case, ".com").
- The deal-site select is about who FOUND the deal: first option "Found by:
  any site", then "✓ Confirmed by 2+ sites", then every source display name
  present, sorted by count, with counts. `aria-label`s "Store" and "Found
  by".
- Test that every store and every source present in deals.json appears.

## 4. Always fresh for returning visitors
- `seo._HEADERS`: add `Cache-Control: no-cache` for `/`, `/*.html`,
  `/deal/*`, `/deals/*`, `/stores/*`, `/best-deals-today/*`, `/join`, and
  `Cache-Control: no-store` for `/deals.json` (keep the X-Robots-Tag lines).
  Cloudflare Pages `_headers` syntax.
- The home page: on `pageshow` with `event.persisted` (back/forward cache)
  and on `visibilitychange` back to visible after ≥ 5 minutes hidden,
  re-fetch deals.json (no-store, cache-busted as today) and re-render
  without a full reload; if `meta.site_version` differs, reload once (the
  existing mechanism).

## 5. Tests
Search normalization and matching (the examples above, accents, `&`),
all-categories while searching and tab restore, the count line, Escape;
store and source option lists complete with counts and labels; `_headers`
content; the pageshow/visibility refresh (offline harness like
tests/support_logic.cjs). `scripts/site_check.js` step 4 keeps working
(type a word from the first card; at least one card stays). One short
bullet in docs/HANDOFF.md under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
