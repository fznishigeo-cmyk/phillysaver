# Codex task 003: correct the locked-stub example in docs/paywall.md

**Read first:** AGENTS.md. This is a small docs-only task, and also a test that
Codex picks up tasks from GitHub on its own.

## What is wrong
docs/paywall.md ("Teaser `deals.json`") shows a locked stub with a `dedupe_key`
field. The real stub (dealradar/publish/paywall.py, `STUB_KEYS` and `_stub`)
carries the opaque `key` instead, and since 2026-10-11 it also carries `src`:
the sites that reported the deal (domains only, never the post URL).

## What to do
1. Make the JSON example match `STUB_KEYS` exactly: `locked`, `key`, `pct_at_checkout`,
   `store`, `category`, `first_seen_at`, `image_url`, `status`, `conditions`, `src`.
2. In the sentence after the example, say that `src` lists the reporting
   sites' domains, so the source filter can count locked deals.
3. Change nothing else. Run `python -m pytest -q` (it must stay green).

## Deliver
Commits on this pull request's branch (`codex/task-003`). Never push to
`claude/dealradar-aggregator-pyqirm`.
