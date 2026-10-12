# Codex task 021: a page per deal + "Best deals today" (search traffic)

**Read first:** AGENTS.md, CLAUDE.md (store facts only: never post text or
images from a source; evidence-backed prices; knobs live in config.yaml;
idempotency), docs/paywall.md. Files: `dealradar/publish/seo.py` (`publish`,
`card_html`, `static_wall`, `_hub_page`, `indexnow_due`, the sitemap),
`dealradar/publish/site.py` (`render_site`, `_serialize_deal`, the teaser,
`copy_variant`), `dealradar/publish/templates/hub.html`, `hubs.yaml`,
`scripts/site_check.js` (step 8), tests in `tests/test_seo*.py`.

## Why
wowpriced.com gets almost no search traffic: Google sees a home page, 12
category hubs and a few info pages. People search for products ("Kettle
chips variety pack deal", "Ninja blender price drop"). One honest page per
live deal, plus a daily "Best deals today" page, gives search engines
hundreds of real landing pages.

## 1. Deal pages: `/deal/<slug>-<id8>/`
- `<slug>`: the item name lowercased, ASCII, words joined by `-`, at most
  60 characters, cut at a word. `<id8>`: the first 8 hex of
  sha256(dedupe_key). Stable for the deal's whole life (a renamed item
  keeps its URL: the id8 decides; build an `id8 -> path` lookup).
- Which deals get a page: every deal the public site shows in full
  (the teaser's unlocked deals; in support mode and open access that is
  every live deal). Never a locked deal. Cap `seo.deal_pages_max` (config,
  default 3000), best rank first; log the count left out.
- Content (facts we already publish in deals.json, nothing new from the
  post): H1 = item; store; sale price, regular price and what it is
  (the basis label: e.g. "90-day average on Amazon" when the deal says so,
  else "list price"); % off and $ saved; condition (used/open-box); the
  verification badge explained in one plain sentence (✓ price history /
  ✓ 2+ sources / single source); "How to get this price" steps and codes
  when `needs_steps`; first seen as an absolute date (UTC, e.g.
  "Oct 12, 2026"), never "x minutes ago"; the source credit links; a big
  **Get deal** button with the same URL deals.json carries (so the
  affiliate tag applies) and `rel="sponsored noopener"`; the site's
  current disclosure line (`meta.disclosure`) right under the button; a
  link to the deal's category hub; up to 6 related live deals from the same
  hub as `card_html` cards. No images (static HTML carries none: keep the
  store tile). Header/footer like hub.html (logo, tabs, Join free / support
  wording via `copy_variant`).
- `<title>`: "{item} — {pct}% off at {store} | wowpriced" (truncate the item
  so the title stays ≤ 70 characters). Meta description: one factual line
  with price, regular price and store. `<link rel="canonical">` to itself.
  JSON-LD `Product` with `name`, `offers` (`Offer`: price, priceCurrency
  USD, availability InStock while alive, url = the page) only; every value
  matches the visible page.
- Ended deals: while the deal row still exists and is dead, its page
  stays, says "This deal has ended" at the top, drops the Get deal button,
  links the hub instead, and carries `<meta name="robots" content="noindex">`.
  When the row is deleted the page is gone (404) and leaves the sitemap:
  the publish removes any `out/deal/*` folder not in this run's set.
- Deterministic bytes: the same deal data renders the same file (no
  timestamps of the run), so Cloudflare re-uploads only changed pages and
  IndexNow only lists real changes.

## 2. Best deals today: `/best-deals-today/`
Top 30 live deals by the site's own order (the order the wall shows
first), as `card_html` cards whose titles link to their deal pages. Title
"Best deals today ({Month D, YYYY}) | wowpriced" (UTC date of the
publish), one intro line with the count of live deals rounded like the
home page ("1,500+"), links to the 12 hubs. Indexable.

## 3. Internal links, sitemap, IndexNow
- In `card_html` (static wall, hubs, best-today, related) the item title
  links to the deal page when one exists; the Get deal button keeps the
  store link. The JavaScript wall (index.html) is unchanged.
- sitemap.xml: add `/best-deals-today/` and every alive deal page (lastmod
  = the deal's last price change or first seen); ended pages are left out.
  The 50,000-URL limit is far away; assert it.
- IndexNow: alive deal pages and best-today join `pages`, so new or changed
  ones are submitted under the existing throttles.
- robots.txt unchanged (everything public is allowed).
- `scripts/site_check.js` step 8: keep the hub picker matching only
  `/deals/<slug>/`; add: the first `/deal/` URL in the sitemap and
  `/best-deals-today/` answer 200 with a `<title>` and a Get deal link
  (never followed).

## 4. Tests
Slug/id8 stability and the 60-char cut; a locked deal gets no page; the
cap and its log; page content (title format, canonical, disclosure under
the button, sponsored rel, JSON-LD values equal the visible ones, no
`<img>`); an ended deal: noindex, no Get deal, absent from the sitemap; a
deleted deal: no file; best-today order and date; titles in hub cards link
to deal pages; byte-identical output for the same data rendered twice;
support mode wording on the pages; IndexNow lists a new deal page once.
tests/test_e2e_offline.py::test_rerun_is_idempotent stays green. Add
`seo.deal_pages_max: 3000` to config.yaml with a one-line comment; one
short bullet in docs/HANDOFF.md under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note: what changed, how many files a 1,600-deal publish
writes, and anything unsure.
