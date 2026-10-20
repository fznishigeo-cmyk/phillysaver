# Task 337c — state_retention_337 breaks 7 pipeline tests (full suite 2026-10-20 03:16Z)

Read CLAUDE.md first (idempotency is a contract; store facts only; money caps). HEAD 21d30cd = 337b (state_retention_337 ON in config.yaml) on top of 5bcd191 (live). On 5bcd191 these all PASS; on 21d30cd they FAIL (reproduced serially, not load):
- tests/test_bounded_prefilter.py::test_disabled_exact_pre_edit_calls_reservations_decisions_and_four_public_trees[omitted] and [False]
- tests/test_bounded_prefilter.py::test_enabled_adversarial_eligibility_change_is_capped_and_public_bytes_stay_identical
- tests/test_bounded_prefilter.py::test_unknown_failed_pipeline_repeat_and_later_resume_add_no_duplicate_paid_work
- tests/test_newsletter_inbox.py::test_real_pipeline_defers_multi_deal_ack_then_idempotently_retries_failed_ack
- tests/test_fast_lane_time_budget.py::test_fast_lane_tiered_replay_stops_parse_and_critic_and_is_idempotent  (posts_seen COUNT 0 == 9 fails: "Every post that was read is recorded")
- tests/test_spend_pacing_shadow.py::test_on_keeps_pacing_critic_keepa_caps_and_retries_deferred_hashes

Do:
1. For each, find the exact cause. Decide per test: (a) a REAL regression (a post read but not recorded, duplicate paid work, a lost ack/reservation/deferred hash, changed public bytes, a cap bypassed) → fix the product code; or (b) the test reads a table/column that 337b intentionally compacts while the behaviour is intact → keep the behavioural assertion and point the check at the compact representation through the db's own reader (never delete or loosen the behavioural part: "every post read is recorded", "no duplicate paid work", idempotent retry, public bytes identical, caps hold). OFF-bytes/pre-edit tests: with state_retention_337 False the old bytes/calls must be exactly preserved — if a test exercises the shipped config but pins pre-337 internals, isolate the flag like other sibling pins (set it False in that test) AND add the equivalent ON assertion.
2. Prefer (a)-style fixes in code when in doubt: posts_seen is part of dedup/idempotency (CLAUDE.md) — if 337b stopped writing posts_seen rows for read posts, that is a regression to fix, not a test to change.
3. Run: those 7 tests, tests/test_state_growers*.py, tests/test_state_retention_337*.py, tests/test_e2e_offline.py (incl. test_rerun_is_idempotent), tests/test_newsletter_inbox.py, tests/test_bounded_prefilter.py, tests/test_fast_lane_time_budget.py, tests/test_spend_pacing_shadow.py, tests/test_db*.py, tests/test_pipeline*.py. `--basetemp=/tmp/b337c`, delete after. No full suite, commit or push.
Reply <= 8 lines: per test (a) or (b) with one-line cause, and the pass counts.
