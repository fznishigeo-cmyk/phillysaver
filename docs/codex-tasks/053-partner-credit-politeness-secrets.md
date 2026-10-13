# Codex task 053: fix the audit's partner-credit, politeness and secrets findings

**Read first:** AGENTS.md, CLAUDE.md (politeness is law; permission bypass is
host-only; evidence or it doesn't exist; store facts only; knobs in
config.yaml; idempotency is a contract). Then `AUDIT-051.md` at the
worktree root (Codex audit of 2026-10-13, report only, NOT committed: the
repo is public; never copy it into docs/ or quote its vulnerability detail in
code comments). Each finding has evidence, a failing scenario, a smallest
fix and failing tests.

Fix findings **1 (remainder), 2, 3, 4, 5, 6, 7, 8, 14, 15, 18, 21**. Task 054
does the others in parallel; do not touch savings math, the critic, dedupe
merging, spend accounting or delivery idempotency.

## Operator rules that decide the judgement calls
- **Finding 1:** already fixed in code (2026-10-13): `site.keeps_source_tracker`
  keeps a link on `affiliate.competitor_tracker_hosts` exactly as posted in
  ingest resolution, the short-link pass and the Amazon-link-from-text
  fallback; `_restore_source_credit_links` puts back any swapped tracker
  without needing a flag. Check every remaining path (publishing,
  `rewrite_affiliate`, `affiliate.link_overrides`, member payload, social
  candidates) never replaces or strips such a tracker; add the audit's tests.
  The policy stays list-based (config); do not widen it to every permission
  source's links.
- **Finding 2:** a permission source's image or credit must never appear
  without its credit link. Simplest compliant route for social (Pinterest,
  Facebook, Instagram, Telegram): never use an image hosted by a source; if a
  deal has no non-source image, it is not a social candidate. Emails: list
  every sighting's credit link (not only the first). Gone cards: keep the
  credit link active while the purchase action stays disabled.
- **Finding 5:** enforce the "membership under $19.99/month" grant condition
  from config (the membership price knob): at or above it, those sources'
  permission bypass is off (they fall back to robots), with a startup warning.
- **Findings 6, 7, 8:** robots honored on every request path, including HEAD
  and API/public-feed paths; robots fetch errors (429, 5xx, timeouts) are
  treated as disallow (conservative), cached per the existing rules.
- **Finding 18:** no exception text, URL or traceback that can carry a query
  credential is ever logged; log status code and error type only. Cover
  Keepa, Meh, verify.py product calls, fetch/base.py; encoded forms too.
- **Finding 21:** every production state write requires STATE_DB_KEY; no
  plaintext state is ever pushed, bootstrap included.
- **Findings 14, 15:** no Amazon price in any email or Pinterest text (alt
  text included); every email carries the affiliate disclosure.

## Tests
Port each finding's failing tests into the regular suite (offline, existing
fixtures; MockTransport). They fail before your fix and pass after. Keep
`tests/test_e2e_offline.py::test_rerun_is_idempotent` green; update byte-hash
baselines only from real output changes. One short HANDOFF bullet under
"In flight" (no vulnerability detail).

Run `.venv/bin/python -m pytest -q`; all green except tests that need local
sockets your sandbox blocks (name them). Do NOT commit or push. End with a
short note: each finding → fixed / partly / not (why), and anything unsure.
