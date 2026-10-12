# Codex task 038: the join card, Kalshi-clean (two steps, redirect, popover)

**Read first:** AGENTS.md, CLAUDE.md. Files: `dealradar/publish/templates/join-card.html`,
`join-card.css`, `join.html`, `support.js` (prompt rules, `googleJoin`, the
email submit), `index.html` (header `#subscribe` link), `dealradar/publish/site.py`
(`{{JOIN_CARD}}` / `{{JOIN_PROMPT}}`, the `/join` "email open" tweak),
`tests/test_join_prompt.py`, `tests/join_prompt_logic.cjs`, `scripts/site_check.js` step 6.

Operator (2026-10-12), comparing with kalshi.com's sign-up popover and its
"First, what's your email?" page: "super professional ... corners all nice
and rounded ... so clean and nice and white. Instead of 'First, what's your
email?' just put 'What's your email?'". Refine, do not reinvent. No login
or account: we only capture the email (that is deliberate).

## 1. Step one (same card on `/join`, the weekly prompt and the header popover)
- Centered heading "Join wowpriced" (22px, 600), centered muted line
  "The day's best deals in one email. Free."
- Buttons stacked, full width, 52px tall, 12px radius, 12px apart:
  **Continue with Google** (black; the GSI button inside a 12px-radius
  container; hidden when Google is not configured, as today) and
  **Continue with email** (white, 1px border, an "@" icon before the text).
- Legal line, 12px, muted, centered: "By continuing, you agree to
  wowpriced's [privacy policy](/privacy.html)." (nothing like "which we
  recommend reviewing").
- `/join` no longer opens the email field by default (undo that tweak in
  site.py and its test); step one is the same everywhere.

## 2. Step two (after "Continue with email", inside the same card)
- Left-aligned heading "What's your email?" (28px, 600), one email input
  (56px tall, 12px radius, placeholder "Email", autofocus), a **Continue**
  button (accent colour, full width, 52px, pill radius 999px; pale and
  disabled until the value looks like an email), a small "Back" text
  button (returns to step one), and the same legal line.
- Same POST to `/subscribe` as today; same error messages.

## 3. After a successful join (email or Google)
- The card shows "You're on the list." + one muted line (today's text).
- On `/join`: redirect to `/` after 1.5 s (a "Back to deals" link shows at
  once). In the prompt or popover: the card closes by itself after 1.8 s.
- Remember the join (`wp.join.joined`) as today.

## 4. Header "Join free" on desktop (> 640px): a popover, like Kalshi
- A click on the header `#subscribe` opens the card as a popover anchored
  under the button (right-aligned with it, 360px wide, 16px radius, soft
  shadow, no page dim), with ✕, Escape and click-outside to close, focus
  trapped and returned. `href="/join"` stays (middle-click, no-JS and the
  site check keep working). Phone: the link still goes to `/join`.
- The timed weekly prompt on desktop uses the same popover position
  instead of the centered modal; the phone bottom sheet stays as is.
- Opening it by click counts as engagement and is never limited by the
  7-day rule; the timed prompt rules are unchanged.

## 5. Tests
Step flow (one -> two -> back), Continue disabled until valid, legal text
exact, redirect timer on `/join` only (fake clock), auto-close in
prompt/popover, popover open/close/focus/Escape/outside-click, header click
on phone does not intercept, GSI container radius, existing prompt rules
unchanged. Update the HANDOFF "Join card" bullet in place.

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
