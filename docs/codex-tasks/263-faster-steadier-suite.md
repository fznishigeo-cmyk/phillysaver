# 263 — Faster, steadier test suite (no weakened checks)

## Why
The full suite (~6,300 tests) takes ~25–30 min with `-n 3` on a 4-CPU box,
and Chromium browser tests (e.g. tests/test_offscreen_cards.py::
test_local_browser_restore, tests/dark_mode_browser.cjs) time out or flake
under load. Every push waits on it. Make it faster and deterministic
without removing or weakening a single assertion.

## Task
1. Measure: `pytest --durations=60` (targeted chunks, `-n 1`) and list the
   slowest tests/fixtures; find repeated expensive setup (site renders, DB
   seeding, offline pipeline runs) that can be shared via session-scoped,
   read-only cached fixtures or cached build artifacts keyed by inputs.
2. Browser tests: deterministic waits (no fixed sleeps where an event or
   condition exists), scroll pinned to elements, longer per-action timeouts
   only where justified, one Chromium per worker instead of per test where
   safe; retry nothing.
3. Keep every assertion; never re-pin a hash; never skip/xfail/quarantine.
4. Report before/after timings for the changed files in HANDOFF.

## Rules
Offline only. You may run chunks of the suite with `-n 2` to measure, with
your own `/tmp/task263-*` basetemp (delete after). No network, no commit, no
push. HANDOFF "In flight": one short entry with the timings.
