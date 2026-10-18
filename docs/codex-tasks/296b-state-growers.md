# 296b — Stop the four tables that grow the state fastest (URGENT, ~1 day)

## Evidence (production size lines from 296, raw SQLite bytes per table)
fast #574 07:18Z vs hourly #221 07:38Z (20 minutes apart):
| table | 07:18 | 07:38 | +20 min |
|---|---|---|---|
| deals | 84,779,008 | 85,233,664 | +454,656 |
| accuracy_samples | 32,141,312 | 32,514,048 | +372,736 |
| deal_click_shown | 16,543,744 | 16,793,600 | +249,856 |
| keepa_recheck_history | 14,684,160 | 14,819,328 | +135,168 |
| posts_seen | 15,654,912 | 15,728,640 | +73,728 |
| source_audit_posts | 11,898,880 | 11,956,224 | +57,344 |
| sightings | 11,735,040 | 11,776,000 | +40,960 |
Whole db 263.2 → 265.5 MB; encrypted state 64.02 → 64.43 MB. state.max_enc_mb (90)
would stop every run within roughly a day at this rate. accuracy_samples is
32 MB with a 35-day retention and still growing ~1.1 MB/hour.

## Task
For each of: accuracy_samples, deal_click_shown, keepa_recheck_history, deals
(and posts_seen / source_audit_posts if cheap):
1. Find every writer and reader (grep the code, tests, publish, report,
   growth report, site check helpers). Write in docs/research/state-size.md
   which window each reader actually needs (e.g. accuracy report looks back N
   days; click learner uses last N days; recheck scheduling uses last N
   attempts per ASIN; deals needed for idempotency/dedupe/revival/"Just missed"/
   deal pages/sitemaps).
2. Add retention knobs under config `retention:` sized to the longest reader
   window + a small margin, ship values that cut steady state well below the
   70 MB warning, and apply them in the existing cooperative maintenance
   (bounded deletes per run, like the incremental cache maintenance, so a fast
   lane never stalls; VACUUM policy as already implemented). Missing knob =
   today's behavior. Also check whether any of these rows store large
   redundant blobs (full JSON payloads, evidence copies, HTML) that can be
   slimmed going forward without losing what a reader needs — only if a test
   proves every reader still works.
3. Never delete what the evidence check, money caps, idempotency
   (tests/test_e2e_offline.py::test_rerun_is_idempotent), deal revival or
   published pages need; live deals are never pruned.
4. Estimate the new steady-state size from the numbers above in the doc.

## Tests
Per table: rows older than the window are deleted, rows a reader needs are
kept (fixture per reader); bounded per-run deletes; rerun idempotent; missing
knobs = today's behavior. Offline, -n 1 -p no:cacheprovider, own
/tmp/task296b-* basetemp (delete after), targeted tests only. No commit/push.
HANDOFF "In flight": one entry. Read CLAUDE.md first.
