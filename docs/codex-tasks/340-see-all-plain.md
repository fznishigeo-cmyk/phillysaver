# Task 340 — homepage "See all" button: rounded count, no rotation line (operator request 2026-10-20 ~02:25Z)

Read CLAUDE.md first. Screenshot: docs/codex-tasks/340/see-all-now.png — under the Trending drop the button reads "See all 1,381+ deals" with the line "Full rotation: 6 drops (about 90 minutes)" under it. Operator: "say see all 1,000 plus deals. Just keep it at 1,000 plus. And remove that full rotation six drops about 90 minutes. Stop advertising that."
Code: dealradar/publish/seo.py ~line 461 (static fallback) and dealradar/publish/templates/drop-fresh.js freshControls()/freshCycleText() ~line 127-133.
Do (ONE new strict-bool flag `fresh_more_plain`; config.py default False = previous bytes; config.yaml true with the operator quote):
1. Button text uses the same rough count wording as the header tagline ("1,000+" for 1,000–1,999 live, i.e. the existing roughCount() rule; below 100 the exact plural) — both in the static HTML and in drop-fresh.js. Reuse roughCount if it is in scope; otherwise mirror its exact rule and test parity.
2. Remove the "Full rotation: … drops (about … minutes)" line entirely (static + JS; no empty <p> left).
3. Nothing else changes (button style, behaviour, drops, timer).
Tests: OFF = previous bytes (add `pre_fresh_more_plain` in tests/history_pins.py chained under the latest helper; isolate in sibling OFF-bytes tests if needed); unit tests for the label at 85, 999, 1,000, 1,381, 2,400 live and absence of "Full rotation" when ON; homepage phone gzip headroom ≥1,000 bytes. Targeted pytest only (`--basetemp=/tmp/b340`, delete after) incl. tests/test_drop_fresh.py, tests/test_home_comment_strip.py and any test grepping "Full rotation" or "See all"; `node --check` changed .js/.cjs. No full suite, commit or push. Reply <= 4 lines.
