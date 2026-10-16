# 222 — Slice HTTP-validator cleanup and keep compaction off the fast tail

## Why

`dealradar/pipeline.py:2761` calls pruning after publication;
`dealradar/db.py:1312` deletes all expired HTTP validators and `:1330` can
VACUUM the whole DB. The fast workflow has a 12-minute total limit, including
state save/deploy. Redis bounds empty scans as well as successful work. Start
with one nonessential table; do not rewrite all retention or paid scheduling.

## Read first

Read CLAUDE.md, AGENTS.md, docs/HANDOFF.md and the
[study](../research/genius-code-study.md), especially R1, Q2 and interruption
limits. Read DB.prune, http_cache schema/validator methods, pipeline deadlines,
run_stats/product backfill, config retention and state scripts. Reference only:
`/home/user/redis/redis/src/dict.c:406` and `:449`.

## Task

1. Add proposed `retention.incremental_cache.enabled: false`, finite validated
   `max_examined_per_run` and `budget_s` in config.yaml. Choose/document small
   bounds from offline fixtures; do not invent live timings. Omitted/false
   preserves current pruning/compaction and public/member output. Ship off.
2. Enabled mode replaces only http_cache's bulk expiry DELETE with a bounded
   sweep. Visit at most the configured number of rows, including fresh rows
   that do not delete. Use primary-key keyset traversal, a persisted last-visited
   key and a sweep high-water mark; no OFFSET or full-table Python load. Newly
   inserted rows behind the cursor join the next sweep. Wrap a completed sweep
   without looping into another full allowance in the same run.
3. Apply the same existing fetched_at cutoff/null semantics. Delete only examined
   identities still expired at deletion time, so a refreshed validator survives.
   Cursor, deletion and a durable per-run examined allowance commit together in
   a small transaction. Resume from completed chunks; failed chunks roll back.
   Include visited-but-not-deleted progress, empty tables and completion states.
   Use run_stats or equally bounded existing run bookkeeping for the allowance;
   do not create an unbounded maintenance ledger.
4. Pass the remaining monotonic deadline from run() and stop before the existing
   tail reserve; start no slice when it cannot fit. Bound SQL work as well as
   Python iteration, checking query plans on the fixture. Prefer the current
   url primary-key index; do not create a large new index synchronously on every
   run. A time check between statements is cooperative, not a hard SQL timeout.
5. With the switch enabled, skip full VACUUM on fast runs only; retain the
   existing hourly compaction behavior. Preserve all other prune predicates and
   retention windows. Report that other table deletes/hourly VACUUM remain
   unsliced; this change cannot guarantee the entire run meets its deadline.
   Do not VACUUM inside each slice or change SQLite's on-disk vacuum mode.
6. Private aggregate summary only: examined, deleted, deferred/completed,
   elapsed and compaction-deferred. No cursor URL/body or source text in logs
   or public deals.json. Never advance a completion/retained-since marker for
   work still pending. Leave other retention watermark semantics unchanged.

## Tests and acceptance (later implementation session)

- Disabled and omitted modes preserve existing DB cleanup and every public file
  across four access modes, with task-119/121 switches retained. No fixture hash
  repinning. Existing encrypted restore/save behavior remains unchanged.
- Temporary SQLite fixtures with many fresh rows before expired rows, all fresh,
  all expired, empty table, NULL timestamps, inserted-before/after-cursor rows,
  refreshed rows, cursor row deletion and multiple sweep wraps. Assert examined
  work is bounded even when no row expires, and every old row is eventually
  reached across runs. Verify keyset query plans do not require full sorting.
- Fake monotonic clock and interruption injected before transaction, during
  delete/cursor update, after commit and before end-of-run remote save. Reopen
  the fixture and replay the same run key: no extra examined allowance, no lost
  pending rows and no false completion. Restoring an older snapshot may repeat
  harmless cleanup; do not assert it recovers unsaved paid calls.
- Assert no fast-run VACUUM when enabled, unchanged hourly/disabled compaction,
  and untouched live offers, prices, current/reserved LLM spend, sent/uncertain
  outgoing claims, products and IndexNow receipts. Test legacy pruning too.
- Record slice examined/deleted counts, CPU/elapsed distribution and backlog
  completion for fixed sizes. Use deterministic work limits as gates, not a
  fragile wall-clock benchmark. Then run offline pipeline idempotency cases and
  `python -m pytest -q`. No real state.db, network or paid calls in fixtures.

## Constraints and finish

CLAUDE.md and all study gates apply: polite robots/permission/1 request/s/host,
no bypass, UA/backoff; evidence verification, facts only and savings.py math;
config LLM caps and Keepa reserves, first-429 stop and observation/death rules;
idempotency and encrypted state on data only. No Amazon history/alerts/watches
or archived price data; preserve Amazon identity, hide mode and current-price
disclosures. No new third-party script, dependency, shopper UI, tracking,
service worker or phone bytes. No changes to source scheduling, parsing budgets,
paid rechecks, outgoing delivery or workflow timeout/concurrency.

Keep disabled, document what is still unsliced and update HANDOFF in the later
implementation session. Review and measured full-job tail behavior precede any
activation. This brief authorizes no live action, commit, push or deployment.
