# Release C — review brief (launch-critical: Facebook ad lands on the homepage at 9pm New York today)

Base: live 7e604bc. This diff adds:
1. Task 256 best value finder, integrated from an older base, shipped in PREVIEW
   (`compare.refresh: true`, `compare.publish: "preview"`): /compare/ pages are
   noindex, no menu link, no JSON-LD, not in the sitemap; Keepa calls bounded by
   compare caps (tokens_per_run 300, tokens_per_day 6000, finder_reserve_tokens
   30 reserved before dispatch, daily halt on an oversized receipt) and must
   never starve price rechecks. Shared HTTP client deadline changes.
2. First-screen mix (rank.top_picks): `space_clothing` (at most 2 clothing cards —
   fashion category or apparel title words — in every 6 of the first 24 picks;
   overflow kept in order right after the 24, nothing dropped) and `title_brand`
   (missing brand = first two title words; `max_same_brand` 2).
3. Config: electronics category factor 1.15; the operator's ~80 everyday staples
   added to the want "everyday" group.
4. Test pins: tests/history_pins.pre_task_279 / pre_first_screen_mix keep recorded
   fixtures on their old values (never re-pinned).

Review for a live launch: correctness and edge cases of space_clothing/title_brand
(server order vs the browser's published pick order, drop rotation 269, allure
spacing, click-price home rows, spotlight), any way the finder's paid Keepa calls
exceed caps or slow/break the fast lane, privacy (nothing private in public bytes
or logs), idempotent reruns, politeness, OFF bytes. Be concrete and brief.
