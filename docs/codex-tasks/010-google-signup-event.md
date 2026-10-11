# Codex task 010: Google sign-ups fire the sign_up event

**Read first:** AGENTS.md, CLAUDE.md, docs/paywall.md (Worker API).

The page fires the analytics event `sign_up` for email sign-ups only
(`dealradar/publish/templates/index.html`: `if (path === "/api/signup")
track("sign_up", ...)`). A first Google sign-in also creates an account,
but the Worker's `/api/google` reply does not say whether the account is
new, so those sign-ups are never counted.

1. Worker (`cloudflare/subscribe-worker/`, see auth.js / worker.js): the
   `/api/google` success reply gains `new_account: true` when that call
   created the account, `false` otherwise. Nothing else in the reply changes.
2. Page: after a successful `/api/google`, call
   `track("sign_up", { method: "google" })` only when `new_account` is true.
3. Tests: the Worker's own test file (worker.test.mjs; run it the way its
   package.json says) covers new vs returning Google accounts; the Python
   suite keeps passing (tests read the template).
4. docs/paywall.md: document `new_account` in the `/api/google` reply.

Run `.venv/bin/python -m pytest -q` and the Worker tests; all green. Do NOT
commit or push. (The Worker goes live only after the operator's deploy
workflow runs; Claude handles that.)
