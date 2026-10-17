# int11 round 2 — fix everything below in this integrated tree (274+275+276+278+273 all ON)

## A. Task 273 (what people buy) was merged into this tree (commit a963db8)
Operator conflict resolution in dealradar/publish/seo.py: home_order(rows, front_page, *, click_price=False); static_wall calls home_order(..., click_price=True) so the Add-to-Cart order (click_price.home_rows: unknown offers last / dropped) runs BEFORE allure spacing, as the browser does (clickPriceHomeRows, then spaceAllure right before browseRows = spotlight.concat(rows)). _image_preconnects keeps the used_tab_only filter and uses home_order. Keep that intent; fix it if wrong. Allure must never be in the first 6 visible cards, at most 1 in 6, in static HTML and in the real JS, with every switch ON.

Failing targeted tests after the merge (pytest -n 3, real Chromium available on the operator side):
- tests/test_what_people_buy.py::test_combined_cap_and_stronger_sales_signal
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[False-6-8-1-False]
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[False-6-8-1-True]
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[False-12-36-6-False]
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[False-12-36-6-True]
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[False-0-6-1-False]
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[False-0-6-1-True]
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[False-0-20-3-False]
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[False-0-20-3-True]
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[False-6-9-2-False]
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[False-6-9-2-True]
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[True-6-8-1-False]
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[True-6-8-1-True]
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[True-12-36-6-False]
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[True-12-36-6-True]
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[True-0-6-1-False]
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[True-0-6-1-True]
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[True-0-20-3-False]
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[True-0-20-3-True]
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[True-6-9-2-False]
- tests/test_what_people_buy.py::test_gone_and_allure_constraints_in_static_and_actual_js[True-6-9-2-True]
- tests/test_what_people_buy.py::test_configured_popularity_marginal_and_quota[Synthetic widget]
- tests/test_what_people_buy.py::test_configured_popularity_marginal_and_quota[Cotton socks]
- tests/test_what_people_buy.py::test_real_browser_ordering_all_and_category[0]
- tests/test_what_people_buy.py::test_real_browser_ordering_all_and_category[1]
- tests/test_what_people_buy.py::test_phone_budgets_with_all_switches_enabled
- tests/test_popular_affordable.py::test_answered_no_opinion_is_persisted_by_apply[False]
- tests/test_popular_affordable.py::test_affordable_popular_drop_beats_expensive_and_appeal_still_multiplies
- tests/test_popular_affordable.py::test_window_cap_requeues_at_unboosted_rank_and_keeps_existing_caps
- tests/test_popular_affordable.py::test_exact_shadow_counts_and_same_key_double_render[True-True]
- tests/test_popular_affordable.py::test_cap_resets_at_the_browser_window_boundary
- tests/test_popular_affordable.py::test_popularity_is_private_with_the_checker_enabled
- tests/test_popular_affordable.py::test_clock_skew_is_fresh_in_factor_and_shadow[1-True]
- tests/test_popular_affordable.py::test_clock_skew_is_fresh_in_factor_and_shadow[1800-True]
- tests/test_popular_affordable.py::test_clock_skew_is_fresh_in_factor_and_shadow[3600-True]
- tests/test_popular_affordable.py::test_clock_skew_is_fresh_in_factor_and_shadow[3601-False]
- tests/test_popular_affordable.py::test_everyday_price_applies_after_appeal_for_every_store[Amazon-https://amazon.com/dp/B0POPULAR1]
- tests/test_popular_affordable.py::test_public_ps_omits_popularity_but_keeps_everyday_price[False]
- tests/test_popular_affordable.py::test_public_ps_omits_popularity_but_keeps_everyday_price[True]
- tests/test_popular_affordable.py::test_first_window_counts_new_cards_not_shifted_positions
- tests/test_popular_affordable.py::test_shadow_includes_the_everyday_tilt_and_share_cap
- tests/test_popular_affordable.py::test_exact_shadow_counts_and_same_key_double_render[False-False]
- tests/test_popular_affordable.py::test_exact_shadow_counts_and_same_key_double_render[False-True]
- tests/test_popular_affordable.py::test_exact_shadow_counts_and_same_key_double_render[True-False]
- tests/test_home_comment_strip.py::test_chromium_home_on_matches_off
- tests/test_popular_affordable.py::test_shipped_pipeline_and_cli_never_output_shadow_data
- tests/test_popular_affordable.py::test_public_ps_rounding_boundary_does_not_reveal_popularity

Notes:
- The 20 tests/test_popular_affordable.py failures and test_home_comment_strip.py::test_chromium_home_on_matches_off already failed BEFORE the 273 merge (at commit 8aaa39c, your round-1 result). Errors include 'TypeError: unsupported operand type(s) for *: NoneType and float', '/ NoneType NoneType', amazon_candidates 0 in shadow counts — likely 276 list_price / 274 hidden comparisons making the regular price None where popular_affordable/everyday-price math expects a number. Fix at the root (hidden comparisons stay usable as a hidden backend signal; public bytes never show them).
- test_chromium_home_on_matches_off: real Chromium times out at tests/home_comment_strip_browser.cjs:48 waiting for exactly 60 '#content .card' (shipped keepa.buybox click-price rule hides unknown offers when fewer than 120 are known, so the fixture renders fewer cards). Make the fixture honest (fresh qualified Buy Box facts) or pin the rule off in that smoke with a 'recorded before task 275' line; the ON-vs-OFF comment-strip comparison must still run with every other shipped switch.

# int11 round 2 — 274 review findings to fix in the integrated tree (274+275+276+278 all ON)
Note: 276 (List Price) already shows a 'Price drop' chip and no % for Amazon deals without a List Price; make 274's suppression path consistent with it (no '-0%', no 'under 40% off' grouping for no-% offers, neutral grouping; ticker omits the % stamp).
- **Blocker — historical discounts still leak through hub summaries.** With shipped settings, a $10 Amazon offer using a $50 historical average loses its comparator in JSON, but its category page publishes “median 80% off.” [seo.py:518](/tmp/claude-0/-home-user-phillysaver/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/wt274/dealradar/publish/seo.py:518) aggregates raw database values; [seo.py:549](/tmp/claude-0/-home-user-phillysaver/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/wt274/dealradar/publish/seo.py:549) publishes them. **Minimal fix:** exclude policy-hidden comparisons from public statistics, omit unavailable medians, and add a database-backed regression.

- **Should-fix — suppressed discounts become false discount claims.** Clearing `pct_at_checkout` in [site.py:515](/tmp/claude-0/-home-user-phillysaver/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/wt274/dealradar/publish/site.py:515) makes the same offer display “-0%” in the ticker and appear under “under 40% off” when browsing its department with Fresh sorting. Evidence: [index.html:1884](/tmp/claude-0/-home-user-phillysaver/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/wt274/dealradar/publish/templates/index.html:1884), [index.html:2131](/tmp/claude-0/-home-user-phillysaver/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/wt274/dealradar/publish/templates/index.html:2131). **Minimal fix:** handle null discounts explicitly, omit percentage stamps, and use neutral grouping for offers without public comparisons.

Targeted checks: **329 passed, 1 Chromium skip**. Both findings reproduced separately offline. Worktree untouched; temporary files removed.

NOT SAFE TO SHIP
# 276 review findings (fix in this integrated tree too)
- **blocker — Stale List Price becomes “fresh.”** A fresh Buy Box confirmation substitutes its timestamp for the product’s List Price timestamp. Offline reproduction published $100 List price / 75% off from a 48-hour-old record despite the 12-hour limit. Evidence: [pipeline.py:1927](/tmp/claude-0/-home-user-phillysaver/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/wt276/dealradar/pipeline.py:1927). **Minimal fix:** validate the List Price’s own observation time; reject stale, missing or mismatched readings.

- **blocker — Hidden reference prices still reach public logs.** The shipping critic path logged: `The $100.00 regular price is inflated…`, exposing the hidden comparison. The previous fix covers only `keepa_check`. Evidence: [pipeline.py:2080](/tmp/claude-0/-home-user-phillysaver/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/wt276/dealradar/pipeline.py:2080). **Minimal fix:** omit critic reason text from public logs in `list_price` mode; retain it privately and test this pipeline path.

- **should-fix — Older feed overwrites newer paid reference.** A feed reading 20 minutes older than the paid confirmation replaced its valid $40 List Price. Timestamp equality then failed, incorrectly switching the offer to Price drop. Evidence: [keepa.py:489](/tmp/claude-0/-home-user-phillysaver/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/wt276/dealradar/fetch/keepa.py:489). **Minimal fix:** prevent List Price updates from regressing behind the stored reading or current confirmation.

260 targeted tests passed, including prior regression fixes, OFF byte pins, payload budgets and four pipeline rerun cases. All three findings reproduced separately offline. No repository edits; review scratch deleted.

NOT