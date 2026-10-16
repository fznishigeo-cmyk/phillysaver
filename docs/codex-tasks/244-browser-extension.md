# 244 — wowpriced browser extension for Chrome and Firefox (one codebase)

## Why

Operator 2026-10-16: an extension like camelcamelcamel's "camelizer", but
better: when a shopper is on a product page, show our honest answer ("Lower
than usual — $X is 23% below its usual $Y") and a link to our price history.
This is a traffic product, not a coupon/affiliate toolbar: Honey-style
affiliate-cookie replacement is out (lawsuits 2024–25; Chrome Web Store
affiliate rules tightened in 2025). Data comes from the static Deal Check
shards built by task 240/241 (`/check/data/<xx>.json` on wowpriced.com).

## Read first

CLAUDE.md, AGENTS.md, docs/HANDOFF.md, docs/codex-tasks/240-deal-check-index.md,
docs/codex-tasks/241-deal-check-page.md (if present), `dealradar/publish/checker.py`
(`js_rules`, `aliases`, `shard`, entry fields and store policies),
tests/fixtures/checker_aliases.json, `dealradar/__main__.py` / CLI pattern,
`dealradar/publish/templates/privacy.html`, brand/ assets.

## Task

1. **`extension/`** — Manifest V3, plain JS (no bundler, no npm runtime deps),
   works unmodified in Chrome and Firefox (`browser_specific_settings.gecko`
   id `extension@wowpriced.com`, `strict_min_version` 121; use `chrome.*` with
   the Firefox-compatible subset).
   - `content_scripts.matches` only the product-page URL shapes the alias
     rules support: Amazon `/dp/`, `/gp/product/`; Best Buy `/site/…/<sku>.p`
     and `/product/…/sku/<id>`; Target `/p/…/A-<tcin>`; Walmart `/ip/…`;
     eBay `/itm/…`; Woot `/offers/…` (and their `www.`/regional subdomains the
     rules accept). No `<all_urls>`, no `tabs`, no `cookies`, no `webRequest`,
     no `scripting`, no remote code. `host_permissions`: `https://wowpriced.com/*` only.
   - The content script reads **only `location.href`** (never page DOM, prices
     or cookies), computes the alias + shard with the rules, asks the
     background service worker to fetch `https://wowpriced.com/check/data/<xx>.json`
     (cache per shard for 30 min in `chrome.storage.session`), and if an entry
     exists shows a small, dismissible corner card in a closed Shadow DOM:
     brand mark, verdict sentence (exactly the shipped `verdict` text; never
     computed), price + "checked <relative time>" for non-`link_only` entries,
     and one button "Price history on wowpriced" → `p_path` (else `deal_path`)
     opened in a new tab with `?ref=ext`. Not found → show nothing (no nag).
     Amazon pages: until Amazon answers the extension question (operator call,
     reference amz378_998), Amazon entries show **only** the item name + the
     button, no price, no verdict — controlled by one constant
     `AMAZON_MODE = "link"` in `rules.json`.
   - Never modify the host page's links, never inject or replace affiliate
     tags, never set cookies, no analytics, no tracking pixels. Per-site "hide
     on this site" toggle and a global off switch in the popup
     (`chrome.storage.sync`).
   - Popup: paste-a-link Deal Check (same lookup code), link to
     wowpriced.com/check/.
   - Icons 16/32/48/128 generated from the existing brand mark in `brand/`
     (Python + Pillow if already a dependency, else commit PNGs rendered once
     with Playwright from the SVG).
2. **Single source of truth for rules:** `python -m dealradar build-extension`
   writes `extension/rules.json` from `checker.js_rules()` (+ `AMAZON_MODE`,
   `version` from `extension/manifest.json`) and a reproducible zip at
   `dist/wowpriced-extension-<version>.zip` (sorted entries, fixed mtimes,
   byte-identical on rerun). A test fails if the committed `rules.json`
   differs from a fresh build.
3. **Store listing kit** in `extension/store/`: `listing.md` (name "wowpriced
   — Is this a good deal?", short + long description in plain words, single
   purpose statement, permission justifications one line each, data-use
   disclosures: collects nothing), and `PUBLISH.md` — a click-by-click
   checklist for the operator for the Chrome Web Store ($5 developer
   registration) and Firefox Add-ons (free; source upload not needed because
   no minification). Screenshots 1280×800 rendered by Playwright from a local
   fixture page into `extension/store/screenshots/` (no real retailer pages
   are fetched).
4. **Privacy page** on the site: add an "Browser extension" section to the
   existing privacy page (collects nothing; requests only shard files from
   wowpriced.com; the shard file name reveals 2 hex characters of a hash, not
   the product). Bytes of every other page unchanged.

## Tests (offline)

- Node: content/popup lookup functions on every row of
  tests/fixtures/checker_aliases.json equal Python `aliases()`/`shard()`.
- Manifest lint: MV3; permissions exactly as above; matches cover every
  fixture store URL that has a store alias and none of the short links;
  no `eval`/`new Function`/remote script URLs anywhere in `extension/`.
- Playwright (Chromium at /opt/pw-browsers, `--headless=new`,
  `--load-extension`): a local static server serves a fake product page at a
  matched path via a host-resolver rule and a fake shard; the card appears
  with the verdict, the button URL is correct, the dismiss works, host page
  links are byte-identical before/after, and no request goes anywhere except
  the fake wowpriced origin. If extension loading is impossible in this
  sandbox, test the content script against a stubbed `chrome` API in node and
  say so.
- `build-extension` is byte-identical on rerun; site output unchanged except
  the privacy page.

## Rules

No retailer requests from our servers, no page scraping in the browser (URL
only). Do not change tests/test_workflows.py. Run tests with -n 2 and your own
basetemp /tmp/task244-*; delete it after each run. Run the full suite once at
the end (`-n 2`), report the exact pass/fail line. Do not commit. Update
docs/HANDOFF.md "In flight" with one short entry.
