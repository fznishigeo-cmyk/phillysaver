# 226 — Compare a time-decayed click score privately, without changing ranking

## Why

`dealradar/clicks.py:188` sums a flat lookback window; `:224` smooths click rate
by generated-publication hours and `:228` produces a shadow order. Existing
collection/ingestion is already bounded and anonymous, and stays off by default.
Redis sorted sets, Monolith feature expiry and SimClusters timestamped sparse
scores suggest comparing fading feedback. They do not supply unique users,
co-click sessions, impressions, purchase attribution or a cold-start graph.

## Read first

Read CLAUDE.md, AGENTS.md, HANDOFF, docs/first-party-clicks.md and the
[study](../research/genius-code-study.md), R3/R4/B1–B3/S1–S3.
Read clicks.py, Worker clicks.js, publish/click_signal.py, click-signal.js,
published eligibility, existing shadow report and volatility click-boost gates.
Read-only references: Redis t_zset.c:1539; Monolith cuckoo_embedding_hash_table.cc:249;
SimClusters summingbird/common/Monoids.scala:321 at paths in the study.

## Task

1. Proposed nested config: `first_party_clicks.decayed_shadow.enabled: false`,
   finite `half_life_hours`, `lookback_hours`, `prior_hours`,
   `min_comparable_hours`, `min_accepted_clicks` and a maximum shadow position
   boost. Validate bounds once in config. Parent collection must already be
   enabled/configured; this flag cannot activate it, add reads or create data.
   Omitted/false preserves existing shadow/report and all public/member bytes.
2. Add a separate private decayed comparison; keep the current flat-window
   features/shadow intact. Evaluate at one completed UTC hour boundary. Use
   only hours within the configured window that have known, uncapped coverage
   and matching generated-publication exposure. No incomplete current hour,
   future hour or click-only hour with no comparable exposure in the rate.
3. For eligible hours h, set weight `2 ** (-(t - end_of_hour(h))/H)` with units
   consistent with H. Sum weighted absolute clicks E and publication hours T
   over the same eligible hours. If T>0 and support thresholds pass, score
   `log1p(E/(T + prior_hours))`. Preserve original wall position as prior/tie
   breaker and bound each proposed movement. Do not compute price/savings math.
4. Report unavailable versus insufficient support versus measured score clearly.
   Missing/capped coverage is not zero engagement. A measured zero requires
   comparable observed hours and no accepted clicks; a zero-click new item
   keeps its content/quality prior, never disappears for lacking clicks.
   Count support in accepted clicks/hours, never unique people or impressions.
5. Recompute from absolute buckets each run at the common evaluation time;
   never increment a stored decayed total with a re-downloaded bucket. Use
   exact key maps plus one ordinary Python sort, no Redis server/skiplist,
   embedding framework, online training or new aggregate ingestion endpoint.
6. Consider only current public promotion-eligible live deals; preserve existing
   confirmed-price/checkout/condition/photo/earning/niche safety gates. Do not
   use hidden, locked, suspect, dead or unconfirmed-label rows to suggest a
   promotion. No public hot badge/order, category-hub reshuffle, social selection,
   alert, Keepa priority, volatility boost or LLM decision changes.
7. Add bounded private report fields: evaluation/window, comparable/omitted/
   capped hours, support status, current/shadow key positions and moves. New
   comparison records hold keys/counts/score/coverage only, not prices, product
   text, user/session identifiers or interest profiles. Repeated run replaces
   its result; respect existing retention. Parent-off produces no new record.
8. Keep Worker/beacon/privacy/abuse contracts unchanged. Do not add HLL, IP/UA
   hashes, cookies, joint-session tracking or uploaded follows. Content-only
   routing to existing categories remains today's cold-start solution; local
   Followed support belongs to proposed 201 and is not implemented here.

## Tests and acceptance (later implementation session)

- Same-checkout four-mode published trees must match for parent-off, child-off
  and enabled shadow. Flat-window shadow/report must match when child is off;
  enabled adds only its private comparison. No output hash repinning.
- SQLite fixtures/fake time: constant rate, old spike/recent spike with equal
  totals, half-life advance, window expiry, zero clicks with coverage, new item
  without coverage, missing/capped hours, partial exposures, boundary/current/
  future hours, repeated ingestion and changed absolute counts. Check formula
  against independently calculated small examples and explicit unknown states.
- Replay same run key twice, then later time with no new events: identical first
  result, no doubled count, appropriate later decay and bounded retained rows.
  One-key abuse bursts cannot exceed movement limits; no unsupported niche item
  loses its baseline admission. Stable ties and current safety gates hold.
- Verify public/member payloads, real pick order, source/social/Keepa selection,
  existing usage/call counts and anonymous Worker contract remain unchanged.
  Existing GPC/opt-out/internal gates retain coverage semantics; do not simulate
  missing beacons as measured non-interest. Use no live D1, APIs or new sends.
- Fixed ~2,300/5,000-public-deal fixtures: query count, rows scanned, CPU/memory,
  support/coverage distribution and proposed moves. No claimed CTR, purchase,
  retention or uniqueness improvement. Run offline pipeline idempotency cases
  and `python -m pytest -q`; if Worker code was touched, explain why and run
  `node --test cloudflare/subscribe-worker/` as well. Prefer no Worker changes.

## Constraints and finish

All study gates and CLAUDE.md apply: robots/permissions, 1 request/s/host,
UA/backoff/no bypass; verified evidence, facts only, savings.py arithmetic;
config LLM caps and Keepa reserves/clocks/death rules; idempotency, encrypted
data-only state. No Amazon history, price alerts, watches, price-change notices
or cached price baselines; preserve shared identity/hide/as-of/disclaimer.
No new third-party scripts, dependencies, services, identifiers, service worker,
accounts, shopper UI or phone bytes. Keep source credits and all opt-outs.

Keep both activation decisions unchanged. Update HANDOFF in the later session
with fixture results and unknown live sample. A public hot score/local learning
or any new collection needs a separate brief and rollout decision. No network,
activation, commit, push or deployment is authorized by this brief.
