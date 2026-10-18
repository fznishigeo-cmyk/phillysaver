# Task 302 — never show the same product twice in a row (operator 2026-10-18, ad day)

## What the operator saw
Phone, Trending wall, live wowpriced.com 1:23pm: three "Blue 84 Los Angeles Clippers NBA Officially Licensed ... hoodie" cards in a row (same product, different listings/colors, all $12.00, source "Amazon price drop"), followed by a fourth Clippers hoodie. Quote: "Three of the same items in a row? Make sure that doesn't happen again."

## Goal
On every wall the visitor sees (homepage Trending/All deals/category tabs, the first paint and the client re-renders incl. the clock-mark reshuffle from task 300 `drop_shuffle`), two cards for the same product family never appear within 8 positions of each other, and at most 2 cards of one product family appear in the first 48. Only the best listing keeps its position (highest current rank); the others move later (do not delete deals; search results may still show all of them).

"Same product family" = same store AND the same normalized title stem: lowercase, strip punctuation/brackets/sizes/colors/pack counts, first 5 meaningful words equal (e.g. all four Clippers hoodies above). Add unit cases for: the Clippers example (4 titles above, write realistic variants), different products from the same brand must NOT match, same title at different stores is allowed.

## Where
Read first: dealradar/rank.py (front-page ordering, niche caps), dealradar/publish/drop_rotation.py (final_order), dealradar/publish/drop_shuffle.py + templates/drop-shuffle.js (task 300 clock reshuffle of the top 15), the card render/order code in dealradar/publish/templates/index.html (topOrder/ordered). Implement the spacing where the order is decided so the server order AND the client reshuffle both respect it (the reshuffle must not swap two same-family cards next to each other). Keep it small.

## Rules (CLAUDE.md applies)
- Behind a new config flag `same_product_spacing` (strict bool, AppConfig default False = previous bytes; config.yaml true). Missing/false must keep every recorded publication hash: chain a `pre_same_product_spacing` helper from `pre_task_295` in tests/history_pins.py (like pre_card_tidy); never re-pin.
- Tests: unit tests for the family key + spacing; an emitted-JS logic test (node) proving the reshuffle never places two same-family cards within 8; OFF/missing bytes test.
- Run the targeted tests you add/touch with `.venv/bin/pytest -q -p no:cacheprovider --basetemp=/tmp/b302 <files>`. Do not run the full suite (the senior engineer runs it). Chromium is unavailable to you; say which browser test to run.
- No commit, no push. Update docs/HANDOFF.md with one entry. Reply in ≤ 8 lines.
