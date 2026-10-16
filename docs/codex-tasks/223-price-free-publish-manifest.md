# 223 — Check a tiny price-free manifest before downloading unchanged deals

## Why

`dealradar/publish/templates/index.html:3217` downloads cache-busted no-store
JSON; drops and the five-minute fallback use it. HEAD polling already avoids
some downloads while waiting (`:3334`). Preserve no-store for prices and use a
small static manifest for additional unchanged-publish checks. This borrows
Quake's baseline discipline and Varnish's stable identity, without old price
patches or a new origin. Changed publishes still need the full JSON.

## Read first

Read CLAUDE.md, AGENTS.md, HANDOFF and the
[study](../research/genius-code-study.md), Q1/V1/V2 and transport decisions.
Read site.py public serialization/versioning, seo.py _headers, home fetchPublish,
poll/drop/merge/first-load/return refresh and member dealsFor, plus existing load
fallback/clock/Back tests. Coordinate with any owner implementing proposed 202.
References are read-only: Quake msg.c:858; Varnish cache_hash.c:427 under the
paths named in the study. Do not copy their code.

## Task

1. Proposed config: `publish.manifest_refresh.enabled: false` and a bounded
   `force_full_after_s`. Omitted/false emits no manifest, flag, code, headers or
   changed site version; preserve all prior public/member bytes. Ship disabled.
2. Enabled publisher writes exactly one current `publish-manifest.json` beside
   deals.json: schema version, SHA256 of the exact UTF-8 deals.json bytes, byte
   length, published_at and site_version. No deals, prices, savings, titles,
   keys, member data, preferences or history. Produce it only from completed
   current JSON, before final audit/sealing; do not disturb publication order.
   No old snapshots/patch files, persisted baseline, KV, API or Worker.
3. Give this stable manifest URL `Cache-Control: no-cache` and noindex through
   existing _headers generation. Preserve deals.json no-store and all HTML
   revalidation rules. Do not add immutable or stale-while-revalidate prices.
   Source alone cannot establish Cloudflare validator/coalescing behavior.
4. Keep first load/static fallback dependable. After a successful public JSON
   load, remember only that validated snapshot and its exact-byte hash in page
   memory. On existing refresh triggers, fetch/revalidate the manifest at its
   stable URL; if its hash equals the last successfully loaded snapshot and the
   full-fetch backstop has not elapsed, reuse that public snapshot. No durable
   price cache/localStorage/IndexedDB/service worker. No changed polling cadence.
5. A changed manifest downloads the normal full public JSON and passes existing
   validation/adoption. Digest the raw text with built-in Web Crypto; without
   support, use the ordinary full-load path. Malformed/oversized manifest,
   invalid schema/hash/size/time, network error or missing validators gets at
   most one ordinary full-fetch fallback for that trigger. Bound manifest bytes
   before parsing, including absent Content-Length; put limits in config.
6. A manifest/body deployment race must never label the fetched body with the
   wrong hash. Validate the actual body and remember its actual digest; adopt a
   valid newer body through the ordinary path, reject regressive publication
   times and define equal-time/conflicting-content behavior conservatively.
   Never combine parts from two publications. Authentication/access transitions
   invalidate reuse; member/API responses retain their existing no-store path.
7. One in-flight transport promise coalesces concurrent existing refresh callers;
   rejection releases it for later retry. Do not replay merge/toast twice,
   advance the drop clock for an unchanged publish, or freeze changed prices,
   removals, access stubs or safety labels. Keep Back anchors, real-drop timing,
   member refresh and task-121 Amazon current-price disclaimers intact.
8. Enforce the full-fetch backstop even when a stale manifest appears unchanged.
   Keep lifecycle handling compatible with hidden/pagehide/bfcache and elapsed
   wall time. No new tracking or remote performance event; fixture measurements
   and existing private observations suffice.

## Tests and acceptance (later implementation session)

- Capture disabled baselines first: all four access modes and the existing
  enabled feature combinations, including Amazon hide/as-of. Compare filenames,
  all bytes, versions and headers. No off-mode artifact/request or repinning.
- Socket-free publisher/browser fixtures: first load, unchanged/changed publish,
  empty list, reprices up/down, removal, gone, locked/access changes, spotlight,
  site-version change, stale manifest, 404/500, malformed/oversized stream,
  invalid hash/time/schema, missing validators and unavailable Web Crypto.
- Fake responses for both directions of manifest/body races and reverse-order
  overlapping triggers. Invalid bodies keep static/current fallback; failed
  fetches cannot poison the remembered digest. Exhausted fallback never loops.
- Same run key re-render and same publish refresh must add no duplicate files,
  DB events, rows, notices, listeners or sends. Backstop expiry forces full
  refresh; member/login/logout/bfcache/return/drop keep current facts and native
  navigation. Reuse current clock/fallback/Back offline harnesses.
- Compare quiet versus changed ~2,300-deal fixtures: requests, response raw/gzip
  bytes, hashing/parse CPU and heap. Include the manifest's added round trip;
  do not promise it improves changed publishes. Keep first-card geometry and
  initial image priorities unchanged. Run offline pipeline rerun tests and
  `python -m pytest -q`; no live Pages or partner requests in this session.

## Constraints and finish

All study gates and CLAUDE.md apply: evidence/facts only, savings.py money math,
config LLM caps, Keepa reserves/clocks/death rules, polite robots/permission,
1 request/s/host/UA/backoff/no bypass, encrypted data-only state and idempotency.
No Amazon price history/alerts/watches, old price archives or durable price
cache; preserve identity predicate/hide/as-of/disclaimer, including mixed stores.
No third-party scripts, dependencies, service worker, tracker, new account,
price arithmetic in JS or new request to a merchant/source/image/API. Respect
GPC/opt-outs. No framework, forced navigation or phone layout change.

Keep off. Update HANDOFF with measured fixture costs and remaining deployment
verification in the later implementation session. Observe actual Pages headers,
transfer bytes and freshness only in a separately authorized rollout. No live
activation, commit, push or deployment is authorized here.
