# 250 — Store policy knob + bounded /p/ output (ships ON; P0 before pushing amazon_history: show)

## Why

Plan 2026-10-16: a price tracker that is better than camelcamelcamel. Two problems block it, and both are live risks.

1. **Unbounded /p/ output.**
   - Commit f459b27 (local, not pushed) sets `amazon_history: show`.
   - `product_pages.prepare` adds every public deal with an exact identity, Amazon included even under hide, to the `seo_product_pages` meta JSON and never removes one.
   - `publish()` writes a /p/ file for every stored page.
   - Pushing show alone therefore emits a page for every Amazon product published since 2026-10-15, and the set grows forever.
   - Cloudflare Pages Free allows about 20,000 files per deployment. That figure is from general knowledge and is not verified. The live file count has never been measured.
2. **No store knob except Amazon's.**
   - Walmart's written affiliate agreement bans price tracking and alerts; the operator phoned Walmart on 2026-10-16 and Walmart said history, lowest price and price-drop alerts are fine. Decision (Claude, as engineer of record): Walmart keeps history, but we need ONE knob to switch any store to live-only in a single config change if Walmart or Sovrn objects in writing.
   - Today only Amazon is gated (`amazon_history.hidden`).

## Read first

- CLAUDE.md, AGENTS.md, docs/HANDOFF.md ("Operator decisions": the Amazon amz378_998 and Walmart entries).
- `dealradar/publish/amazon_history.py`: `hidden`, `watches_hidden`, `template`, `watches_template`.
- `dealradar/publish/product_pages.py`:
  - `prepare`: meta state, the `cfg.amazon_history == "hide"` offer filter, admissions and sticky write-back.
  - `_admission_order`.
  - `publish`: wanted-folder cleanup and the /p/ index.
- `dealradar/publish/checker.py`: `HOSTS`, `store_key`, `_store`. Reuse these; do not duplicate them. checker imports amazon_history at module level, so use a function-level import to avoid a cycle.
- `dealradar/publish/site.py`:
  - ~240: the history-checked line.
  - ~527: the badge.
  - ~538: the per-deal `amazon` watch flag.
  - ~735: the prepare call and `product_path` wiring.
  - ~816: `site_version`.
- `dealradar/publish/seo.py`: ~1050–1071 (deal-page observations, lowest_seen, the "Price history" link, `product_pages.publish`) and the /p/ sitemap and IndexNow code.
- `dealradar/publish/product_offers.py` ~141.
- `dealradar/publish/telegram_pub.py` ~60.
- `dealradar/publish/templates/engagement.js`: the `/*amazon-history*/` blocks.
- `dealradar/products.py` ~77.
- `dealradar/config.py`: `amazon_history`/`amazon_watches` parsing (~770–780), `validated_checker`, the `seo_product_pages_*` fields.
- config.yaml: ~141 (`amazon_history`) and `seo.product_pages`.
- Tests: tests/test_product_price_pages.py, tests/test_safety_real_numbers.py, tests/test_e2e_offline.py::test_rerun_is_idempotent.

## Task

1. **Config `store_policy:`** at top level, next to `amazon_history`, validated in config.py:
   - Keys: `walmart, ebay, bestbuy, target, woot, other`, which is `checker.CHECKER_STORES` minus amazon.
   - An `amazon` key is rejected with the message "Amazon uses amazon_history / amazon_watches".
   - Values are exactly `history | live_only`. Any store not listed is `history`.
   - Ship every store as `history` (`walmart: history` written explicitly) with this comment: Walmart verbal OK 2026-10-16 (operator call); set `live_only` if Walmart or Sovrn objects in writing.
2. **One policy chokepoint** in `amazon_history.py`. Keep the module name and update its docstring.
   - `store_of(deal)` is checker's `_store` made public and shared: Amazon first, then the host, then the Walmart/eBay name fallback. checker uses it too.
   - `live_only(cfg, deal)` is true when `store_policy[store_of(deal)] == "live_only"`. It is never true for Amazon.
   - `hidden(cfg, deal)` becomes today's Amazon rule OR `live_only(cfg, deal)`. Every existing caller then gates Walmart with no new call sites: product_pages, seo deal pages and lowest-seen, product_offers, the site badge and history-checked line, telegram_pub and checker.
   - **Watches.**
     - A deal carries the existing per-deal no-watch flag (`public["amazon"]`, read by the engagement.js amazon-history blocks) when it is Amazon and `watches_hidden(cfg)` is true, OR when it is `live_only`.
     - Keep the field name and add a comment that it means "no saved-price watch".
     - Emit the flag and keep the JS blocks whenever either case can apply.
     - JSON bytes for non-Walmart deals must not change.
   - **`product_pages.prepare`.**
     - The offer filter that today runs only under `amazon_history == "hide"` now runs whenever any offer is hidden.
     - A hidden offer never contributes a price, check, date, credit, store name or photo.
     - A page whose offers are all hidden is not built.
     - Keep the sticky-admission write-back for filtered pages.
   - **`site_version`:** fold in the policy map only when it is not all-history, the same way `amazon-history-hide` works.
3. **Bounded /p/ output** under `seo.product_pages`, validated:
   - `keep_days: 90`: write a page only if it is indexable, has a live public deal, or its newest offer's `published_at` falls within `keep_days`.
   - `prune_days: 180`: prepare removes from state, including admission bookkeeping, any page that is not indexable, has no live deal, and whose newest offer is older than `prune_days`.
   - `max_pages: 6000`: after the two rules above, write at most this many files. Indexable pages come first, then `_admission_order`, with the path as a deterministic tiebreak.
   - Validation: `1 ≤ max_indexable ≤ max_pages ≤ 20000` and `1 ≤ keep_days ≤ prune_days ≤ 730`.
   - prepare returns models and paths only for pages that will be written. No card `product_path`, deal-page link, related rail, hub list, /p/ index, sitemap entry, IndexNow ping or checker `p_path` may ever point at an unwritten path. The existing wanted-folder cleanup removes dropped folders.
   - Do not move `seo_product_pages` into a table in this task. Pruning bounds it.
4. **Private run summary** (product_pages block):
   - Fields: `written`, `not_written_old`, `capped`, `pruned_state`, `state_pages`, `state_bytes` (length of the meta JSON) and `walmart_hidden_offers`.
   - Site-level `site_files`: a count of the files under site/ after publish.
   - Log a private warning when `site_files > 15000`.
   - No new public field.

## Tests (offline; new tests/test_store_policy_pages.py, plus the existing files)

- **Config:** ships `walmart: history`; an `amazon` key is rejected; bad values and ranges are rejected; an unlisted store is `history`.
- With `store_policy: {walmart: live_only}` set in the test config:
- **Walmart-only product:** no /p/ file, no `product_path`, no "Price history" link.
- **Mixed Best Buy + Walmart product:**
  - The page exists, with no Walmart price, date, credit or check anywhere in its bytes.
  - Its store name and photo come from Best Buy.
- **Walmart deal page:** no chart, no lowest-seen, no "own price history" line, and the badge is not history-checked.
- **Watch flag:** a Walmart deal's JSON carries the no-watch flag; a Best Buy deal's JSON bytes are unchanged.
- **Elsewhere:** the Telegram text has no history line, and checker output is unchanged.
- **Shipped (all history):** Walmart output is byte-identical to pre-task HEAD.
- **Bounds:**
  - A page that is old, not indexable and has no live deal gets no file, and its path appears nowhere under site/ (grep the tree).
  - An indexable old page is always written.
  - `prune_days` removes the page from meta.
  - The `max_pages` order is deterministic across two runs.
  - With 200 Amazon products in state under `amazon_history: show`, the file count is ≤ `max_pages`.
- **No Walmart, every page inside `keep_days`:** every published byte equals HEAD. The existing byte tests pass unmodified.
- **Rerun:** `test_rerun_is_idempotent` and `test_pipeline_rerun_with_product_pages_adds_no_rows_spend_or_alerts` stay green. The same run key twice gives the same files, the same meta JSON, no new rows and no sends.

## Rules

- Publication only: stored rows stay as they are.
- No new price math; savings.py is untouched.
- No Keepa, LLM or retailer calls.
- Do not change the `amazon_history` or `amazon_watches` values.
- Do not change tests/test_workflows.py.
- Run tests with `-n 1` and your own basetemp /tmp/task250-*, and delete it after each run.
- Do not run the whole suite.
- Do not commit.
- Update docs/HANDOFF.md "In flight" with one short entry.
- This brief was numbered 246 in the planning workflow; 246-248 are taken by the app briefs.

