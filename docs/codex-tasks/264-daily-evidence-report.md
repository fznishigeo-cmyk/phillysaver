# 264 — Daily evidence report: the pipeline proves its own duties (private)

## Why
Operator 2026-10-17: "thousands of things per day with perfect precision".
Today Claude checks duties by reading GitHub Actions logs by hand (posting
counts, site check lines, AI spend, Keepa tokens, photo share). Make the
system write one private, machine-readable evidence file per day instead.

## Task
1. Extend the private growth report (dealradar/growth/report.py, delivered by
   email + the private reports repo; stdout stays name-only) with a
   `duties` section for the previous and current UTC day, built from state.db
   only (no new network calls):
   - Pinterest pins posted today / daily_max, last pin time; Facebook posts
     today / 1; Instagram drafts; any posting errors (types only).
   - Runs: count of fast/hourly runs recorded, last run time, any run marked
     failed/cut short (from run_stats).
   - AI spend today vs the configured cap; Keepa tokens consumed vs plan;
     price rechecks starved (yes/no) from the token plan.
   - Live deals, photo share (same formula as the site check's photo floor),
     earning-link share (link_mix).
   - Each item with a status: ok / warn / fail against thresholds from
     config.yaml (`growth.duties` block, validated).
2. Also write the same section as JSON to the private reports repo path the
   report already uses (e.g. `reports/duties/YYYY-MM-DD.json`), so a
   scheduled check-in can read one file.
3. Nothing from this section in public logs, Actions output or the public
   site.

## Tests (offline)
Section built from a seeded DB; thresholds; missing data → "unknown" not
"ok"; stdout/stderr/log capture contains no numbers from the section;
existing report tests pass.

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task264-*` basetemp
(delete after), targeted tests only, never the full suite. No network, no
commit, no push. HANDOFF "In flight": one short entry.
