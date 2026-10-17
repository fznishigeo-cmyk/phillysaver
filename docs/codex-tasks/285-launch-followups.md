# 285 — Launch follow-ups from the last review rounds (five small fixes)

## Why
The launch release (first-screen mix, task 282 appeal rating, task 256 finder
preview) shipped with five should-fix/nit review findings deferred so the
Facebook ad could start on time. Each is small and independent; fix all five
on the live code, each with its own regression test.

## Fixes
1. **Sparse picks bypass the clothing cap** (`drop_rotation.clothing_order`,
   `site.py` pick flags). With few picks (e.g. 12 shirts + 6 gadgets → 10
   picks) the non-pick overflow after the picks can put 4–6 clothing cards in
   one six-card window of the first 24, in both static and browser order.
   Enforce `max_clothing_per_window` across the whole protected prefix
   (`mix_slots`), including non-pick overflow; deferred offers stay on the wall
   and in search. Server, static mirror and real-JS order must agree.
2. **Finder refresh can erase still-fresh comparison rows** (`compare.py`:
   the ASIN list is replaced before product reads; pruning then deletes old
   facts). Keep the last valid selection until replacement price facts exist;
   a run that runs out of time keeps yesterday's valid rows.
3. **Finder preview pages emit JSON-LD** (`compare_pages.py` → `seo.py`
   breadcrumb helper). Preview pages carry no structured data at all; assert
   its absence.
4. **Appeal rating ignores the run deadline** (`appeal_score.ratings`, called
   from publishing). Pass the pipeline deadline through and stop admitting
   batches when publishing time would run out (simulated-clock test: four slow
   batches never push publishing past the deadline).
5. **Expired appeal rows accumulate** (`appeal_scores` table). Prune rows
   older than `appeal_score.ttl_h` (and rows whose deal is gone) in
   `DB.prune()`, keeping the durable per-run claims in `meta` so reruns stay
   idempotent.

## Rules
OFF bytes and every recorded hash unchanged (never re-pin; pin old values via
tests/history_pins.py when a shipped value changes). Idempotent rerun. Phone
budgets pass. Offline only, `-n 1 -p no:cacheprovider`, own `/tmp/task285-*`
basetemp (delete after each run), targeted tests only. No network, no commit,
no push. HANDOFF "In flight": one entry.
