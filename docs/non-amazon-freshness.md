# Non-Amazon price evidence

`non_amazon_freshness` in `config.yaml` is enabled. Source reads become due after
12 hours without price evidence, with at most 20 credited posts per run (including
retries of the same run key). Each pass has a 60-second budget. HTML requires `allowed_html`, an exact source-owned
host, robots permission and the existing source breaker. Redirects are not followed.
Slickdeals uses RSS only, even if `allowed_html` is mistakenly enabled. Feed-only
sources must still contain the credited item; absence or a feed 304 proves nothing.

Rereads join the existing parse queue and share its post, time and AI spend caps.
Cached extracted facts still pass `verify_evidence`; changed facts use the same
parser, savings arithmetic and dedupe rules as intake. No raw article/feed bodies
are stored. A 304 or a failed read never refreshes the clock.
Successful API checks run before source intake; source rereads remain available
when those checks fail. A later lower price updates through the normal price path.

The clock uses successful evidence for the current price: official API checks and
the first time each distinct post showed that price. Rereading the same post
(changed bytes or a 304) never extends it: it can end or reprice a deal, not
re-confirm it (operator, 2026-10-14). It ignores failed attempts, unrelated prices, future
timestamps and evidence before the current price began. At 24 hours offers receive
the muted `Posted N days ago` line, sort after fresh offers and leave picks, pushes,
best-today and browser price watches. `N` is the age of the original posting, not
the evidence age. After 48 hours they leave publication but stay alive internally.
Per-store overrides use `stores: {Walmart: {stale_hide_h: 36}}`. Source/API suspects
also leave publication while awaiting the existing confirmation rules; age never
creates a death or a GONE card. Amazon's scheduler and its clocks are unchanged.

Run summaries and the private growth report show per-store before/after counts
in `<12h`, `12–24h`, `24–48h`, `48–96h`, `>96h`, plus unknown clocks and official
API/source-only coverage. Before uses the previous public set when available;
the first run explicitly labels an eligible baseline if there is no prior publish.
API coverage means the offer has a compatible official lookup path, independent
of activation/credentials; conditional prices and unsupported variants are source
only. Public locked stubs are excluded. Missing state stays unavailable.

## Official API quotas and diagnostics (checked 2026-10-14)

- [eBay's Buy API limits](https://developer.ebay.com/develop/api/buy/api_call_limits):
  Browse methods other than `getItems` share 5,000 calls/day; `getItems` has its own
  limit. Production Buy access requires eBay approval. This reader uses
  `get_item_by_legacy_id`, with 30 requests/run (they run before intake, at 1/s) and a conservative 4,500/day cap
  that also counts OAuth and retries. At 168 scheduled runs/day the sustained
  allowance averages about 27 requests/run; the run cap is burst capacity, not a
  promise to recheck every item in a large inventory on every run. Oldest checks
  rotate first, after suspects, to prevent starvation. OAuth is reused in memory.
- [Best Buy's official limits](https://developer.bestbuy.com/legal): Products and
  the listed related APIs share 50,000 calls/day and 5 calls/second. Our reader
  retains the stricter 1 request/second/host, batches at most 100 SKUs according to
  the [Products response contract](https://bestbuyapis.github.io/api-documentation/),
  and allows 20 requests/run, 45,000/day including retries. Best Buy remains OFF
  until `BESTBUY_API_KEY` is available. eBay remains ON but needs valid production
  `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET`.
- `recheck_h: 0` makes both stores due each run. Existing one-hour suspect intervals
  remain. Daily and run request counts are reserved in state before sending;
  repeated run keys cannot buy a second allowance. HTTP/token failures emit only
  status and allow-listed codes (e.g. `token:http_401:invalid_client`,
  `token:http_403:other`). Arbitrary error descriptions, credentials and URLs are
  never logged. Changing prices still requires verified API facts.

## Walmart

**Adapter not available in this change.** Walmart's official
[affiliate documentation index](https://walmart.io/docs/affiliates/introduction)
lists Product Lookup; however its lookup/authentication contract could not be
retrieved through the available documentation tools. Its documented
[catalog snapshot API](https://www.walmart.io/docs/opd/recipe-register/catalog-product-snapshot)
is a daily bulk feed, not a fresh single-product lookup. This does **not** establish
that Walmart lacks an affiliate API. No speculative endpoint, retailer-page
fallback, new dependency, credential or enabled adapter was added. A verified
current lookup/authentication/response contract and affiliate approval are still
needed before implementing the requested OFF adapter.
