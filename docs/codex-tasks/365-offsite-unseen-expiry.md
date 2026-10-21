# 365: expire deal rows that are NOT on the site and unseen for N days (ships OFF)

## Why (production numbers, fast lane #757, 2026-10-21 08:58Z)
state.db.enc is 76.4 MB and grows 0.25-0.4 MB/hour; at 90 MB `state.max_enc_mb`
refuses the state push (~2026-10-22 evening). The `[commit_state] size` line now has
a deal breakdown (dealradar/state_size.py deal_sizes):
- deals table 112 MB of 307 MB raw; alive 30,692 rows (31.7 MB payload), dead 3,227.
- alive by last_seen_at: <1d 4,736 rows; 1-7d 18,699; 7-14d 7,257; >14d 0 (DB is ~12 days old).
- the site publishes ~1,613 deals (run summary "published"), 700 more are hidden as photoless,
  and amazon_used is disabled with 5,980 "live_rows_suppressed".
So ~28,000 alive rows are not on any public surface, and nothing ever removes them:
DB.prune / state_retention only delete DEAD deals `retention.deals_days` after death.
Operator rule (2026-10-11, comment in DB.prune): a LIVE deal is never removed for its
age, because Amazon "used - like new" offers stay up until bought. That rule protects
deals ON THE SITE. This task adds an operator-switchable expiry for rows that are
NOT on the site, ships OFF, and the operator decides whether to turn it on.

## What to build
1. Config `retention.offsite_unseen_days`: strict int 1..3650 or null; default null = OFF
   = byte-identical previous behavior (writer, DB, publication). Ships null in config.yaml.
2. When set to N: delete alive (status 'alive' or any non-dead status) deal rows whose
   last_seen_at is older than N days AND that are not on any public surface now.
   "On a public surface" must be decided from facts the pipeline already has; find them
   (e.g. what render_site/rank.site_deals publishes this run, spotlight rows, saved-deal /
   deal pages / product pages / tool pages / finder pages, the static mirror, IndexNow
   receipts that still point at a live URL). If you cannot prove a row is off every
   surface, keep it. A row published in the CURRENT run's render is always kept,
   whatever its age. Prefer computing the protected key set from the same function the
   render uses, not a second copy of the rules.
3. Remove the dependent rows of each expired deal the same way dead-deal pruning does
   (sightings; keepa_recheck_history already prunes on deal_key NOT IN deals; check
   product_offers, price_history, click/accuracy tables, saved deals, outgoing guards,
   indexnow) — but NEVER delete idempotency/send guards: re-running the same run key must
   add no rows and resend no alerts (CLAUDE.md), and a product that is sighted again later
   and re-created must not re-send a member alert that was already sent. Prove both in tests.
4. Bounded like existing maintenance: state_maintenance_max_rows, state_maintenance_budget_s,
   transactional chunks of 64, durable cursor, run-key allowance (see state_retention.py,
   task 337). Works both with state_retention_337 true (production) and false.
5. Log one content-free line per run: rows expired, rows protected as on-site, rows left
   because of the bound, by status. No keys/titles/URLs in logs.
6. Offline measurement (scripts/, temp DB, no network): a synthetic DB shaped like the
   production breakdown above (33.9k deals, 52.7k sightings, plus keepa_recheck_history /
   price_history / product_offers rows per deal in realistic ratios); report before/after
   raw bytes after VACUUM and gzip bytes for N = 7 and N = 3, and how many runs the bound
   needs to drain the backlog. Put the numbers in docs/research/offsite-expiry-365.md.

## Tests (pytest, no network)
- OFF (null) = byte-identical DB + publication to before; strict config parsing.
- On: an alive row unseen for N+1 days that IS published this run is kept (several
  surfaces); an unpublished alive row unseen N+1 days is deleted with its dependents;
  unpublished but seen within N days is kept; dead-row policy unchanged.
- Re-sighting an expired product re-creates it cleanly; no duplicate member alert;
  same-run-key replay adds no rows; bounded chunks resume across runs.
- Existing suites touching retention/state size/publication stay green.

## Rules
Follow CLAUDE.md (evidence, idempotency, knobs in config.yaml, store facts only).
Do not change what the site publishes. Do not commit or push. Write a short HANDOFF
note in docs/HANDOFF.md. Run the relevant tests; the full suite is run by Claude.
