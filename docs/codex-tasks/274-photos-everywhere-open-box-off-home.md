# 274 — Photos on every static card; open box off the homepage

## Why
Operator 2026-10-17 ~04:00Z, screenshots:
- Category pages (/deals/beauty-personal-care/, /deals/pets/) and the deal
  pages' "Related live deals" show colored store tiles ("Woot", "Amazon")
  instead of product photos: "when you click from a rich site to the other
  pages there are no images". `seo.card_html` draws a photo only for the
  home page's first 4 cards (`photo=`); every other static grid gets the tile.
- Deal pages (/deal/…/) have no product photo at all ("can this have an
  image on it too? like those rich pages").
- Related cards are narrow and very tall ("really long … stretched"): five
  ~170 px columns with a 10-line description each.
- "Toggle down the open box … there is only 1 of them and typically they
  are weird sizes … keep it in the open box section. NOT ON THE HOMEPAGE."
- "Make sure those rich pages are nice … and make sure Google doesn't mark
  it as spam."

## Task (each behind its own validated switch; missing = off; ship ON)
1. **`site.static_photos`**: every static card grid (hubs/category pages,
   store pages, event pages, best-deals-today, deal-page related deals,
   anything else calling `card_html`) draws the product photo when the deal
   has an https `image_url`: the first 4 on a page eager as today, the rest
   `loading="lazy" decoding="async"`, explicit width/height (no layout
   shift), `alt` = item, `referrerpolicy="no-referrer"`, the existing
   onerror fallback to the store tile. No photo → store tile as today.
2. **Deal-page photo** (same switch): a product photo at the top of
   `_deal_page` content (above the price) when the deal has an https
   `image_url`; max ~320 px tall on phones, contained (object-fit), white
   tile in light mode and the dark-mode tile from 254 in dark; `alt` = item;
   eager + `fetchpriority="high"` (it is the page's LCP image).
3. **Related deals look right** (same switch): related grids use the
   home page's card width (min ~240 px columns, 2 per row on phones), the
   description clamped to 2 lines, at most 4 related cards.
4. **`front_page.used_tab_only`** (operator rule above): on the home page
   wall, cards with condition `used` or `open_box` never appear in All or
   the department tabs; they appear only in the Used/Open box tab and in
   search results (after new items). Gate the JS in index.html with a
   `/*used-tab-only*/…/*/used-tab-only*/` block stripped when OFF (pattern:
   `_SEARCH_AMAZON_BLOCK` in site.py); fold it into `site_version`.
   Static home cards (the no-JS fallback) follow the same rule.
5. **Category/hub pages put new items first** (same switch as 4): within
   each hub's "Deals right now", new-condition deals come before used /
   open_box / refurbished, each group in today's order.
6. **Google-safe** (no new switch needed; verify, fix only if wrong):
   ended deal pages and thin pages stay noindex (today's rules); images are
   real product photos only (never store tiles in og:image/JSON-LD); no
   duplicate `<title>`/meta description across hubs; Amazon pages obey
   `amazon_history: hide` (no price-history link/chart/lowest/highest/check
   counts/90-day stats for Amazon — operator 2026-10-17, Amazon's email).
   Add a test that the shipped config emits none of those for an Amazon deal.

## Tests
Unit/byte tests: photo count per static page type (all deals with https
image get `<img>`; first 4 eager, rest lazy; width/height present), deal
page hero image + alt, related card count ≤ 4 and clamp CSS present, hub
ordering, used/open_box absent from All and department tabs but present in
the Used tab and in search (logic test `*_logic.cjs`), OFF = today's bytes
(recorded pins set the switches off, "recorded before task 274"; never
re-pin), phone budgets unchanged (lazy images do not count), dark mode
contrast. One browser test at 390×844 and 1280×800 on a fixture site: hub
page and deal page show photos, no layout shift (CLS 0 on load), no console
errors. (The Codex sandbox cannot launch Chromium; write the test so it
skips with exit 77 there — the operator runs it for real.)

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task274-*` basetemp
(delete after each run), targeted tests only, never the full suite. No
network, no commit, no push. HANDOFF "In flight": one entry. Ship both
switches ON in config.yaml.
