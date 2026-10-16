# 234 — Hands-off scorecard: one daily number for "runs on its own"

## Why

Operator goal: hand the site over from ~90% to ~99% unattended. Today we have no
number for it, and two misses were found by hand on 2026-10-16:
- the daily report email failed twice while the run stayed green (fixed in
  ded9a30, which now turns the run red);
- the hourly run stopped parsing at its deadline with a growing pile of posts
  left (log lines `run deadline reached: stopping parse with N posts left`:
  274 → 292 → 316 → 328 between 11:19Z and 14:21Z; fast lane 17–65 per run).
Nothing in the report shows either. We need a short private scorecard that says
whether the system ran by itself yesterday, and what got worse.

## Read first

CLAUDE.md, AGENTS.md, docs/HANDOFF.md. `dealradar/db.py` (`run_stats` schema,
`start_run`, `finish_run`, the ALTER-column migration pattern near
`products_backfilled`, `llm_calls` statuses), `dealradar/pipeline.py` (where
`finish_run` is called and where the parse stats with `evaluation_gap` come
back from `parse/extractor.py`), `dealradar/growth/report.py` (`collect`
sections, `_plain_summary`, `_collapse_unmeasured`, status footer),
tests/test_growth_report.py, tests/test_e2e_offline.py::test_rerun_is_idempotent.

## Task

1. Record per run, in `run_stats` (new INTEGER columns, default 0, migrated the
   same way as `products_backfilled`): `posts_left` = posts whose
   `evaluation_gap` is `deadline` this run, and `deadline_hit` = 1 when parse
   stopped at the deadline. Same run key twice → same row values, no new rows.
2. New report section `hands_off`, state.db only (no new request, secret,
   dependency or public file). For the report day (UTC, yesterday) and the
   7 days before it:
   - `hours_published`: UTC hours with at least one finished run
     (`finished_at` set) out of 24;
   - `longest_gap_min`: longest gap between consecutive finished runs of any
     kind, including from the last finish to the end of the day;
   - per kind (`fast`, `hourly`): runs started, runs finished, runs started
     but never finished;
   - `posts_left`: the last finished run's value per kind, the day's maximum,
     and the change against the previous day's last value (growing backlog);
   - `ai_calls_lost`: `llm_calls` rows that day still `uncertain` or `reserved`.
3. Plain summary: one line at the top of the operator summary, e.g.
   `Hands-off: 24/24 hours published; longest gap 38 min; parse backlog 328
   (+54 vs day before); 3 runs never finished; 12 AI calls lost.` Add a one-word
   verdict first: `OK` when hours_published is 24, no run went unfinished and
   the backlog did not grow; otherwise `CHECK`. Missing data says `unknown`,
   never 0.
4. Leave every other section, delivery, the run's exit code and all published
   site bytes unchanged.

## Tests (offline)

- Fixture runs across two days: gaps, an unfinished run, a growing backlog →
  exact numbers and the `CHECK` line; a clean day → `OK`.
- Empty/missing run_stats → `unknown`, section status ok, no crash.
- Migration on an old state.db without the new columns; rerun idempotency of
  the run_stats values (extend, don't weaken, the existing idempotency test).
- No post text, prices or URLs in the section.

## Rules

Do not change tests/test_workflows.py. Run tests with -n 1 and your own basetemp
/tmp/task234-*; delete it after each run. Do not run the whole suite. Do not
commit. Update docs/HANDOFF.md "In flight" with one short entry.
