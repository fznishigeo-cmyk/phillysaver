# Task 332b — native Chromium results for 332 (I ran them; you can't)

Logs: docs/codex-tasks/332b/b332_<test>.log. Shots: none needed from you.
- PASS: search_analyze (inline+lazy 390/1440 light/dark), header_polish, drop_shuffle (CLS 0), tool_pages.
- FAIL search_box_glow_browser.cjs: assertAnalyzeGeometry line 88 expects label 'Analyze' but desktop now shows 'AI Analysis' (flag ON). Make the shared helper flag-aware (same rule page_polish uses: calm && width>=900 → 'AI Analysis').
- FAIL page_polish_browser.cjs line 56 → assertAnalyzeGeometry line 67 "label fits without clipping" (actual false). Find at which width/page the 'AI Analysis' (or 'Analyze') label clips and fix the REAL layout if it clips (equal-width desktop buttons must still fit their labels at 900–1599 incl. 317's laptop range and the 329 timer ring; report measured label vs button widths), or the helper if the measurement is wrong. Do not shrink text below today's size.
- FAIL search_more_browser.cjs line 145: waits for '#search-amazon-result [data-search-amazon]' — that is the pill the operator asked to remove; with the flag ON assert it is ABSENT (and the rest of the search-more flow unchanged); keep the old assertion for OFF.
- FAIL search_amazon_browser.cjs (args: shots dir, then chromium): page.goto https://wowpriced.test/ fails at line 34 — check whether this test fails on the base too (git stash; it may need a routed fixture); if it is ours, fix; if pre-existing, say so.
Re-run targeted pytest (`--basetemp=/tmp/b332y`, delete after) and `node --check` every changed .cjs. No full suite, commit or push. Reply <= 6 lines.
