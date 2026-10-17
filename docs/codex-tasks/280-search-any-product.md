# 280 — Search any product: Amazon results on our page (Keepa), preview today

## Why
Operator 2026-10-17: visitors search "cupping", "hijabs" and get "No deals match".
Decision (00:10Z, repeated 18:00Z): "search any product" uses our Keepa tokens.
When our live deals have no match, the search shows current Amazon products for
the query on our page (photo, title, today's price, tagged link), not only the
"Search Amazon for …" button. Preview today on a hidden switch, live after the
operator's OK.

## Design (keep it small)
- A Cloudflare **Pages Function** at `functions/api/search.js` (same Pages project
  `wowpriced`; no separate Worker). It calls Keepa's product search for the query
  (US, first page only) with the key from the Pages secret `KEEPA_KEY` (never in
  the repo, the page or any log). Returns at most 10 items:
  `{asin, title, image, price_cents, as_of}` — current price only (Buy Box/new
  current from the response; skip items with no current price). No history, no
  ratings, no reviews, no sales counts, no "lowest" wording (Amazon rules; see
  DECISIONS 2026-10-17).
- Query hygiene: trim, lowercase, collapse spaces, 2–60 chars, letters/numbers/
  spaces/hyphen only; anything else → 400 without calling Keepa.
- Cost control (money caps are non-negotiable): cache each normalized query's
  result for `search.cache_hours` (config, ship 6) with the Cache API; a daily
  token budget `search.tokens_per_day` (config, ship 3000) and a per-minute cap
  `search.max_per_minute` (ship 20) enforced in the function (KV namespace
  `SEARCH_BUDGET` if available; if no KV binding, fail closed = no Keepa call).
  Budget exhausted / Keepa error / timeout (4 s) → `{items: [], reason}` and the
  page falls back to today's "Search Amazon for …" button. Price rechecks in the
  pipeline are never affected (separate budget, Keepa refill is shared: leave it
  conservative).
- Browser: when the local search has no matches and `search.amazon_results` is
  `preview` (only with `?preview=search` in the URL) or `on`, fetch
  `/api/search?q=` once per query (debounced, abortable), show a section
  "From Amazon right now" with up to 10 cards (photo, title, price, "Amazon price
  as of HH:MM" disclaimer, tagged `https://www.amazon.com/dp/ASIN?tag=…` link,
  `rel="sponsored nofollow noopener"`, target _blank), above the existing
  "Search Amazon for …" button which stays. Off = today's bytes exactly.
- Config (config.yaml, `search:` block): `amazon_results: preview`,
  `cache_hours: 6`, `tokens_per_day: 3000`, `max_per_minute: 20`. Publish the
  switch to the page the same way other switches are published.
- Deploy: the fast-lane "Deploy site to Cloudflare Pages" step must include the
  `functions/` directory (wrangler pages deploy uses `./functions` from the
  working directory) and set the secret once via
  `wrangler pages secret put KEEPA_KEY --project-name wowpriced` from the
  existing `KEEPA_KEY` Actions secret (masked; skip silently if missing).
  Document the one manual step if KV must be created in the dashboard.
- Tracking: a GA4 event `search_amazon_results` with result count only (the
  query string may be sent truncated to 40 chars, never personal data).

## Tests
- Function unit tests with a mocked Keepa response (node): normalization, 400 on
  bad queries, cache hit = no Keepa call, budget exhausted = no Keepa call and
  `items: []`, timeout → empty, no history/rating/sales fields ever in output,
  key never echoed.
- Page: off = recorded bytes (never re-pin); preview only with the URL flag;
  results render with tag + disclaimer; empty result keeps the button.
- Real-Chromium smoke (exit 77 only when Chromium cannot launch).
- Idempotent pipeline rerun unchanged.

## Rules
Offline only (no real Keepa calls), `-n 1 -p no:cacheprovider`, own
`/tmp/task280-*` basetemp (delete after each run), targeted tests only. No
network, no commit, no push. HANDOFF "In flight": one entry.
