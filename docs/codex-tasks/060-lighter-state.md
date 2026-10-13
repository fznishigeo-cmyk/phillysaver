# Codex task 060: lighter state file (compress before encrypt, compact, bound growth)

**Read first:** AGENTS.md, CLAUDE.md (state.db lives on the `data` branch
only; idempotency; surgical changes). Files: `scripts/state_crypt.sh`,
`scripts/restore_state.sh`, `scripts/commit_state.sh`, `dealradar/db.py`
(`prune`, schema), `dealradar/pipeline.py` (where prune runs),
`dealradar/persistence.py` (`checkpoint_state`), `config.yaml` (`retention:`),
`tests/test_workflows.py` (real-git tests of the scripts), README.md
(state section).

## Why
`state.db.enc` on the data branch is 52,338,720 bytes (2026-10-13) and is
pushed after every run (~every 10 min) plus before every social/Telegram
send. A pre-send checkpoint timed out live at 60 s (now 150 s). The file is
SQLite encrypted with `openssl enc -aes-256-cbc` with NO compression, and
nothing ever runs VACUUM, so deleted rows (posts_seen, dead deals,
post_parses) leave free pages that are pushed forever. SQLite text/JSON
usually compresses 3-5x. Tables that grow without bound today: `llm_calls`,
`llm_reservations`, `run_stats`, `http_cache`, `outgoing`, `alerts_sent`,
`price_history`.

## Do
1. **Compress before encrypting, backward compatible.**
   `state_encrypt` writes gzip (`gzip -n -6`, deterministic header) then
   encrypts. `state_decrypt` decrypts, then: if the plaintext starts with the
   gzip magic (1f 8b) decompress it; if it starts with "SQLite format 3"
   use it as is (old files); anything else fails. `restore_state.sh` keeps
   its fail-closed check (a decrypted file that is not SQLite -> exit 1,
   never start empty). `commit_state.sh` `same_state` must compare the
   DECOMPRESSED plaintext with state.db (otherwise every run commits).
   Old uncompressed `state.db.enc` restores; the next commit writes the new
   format; a corrupt gzip fails closed.
2. **Compact when fragmented.** After `prune` (and only there), if
   `PRAGMA freelist_count` is more than 10% of `PRAGMA page_count`, run
   `VACUUM` (outside any transaction; WAL checkpoint first). Log page counts
   before/after.
3. **Bound growth** with retention knobs in `config.yaml` `retention:`
   (defaults shown; justify each in a comment):
   - `llm_calls_days: 35` for settled/released/uncertain rows (the daily cap
     needs only today and tomorrow; reports use 7 days). NEVER delete a
     `reserved` (pending) row, and never delete rows of the current or next
     UTC day. Same rule for `llm_reservations` if it is per call.
   - `run_stats_days: 60`, `http_cache_days: 30` (by fetched_at),
     `outgoing_days: 60` for `sent`/`rejected` rows only (never `pending`,
     which guards against resending an uncertain delivery).
   - Leave `price_history` and `alerts_sent` alone (verification and
     dedupe depend on them); just report their row counts.
4. **Measure.** commit_state.sh logs `state.db <bytes> -> state.db.enc
   <bytes>` each commit. The run summary gains `state_db_bytes`.

## Keep exactly
Fail-closed restore; key never on a command line or in a log (keep
`-pass env:STATE_DB_KEY`); data branch only; snapshot/retry semantics of
commit_state.sh; the 054 ledger semantics (spend caps must still see every
call of the current and next UTC day); idempotent re-runs
(tests/test_e2e_offline.py::test_rerun_is_idempotent).

## Tests (real git + real openssl/gzip, like the existing script tests)
- old-format (uncompressed) state.db.enc restores; next commit writes gzip;
  that restores byte-identical; unchanged state -> no new commit.
- wrong key / corrupt gzip / non-SQLite plaintext -> restore exits 1 and
  leaves no state.db.
- VACUUM runs only above the 10% threshold; data intact after it.
- Retention: pending ledger rows and today/tomorrow rows survive; pending
  outgoing survives; old settled rows go; spend_usd_for_day unchanged for
  kept days.

Run `.venv/bin/python -m pytest -q`; all green except tests needing local
sockets your sandbox blocks (name them). One short HANDOFF bullet and the
README state paragraph. Do NOT commit or push. End with what changed, the
measured compression ratio on a synthetic DB, and anything unsure.
