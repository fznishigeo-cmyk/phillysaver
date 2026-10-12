# Codex task 039: a Groupon-style deals pop-up + a footer sign-up box

**Builds on task 038** (this worktree already holds 038's join card: two
steps, redirect on /join, header popover). **Read first:** AGENTS.md,
CLAUDE.md. Files: `join-card.html`, `join-card.css`, `support.js`,
`index.html` (footer, `{{JOIN_PROMPT}}`), `site.py`, tests, HANDOFF.

Operator (2026-10-12), showing groupon.com: "It should pop up like how
Groupon just pops up ... entice them for the email ... 'Save up to 90%
off', sign up for emails" and Groupon's footer box "Sign up for the latest
deals" (email field + round green arrow; after submit "Thank You for
Signing Up!"). We only capture the email (no account, no login).

## 1. The timed pop-up (same timing/frequency rules as today)
- Desktop: a centred modal over a soft dim (Groupon), not the header
  popover; the header "Join free" click keeps 038's popover.
  Phone: the bottom sheet as today (<= 45% height).
- Content, single step (no "Continue with email" step here):
  - two-line headline: "Save up to" (28px, 700) and "90% OFF" (44px, 800,
    accent green), left-aligned; round ✕ button top-right (40px circle,
    1px border).
  - line: "The day's best verified deals in one free email. Unsubscribe
    anytime."
  - label "Email address", one input (56px, 12px radius, placeholder
    "Email address"), then a full-width pill button "Get the deals"
    (accent green, 56px, 999px radius).
  - under it, smaller: "or" + the Google button (only when configured).
  - legal, 12px muted: "By joining, you agree to get deal emails from
    wowpriced. See our [privacy policy](/privacy.html)."
- Success: "You're on the list." + one muted line; closes after 1.8 s.
- Same `/subscribe` POST, analytics events and `wp.join.joined` as today.

## 2. Footer box (home and the info/hub pages that share the footer)
- Above the footer links: heading "Sign up for the latest deals", an email
  input (48px, 12px radius) with a round accent-green arrow button (48px
  circle, aria-label "Sign up"), and small muted "By subscribing you agree
  to our [privacy policy](/privacy.html)."
- On success the box shows "Thank you for signing up!" in place of the
  form; errors as today. Hidden when the visitor already joined.
- Same POST and events (`sign_up` with `method: "footer"`).

## 3. Tests
Pop-up content (headline, button text, legal), single-step submit,
success auto-close, Google below "or" only when configured, footer form
submit/success/error/hidden-after-join, the existing timing/frequency
rules unchanged, header popover unchanged. Update the HANDOFF "Join card"
bullet in place.

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
