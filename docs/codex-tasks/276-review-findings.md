- **blocker — Hidden averages still enter public logs.** With `list_price` enabled, an offline cross-check logs `keepa check B012345678: final=$10.00 avg90=$50.00 -> True`. Evidence: `dealradar/verify.py:983`; INFO logs reach stdout through `dealradar/cli.py:31`. **Minimal fix:** suppress historical values in this mode and add a log-sanitization test.

- **should-fix — Category pages advertise unsupported discounts.** A $10 offer with List price $10.50 displays 4.8% off, while its category intro and description promise “at least 40% off.” The same claim appears for Price drop offers without any public reference. Evidence: `dealradar/publish/seo.py:498`, `dealradar/publish/templates/hubs.yaml:12`. **Minimal fix:** use neutral category copy in `list_price` mode, retaining qualification floors privately.

- **blocker — The reported regression remains unfixed.** `tests/test_unconfirmed_label.py:241` fails because `tests/unconfirmed_label_logic.cjs:45` expects a watch match while the new mode disables watches. This reproduces without Chromium. **Minimal fix:** explicitly select `history` for this legacy watch test and separately verify watch suppression with the shipping mode.

Validation: **147 passed, 1 failed**; shipping payload budgets passed. No files edited, committed or pushed; review scratch removed.

NOT# 276 — real Chromium results (operator, outside the Codex sandbox, 2026-10-17 ~05:45Z) — include as findings
BLOCKER: tests/test_unconfirmed_label.py::test_browser_label_sort_ticker_watch_and_surprise FAILS (node assertion at test_unconfirmed_label.py:241) in wt276 with Chromium available; 171 other targeted tests pass. Root-cause (likely the card's crossed-out/price-drop markup changed what the label/sort/ticker logic reads) and keep the existing browser behavior.

--- Also (operator 2026-10-17 ~06:10Z): this worktree was rebased onto 75ad39f (live: price history off for every store — amazon_history hide, store_policy live_only, seo.product_pages off; keepa.buybox_rule on; search_amazon on; 35 s pop-up). Conflicts were union-merged. Run your targeted tests: fix any failure the rebase caused (recorded fixtures pin the old values with tests/history_pins.py pre_history_removal / pre_history_removal_raw, or the existing "recorded before" lines) — never re-pin a hash; shipped-expectation tests follow the live config.
