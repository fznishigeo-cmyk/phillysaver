# 296 — Keep state.db.enc well under GitHub's 100 MB file limit

## Why
The encrypted state on the `data` branch grows ~4.5 MB/day: 54.5 MB (Oct 17,
fast #541) → 59.0 MB (Oct 18 03:33Z, hourly #217; raw state.db 254.8 MB).
GitHub warns above 50 MB and rejects files above 100 MB: at this rate the
push that commits state fails around Oct 27, and every run after it loses its
state. Task 060 added gzip-before-encrypt, VACUUM and some retention — read
what exists first (dealradar/persistence*, scripts/commit_state.sh, config
`state:`/retention keys).

## Task
1. Size report: commit_state prints one line per run with the top tables by
   bytes (sqlite `dbstat`, or page counts if dbstat is unavailable) and the
   gz/enc sizes, so we can see what grows. No secrets, no row contents.
2. Retention for the biggest growers (likely sightings, price history,
   http/fetch caches, AI caches, social/IndexNow receipts — confirm from the
   schema and code paths): config knobs under the existing retention section
   with conservative defaults (keep everything any published page, savings
   rule, Deal Check, dedupe or idempotency check reads; prune only rows older
   than what those readers look back). Missing knob = today's behavior.
   Never delete what the evidence check (`parse/extractor.verify_evidence`),
   idempotency (tests/test_e2e_offline.py::test_rerun_is_idempotent) or money
   caps need.
3. A guard: if state.db.enc would exceed `state.max_enc_mb` (ship 90), the run
   fails loudly before pushing (better a red run than a lost state), and a
   warning line appears above `state.warn_enc_mb` (ship 70).
4. Estimate (documented in docs/research/state-size.md) how many days of
   headroom the shipped defaults buy, using a synthetic DB shaped like the
   numbers above.

## Tests
Retention keeps every row a reader needs (fixtures for each reader); rerun
idempotent; guard trips at the limit; size line format; missing knobs =
today's behavior. Offline only, `-n 1 -p no:cacheprovider`, own
`/tmp/task296-*` basetemp (delete after), targeted tests only. No network,
no commit, no push. HANDOFF "In flight": one entry. Read CLAUDE.md first.
