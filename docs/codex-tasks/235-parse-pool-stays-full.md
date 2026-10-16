# 235 — Keep the parse pool full (no batch convoy)

## Why

Live 2026-10-16: the hourly run stops parsing at its deadline with a growing
pile left (`stopping parse with N posts left`: 274 → 292 → 316 → 328 between
11:19Z and 14:21Z) while money is not the limit (spent $0.91 of $6.83 allowed
by 14:23Z; a Haiku read costs ~$0.0007). Throughput is falling: hourly
`posts_sent` 105 → 91 → 75 → 59 in a ~165 s parse window, fast lane 18–39 in
~90 s. No 429s in the logs.

Cause in code: `parse/extractor.py` (the `with ThreadPoolExecutor` loop,
~line 574) submits `workers` posts, then waits for ALL of them before
submitting the next batch. One slow job stalls the other five workers. The
slowest jobs are second opinions: `_extract` runs the Haiku read and then,
when it smells like a deal, a Sonnet re-read in the same future (8 per hourly
run, `max_second_opinions_per_run`). Lesson from the study (Redis event loop /
curl multi): keep a fixed number of requests in flight and refill a slot the
moment one finishes.

## Read first

CLAUDE.md, AGENTS.md, docs/HANDOFF.md. `parse/extractor.py` (`parse_posts`,
`_extract`, `_record`, the shared `reads` dict for identical fingerprints, the
deadline check, `SpendCapReached` handling, `evaluation_gap`), `parse/claude_client.py`
(spend gate, reservations, settle), `config.yaml` `parse.concurrency`,
existing parse/spend-cap/second-opinion/idempotency tests.

## Task

1. Replace batch-wait with a sliding window: at most `parse.concurrency` reads
   in flight; when any read finishes, submit the next post. Keep everything
   else identical:
   - results are RECORDED strictly in submission order (record the head of the
     queue once it is done), so pairs, stats and DB writes are deterministic
     and identical to today for the same inputs and replies;
   - identical-fingerprint posts still share one read (also across the window);
   - cached parses, structured posts and prefilter skips cost no slot;
   - the deadline check runs before each submit (same formula); posts not
     submitted stay unrecorded with `evaluation_gap` = `deadline` as today;
   - `SpendCapReached`: stop submitting, still wait for and record reads
     already in flight (they were paid for), same stats as today;
   - the pool still exits only after every submitted read settles, so no
     paid call is left unsettled when state is saved.
2. Add private counters to the parse stats: `parse_seconds` (wall time of the
   loop) and `max_in_flight`. No new config knob, no change to concurrency,
   caps, models, prompts, second-opinion rules or evidence checks.

## Tests (offline)

- Fake parser with per-post delays (one slow post among fast ones): the fast
  posts keep the other slots busy (assert submit order / in-flight count over
  a fake clock or events, not wall time), and recorded order equals input
  order.
- Same inputs and fake replies → identical pairs, stats (except the two new
  counters), cache rows and spend as the current code (compare against a
  fixture recorded from the old loop, not a copy of the new one).
- Deadline mid-stream, spend cap mid-stream, duplicate fingerprints inside one
  window, structured/cached/skipped posts mixed in.
- Rerun idempotency stays green (tests/test_e2e_offline.py::test_rerun_is_idempotent).

## Rules

Do not change tests/test_workflows.py. Run tests with -n 1 and your own basetemp
/tmp/task235-*; delete it after each run. Do not run the whole suite. Do not
commit. Update docs/HANDOFF.md "In flight" with one short entry.
