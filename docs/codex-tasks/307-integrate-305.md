# Task 307 — add task 305 (header_polish) to the integrated 302/303/304 tree

This tree = your task 306 result (302 same_product_spacing + 303c drop_fresh + 304 page_polish, all green on the targeted files) plus the task 305 diff (header_polish: dealradar/publish/header_polish.py, tests/test_header_polish.py, tests/header_polish_browser.cjs, config/history_pins wiring), merged by me keeping both sides of each conflict (config.yaml, config.py, site.py, history_pins.py, HANDOFF.md).

Targeted run (20 files: `ls tests/test_*.py | grep -E "drop|same_product|page_polish|header_polish|card_tidy|search_box_glow|home_comment_strip|photoless|search_analyze|search_more|support|event|brand_pages|first_screen|e2e_offline|config"`): 576 passed, 6 failed:
- tests/test_header_polish.py::test_recorded_off_missing_back_off_bytes[shipped|inline|membership] — OFF bytes were recorded on the 302 base; drop_fresh/page_polish (now on in config.yaml) change them.
- tests/test_header_polish.py::test_only_home_layout_changes_and_rerun_is_stable[False|True]
- tests/test_drop_fresh.py::test_emitted_fresh_logic[True-True]

## Do
1. Make all 20 files green by fixing the interaction, not by weakening tests: never re-pin recorded hashes (isolate the other new flags in a feature's own tests the way 306 did, e.g. header_polish's tests turn drop_fresh/page_polish off via the pre_* helpers, or compare with the config's own OFF render); keep idempotency; never raise the phone budget or shrink the 1,000-byte headroom (report the final gzip size and headroom).
2. Check the real interaction too: header_polish moves #cattabs and the tagline into a `.hnav` row inside the header and moves the seasonal event-links nav after </header>; drop_fresh (lazy module, confetti, "See all" button, scrolled-down pill, toast) and page_polish (homepage ?q=/?analyze= handling) must still work with that markup on both desktop and phones. If test_emitted_fresh_logic[True-True] shows a real break, fix the code, not the test.
3. Keep every behaviour of 302/303c/304/305 (briefs in docs/codex-tasks/).
4. Run the 20 files until green (`.venv/bin/pytest -q -n 4 -p no:cacheprovider --basetemp=/tmp/b307 <files>`). No full suite, commit or push. Name the Chromium tests I must run. Update docs/HANDOFF.md. Reply <= 8 lines.
