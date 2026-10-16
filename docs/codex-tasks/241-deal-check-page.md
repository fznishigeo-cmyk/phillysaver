# 241 — Deal Check page at /check/ (ships off)

## Why

Operator 2026-10-16: "be like camelcamelcamel, but better", "AI sells", clean
sharp design. Task 240 built the static lookup shards (`dealradar/publish/checker.py`,
`checker.enabled: false`). This task builds the page people use: paste any
product link, get an honest answer in under a second from prices we already
verified. camelcamelcamel leads with a chart; we lead with the answer.

Amazon update (operator phone call with Amazon Associates, reference
amz378_998): price history charts, lowest recent price and Keepa as the source
are approved; price **alerts/watches were not asked** and stay off
(`amazon_watches: "off"`). Walmart stays live-only (its affiliate agreement
bans price tracking/alerts). eBay stays live-only.

## Read first

CLAUDE.md, AGENTS.md, docs/HANDOFF.md, docs/codex-tasks/240-deal-check-index.md,
`dealradar/publish/checker.py` (`aliases`, `js_rules`, `build`, `publish`,
`shard`), `dealradar/config.py` (`validated_checker`), `dealradar/publish/site.py`
(render order, `_headers`, robots), `dealradar/publish/seo.py` (page templates,
sitemap, `_headers`), `dealradar/publish/product_pages.py` (`answer`,
`READER_STYLE`, header/footer pattern), `dealradar/publish/amazon_as_of.py`,
`dealradar/publish/amazon_history.py`, templates `index.html`, `site-footer.*`,
tests/test_checker_index.py, tests/fixtures/checker_aliases.json.

## Task

1. **Amazon policy in the checker follows the operator's decision.**
   `checker.stores.amazon` may be `history` only while `amazon_history: show`
   (validator rejects `history` when hide; `link_only` always allowed). Amazon
   `history` entries carry `price` and `at` and are rendered as "Amazon price
   as of <time> — details" exactly as the site already words Amazon prices
   (reuse amazon_as_of wording/helpers, never invent new wording), plus the
   verdict/checks/since fields. Ship config `amazon: history`. Walmart and eBay
   rules unchanged.
2. **Page `site/check/index.html`** built in the publish path only when
   `checker.enabled` (off = no /check/ files, every published byte unchanged).
   - Same header/footer/brand tokens as the product pages (READER_STYLE family),
     light and dark, no new third-party script, no web font, inline CSS+JS,
     total page < 40 KB gzip. Lighthouse mobile performance ≥ 95, accessibility
     100 (check with the repo's existing lighthouse tooling if present; else
     keep to the same patterns the product pages pass with).
   - Layout (camelcamelcamel-clean): big H1 "Is this a good deal?", one-line
     subhead "Paste a product link from Amazon, Best Buy, Target, Walmart,
     eBay, Woot and more. Our AI-powered deal scanner checks it against
     prices we've verified.", one large input + "Check" button, store logos as
     plain text chips (no trademark images), then the result card.
   - Result card, answer first: the verdict sentence (`verdict` exactly as
     shipped by 240, which is `product_pages.answer()` text) in large type;
     then price, store, "checked <relative time>" (from `at`, computed in the
     browser from the ISO value; show the absolute date on hover/title), then
     buttons "See the deal" (`deal_path`) and "Price history" (`p_path`) when
     present. Ended entries say "Last verified price $X on <date> — this deal
     has ended" and never show a "buy" button. `link_only` entries show only
     item + "See the deal". Not found: "We haven't verified a price for this
     product yet." + a link to search our deals for the words in the URL slug
     (existing site search URL format) — no guessing, no external requests.
   - Lookup: JS reads the alias rules rendered into the page from
     `checker.js_rules()` (single source of truth), computes aliases exactly
     like Python, computes the shard with `crypto.subtle.digest("SHA-256")`,
     fetches `/check/data/<xx>.json` (same origin only), shows the entry.
     URL `?u=<encoded link>` prefills and runs the check (shareable result
     links); the page never sends the pasted URL anywhere but its own origin's
     shard file name. Short links (amzn.to etc.): say "Open the link first,
     then paste the full product address" — never resolve them.
   - Keyboard + screen reader: label for input, result region `aria-live=polite`,
     44px targets, visible focus.
   - SEO: indexable, canonical `/check/`, title "Is This a Good Deal? Free
     Price Checker for Amazon, Best Buy, Target & More | wowpriced", meta
     description, `WebApplication` JSON-LD (no ratings, no prices), in
     sitemap, linked from the site footer and `/p/` index only when enabled.
     Short FAQ section (3 questions: where prices come from, how often we
     check, why some products are not found) in plain words, no claims we
     can't back.
3. Private run summary gains nothing new beyond 240's `checker` block.

## Tests (offline)

- Off (default): no /check/ files, every published byte equals today's
  (extend the existing off-matches-head pattern; do not re-pin unrelated tests).
- On: page exists, sitemap/footer/robots/_headers as specified; page JS rules
  equal `checker.js_rules()` JSON byte-for-byte.
- **JS/Python parity:** a node test runs the page's alias + shard functions on
  every row of tests/fixtures/checker_aliases.json and equals Python
  `aliases()` and `shard()` (use the repo's existing node test helper pattern,
  e.g. tests/test_tracking.py `_run_node`).
- **Browser test** (Playwright, Chromium at /opt/pw-browsers, offline static
  server on a temp site built with `baseline_config("open")` + checker on):
  live hit, ended hit, link_only hit, not-found, short link, `?u=` prefill,
  keyboard-only flow, no request leaves the origin.
- Amazon validator: `history` rejected when `amazon_history: hide`.
- Rerun idempotency unchanged (tests/test_e2e_offline.py::test_rerun_is_idempotent).

## Rules

Evidence or it doesn't exist; math stays in savings.py; the page never
computes a verdict, it only displays the shipped text. No Keepa/LLM/retailer
calls. Do not change tests/test_workflows.py. Run tests with -n 2 and your own
basetemp /tmp/task241-*; delete it after each run. Run the full suite once at
the end (`-n 2`), report the exact pass/fail line. Do not commit. Update
docs/HANDOFF.md "In flight" with one short entry.
