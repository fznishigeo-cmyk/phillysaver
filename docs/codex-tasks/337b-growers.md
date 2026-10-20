# Task 337b — 337 is not enough (model 80 MB flat, guard 90 MB); trim the real growers

Your 337 reply: fixture cold-hour growers posts 2.396, audits 1.516, clicks 0.954, accuracy 0.500 MB/hour; cleanup added only validators/receipts/IndexNow; model 80.43 MB after VACUUM. Live is +0.35 MB/hour at 71.2 MB (hourly #260), guard 90 MB. Target: steady state < 60 MB.
Do, in the same flag `state_retention_337` with config.yaml knobs (state/retention section):
1. posts: keep what idempotency/dedup needs (run keys, post ids/hashes/urls seen, sent alerts) forever or for the existing dedup window, but drop or null out bulky per-post columns (raw text/html/LLM inputs/outputs if any are stored — CLAUDE.md says never persist full post text) for posts older than N days (default 7) that have no live deal. If full post text is stored at all, that is itself a CLAUDE.md violation: stop storing it (flagged) and purge it.
2. audits (critic/accuracy/run audits): keep N days (default 14) of detail; older → aggregate counts only.
3. clicks: keep raw click rows N days (default 30); older → daily aggregates per deal/store/source (whatever reports read). Check every reader (growth report, money report, click_signal, ranking) still works from aggregates.
4. accuracy samples: keep N days (default 14) or last K per deal.
Never touch: deals/published facts/evidence quotes, money accounting for today, run keys/sent alerts (idempotency), anything the "never remove a deal unless really gone" logic needs.
Report: per-table MB before/after on your largest fixture, modelled steady state, and confirm tests/test_e2e_offline.py::test_rerun_is_idempotent + alerts dedupe tests pass. Targeted pytest only (`--basetemp=/tmp/b337b`, delete after). No full suite, commit or push. Reply <= 8 lines.
