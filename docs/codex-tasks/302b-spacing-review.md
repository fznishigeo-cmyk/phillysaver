# Task 302b — review fixes for same-product spacing (continue in this tree)

Senior review of your task 302 (do not start over; edit your own code):

1. Never drop cards. In `space_rows` (Python) and `spaceSameProducts` (JS), when no pending row fits, the loop `break`s and the remaining rows vanish from the wall (only search keeps them). Instead append the remaining pending rows in their current order and stop. Add a test where a family has 30 members and 10 other cards: the output must contain all 40 rows (same multiset), spacing honored as far as possible.
2. Bound the work for phones. The wall has ~3,600 live deals; `findIndex` over all pending rows with `out.slice(...)` per check can go quadratic or worse. Only space the first 240 positions (the visible pages); rows after that keep their order (still respecting nothing). Keep per-step scans bounded (e.g. look ahead at most 60 pending rows for a fitting one; if none fits within the look-ahead, take the first pending row). Mirror the same rule in Python and JS so the static wall and client agree.
3. Add a node performance test: 4,000 rows with 300 families of 10 + 1,000 singles, `spaceSameProducts` must finish under 50 ms (measure with performance.now, run 5 times, take the median) and keep all 4,000 rows.
4. Keep everything else (flag, pins, OFF bytes). Run your targeted tests with `.venv/bin/pytest -q -p no:cacheprovider --basetemp=/tmp/b302b <files>`; no full suite; no commit/push. Update the HANDOFF entry. Reply ≤ 6 lines.
