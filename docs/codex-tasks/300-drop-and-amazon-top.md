# 300 — Every "Next drop" visibly reshuffles the top 15; the Amazon button sits at the top of search results

## Why (operator, 2026-10-18 ~13:45Z, with screenshots)
1. "Next drop didn't reshuffle... looks unprofessional. People would be waiting
   for that drop and nothing happened. It needs to reshuffle and swap the top 15
   products." The timer reached 0:00, reset to 14:51, and the first cards did not
   change. The visitor watch had flagged "top_rotation: first 6 unchanged after
   drop mark" on 3 of 4 views (2026-10-18 00:05Z) — it was real.
2. Searching "keys" (with some matching deals): "More from Amazon for "keys"" with
   the big "See all "keys" on Amazon →" button appeared only at the BOTTOM after
   the deal cards. It must be at the TOP of the results.

## Task
A. Drop (read the existing rotation/drop code first: task 269 rotation,
   268 drop moment, manifest_refresh, drop_rotation.final_order, the countdown):
   find why the top cards stay the same at a drop mark (no new deals in the
   publish? rotation only on reload? same seed?). Then guarantee: at every drop
   mark (and on load after a mark), the Trending first 15 cards are a different
   selection/order than the previous mark, chosen deterministically from the
   eligible top pool (e.g. the top 60–120 by the existing ranking) with a seed
   derived from the drop index, so it reshuffles even when no new deals arrived.
   Keep every existing rule: clothing at most 2 per 6, niche/team-merch demote
   (never cards 1–8), adult never in the spotlight or first screens, open box off
   the homepage, gone cards ≤ 1 in 10, photos required on first screens,
   Back/reload exact restore of the CURRENT selection, no layout shift (CLS
   stays ~0), reduced motion respected, a subtle "New picks" moment is fine.
   Behind a config knob (missing/false = today's bytes; ship on); recorded pins
   via tests/history_pins.py (chain a new pre_task_300 from pre_task_295; never
   re-pin).
B. Search: when a query has matching deals AND search_more (related Amazon) is
   on, show the big circling tagged Amazon button ("See all "<q>" on Amazon →")
   at the TOP of the results (above the deal cards, where the "Not what you're
   looking for?" line is), and keep the related Amazon cards section below.
   Do not duplicate two identical big buttons on one screen; the bottom section
   keeps a small text link.

## Tests
Node harness: drop index n vs n+1 → first 15 differ (and still obey every mix
rule), deterministic for the same n, reload restore exact; browser test (I run
Chromium natively) for the timer hitting 0 and the first cards changing without
layout shift; search harness: button above cards for a matching query. Offline,
-n 1 -p no:cacheprovider, own /tmp/task300-* basetemp (delete after), targeted
tests only. No commit/push. HANDOFF one entry. Read CLAUDE.md first.
