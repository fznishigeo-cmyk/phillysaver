# Codex task 074: a full Disclaimer & Affiliate Disclosure page

Shipped and owned elsewhere (keep working): 061-069 (spotlight, price chart,
static fallback, phone filters + Back, saved deals, AI yield, deal-page
links, installable app). Running in parallel: 070 (`seo.py` audit/sitemap
lastmod), 071 (price watches, `engagement.js`/`index.html`), 072 (IndexNow),
073 (intake weights). Touch the footer and site/seo page lists minimally.

**Read first:** AGENTS.md, CLAUDE.md, docs/HANDOFF.md. Files:
`dealradar/publish/templates/about.html`, `privacy.html`, `how-it-works.html`,
`site-footer.html`, `dealradar/publish/site.py` (`affiliate_copy`,
`AFFILIATE_ABOUT`, static page list, footer), `dealradar/publish/seo.py`
(sitemap/static pages), `config.yaml` (`affiliate:`, `support:`),
tests that list footer links/pages (grep `privacy.html`).

## Why
The operator wants a strong, standard disclaimer page like other deal
sites have. Today every page carries the short FTC/Amazon disclosure, but
there is no full page. Write our OWN plain-English wording (do not copy
any other site's text).

## Do
1. New page `disclosure.html` ("Disclaimer & Affiliate Disclosure"), same
   look as privacy.html/about.html (shared header/footer/tokens, phone
   first, 360px clean, dark mode). Sections, short paragraphs:
   - **How we make money:** some links are affiliate links (ours or the
     source's; partner sources keep their own affiliate credit); we may
     earn a commission at no extra cost to the shopper; it never changes
     which deals qualify (deals publish only from verified facts and
     discount floors) — state this factually, matching how-it-works.
   - **Programs we use:** generated from config, only programs actually
     active: Amazon Associates (exact required sentence: "As an Amazon
     Associate, wowpriced earns from qualifying purchases."), eBay Partner
     Network (when `ebay_campaign_id` is set), Sovrn Commerce (when
     `sovrn_enabled`). Nothing hard-coded that config would contradict;
     never list a network we are not in.
   - **Prices and availability:** prices and stock change fast; checked
     at the times shown; not guaranteed; the retailer's checkout price is
     final; codes/rebates/steps can expire.
   - **No warranty / your own judgment:** information is provided in good
     faith for general information; no warranty of completeness or
     accuracy; we are not the seller; returns, shipping, warranties and
     support come from the retailer.
   - **Links to other sites:** we don't control external sites; their
     privacy policies and terms apply.
   - **Limitation of liability:** plain, short, standard wording,
     "to the extent permitted by law".
   - **Support payments:** voluntary, not tax-deductible, buy nothing
     extra (reuse the existing support note facts).
   - **Contact and updates:** hello@wowpriced.com; "Last updated" date;
     changes posted on this page.
2. Link it from the shared footer as "Disclosure" (next to Privacy) on
   every page, include it in the sitemap like privacy.html, and link it
   from the short disclosure line on deal pages and the home wall
   ("Disclosure" → /disclosure.html) without making those lines longer
   on phones.
3. No new tracking, no popups, no consent banner changes.

## Tests
Page renders with each section; programs list follows config (Amazon
only / + eBay / + Sovrn / none); Amazon sentence exact; footer link on
every generated page; sitemap entry; byte-hash baselines regenerated only
where this change alters output (say which); 360px no horizontal scroll
(structural check).

Run `.venv/bin/python -m pytest -q`; all green except tests needing local
sockets your sandbox blocks (name them). One short HANDOFF bullet. Do NOT
commit or push. End with what changed and anything unsure.
