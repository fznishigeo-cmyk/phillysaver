# Incremental HTTP-validator maintenance (task 222)

`retention.incremental_cache.enabled: false` ships disabled. Omitted/false
keeps the existing bulk HTTP expiry delete and compaction policy. Enabled
scheduled runs and the manual `prune` command examine at most
`max_examined_per_run` URL identities per run key, including fresh/NULL rows.
The defaults are 256 examined rows and a cooperative 0.25-second budget.
Validation requires an integer 1–4096 and finite seconds in 0.05–5; the enabled
value must be a YAML boolean. No new index, dependency, request or public field.

Each transaction visits at most 64 keys using the existing URL primary-key
index. One private `meta.http_cache_sweep` record holds the last visited key
and the sweep's high-water key. `run_stats.cache_examined`, `cache_deleted` and
`cache_completed` share the existing run retention window. Updates to those
counters, the cursor and expiry deletions commit together. No helper that
commits independently is used inside a chunk. A failed/timed-out chunk rolls
back; earlier chunks remain committed. Restarting a run key retains its examined
allowance, and a completed sweep cannot wrap again under that key.

The high-water key is captured when a sweep starts. Inserts behind the cursor
or beyond that key join the next sweep; other newly inserted keys can join the
current sweep. Deleted cursor/end rows do not prevent progress. Completion
means this sweep's key range is exhausted, not that all validators have expired
or that newly inserted keys have already been visited. The next distinct run
key may start a new sweep. Empty tables checkpoint completion too. There is no
new retained-since timestamp; unrelated retention watermarks keep their rules.

Deletion uses `url = examined_key AND fetched_at < existing_cutoff` at deletion
time. The boundary and NULL semantics match the old bulk delete. A validator
refreshed after selection survives. Fresh rows still consume examined allowance;
there is no expiry-filtered unbounded scan, OFFSET, full-table Python load or
synchronous index build. Fixture query plans use the existing covering primary
key for traversal, primary-key searches for deletes, and no temporary sort.

`run()` passes its absolute monotonic deadline minus the existing 90-second
tail reserve. Every chunk needs 10 ms of remaining scheduling headroom within
both that deadline and the configured local budget. Time is checked between
statements, and expiration before commit rolls back the current chunk. This
headroom is conservative for the offline fixtures below; it is not a prediction
of live I/O. A running statement, lock wait or commit can exceed it. This is
cooperative scheduling, not a hard SQL timeout or an entire-run deadline guarantee.
A resumed invocation receives a new local time budget but shares the durable
per-run row allowance. The manual command uses a timestamped `prune:` run key
and retains hourly compaction policy.

Enabled fast runs skip full VACUUM. Hourly and manual runs retain the existing
>10% free-pages threshold, WAL checkpoint and VACUUM behavior. Other table
retention deletes, existing row counts and hourly VACUUM remain unsliced; the
SQLite on-disk vacuum mode and encrypted restore/save scripts are unchanged.
The private run summary/log contains only examined/deleted counts, deferred and
completed flags, elapsed seconds for cache cleanup, and compaction-deferred.
For fast runs, compaction-deferred denotes the policy even without fragmentation.
Cursor URLs, validators, source text and bodies never enter that summary or
public/member data.

A local committed chunk survives process interruption/reopen. An end-of-run
remote save preserves it in the existing encrypted snapshot. Restoring an older
snapshot can repeat harmless validator cleanup. This change neither saves
unsaved paid-call accounting nor changes outgoing delivery/checkpoint semantics;
it makes no exactly-once or unattended-availability claim.

## Offline observations — 2026-10-16

Real temporary SQLite fixtures, Python 3.12.3; no production state or network.
Reproduce with `tests.test_incremental_cache.fixture_profile(path, size, expired)`.
Fresh-prefix means the first half of the ordered keys are fresh and the second
half expired. Each nonempty chunk examined 64 identities; fresh chunks deleted
0, expired chunks deleted 64. Each run examined at most 256. Timing includes
chunk SQL/commit and is observational, never a test gate. Values below are
median / p95 / maximum milliseconds from one local replay, not live timings.

| Rows | Shape | Runs / chunks to complete sweep | Examined / deleted | Chunk elapsed ms | Chunk CPU ms |
|---:|---|---:|---:|---|---|
| 0 | empty | 1 / 1 | 0 / 0 | 0.681 / 0.681 / 0.681 | 0.194 / 0.194 / 0.194 |
| 1,024 | fresh | 4 / 16 | 1,024 / 0 | 0.395 / 0.528 / 0.528 | 0.184 / 0.334 / 0.334 |
| 1,024 | fresh-prefix | 4 / 16 | 1,024 / 512 | 0.416 / 1.145 / 1.145 | 0.170 / 0.266 / 0.266 |
| 1,024 | expired | 4 / 16 | 1,024 / 1,024 | 0.863 / 1.773 / 1.773 | 0.260 / 0.619 / 0.619 |
| 8,192 | fresh | 32 / 128 | 8,192 / 0 | 0.595 / 0.892 / 1.252 | 0.309 / 0.382 / 0.633 |
| 8,192 | fresh-prefix | 32 / 128 | 8,192 / 4,096 | 0.498 / 0.883 / 4.210 | 0.209 / 0.283 / 1.549 |
| 8,192 | expired | 32 / 128 | 8,192 / 8,192 | 0.462 / 0.726 / 3.432 | 0.205 / 0.314 / 1.401 |

These fixtures support a small four-chunk default and a 10 ms cooperative
headroom; the 0.25 s local cap leaves margin for this observed work. The hard
gates are examined counts, SQL query plans/VM-work bounds, completion/replay
and transaction integrity, not elapsed time. Backlog completion here is in
eligible run counts; actual scheduled backlog age/job tail remains unmeasured.
Review and measured full-job tail behavior are required before activation.
