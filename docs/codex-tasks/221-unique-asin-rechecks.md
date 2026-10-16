# 221 — Request each ASIN once per compatible recheck group

## Why

Task 220's [study](../research/genius-code-study.md) maps groupcache singleflight
to our pipeline, not a static-site origin stampede. `dealradar/verify.py:805`
collects one ASIN per deal; `:829` sends that list, while `:528` limits without
deduplication. Distinct offer/promo keys can share an ASIN. Existing sweep
dedupe and scheduler attempted-key guards do not remove this narrow duplication.
Keepa's billing of repeated ASIN input has not been measured; promise fewer
duplicate slots, not a particular token saving.

## Read first

Read CLAUDE.md, AGENTS.md, docs/HANDOFF.md and the study (F1/F2 and dedupe table).
Read `dealradar/verify.py` keepa_products/recheck_prices, pipeline recheck
selection, keepa_budget.py, keepa_rechecks.py and the corresponding existing
tests. Reference only: `/home/user/golang/groupcache/singleflight/singleflight.go:41`.
Respect the owners of tasks 119–121 and current scheduling decisions.

## Task

1. Add proposed `keepa.unique_asin_rechecks.enabled: false` in config.yaml and
   validated config. Omitted/false preserves the current call sequence, metrics,
   verdicts and public/member output. Do not activate it.
2. In recheck_prices only, before transport batch sizing, group deals by normalized
   ASIN within the existing new/used request-policy group. Preserve first-ASIN
   occurrence order and each group's deal order. Batch limits and affordability
   apply to unique requested ASINs, not duplicate consumers. All existing
   selected deals still receive a result or the existing no-opinion outcome.
3. Fan the single product response to every matching deal and run the existing
   judge/image/observation logic for each distinct offer. Never merge deal rows,
   variants, conditions, promos, source credits or death state. Avoid mutable
   result aliasing between consumers. Keep the product's actual observation time.
4. Keep stats, used offers, Buy Box and live/update policy separate. Do not reuse
   a stats-only product to judge a used-offer death, or a cached read as a forced
   live result. Preserve request order/class priority, reserves, used share,
   deadline headroom and first-429 stop. No new concurrency or generic cache.
5. Reserve and record each actual transport once. Apportion its accounted and
   estimated cost across unique ASIN groups and then their consumers so sums
   reconcile with the existing ledger. Preserve useful-reading deduplication;
   extra consumers must not manufacture additional independent paid samples.
   Reuse must not refresh observation clocks or satisfy two-reading deaths.
6. Add only private aggregate diagnostics: selected deal consumers, unique ASINs,
   duplicate slots avoided and actual product requests. No keys/URLs/credentials
   in diagnostics. Do not change the public payload to describe optimization.
   Scope ends at one recheck invocation; cross-phase/redirect reuse is follow-up.

## Tests and acceptance (later implementation session)

- Capture a same-checkout disabled baseline before editing. Omitted/false must
  reproduce transport order, token/observation totals and all published files in
  support, dormant open, locked visitor and member configurations. Keep existing
  enabled switches and public locked stubs intact; do not repin failures away.
- Use socket-free fake transports: two distinct deals sharing an ASIN, repeated
  ASINs crossing a would-be batch boundary, more unique ASINs than the cap,
  mixed case, absent/invalid ASIN, new+used same ASIN and distinct request options.
  Assert both request bodies and per-deal verdict/price/image/death outcomes.
- Cover partial/malformed/unknown-product answers, exception/lost response and
  429. One receipt/reservation per actual request, conservative lost-response
  debit, unchanged reserve/used-share behavior, no requests after first 429.
  Fan-out must not turn a missing product into a death or new confirmation.
- Repeat the same run key and cached product observation, then advance one hour:
  no duplicate spend allowance, rows, accuracy samples, alerts or independent
  death reading; later genuinely distinct observations still work. Run existing
  offline pipeline rerun-idempotency cases in both access modes.
- Compare fixed duplicate-rich and all-unique fixtures: actual request count,
  unique ASINs, receipt/estimated tokens, selected-deal coverage and verdict
  equality. No paid calls or claimed live saving. Then `python -m pytest -q`;
  Worker tests only if that directory was touched (it should not be).

## Constraints and finish

CLAUDE.md non-negotiables and all six study gates apply: robots/permissions,
no bot-wall bypass, 1 request/s/host, UA/backoff; verified evidence, facts only,
math only in savings.py; existing LLM caps and Keepa reserves/accounting;
encrypted data-branch state and idempotent reruns. Preserve Amazon hide mode,
identity detection and current-price as-of/disclaimer; no Amazon history,
historical cache, alerts or watches. No new request lane, dependency, third-party
script, service worker, shopper control or phone payload. Honor GPC/opt-outs.

Keep switch off. Update HANDOFF with exact scope, observed fixture results and
remaining review/live measurement under the later session's instructions. No
activation, push, deployment, network probe or external message in this brief.
