# Amazon product search preview

`search.amazon_results: preview` shows current Amazon product cards only when
the visitor opens `/?preview=search` and searches for something absent from the
local live results. Normal visitors retain the existing Amazon search button.
After approval, `on` enables the cards for everyone; `off` restores the original
published bytes. An empty affiliate tag or disabled `site.search_amazon` also
disables this feature. Android's existing limited-Amazon policy still applies.

The Pages Function is `functions/api/search.js`, in the existing `wowpriced`
project. Both fast and hourly deployments run Wrangler from the repository
root, so it bundles `./functions`. Scheduled deploys never change Pages secrets.
To install or rotate the key, explicitly run Actions → **install search secret**
(`install-search-secret.yml`), which copies the existing repository `KEEPA_KEY`
secret into Pages. An absent key silently skips installation; a failed write
fails the workflow without printing command output. No key is written to an asset
or printed.

The operator has created the dashboard bindings (2026-10-17): KV namespace
`wowpriced_search_budget` and D1 database `wowpriced_search_limits`, under the
`wowpriced` Pages project:
KV as **SEARCH_BUDGET**, D1 as **SEARCH_LIMITS**. Redeploy Pages after binding.
The function creates its counter and dispatch tables automatically; there is no
separate Worker. Use the same database for both environments to share the cap.
Do not delete/reset the database during a UTC day: that would reset spending.

The extra D1 binding is necessary to honor the brief's hard money caps. KV alone
has eventual consistency and no atomic compare-and-set, so concurrent requests
in different regions could otherwise overspend. D1's conditional transaction is
the authoritative daily/per-minute counter; KV mirrors it and provides an
additional fail-closed check. Without either binding, a key, or readable published
settings, an uncached request returns empty results and makes no Keepa call.
This is the only infrastructure addition to the brief's KV design.

`config.yaml` is the source for the published, no-store `search-config.json`:
six-hour Cache API lifetime, 3,000 tokens per UTC day and 20 misses per minute.
Each miss reserves 110 tokens before dispatch (10 search tokens plus up to 100
first-page product tokens). No refunds, retries, pagination, product follow-up
requests or LLM calls. This permits at most 27 misses per day at shipped settings;
cache hits spend nothing. Failed requests retain their reservation. The four-second
deadline covers both headers and body. An oversized receipt halts that UTC day.
A shared, non-expiring D1 dispatch lease spans the upstream request, then starts
a one-second cooldown after completion and oversized-receipt accounting.
Slow KV writes, delayed storage responses
and UTC rollover cannot overlap dispatches. A terminated execution or failed
lease release fails closed: after confirming no search execution remains active,
the operator can set `held = 0, ready = <current Unix milliseconds + 1000>` in the
single `search_dispatch` row. Never clear a lease held by an active execution.
Old counter days expire; the ledger contains no queries, product facts, keys or customer data.

Only ASIN, product title, permitted Amazon image URL, positive qualified current
Buy Box/new price in cents and that reading's timestamp leave the function. Missing/stale
prices are skipped; no averages, history, ratings, reviews or sales data are
returned. Browser requests are debounced, abortable, and made once per normalized
query per page lifetime; equivalent case/spacing edits keep pending results.
The preview flag is captured before browse restoration rewrites the URL. The
fallback button stays on empty/error results. The GA4-only `search_amazon_results` event sends a count from 0–10 and no query text.

All development checks are offline. Real Keepa response/billing and Pages binding
verification remain for the reviewed deployment. Node function tests use Node 22's
in-memory SQLite to execute the actual D1 statements, including concurrent callers
with independently stale KV mirrors. Chromium smoke intercepts every request;
exit 77 means the local browser could not launch.
