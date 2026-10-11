# Codex task 004: bring README section 5 (Scheduling & state) up to date

**Read first:** AGENTS.md. This is a docs-only task, and also a test that Codex
picks up tasks tagged on GitHub on its own.

## What is wrong
README.md section "5. Scheduling & state" lists three workflows, and some rows
are out of date. For example, it says fast.yml runs `*/15` with no site deploy.
The real workflows are in `.github/workflows/`.

## What to do
1. Rewrite the workflow table so it lists every file in `.github/workflows/`:
   the name, its schedule (or "manual"), and one plain sentence on what it does.
   Read each YAML file. Don't guess.
2. Keep the bullets under the table accurate:
   - the shared concurrency group;
   - the encrypted `state.db.enc` on the `data` branch;
   - Cloudflare Pages as the only host.
   Fix any statement that no longer matches the files.
3. Change nothing outside section 5 of README.md. Run `python -m pytest -q`;
   it must stay green, and tests/test_workflows.py checks parts of the README.

## Deliver
Commits on this pull request's branch (`codex/task-004`). Never push to
`claude/dealradar-aggregator-pyqirm`.
