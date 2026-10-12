# Codex task 033: a Kalshi-style sign-up card (tasteful, once, Google-safe)

**Run after tasks 030 (/join page) and 031 (search) are merged.**
**Read first:** AGENTS.md, CLAUDE.md. Files: `dealradar/publish/templates/join.html`,
`index.html`, `support.js`, `cloudflare/subscribe-worker/` (does it already
support Google sign-in from the membership era? read `auth.js`; reuse it
only if it can return a verified email without creating a paid account),
tests.

Operator (2026-10-12), pointing at kalshi.com: after ~10 seconds on the site
a clean "Create your account" card appears: "Continue with Google",
"Continue with Apple", "Sign up with Email", small legal line, ✕. They want
the same polish for "Join free" (the deals email list). Dignified casino:
it must feel premium, never spammy.

## 1. The card
- Title "Join wowpriced", line "The day's best deals in one email. Free.
  Unsubscribe anytime." Buttons (full width, 48px, 10px radius, dark solid
  for providers, outline for email): **Continue with Google** (only when the
  Worker can do Google sign-in safely; otherwise omit it: no dead buttons),
  **Continue with email** (reveals the email field + Join free in place, same
  POST as /join). No Apple button (needs an Apple Developer account the
  operator does not have yet; leave a config switch `join.apple: false`).
  Legal line: "By joining you agree to our privacy policy" (link).
- Same card on `/join` (one component, two placements).

## 2. When it appears (Google's intrusive-interstitial rules protect search traffic)
- Once per visitor per 7 days (localStorage, guarded), never on `/join`,
  never after a successful join (remember it), never while the support
  banner is open, never in the first 15 seconds, and only after the visitor
  has scrolled at least one screen or clicked a tab/filter (engaged).
- Desktop: centered modal over a soft dim, Escape / ✕ / click outside close
  it, focus trapped and returned. Phone (≤ 640px): a bottom sheet no taller
  than 45% of the viewport, the page behind still visible and scrollable
  after closing; never a full-screen cover.
- `track("signup_prompt_shown")`, `track("signup_prompt_closed")`,
  `track("sign_up", {method: "email"|"google"})`.
- Config switch `join.prompt: true`, `join.prompt_delay_s: 15`.

## 3. Tests
Timing/engagement/frequency rules (fake clock + storage, including a
throwing storage = never shows), never on /join, never after joining, never
with the banner open, the phone sheet height cap, focus handling, Google
button hidden when unsupported, the switches. One short bullet in
docs/HANDOFF.md under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
