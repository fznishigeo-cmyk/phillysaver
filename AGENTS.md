# AGENTS.md — dealradar (wowpriced.com)

For any coding agent (Codex, ChatGPT, Claude, or a human) working in this repo.

1. **Read `CLAUDE.md` first. Its rules are binding for every agent**, not only
   Claude: politeness (robots.txt, no bypassing bot walls, 1 req/s/host),
   evidence-backed prices, facts only, math in `dealradar/savings.py`, LLM
   spend caps in `config.yaml`, `state.db` only on the `data` branch, and
   idempotent re-runs.
2. **Read `docs/HANDOFF.md`** for what is live, what is in flight and who
   owns it, so two agents never work the same thing.
3. **The branch `claude/dealradar-aggregator-pyqirm` is live.** The scheduled
   GitHub Actions deploy it within ~15 minutes of a push. Work on your own
   branch and merge only green work; never push a red suite.
4. **Before every push:** `python -m pytest -q` (all green) and, if you
   touched `cloudflare/subscribe-worker/`, `node --test cloudflare/subscribe-worker/`.
5. **Secrets live only in GitHub repository secrets.** Never print, paste or
   commit a key, token or password.
6. **Operator decisions are standing rules.** They are recorded in
   `docs/HANDOFF.md`; do not undo them.
7. When you finish or stop, update `docs/HANDOFF.md` (what you changed, what
   is left) in the same commit.
