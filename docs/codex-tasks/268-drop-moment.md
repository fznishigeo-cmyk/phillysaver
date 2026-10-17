# 268 — Make each deal drop feel like an event (ships ON)

## Why
Operator 2026-10-17: deals drop every ~15 minutes with a flash — "I like how
you did that … it could be a little bit more". Make the drop moment more
exciting without hurting speed, accessibility or the Back/restore behavior.

## Task
1. Read the current drop code in templates/index.html (drop clock, "Dropping…",
   reshuffle, HEAD check of deals.json) and keep its logic and timings.
2. When a drop brings new cards: a slim banner under the header
   "🔥 New deals just dropped" (role=status, auto-hides after 6 s, tappable to
   scroll to the first new card), a 1.2 s glow/pulse outline on new cards
   (CSS only, GPU-cheap: box-shadow/opacity, no layout shift), and the
   countdown chip briefly highlighted. When a drop brings nothing new: no
   banner.
3. `prefers-reduced-motion: reduce` → no pulse/animation, banner only.
   Dark mode looks right (task 254 tokens).
4. Never move the card the visitor is looking at (no scroll jumps); Back
   restore (task 237) and saved deals unaffected.
5. Phone budgets: do not raise a limit (tests/phone_budget_fixture,
   scripts/publication_budget.py).

## Tests
Logic test (existing *_logic.cjs harness style) for banner shown/not shown,
auto-hide, reduced motion; browser test (pytest wrapper, Chromium at
/opt/pw-browsers/chromium, exit 77 = skip) at 390×844 and 1280×800 driving the
fake clock through one drop: banner visible, glow class on new cards only,
no layout shift of the first visible card (CLS 0), no console errors.

## Rules
Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task268-*` basetemp
(delete after each run), targeted tests only, never the full suite. No
network, no commit, no push. HANDOFF "In flight": one entry.
