# int13 — integrate 269 (top card rotates every drop, reviewed SAFE) and 272 (support panel at 2 min, SAFE) onto live 7c882fc

Live 7c882fc added: 275 click price (homepage Amazon cards need a fresh Buy Box reading; click_price.home_rows puts unknown offers last/drops them), 274 static photos + used_tab_only (no open box on the homepage incl. ticker), 276 List Price / ordinal public `ps`, 278 site.strip_comments (developer comments removed from the published home page at the very end), 273 what people buy (want lift, allure never in first 6 / at most 1 in 6 via seo.home_order and the browser's spaceAllure before browseRows = spotlight.concat(rows)).
Task 269 was built and reviewed on 75ad39f (before those). Both are applied here as WIP commits (269 conflicts resolved: pick numbering now happens once right after `picked` in site.py; the 269 `pick_checkout` flag block kept).
Make 269 work correctly on top of every 7c882fc behavior, keeping the operator rules: rotation never touches spotlights; it rotates only ordinary top picks; checkout spacing (one promo per five); allure never in the first 6 and at most 1 in 6 AFTER rotation; open box never on the homepage; Add-to-Cart order (unknown offers last) preserved; comment strip must not remove any marker the rotation script or its injection needs (inject before the strip, or use markers the strip keeps). Phone budget must pass with everything ON. OFF (drop_rotation_windows: 0) = 7c882fc bytes exactly.
Failing after the merge (pytest -n 3, real Chromium available on the operator side):
- tests/test_drop_rotation.py::test_arrivals_survive_saved_startup_refresh_and_back
- tests/test_drop_rotation.py::test_spotlight_untouched_while_ordinary_picks_rotate[spotlight-cycle-3]
- tests/test_drop_rotation.py::test_spotlight_untouched_while_ordinary_picks_rotate[spotlight-spacing-3]
- tests/test_drop_rotation.py::test_spotlight_untouched_while_ordinary_picks_rotate[spotlight-fallback-3]
- tests/test_drop_rotation.py::test_spotlight_untouched_while_ordinary_picks_rotate[spotlight-extra-3]
- tests/test_drop_rotation.py::test_spotlight_untouched_while_ordinary_picks_rotate[spotlight-extra-fallback-3]
- tests/test_drop_rotation.py::test_spotlight_untouched_while_ordinary_picks_rotate[spotlight-extra-first-promo-3]
- tests/test_drop_rotation.py::test_spotlight_dom_contains_only_published_spotlights_across_drops
- tests/test_drop_rotation.py::test_spotlight_untouched_while_ordinary_picks_rotate[spotlight-extra-other-3]
- tests/test_drop_rotation.py::test_spotlight_untouched_while_ordinary_picks_rotate[spotlight-extra-auto-3]
- tests/test_drop_rotation.py::test_row_filling_preserves_spotlight_and_ordinary_inventory_across_drops[1]
- tests/test_drop_rotation.py::test_spotlight_untouched_while_ordinary_picks_rotate[spotlight-views-3]
- tests/test_drop_rotation.py::test_spotlight_untouched_while_ordinary_picks_rotate[spotlight-single-3]
- tests/test_drop_rotation.py::test_row_filling_preserves_spotlight_and_ordinary_inventory_across_drops[2]
- tests/test_drop_rotation.py::test_spotlight_untouched_while_ordinary_picks_rotate[spotlight-new-3]
- tests/test_drop_rotation.py::test_spotlight_untouched_while_ordinary_picks_rotate[spotlight-new-plain-3]
- tests/test_drop_rotation.py::test_spotlight_untouched_while_ordinary_picks_rotate[spotlight-paginated-3]
- tests/test_drop_rotation.py::test_row_filling_preserves_spotlight_and_ordinary_inventory_across_drops[3]
- tests/test_drop_rotation.py::test_spotlight_untouched_while_ordinary_picks_rotate[spotlight-picked-first-promo-3]
- tests/test_drop_rotation.py::test_spotlight_untouched_while_ordinary_picks_rotate[spotlight-off-0]
- tests/test_drop_rotation.py::test_row_filling_preserves_spotlight_and_ordinary_inventory_across_drops[5]
- tests/test_drop_rotation.py::test_standalone_checkout_spotlight_keeps_original_slot
- tests/test_drop_rotation.py::test_exact_server_promo_flags_with_and_without_paid_label
