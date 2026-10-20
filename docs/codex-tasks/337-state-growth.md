# Task 337 — state.db.enc passed the 70 MB warning; find what grows and trim it before the 90 MB guard (backend only)

Read CLAUDE.md first (state.db lives on the data branch only; idempotency contract; store facts only; money caps). Previous work: docs/codex-tasks/296-state-size.md, 296b-state-growers.md, 296c-write-less.md — read them and the retention code they added before changing anything.

Live numbers (hourly runs, [commit_state] lines): enc_bytes 70,169,840 at hourly #257 (2026-10-19 19:45Z) → 71,227,392 at hourly #260 (22:37Z): ≈ +0.35 MB/hour; state.max_enc_mb 90 fails the push in about two days. Today's releases added: images.post_ingest_keepa photo lookups (331), keepa visitor reserve (311/323), photo_lookup_* fields, tool pages (static only), more Keepa product reads.

## Do
1. Measure, don't guess: write a small offline script/test helper that, given a state.db, reports per-table row counts and bytes (dbstat if available, else length() sums) and the top growers between two snapshots. Run it on the largest fixture/e2e state you can build; reason from schema + code paths about which tables/columns grow per run (caches of Keepa/API responses, run logs, price observations, click signals, photo lookup markers, critic/AI logs, link facts, history rows) and by how much per hourly run. Report the top 5 growers with evidence.
2. Add/extend retention ONLY for data that is not needed for published facts, evidence, idempotency (run keys / sent alerts must stay deduplicated), money accounting for the current day/run caps, or "never remove a deal unless it's really gone" accuracy. Knobs in config.yaml under the existing state/retention section (no scattered constants), behind ONE strict-bool flag `state_retention_337` (config.py default False = previous behaviour/bytes; config.yaml true). Prefer pruning old raw responses/logs/markers by age and VACUUM-friendly deletes in the existing maintenance step.
3. Estimate the steady-state size after the change and the new growth per hour; target < 60 MB and flat-ish.

## Tests
Unit tests for each pruning rule (keeps recent, drops old, never touches protected tables/rows), OFF = previous behaviour, tests/test_e2e_offline.py::test_rerun_is_idempotent still passes, plus a test that alerts/run keys survive pruning. Targeted pytest only (`--basetemp=/tmp/b337`, delete after). No full suite, commit or push. HANDOFF note. Reply <= 8 lines: top growers (MB/hour each), what you prune, expected size.
