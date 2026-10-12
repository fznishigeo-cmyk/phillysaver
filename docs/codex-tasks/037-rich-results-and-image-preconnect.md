# Codex task 037: rich-result structured data + faster first photos

**Read first:** AGENTS.md, CLAUDE.md (store facts only; evidence-backed
prices). Files: `dealradar/publish/seo.py` (deal pages: the Product
JSON-LD near `product: dict[str, Any] = {...}`; hubs, best-today, store and
price pages; `_landing_page`), `dealradar/publish/templates/index.html`
(`<head>`), `dealradar/publish/site.py`, tests.

Goal: more of our pages eligible for Google product rich results and
breadcrumbs (more clicks from the same rankings), with no visible change.

## 1. Deal pages (`/deal/<slug>/`)
- Live deal: `Product` with `name`, `image` (the deal's published
  `image_url`, only when it has one and it is https), `brand` (`{"@type":
  "Brand","name":...}` only when the deal has a brand), `offers`:
  `Offer` with `price`, `priceCurrency` USD, `availability` InStock,
  `url` (our page), `seller` (`{"@type":"Organization","name": store}`),
  `priceValidUntil` = the publish date + 2 days (ISO date), and
  `itemCondition` (`UsedCondition` / `RefurbishedCondition` when the deal's
  conditions contain "used" or "open_box" / "refurbished" respectively, else `NewCondition`).
- Ended deal: NO Product block at all (a Product without offers is a
  Search Console error). Keep the page and its text as today.
- `BreadcrumbList`: Home → the deal's category hub → the deal.
- Values only from the published deal (never the post text); prices exactly
  as published (same string formatting as the page).

## 2. Hubs, best-today, store and price pages
- `BreadcrumbList` (Home → this page; category hubs: Home → hub).
- `ItemList` of the deal-page URLs shown on the page, in page order
  (`ListItem` with `position` + `url` only), max 30.

## 3. Home: faster first photos
- In the home `<head>`, `<link rel="preconnect" href="https://m.media-amazon.com">`
  and the same for the single most common other image host among the first
  24 cards of the published wall (computed at render; omitted when none or
  when it is our own domain). No other change.

## 4. Tests
JSON-LD parses as JSON on every emitted page type; live deal has every field
above (and omits image/brand when absent); ended deal has no Product; used /
open-box map to the right itemCondition; breadcrumbs point at real emitted
paths; ItemList URLs match the page's cards in order; preconnect hosts as
specified; `</script>`-safe escaping kept. One short bullet in
docs/HANDOFF.md under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
