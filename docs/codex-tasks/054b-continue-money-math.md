# Codex task 054b: finish task 054 (it stopped at its time limit)

Task 054 (`docs/codex-tasks/054-money-math-correctness.md`, audit
`AUDIT-051.md` findings 9, 10, 11, 12, 13, 16, 17, 19, 20, 22, 23) ran out of
time with most of its work done. That partial work is already applied in
this worktree on top of the current code, which has moved on since
(tasks 053, 055, 056 and fixes). Five files carry merge conflict markers
(`<<<<<<< ours` = current code, `>>>>>>> theirs` = task 054's change):
`dealradar/pipeline.py`, `dealradar/publish/admin_alerts.py`,
`dealradar/publish/social.py`, `dealradar/verify.py`,
`tests/test_publish_admin.py`.

1. Resolve every conflict keeping BOTH intents: everything the current code
   does (053 robots/credit rules with official keyed APIs exempt, Keepa
   `max_attempts=1` + `respect_robots=False`, 055/056 newsletter inbox, the
   admin warning once-a-day dedup, social image rules, earning links, eBay
   EPN) plus task 054's fixes.
2. Finish whatever task 054 left undone; read task 054's brief and every
   listed finding again and check each one is fixed with a test that fails
   before and passes after. Finding 13 matters live: a deal seen in two feeds
   of ONE partner (e.g. hip2save + hip2save_amazon, same site) must not show
   "2+ sources" / count as independent confirmation.
3. Keep `tests/test_e2e_offline.py::test_rerun_is_idempotent` green; spend
   caps in config.yaml are never exceeded or bypassed; never weaken
   `verify_evidence`.
4. One short HANDOFF bullet for 054 under "In flight" (no audit detail).

Run `.venv/bin/python -m pytest -q`; all green except tests that need local
sockets your sandbox blocks (name them). Do NOT commit or push. End with a
short note: each finding -> fixed / partly / not (why), and anything unsure.
