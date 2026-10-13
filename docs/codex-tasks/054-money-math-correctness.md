# Codex task 054: fix the audit's money, math and correctness findings

**Read first:** AGENTS.md, CLAUDE.md (evidence or it doesn't exist; math is
code, never the LLM, per SAVINGS_RULES.md; money has caps; idempotency is a
contract; knobs in config.yaml). Then `AUDIT-051.md` at the worktree root
(Codex audit of 2026-10-13, report only, NOT committed: the repo is public;
never copy it into docs/). Each finding has evidence, a failing scenario, a
smallest fix and failing tests.

Fix findings **9, 10, 11, 12, 13, 16, 17, 19, 20, 22, 23**. Task 053 does the
others in parallel; do not touch link resolution/affiliate rewriting,
robots/politeness, social image/credit rules, logging of credentials or the
state scripts.

## Operator rules that decide the judgement calls
- **Finding 9:** a published checkout price comes only from evidence-verified
  numbers; an unverified input drops the deal (never guess). Never weaken
  `parse/extractor.verify_evidence`.
- **Finding 10:** a Keepa deal without a critic verdict is not published
  (fail closed); it is retried on a later run, never silently passed.
- **Findings 11, 12, 13:** a merge never moves one variant's price onto
  another product's link; a later higher price or an ended-deal update
  retires the stale price; "independent sources confirmed" requires different
  source sites AND the same price within the existing tolerance.
- **Findings 16, 17:** spend is reserved before a call (worst case for the
  model actually used) and settled after; a failed call still records what
  was billed; each call is charged to its own UTC day. The per-run and
  per-day caps in config.yaml are never exceeded or bypassed.
- **Findings 19, 20:** re-running the same run key creates no extra social
  posts or deliveries; an accepted delivery is never re-sent after a lost
  response or failed state save (record before/after with an idempotency
  key; prefer missing one post over sending twice).
- **Finding 22:** one rounding rule for the same percentage everywhere.
- **Finding 23:** used / open-box / refurbished exclusions hold whatever the
  condition's spelling or formatting (normalize once).

## Tests
Port each finding's failing tests into the regular suite (offline, existing
fixtures). They fail before your fix and pass after. Keep
`tests/test_e2e_offline.py::test_rerun_is_idempotent` green; update byte-hash
baselines only from real output changes. One short HANDOFF bullet under
"In flight".

Run `.venv/bin/python -m pytest -q`; all green except tests that need local
sockets your sandbox blocks (name them). Do NOT commit or push. End with a
short note: each finding → fixed / partly / not (why), and anything unsure.
