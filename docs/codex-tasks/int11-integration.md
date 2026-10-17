# int11 — integrate 275 + 274 + 276 + 278 (all shipped ON) on top of 75ad39f

This worktree holds four reviewed tasks merged together (local WIP commits):
275 click price (keepa.click_price_rule on), 274 photos everywhere + used tab only
(site.static_photos, front_page.used_tab_only), 276 Amazon List Price strikethrough
(publish.amazon_reference: list_price), 278 home comment stripping (site.strip_comments),
plus a mobile-audit screenshot-copy step. Each task's tests were written without the
others, so ~30 targeted tests now fail where one task's recorded/OFF fixtures meet another
task's shipped-ON switch.

Goal: every targeted test passes with all four features shipped ON.
- Recorded / "OFF matches HEAD" fixtures pin the other tasks' new switches OFF the same way
  they already do ("recorded before task 274/275/276/278"); use tests/history_pins.py where
  history settings matter. Never re-pin an existing hash.
- Shipped-expectation tests follow the live config with all four ON.
- Phone budgets: with 278 ON the shipped home has ~11 KB headroom; budget tests that assumed
  a single task's bytes should measure the shipped combination (never raise a limit).
- Do not change product behaviour beyond what a test interaction proves is a real bug; if you
  find a real cross-feature bug (e.g. ordering wrappers in index.html or seo.py), fix it with a
  regression test.
