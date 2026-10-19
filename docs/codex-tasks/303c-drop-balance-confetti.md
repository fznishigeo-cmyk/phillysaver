# Task 303c — balanced drops, 90 cards, and iMessage-style confetti (operator 2026-10-18, follow-up to 303b)

Continue from your 303/303b work in this tree. The operator refined the goal after 303b was specified.

## Operator decisions (quotes)
- "Let's do 90 deals instead of 60" → `front_page.fresh_page_size: 90` in config.yaml (validator stays 24..120).
- "it doesn't need to be perfect ... some items can appear again. Some of these items are tremendously good ... I don't want people to be let down when the drop comes. It's like one item moved two inches to the left."
- "keeping that algorithm ... gadgets, sexy items, boyfriend, girlfriend, husband, wife, under $50 ... the cheaper items, quick buys" — the existing rank/appeal settings (impulse price curve, gadgets 1.3, allure 1.35 with spacing, kids/staples/bestseller lifts, team-merch/car-part demotes, earning links first) must keep driving which deals rotate in; the rotation walks the existing ranked order, it does not replace it.
- "when the drop happens, I want iPhone type ... the iPhone confetti ... on the whole home screen. Only the home screen." Reference she supplied (free to use without credit): a full-viewport fixed canvas, 9 colors rgb(149,58,255) rgb(255,195,41) rgb(255,101,26) rgb(123,92,255) rgb(76,126,255) rgb(71,192,255) rgb(255,47,39) rgb(255,91,134) rgb(233,122,208), rectangles 22x14 and circles 12, two depth layers (0.55 scale/0.5 opacity background, 1.0 foreground), a ~1 s emission with an initial burst from the top centre, drag ramp, keyframed gravity, wave flutter and 3D-ish yaw/pitch/roll; particles removed off-screen; ~6–10 s total.

## Change the drop selection (replaces 303b's "0 overlap")
1. Page of 90. At each mark: the first 15 cards are all new vs the previous mark; across all 90, at least 75% are new vs the previous mark.
2. Up to 25% may be returning "winners": rows from the previous one or two marks with the strongest existing signals (rank score, click/save signals already in the data); a returning card never keeps its position — it must move at least 12 positions and never into the first 15.
3. Fresh rows come from the deterministic rotation through the deduplicated good pool (303b), so a non-winner returns only when the rotation wraps. All mix rules from 303b stay (≥70% women-oriented when the pool allows, allure spacing, clothing caps, checkout cap, category runs, family spacing, ≥19 of first 24 at ≤ $50 when possible, earning links first among equals). Same result for every visitor per mark.

## Confetti (only on the homepage Trending view, only when a mark passes while the page is open and visible)
- A small self-contained module (no external library), lazy-created canvas `position:fixed;inset:0;pointer-events:none;z-index` above the grid but below modals; DPR-aware; cap ~300 particles; stops and removes the canvas when done; skips when `document.hidden`, when prefers-reduced-motion is set (badges + toast still show), and on any other page/tab/search. No credit comment (operator: the author allows use without credit).
- Fires together with the 303b drop moment (entrance animation, "Just dropped" badges on new cards only — not on returning winners, toast "{N} new deals just dropped", scrolled-down pill). Never fires on first page load.
- Must not cause layout shift, block taps, or keep a requestAnimationFrame running after it ends.

## Tests
- Node logic over 20 marks with the 1,200+400 pool: page 90; first 15 all new; ≥75% new overall; returning winners moved ≥12 and never in the top 15; mix rules; determinism; < 50 ms per mark on 4,000 rows.
- Confetti unit test in node with a fake canvas/rAF: starts only on a mark, respects reduced motion and hidden tab, stops and removes the canvas, particle cap.
- Update tests/drop_shuffle_browser.cjs (I run Chromium): at a mark at top-of-page ≥ 75% of the 90 data-keys change and the first 15 all change, confetti canvas appears then disappears within 12 s, no canvas with reduced motion, scrolled-down pill.
- OFF/missing bytes unchanged (drop_fresh false), strict config tests. Targeted pytest only: `.venv/bin/pytest -q -p no:cacheprovider --basetemp=/tmp/b303 <files>`. No full suite, commit or push. Update docs/HANDOFF.md. Reply <= 8 lines.

- Budget: the homepage is ~1 KB under its phone gzip budget (tests/test_home_comment_strip.py headroom 1,000 bytes). Put the drop_fresh selection + drop moment + confetti in a separate lazily loaded asset (pattern: dealradar/publish/search_analyze_lazy.py / search-more.js with ?v= hash), loaded after first paint on the homepage only; the inline page may only gain a tiny loader. The budget test must stay green.
