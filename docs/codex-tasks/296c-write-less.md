# 296c — Write less state per run (accuracy samples, click exposure), and an R2 plan

## Why
296b bounded retention, but its own estimate (docs/research/state-size.md)
says steady state is still ~141 MB encrypted for dated tables alone, and the
encrypted state grows ~1.2 MB/hour now (64.4 MB at 07:38Z Oct 18; the push
guard refuses at 90 MB in ~20 hours). The write RATE must fall. Measured raw
growth per 20 minutes: accuracy_samples +373 KB, deal_click_shown +250 KB,
deals +455 KB (296b compaction should cut deals; confirm with a test that the
bytes written per upsert fall), keepa_recheck_history +135 KB.

## Task (each behind a config knob; missing = today's behavior; ship on)
1. accuracy_samples (writer accuracy.record; reader accuracy.report_data,
   7 completed local days): store only what report_data and any other reader
   needs (no raw payload copies), and/or aggregate per (day, source/store,
   outcome) counters if the report only needs counts/rates — prove the report
   output is byte-identical (or numerically identical) on fixtures.
2. deal_click_shown (writer clicks.observe_publication; reader clicks.features
   over first_party_clicks.lookback): record exposure in a compact form
   (e.g. per deal per day, or only deals actually on the published first
   screens/pages if that is what features needs) with identical learner
   features on fixtures.
3. Measure bytes written per run before/after on a synthetic run shaped like
   production (3,300 live deals, ~2,000 rechecks per hourly run, fast lane every
   15 minutes) and update the estimate in docs/research/state-size.md:
   steady-state encrypted size must land well below 70 MB.
4. Design note only (no code): moving state.db.enc off the git data branch to
   Cloudflare R2 (the project already uses Cloudflare): what changes in
   restore/commit_state, which secrets/permissions the operator must create,
   cost on the free tier, and a rollback path. Put it in
   docs/research/state-size.md under "R2 option".

## Tests
Equivalence tests per reader; retention + compaction from 296b stay green;
rerun idempotent (tests/test_e2e_offline.py::test_rerun_is_idempotent);
missing knobs = today's bytes. Offline, -n 1 -p no:cacheprovider, own
/tmp/task296c-* basetemp (delete after), targeted tests only. No commit/push.
HANDOFF "In flight": one entry. Read CLAUDE.md first.
