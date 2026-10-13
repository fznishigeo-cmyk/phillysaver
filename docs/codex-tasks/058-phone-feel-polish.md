# Codex task 058: make the phone experience feel native (polish, not redesign)

**Read first:** AGENTS.md, CLAUDE.md (surgical changes; match the style).
Files: `dealradar/publish/templates/index.html` (all CSS/JS: the
`@media (max-width:640px)`, `(max-width:520px)`, `(max-width:899px)` and
`(max-width:360px)` blocks, `.tkpause`, `.cattab`, `#controls`, `.getdeal`,
`.code`, `.credit a`, `.meta`, card chips), the static card markup in
`dealradar/publish/seo.py` (`card_html`), `tests/test_support.py`
(byte-hash baselines), `tests/test_front_page.py`, `tests/test_lighthouse_polish.py`.

Operator (2026-10-13): 70-80% of visitors will be on phones; the site must
"feel native in the hand". Task 057 just refreshed the header and made the
categories one swipeable row on phones. A headless-Chromium audit
(`scripts/mobile_audit.cjs`, iPhone 14 / Pixel 7 / 360px / dark) of the
current page found, at phone widths:
- tap targets under 44px: ticker PAUSE 56x23; category tabs 34-40 tall;
  `#minpct` input 70x20; Get deal 72-81x37; coupon `.code` buttons 78x22;
  source credit links ("via Hip2Save") ~52x14; card title links ~34 tall;
- ~264-277 text elements under 12px;
- card chips (store / price history / just in) wrap onto 3 lines, making
  cards tall; only 2 cards fully above the fold on an iPhone 14.

## Do (phones only unless noted; desktop must look the same)
1. Touch targets: every interactive element >= 44x44 CSS px of hit area on
   phones (use padding or a ::after hit-area extension where the visual size
   should stay small, e.g. credit links, PAUSE), without layout shift.
   Get deal >= 44px tall; coupon code buttons >= 36px tall with a 44px hit
   area; `#minpct` 44px tall.
2. Text: nothing under 12px on phones (meta lines, chips, ticker, footers);
   keep the visual hierarchy (prices biggest, titles next).
3. Card density on phones: keep the chip row to one line (e.g. hide the
   lowest-value chip like "just in" when it would wrap, or let the row clip
   with a fade), so cards are shorter and ~4 cards are visible after a short
   scroll. Never hide price, % off, store, Get deal or the source credit.
4. Native feel: `touch-action: manipulation` on interactive controls (no
   double-tap-zoom delay), `-webkit-tap-highlight-color` matched to the
   brand, `-webkit-text-size-adjust:100%`, `env(safe-area-inset-*)` padding
   for the fixed bottom bars (Join / paywall / toasts) so they clear the
   iPhone home indicator, `inputmode="numeric"` on `#minpct`,
   `enterkeyhint="search"` on `#q`, `autocomplete="off"` where right.
5. Keep `prefers-reduced-motion`, focus rings, ARIA, the swipeable tabs and
   the existing desktop layout exactly as they are.

## Tests
CSS/markup assertions for each rule above (44px rules inside the phone
media query, safe-area padding, inputmode/enterkeyhint present, no font-size
under 12px inside the phone blocks); byte-hash baselines in
tests/test_support.py updated from the new output only. One short HANDOFF
bullet.

Run `.venv/bin/python -m pytest -q`; all green except tests that need local
sockets your sandbox blocks (name them). Do NOT commit or push. End with a
short note of what changed and anything unsure.
