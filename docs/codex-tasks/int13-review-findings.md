- **should-fix — Failed repair preserves invalid promo spacing.** With 24 accepted picks, promos at indices 0/5/10/15/20, and the other 19 marked Allure, the published renderer shows five consecutive promo cards. Failed search returns the already-invalid input at [drop-rotation.js:85](/tmp/claude-0/-home-user-phillysaver/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/wtint13/dealradar/publish/templates/drop-rotation.js:85). Minimal fix: provide a validated fallback that defers excess promos when spacing is impossible; add this sparse-inventory regression.

- **should-fix — Repair bypasses the three-window rotation limit.** Repair combines all picks into one pool at [drop-rotation.js:23](/tmp/claude-0/-home-user-phillysaver/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/wtint13/dealradar/publish/templates/drop-rotation.js:23). With server-accepted 120 picks, promos every five positions, Allure at indices 24/119, and seed 1, pick 120 becomes visible card 13. This brings the fifth window onto the first screen. Minimal fix: constrain repair to the protected window membership and rotation boundary, including boost shares.

Targeted checks: **153 passed, 1 Chromium check skipped**. OFF bytes matched HEAD in four modes with historical support defaults. Worktree unchanged; review scratch removed.

NOT
--- Operator (2026-10-17 16:00Z): ALSO the full suite on this exact tree (real Chromium, -n 3): 11 failed, 7204 passed. Fix each at the root (this is the last round; scope = these + the 2 findings above; rotation never touches spotlights). Recorded pins keep pre-rotation switch values (drop_rotation_windows 0) — never re-pin. A public/tracking payload must never carry private values. Browser failures you cannot launch here: reason from the error and add a non-browser regression where possible.
- test_matches_pre_change_posts_errors_database_and_public_bytes[1]
    E       AssertionError: assert {'posts': ['a...64f6c9', ...}} == {'db': {'accu...64f6c9', ...}}
    E         
    E         Omitting 3 identical items, use -vv to show
    E         Differing items:
    E         {'site': {'404.html': 'ebcfabae3989efe38fef3c7012b34f7e0eebcc69a3be329a09fe3f5fcecbd946', '89b51421654dec4627eb3ed0290...a0691495f1f7fd8c08795ffabf377e4', '_headers': '21238944fa6695d856489a0a64bf41a250ae00fcc1837ba72cfb9b31ba64f6c9', ...}} != {'site': {'404.html': '1b0673ace89d162167fa9ad
- test_matches_pre_change_posts_errors_database_and_public_bytes[4]
    E       AssertionError: assert {'posts': ['a...64f6c9', ...}} == {'db': {'accu...64f6c9', ...}}
    E         
    E         Omitting 3 identical items, use -vv to show
    E         Differing items:
    E         {'site': {'404.html': 'ebcfabae3989efe38fef3c7012b34f7e0eebcc69a3be329a09fe3f5fcecbd946', '89b51421654dec4627eb3ed0290...a0691495f1f7fd8c08795ffabf377e4', '_headers': '21238944fa6695d856489a0a64bf41a250ae00fcc1837ba72cfb9b31ba64f6c9', ...}} != {'site': {'404.html': '1b0673ace89d162167fa9ad
- test_switch_off_matches_prechange_spend_verdicts_and_every_site_byte
    E       AssertionError: assert '639d0b4df25a...c70f78f8a4901' == '68528e66ca7c...7477b0e56f009'
    E         
    E         - 68528e66ca7c01ca37cec91aa0ff427e5daedfb60ed566cf71a7477b0e56f009
    E         + 639d0b4df25af218a66639e6283844ff45b1aafb507b0d8a401c70f78f8a4901
    E       AssertionError: evalmachine.<anonymous>:155
- test_local_browser_restore[False]
    E       AssertionError: {"width":390,"height":844,"reloadError":0,"backError":0,"enabled":false,"measured":"","first":["visible","visible","visible","visible","visible","visible","visible","visible"]}
    E         locator.click: Timeout 30000ms exceeded.
    E         Call log:
    E         [2m  - waiting for locator('#moremore')[22m
    E         [2m    - locator resolved to <button type="button" id="moremore" class="pwbtn alt">Show more deals</button>[22m
- test_filter_and_back_events_are_ga4_only_with_bounded_method[ids0]
    E       assert ('PRIVATE' not in '{"open":{"s...rity":true}}'
    E         
    E         'PRIVATE' is contained here:
    E           ity":true}} and '23.45' not in '{"open":{"s...rity":true}}'
    E         
Failed list:
FAILED tests/test_fetch_parallel.py::test_matches_pre_change_posts_errors_database_and_public_bytes[1]
FAILED tests/test_fetch_parallel.py::test_matches_pre_change_posts_errors_database_and_public_bytes[4]
FAILED tests/test_critic_tiering.py::test_switch_off_matches_prechange_spend_verdicts_and_every_site_byte
FAILED tests/test_int12_review.py::test_public_score_magnitude_is_private_but_score_order_and_ties_survive[history]
FAILED tests/test_int12_review.py::test_public_score_magnitude_is_private_but_score_order_and_ties_survive[list_price]
FAILED tests/test_int11_integration.py::test_browser_visible_unknown_and_static_wall_both_get_buybox[False-False]
FAILED tests/test_int11_integration.py::test_browser_visible_unknown_and_static_wall_both_get_buybox[False-True]
FAILED tests/test_int11_integration.py::test_browser_visible_unknown_and_static_wall_both_get_buybox[True-False]
FAILED tests/test_int11_integration.py::test_browser_visible_unknown_and_static_wall_both_get_buybox[True-True]
FAILED tests/test_offscreen_cards.py::test_local_browser_restore[False]
FAILED tests/test_tracking.py::test_filter_and_back_events_are_ga4_only_with_bounded_method[ids0]
