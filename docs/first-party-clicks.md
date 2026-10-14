# First-party click signal (090, built OFF)

`first_party_clicks.enabled: false` remains the shipped default. The operator
approved collection on 2026-10-14; activation waits for the secret below.
Live ranking stays unchanged.

## Activation (097: one secret)

Operator:

1. In the GitHub repository, open **Settings → Secrets and variables →
   Actions → New repository secret**. Name it `CLICK_SIGNAL_TOKEN` and set
   it to a long random string of at least 32 characters. Save it there only;
   never paste it into chat or a log.
2. Tell Claude: "CLICK_SIGNAL_TOKEN is saved; turn on the click signal."

Engineer (after the secret exists):

1. Flip `first_party_clicks.enabled: true` in `config.yaml`, validate and
   ship the change. The endpoint already matches `newsletter.worker_url`
   and `paywall.api_url`: `https://wowpriced-subscribe.wowpriced.workers.dev`.
2. Run **deploy subscribe worker** from GitHub Actions on that revision.
   It finds `wowpriced-clicks` with `wrangler d1 list --json`, creates it only
   if missing, substitutes its UUID into the deploy copy's `CLICKS` binding,
   applies the schema, and installs `CLICK_SIGNAL_TOKEN` on the Worker.
   No manual D1 creation is needed. `CLICK_SIGNAL_D1_ID` is optional; an
   explicit repository secret overrides lookup/creation. The existing
   `CLOUDFLARE_API_TOKEN` needs **Account → D1 → Edit** for this account;
   a list/create failure names that permission without printing API output.
3. Verify `click_signal.status: ok` in the next fast/hourly run's summary.
   Both workflows already pass the same repository `CLICK_SIGNAL_TOKEN`.
   Initial hours may be unavailable while polling catches up; zero clicks
   are valid. Check coverage after the first collection hour closes.

This change leaves the switch **off**. While off, no D1 lookup, creation,
schema setup, token installation or click ingestion runs, and site output
is byte-identical. Storage stays on the existing free Cloudflare account;
the privacy and quota contracts below are unchanged.

## Collection and privacy

`click-signal.js` is emitted only with the switch and a validated endpoint.
It listens passively for Get deal clicks: dynamic wall/search, static
fallback, category/store/price/best/product hubs, primary deal links and
related cards. Keyboard, modified and middle clicks keep normal navigation.
A false return, thrown exception or unavailable `sendBeacon` simply loses
that count; there is no retry, redirect wrapper or awaited navigation.

The body has exactly these fields:

| Field | Values |
| --- | --- |
| `key` | Existing public SHA256(dedupe_key) first 16 hexadecimal characters |
| `surface` | `wall`, `search`, `hub`, `deal_page` |
| `position` | `1-8`, `9-30`, `31-60`, `61-120`, `121+`, `detail` |
| `hour` | UTC Unix hour, an integer; no finer client timestamp |

This public key identifies a deal, never a shopper. There is no user/session
identifier, search text, title, price, destination URL or raw referrer in the
body. Nothing is written to browser storage. Every click rechecks
`navigator.globalPrivacyControl` and `wp.adsOptOut === "1"`, the existing
tracking preference; either suppresses collection. Unreadable preference
storage also suppresses collection. The Worker independently rejects
`Sec-GPC: 1`, an explicit opt-out header and any request with a Cookie header.
The browser's localStorage preference is enforced in the browser: the Worker
cannot read it, and no extra preference field is transmitted.

Use our existing **wowpriced-subscribe.wowpriced.workers.dev** origin. This is
our own collection service, not a third-party analytics recipient. Its
separate registrable domain keeps wowpriced.com's GA/ad cookies away from
beacons, and the application sets no cookies there. `sendBeacon` has no
credentials-omit option; do not substitute a site subdomain that inherits
parent-domain cookies. The validator also permits a dedicated site subdomain
for an operator who has made it cookie-free; requests containing cookies
are discarded. Only the exact configured existing Worker origin or a site
subdomain can be used, with HTTPS and no credentials/query/path/port.

Neither click route reads an IP nor persists headers, UA, cookies or raw
events. The UA is checked in memory for obvious bots; surface and position
are validated then discarded. The only durable collection data is hourly
counts by deal. Workers request observability is disabled in wrangler.toml;
do not enable request logs, Tail or Logpush on these routes. Like every HTTPS
service, Cloudflare's network receives transport metadata; this application
neither collects nor stores it.

## Free storage and bounds

D1 is a Durable-Object-free store available on Workers Free: 100,000 row
writes and 5 million row reads/day, with 5 GB total storage. Its free limits
stop queries when exhausted. Counts apply per row rather than JSON size;
secondary indexes add writes, so this schema has none. See
[Cloudflare D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/).

The collection uses **zero KV operations**. KV's 1,000 writes/day cannot
safely be guaranteed by per-isolate flush timers: isolates multiply and can
be evicted before flushing. See
[KV limits](https://developers.cloudflare.com/kv/platform/limits/).
Analytics Engine and Durable Objects are not used.

`click_hours` has one INTEGER PRIMARY KEY row per UTC hour, an aggregate JSON
map of deal keys to counts, a total, and the hourly quota. One atomic SQL
UPSERT increments a count only while total is below that row's quota, even
with concurrent isolates. Quotas freeze when the hour's row is created;
config changes apply to new hours, preserving historical cap reporting.

- Default 1,000 accepted clicks/hour: at most 24,000 click writes/day.
- Hard maximum 3,000/hour: at most 72,000 click writes/day.
- Authenticated reads create at most one zero-count heartbeat row/hour and
  prune expired rows (24/day in steady state, at most 24 per read during
  catch-up). Normal maximum is therefore about **72,048 writes/day**, plus
  schema setup and bounded catch-up pruning. Limits are shared with other
  account databases: check their usage before activation.
- A row holds at most 3,000 distinct 16-character keys (roughly 66 KB at that
  cardinality). Seven days is roughly 11 MB of aggregate JSON at the maximum,
  plus SQLite overhead. No raw events or per-person rate-limit table exists.
- Best effort isolate limits precede D1: 120 requests/minute, 12 accepted
  requests/deal/minute by default; maps reset each minute and are bounded by
  the request limit. They never key by IP, UA, fingerprint or user.
- Bodies are streamed with a 256-byte cap, including absent/forged lengths.
  Invalid keys, extra fields, old/future hours, missing/foreign origins and
  obvious crawlers are silently rejected. These controls cannot authenticate
  a human: forged origins or distributed traffic can poison/saturate an
  anonymous signal. It must remain a shadow experiment.

Workers Free has an account-wide 100,000-request/day ceiling and 10 ms CPU
budget. Rejected requests still consume that request allowance; no in-Worker
limiter can eliminate ingress usage or reserve requests for subscriptions.
Saturation may also affect other Worker routes. No paid upgrade or fallback
is requested; collection fails silently and Get deal continues navigating.
See [Workers limits](https://developers.cloudflare.com/workers/platform/limits/).
Remote D1 CPU/latency and account-wide headroom still need observation after
operator-approved activation; local SQLite tests do not measure those.

## Ingestion and availability

`GET /c/counts?since=<UTC Unix hour>` requires
`Authorization: Bearer <CLICK_SIGNAL_TOKEN>`, returns `Cache-Control: no-store`
and no CORS grant, and is limited to two successful read attempts/minute per
isolate. It returns a complete interval of at most six **closed** hours,
including empty hour entries, absolute counts, `available` and `capped` flags.
A stored click or authenticated polling heartbeat provides collection
evidence; absent rows are **unavailable**, not observed zeroes. A heartbeat
proves observation during an hour, not uninterrupted service for that hour.
There are no raw-event export or public statistics routes.

Both pipelines make at most one request per run, through their existing
polite client (descriptive UA, shared host pacing, one attempt, bounded body,
no redirects/cache). This authenticated administrative API is not a crawl.
Missing configuration/token, errors or malformed responses never affect
publishing or advance the successful cursor. The attempt is remembered in the retained run ledger so a same-run retry
cannot hammer a failed endpoint, including after an intervening run.

The first read starts 24 hours back. Later reads overlap the last closed
hour to recover counts whose D1 write completed at the hour boundary, using
at most six hours per run. An outage longer than seven days explicitly loses
older remote counts; catch-up clamps to the available seven-day retention.
A backward cursor, huge count, malformed bucket or partial response is
rejected before any writes. A transaction stores absolute counts with
`MAX(old, received)`, coverage and cursor together. Retrying a response adds
no counts. SQL queries and feature consumers never reach public JSON.

Local state (encrypted `data` branch only):

| Table/key | Meaning |
| --- | --- |
| `deal_clicks(deal_key, hour, count)` | Absolute accepted aggregate; primary key `(deal_key,hour)` |
| `deal_click_coverage(hour,capped)` | Observed collection hours and quota saturation |
| `deal_click_shown(deal_key,hour,seconds,position)` | Estimated publication availability, not impressions |
| `deal_click_runs(run_key,hour,ingested,published)` | Bounded pipeline retry ledger; no browser identifier |
| `click_signal_publication` meta | Previous generated public keys/time; no visitor state |
| `click_signal_shadow` meta | Latest feature snapshot, both top-120 orders and movements |

Local hourly tables retain 30 days by default (configurable 7–90), pruned
while enabled. Remote pruning happens on reads; if polling stops, old
aggregates may remain until reads resume. Switching off resets the exposure
anchor without erasing historical counts. The latest snapshot replaces its
predecessor; this task does not create a historical ML training archive.

## Proxy, shadow and feature contract

`dealradar.clicks.features(db, cfg, now, published)` takes the current
**public, live** rows in normal wall order. Its private feature columns are:

| Features | Definition |
| --- | --- |
| `discount_pct`, `savings_usd` | Existing verified publication values; no new savings arithmetic |
| `price_band` | `<10`, `10–<25`, `25–<50`, `50–<100`, `100+` USD, or unknown |
| `category`, `store`, `condition` | Published labels and normalized condition/eligibility list |
| `stock_evidence` | Existing `social.plenty_of_stock` policy result, not quantity in stock |
| `corroboration_count` | Existing independent current-price sighting count in configured window; unknown without source row |
| `age_hours`, `confirmation_age_hours` | Since published first-seen / verified confirmation; absent or future confirmation is unknown |
| `image_present`, `position` | Published image flag and 1-based public-live wall order (includes spotlight) |
| `clicks` | Accepted deal clicks in closed hours of the configured lookback; unknown without collection coverage |
| `shown_hours`, `clicks_per_hour_shown` | Publication proxy and clicks on matching covered hour buckets divided by that proxy |
| `covered_hours`, `capped_hours` | Collection observation and saturation, not complete visitor coverage |
| `engagement_score` | `log(1 + comparable_clicks / (shown_hours + prior_hours))`, or 0 without exposure |

The previous generated snapshot owns the interval until the next generated
snapshot. Repeated run keys/out-of-order renders add nothing, hour boundaries split
seconds, and a gap over 30 minutes is unknown (no forward fill). Only hours
with collection evidence enter the denominator. Click timestamps are hourly,
so clicks cannot be matched to a partial hour's exact visible seconds.
`shown_hours` means **available in generated public inventory**, including
hub/search availability; it is neither actual viewport exposure nor proof
that a Pages deployment succeeded. It does not track impressions. Nonzero
clicks with unknown exposure are retained but do not receive a ranking boost.

Shadow order sorts all public-live candidates by
`position - shadow_weight * engagement_score` (position breaks ties), takes
120, and compares against the original top 120. Defaults are two prior hours
and weight 10; zero/unknown engagement preserves live order. The original
rows, `pick`, `ps`, paid recheck priorities, social rules and all live ranking
remain untouched. Run logs contain movement counts only; the encrypted latest
snapshot holds both orders and all moves. The existing private growth report
reads that snapshot locally, showing timestamp, accepted clicks, coverage,
cap counts and up to 20 moves. It adds no report API call or delivery path.

These are accepted **clicks**, not unique shoppers, purchases or unbiased
CTR. Privacy controls, unavailable storage, missing beacons, stale tabs,
blocking, bot filtering, quotas and outages all undercount. The anonymous
endpoint cannot prove human intent. No live ranking/ML rollout is authorized.
