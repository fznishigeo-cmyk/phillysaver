# 289 follow-up: make it look 2026, not 2015 (operator 7:45pm, Brad's Deals About as reference)
Keep the pages and their text. Polish only:
- Modern feel: big confident type with tight letter-spacing on headlines,
  pill-shaped buttons, soft teal→amber gradient hero, rounded 16–20px cards with
  soft layered shadows, lots of air, max ~1100px content width, 8px spacing grid.
- About: a "What makes us different" block of four bold-label one-liners
  (Brad's Deals pattern): **Our why** — / **How it works** — / **Our promise** —
  / **Who we are** — each a single line built from the page's existing facts.
  Then two pill buttons side by side: "Work with us" (→ /careers) and
  "Contact us" (→ /contact).
- No team photo, no press logos, no "as seen in" (we have none; never invent).
- Subtle motion only: cards fade/rise 8px on first scroll into view, button
  hover lift; all off under prefers-reduced-motion; no layout shift.
- Re-shoot the screenshots (390×844 and 1440×900, light and dark) after.
- Operator 2026-10-17 8:31pm: KEEP the existing Join/sign-up wording everywhere
  (she is collecting emails; the newsletter starts in a few weeks). Undo any
  "first issue soon" copy change from round 1 (support.js / join prompt): the
  brand-page pop-up uses exactly today's homepage sign-up modal wording.
- Also fix: tests/brand_pages_browser.cjs:62 fails in real Chromium (pop-up not
  visible at 25 s on the second non-join page). Check whether the once-per-day
  memory uses a key the test does not clear, or a real bug; fix the right one.
