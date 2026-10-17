# 255b — Homepage boost: review fixes (private shadow) + everyday-price tilt (ships OFF)

Continue from task 255 (committed in this worktree as `cx/255`). Read
docs/codex-tasks/255-popular-affordable-drops.md first.

## Why

An adversarial review of 255 found that the shadow leaks Amazon-derived
popularity into PUBLIC places. The GitHub repo and its Actions logs are public
(docs/HANDOFF.md says so; dealradar/growth/__init__.py: "Actions logs are
public, so nothing here ever logs a number"). The operator also asked
(2026-10-17) that the homepage lean toward everyday, affordable items ("not
$300 now $100"), as a soft tilt with "subtlety and some vagueness", never a
hard filter.

## Fixes (all required)

1. **BLOCKER — no shadow data in public output.** Remove
   `summary["popular_affordable"]` and its `log.info` from pipeline.py, and make
   sure neither the "run done" log line nor `cli.py`'s JSON print can contain
   it. Keep the 26 h meta ring (`popular_affordable_shadow`, state.db is
   encrypted). Add a private section to the growth report
   (`dealradar/growth/report.py`, same pattern as `_product_shadow`):
   `popular_affordable` = the latest ring entry plus a 24 h aggregate (runs,
   median of each count, median prices without/with, up to 3 example titles).
   The report is delivered privately (email + private repo); its stdout logs
   stay name-only like the other sections. Test: run a shipped-config pipeline
   offline with qualifying deals and assert no stdout/stderr/log record
   contains `popular_affordable`, `monthly_sold`, `qualifying_at` or an
   example title.
2. **`ps` must not reveal the boost.** site.py publishes `ps = round(pick_score)`;
   divide the popularity factor out before publishing (the boost lives only in
   the `top_picks` order). Test: two deals that differ only in
   `keepa_popularity` publish identical `ps` with the boost enabled.
3. **`first_window_changed`** = number of cards in the with-boost first 24 that
   are not in the without-boost first 24 (a set difference, not shifted
   positions). Test it.
4. **The shadow can never stop a publish.** Wrap `compare` + `record` in
   render_site; on any exception log only the exception type and set the
   summary to None. Test with a monkeypatched exception: the site still
   publishes the same bytes.
5. **`_run_popular_page` errors** (keepa.py): catch `RuntimeError` and
   `httpx.HTTPError` exactly like `_run_pass`, log, and return True so the
   six-page ceiling still holds. Test.
6. **Clock skew:** treat a `sold_at` up to 1 hour after `now` as age 0 (fresh)
   in the factor and in the shadow's `fresh` count. Test.

## New: everyday-price tilt (ships OFF)

7. `config.yaml` → `publish.front_page.everyday_price` (after
   `popular_affordable`):

   ```yaml
       everyday_price:          # operator 2026-10-17: lean toward everyday prices, softly
         enabled: false         # flip together with popular_affordable after the shadow
         full_until: 60         # final_price <= this: factor 1.0
         floor_from: 200        # final_price >= this: floor_factor
         floor_factor: 0.85     # linear between full_until and floor_from
   ```

   `rank.everyday_price_factor(deal, options)` is pure; missing/invalid price
   → 1.0. `pick_score` multiplies by it after the appeal block, only when
   enabled. It applies to every store (price is public), so unlike the
   popularity factor it MAY stay in `ps`. Omit `everyday_price` from public
   `meta.front_page` like `appeal`. With `enabled: false` every published byte
   equals today's (extend the existing OFF-bytes test).
8. **Shadow covers both.** The private comparison is "today's order" vs "both
   tilts on" (popularity boost with its cap + everyday price). Report, in the
   ring and the growth-report section: median final price of the first 24
   without/with, share of first-24 cards ≤ $60 without/with, cards entered/left,
   boosted count, appeal-category cards in the first 24 without/with.

## Rules

- Ships with `popular_affordable.enabled: false`, `everyday_price.enabled:
  false`, `shadow: true`, `keepa.popular_affordable_page: false`.
- Nothing popularity-derived in any public byte, log, HANDOFF.md or test
  fixture text (use synthetic titles in tests).
- Never re-pin an existing hash. Offline tests only, `-n 1`
  `-p no:cacheprovider`, own `/tmp/task255b-*` basetemp (delete after); run
  tests/test_popular_affordable.py, test_front_page.py, test_appeal.py,
  test_unique_asin_rechecks.py, test_e2e_offline.py -k rerun, test_keepa.py,
  test_growth_report*.py (whatever exists), never the full suite.
- No network, no commit, no push. Update the HANDOFF "In flight" 255 entry in
  one short paragraph (switches, files, tests).
