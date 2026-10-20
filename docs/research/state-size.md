# State size and retention — tasks 296 / 296b / 296c / 337 / 337b

## 337b: retain identities, compact hot growers (2026-10-20)

This supersedes 337's receipt clearing and size conclusion below. The strict
`retention.state_retention_337` flag still defaults false and ships true.
No production database, key, network, commit or push was used. These are
disposable synthetic fixtures, not measurements of live state.

### Policies and readers

| Data | Flagged policy and reader verification |
| --- | --- |
| Posts | No writer/schema persists `RawPost.text`, HTML or raw LLM prompts/responses. `post_parses` contains model-validated extracted facts/quotes and already expires after two days; no full-body purge was necessary. New flagged `DB.record_post` emits compact receipts immediately. Legacy receipts backfill after `post_receipt_details_days: 7` only without a live/hidden/suspect sighting. Binary SHA256 primary keys remove duplicate hex indexes; UTC-second clocks and ordinary JSON retain every ID, UID, URL and published clock. Per-row zlib was rejected after it harmed final gzip compression. The existing 30-day dedup window remains. `post_is_known`, `post_first_seen`, all source/intake readers, reopened read-only connections and OFF-after-migration readers include both formats. Indexed binary predicates avoid scanning decoded-hex unions; a locked ID high-water prevents ID reuse. |
| Source audits | `audit_hot_days: 1` moves completed detail to a lossless binary-key table, retaining discovery time, parse-time delay and all counters. `audit_detail_days: 14` then aggregates daily source counts and removes detail. The seven-day reader floor is nine days; pending discoveries and recent completions survive regardless of discovery age. All source audit/intake readers merge both formats. Composite hash/source cursors preserve unusual source names and identical hashes across sources. |
| Critic/run audits | Old legitimacy outcomes, comparison counts on their comparison day, avoided-call/deal totals and estimated costs become daily counts; event/batch identities remain tombstones. Recent comparisons defer archival. Historical daily reports merge detail and counts. Completed efficiency metrics become summed counters; unfinished attempts survive. Run maintenance JSON becomes a retired marker after 14 days, preventing another maintenance allowance. Existing bounded critic quorum samples remain because the tiering guard reads them. Persisted critic/audit facts on deals never change. |
| Clicks | `click_raw_days: 30` atomically archives raw clicks and coverage to daily rows per existing public deal key. Totals plus exact hourly slots and availability/cap bitmaps preserve partial days, local-zone/DST boundaries and missing coverage. The raw floor is eight days, beyond the Worker's seven-day catch-up/replay interval. Shared readers serve `clicks.features`, the ads guard and volatility's click priority. Growth reads the unchanged feature snapshot; money-report click figures still come from GA4. `rank.py` has no raw-click SQL; live ranking stays unchanged. Exact exposure packing and its eight-day-or-configured-lookback floor remain. |
| Accuracy | `accuracy_detail_days: 14`, capped by `accuracy_last_per_deal: 24` per deal/source. Both legacy and compact samples archive exact checked-time/bucket/outcome counts in the deletion transaction. Weighted counts preserve the daily/weekly scoreboard and Wilson intervals. Per-key observed-time high-waters suppress replayed cached readings after retirement; run identities remain. Readings older than an archived watermark are conservatively skipped, rather than counted as fresh. |

All new sweeps use the existing 64-row transactions, frozen durable cursors,
4096 examined rows per table/run and shared one-second cooperative allowance.
Cold post/audit sweeps use binary keys without another random-hash expiry index.
Counts, deletion, cursor progress and allowance roll back together. OFF on an
untouched database preserves prior persistent bytes; migrated data retains
readers after OFF.

Deals, published prices/evidence, sightings, product/death-confirmation facts,
Keepa history and current/future money accounting keep their prior policies.
The flag additionally preserves old run, click-run, accuracy-extra, critic-batch,
AI-yield and AI-take run guards that legacy time-based cleanup could remove.
Sent/pending delivery and alert identities remain untouched. Warning/stop
limits remain 70/90 MB.

### Fixture and projection boundaries

`python -m scripts.state_growers_experiment` builds the largest fixture used
here: 448,000 receipts over 30 days, 189,800 source audits over 14 days,
672,000 accuracy samples (2,000/hour over 14 days), 3,300 live offers,
66,000 immutable sightings and 66,000 post-price facts, 12 protected Keepa
samples per offer and eight days of exact exposure. It adds 45 days of
100 accepted public deal-key click buckets/hour (108,000 rows), with complete
coverage. These are explicit synthetic inputs, not observed production rates.

The first cleanup uses shipped bounds until every initial frozen cohort has
completed. A separate **writer steady encoding** projection losslessly encodes
remaining young legacy receipts: new flagged writers already emit that format,
and the seven-day backfill eventually replaces those young legacy rows. It is
not a claim that normal maintenance immediately rewrites young/live-linked
legacy receipts. It changes no receipt identity, clock, URL or retained count.
The continuation has 24 hourly arrival batches and publications through
24h46m, using compact writers, hourly samples/clicks, five publications
and maintenance slots/hour, plus 3,300 protected own-price facts. Extra bounded
drains and VACUUM are counted explicitly. Actual gzip -6 + AES/PBKDF2 uses a
public fixture password; owned scratch is removed on exit.

Inventory and historical post-fact cohorts are fixed. Continuous new inventory
and production's other permanent receipt ledgers are unmeasured. A separate
`python -m scripts.state_growers_experiment --history-only` adds **297,000 own
price checks** (3,300 offers × 90 days), using the real `check:keepa:date/hour`
identity shape and one check per offer/day. Its incremental encrypted size is
**6,601,408 bytes** (70,205,440 SQLite bytes including indexes). We add that
entire cost without credit for the own-check day already present, plus 5% for
separate-stream/layout compression differences.

### Measurements (decimal MB)

[Content-free measurement artifact](state-337b-measurements.json).

| Table (including owned indexes) | Before | Bounded cleanup | Writer encoding | Continuation |
| --- | ---: | ---: | ---: | ---: |
| `posts_seen` | 140.042 | 32.182 | 0.012 | 0.012 |
| `posts_seen_cold` | 0.000 | 53.408 | 68.129 | 69.157 |
| `source_audit_posts` | 50.000 | 3.490 | 3.490 | 3.551 |
| `source_audit_posts_cold` | 0.000 | 10.478 | 10.478 | 10.478 |
| `accuracy_samples_compact` | 51.429 | 6.160 | 6.160 | 6.160 |
| `accuracy_sample_keys` | 0.369 | 0.369 | 0.369 | 0.369 |
| `accuracy_sample_runs` | 0.041 | 0.041 | 0.041 | 0.041 |
| `accuracy_archive_watermarks` | 0.000 | 0.188 | 0.188 | 0.188 |
| `accuracy_retired_runs` | 0.000 | 0.000 | 0.000 | 0.020 |
| `state_audit_counts` | 0.000 | 0.127 | 0.127 | 0.164 |
| `deal_clicks` | 6.361 | 4.284 | 4.284 | 4.284 |
| `click_daily_counts` | 0.000 | 0.201 | 0.201 | 0.213 |
| `deal_click_coverage` | 0.016 | 0.012 | 0.012 | 0.012 |
| `click_daily_coverage` | 0.000 | 0.004 | 0.004 | 0.004 |
| `deal_click_days` | 7.025 | 7.025 | 7.025 | 7.897 |
| `deals` | 2.531 | 2.531 | 2.531 | 2.531 |
| `sightings` | 16.544 | 16.544 | 16.544 | 16.544 |
| `price_history` | 21.012 | 21.012 | 21.012 | 21.733 |
| `keepa_recheck_history` | 27.230 | 27.230 | 27.230 | 27.230 |
| `run_stats` | 0.008 | 1.147 | 1.147 | 1.888 |
| `meta` | 0.098 | 0.098 | 0.098 | 0.098 |

| Snapshot | SQLite MB | Encrypted MB |
| --- | ---: | ---: |
| Largest starting fixture | 323.940352 | 98.550688 |
| Actual bounded cleanup + VACUUM | 186.888192 | 57.045696 |
| Projected mature writer encoding | 169.439232 | 52.280160 |
| Following continuation + VACUUM | 173.117440 | 52.734864 |

Initial maintenance took **276 bounded calls**, followed by 120 scheduled
publication/maintenance slots and **60 extra drain calls**. These are synthetic
catch-up calls, not a promise of immediate production saving. Protected deals,
sightings, Keepa samples and alert rows were compared byte-for-byte; price
history gained exactly the intended 3,300 rows. Integrity/FK checks passed.

The measured continuation delta is **0.454704 MB** over publication clocks
spanning 24h46m, approximately **0.01836 MB/hour**. The original final drain
used the 24h cutoff, potentially over-retaining a small boundary cohort; the
helper now uses the latest publication clock (24h46m). The final replay fix
was mirrored into the in-flight synthetic database with equivalent retired-run
receipts; the application uses direct transactional inserts, not triggers.
The quoted sizes include its 0.020 MB identity table. The 5% projection margin
also exceeds these small cutoff/schema differences; the full corrected long
experiment was not repeated after those final changes.

**Fixed-cohort steady projection with full own-history envelope:**
52.734864 + 1.05 × 6.601408 = **59.666342 MB**.
This meets the requested 60 MB target **for the modeled cohorts**. Adding the
separate 10 MB allowance for unmodeled state gives **69.666342 MB**;
that broader allowance does not meet 60 MB. Aggregates/permanent identities
still grow, so neither figure proves globally flat production state. Inspect
two decrypted live snapshots and post-deploy size lines before declaring live
state below 60 MB. The 70/90 MB guards were not relaxed.

### Targeted verification

**609 distinct targeted cases passed in 139.78 s** across retention, source
and accuracy reports, clicks/ads guard/volatility, money reports, critic and
efficiency readers, pipeline reruns and Telegram delivery. After the final
retired-run replay guard, **239 cases passed in 55.50 s**, including both
`tests/test_e2e_offline.py::test_rerun_is_idempotent` modes and alert dedupe
(first-send, once-per-deal, legacy sent receipts, dry-run and uncertain-send
suppression). A final **50 grower-only cases passed in 4.45 s**, including all five new
retention knobs and newer-observation replays of retired run keys. Across the
final selections, **616 distinct cases** passed (the last run adds seven knob
cases to the broad 609). Python compilation and
`git diff --check` pass. Every pytest run used `--basetemp=/tmp/b337b`; that
folder and owned model scratch are removed after use. No full suite, commit,
push, deployment or live calls. Owner review and live measurements remain.

## 337: receipt details and disposable API/IndexNow payloads (2026-10-19)

The supplied production totals imply **0.368913 MB/hour encrypted** over
19:45–22:37 UTC, and **50.89 hours** from 71,227,392 bytes to the unchanged
90 MB refusal guard if that rate continues. Those two totals cannot identify
production's top tables. No decrypted production snapshots are available in
this worktree; no live API, secret, state restore or network call was made.
The per-table measurements below are explicitly offline fixture measurements.

### Read-only size/delta helper

Run `python -m scripts.state_growth state.db` or
`python -m scripts.state_growth later.db --before earlier.db --hours 2.866667`.
Unlike the existing eight-table commit log, it reports **every user table**,
row counts, physical bytes including owned indexes from `dbstat`, and logical
byte totals for every column. Without `dbstat`, per-table bytes are labelled
`length_sums`: UTF-8/BLOB lengths, textual numeric lengths, no index or SQLite
record/page overhead. Mixed physical/logical snapshot comparisons fail instead
of manufacturing a rate. A read-only transaction keeps each inventory coherent;
added/dropped tables and negative deltas are included. Output contains counts,
schema names and byte totals, never row contents, keys, URLs or evidence.

### Writer/reader audit and protected state

| Data | Growth path and reader requirement | Task 337 action |
| --- | --- | --- |
| `posts_seen` | `DB.record_post` writes one receipt per processed content hash. Pipeline and fetch adapters use the hash to avoid repeat work; `post_first_seen`, source reporting/intake use source/discovery time for up to 30 days. Repository-wide SQL search finds **no reader** of `uid`, `url`, or `published_at`. Credited URLs and price evidence are independently retained in `sightings` and deal payloads. | After two days, clear only these three unused detail columns, retaining row ID, hash, source and discovery time. The existing 30-day receipt policy is unchanged. Pending discoveries live separately in `source_audit_posts`. |
| `source_audit_posts` | One discovery receipt per source/hash, parse counters filled once. Completed 7-day reports and arbitrarily old deferred discoveries remain readers. | Existing 9-day rule, including recent parse-completion protection and pending discoveries, stays. No further safe expiry was found. |
| Accuracy | `accuracy.record` adds a receipt for each new observed price/run identity. At 2,000 fresh readings/hour: 48,000/day. Current writes intern keys/runs and use `accuracy_samples_compact`; old `accuracy_samples` drains through existing migration. Reports need nine days, sampling needs six hours, two identities suppress replays/cached readings. | Keep the existing encoding/window and both uniqueness guards. |
| Clicks | `clicks.observe_publication` updates hourly slots in `deal_click_days`; for a fixed 3,300-offer set, a new day adds 3,300 rows × 192 payload bytes, rather than 3,300 rows every publication. A midnight allocation is a burst, not a sustained hourly rate. Features require exact covered hours over the configured learner window. | Keep 8 days (or longer configured lookback) and run receipts. No exposure sampling. |
| Keepa/API responses | `PoliteFetcher.api_get` keeps response bodies in **`_api_bodies` memory**, never SQLite. `http_cache` persists only opaque `api:` identities, validators and a fetched clock; current price calls with `conditional=False` bypass even that cache. A bodyless later process already retries a 304 unconditionally. | Expire only `api:` validators older than one day; ordinary feed/page validators and NULL/future clocks retain their prior policy. No request, token reserve or cap logic changes. |
| Keepa history | `History.record` replaces each retained offer's at-most-12-sample ring. Scheduler, death confirmation, stability and token/cached-observation accounting read those facts, including old stability anchors. | Existing orphan/excess-sample cleanup only; no age cutoff. Fixed inventory saturates; new inventory is unbounded. |
| Deal/photo fields | `verify._harvest_image` writes `photo_lookup_asin` and `photo_lookup_found_none` on the existing deal; later reads overwrite them. The new visitor reserve overwrites a single small `keepa_budget` meta record. Full product reads are reduced to selected facts, not saved raw responses. | Keep photo retry/suppression and liveness markers, image URLs, evidence, critic facts and popularity. A cold metadata addition is not one appended record/hour. Never expire an alive/hidden/suspect deal. |
| Price observations | `price_checks.record` is bounded by the shipped **one own check/offer/UTC day**: at most 3,300/day for this fixed fixture (137.5/hour averaged), plus permanent post facts. `verify`, `savings.py`, volatility, product history and Deal Check read observations; some readers need lifetime post facts. | Preserve 90-day own checks and all post-price facts, even though public history is hidden. |
| Run/AI/critic logs | Existing run/efficiency/yield windows, LLM call ledger/reservations, critic rolling samples, per-run spend claims, delivery batches and receipts have report/replay/cap readers. Cached extracted facts (`post_parses`) already expire after two days. | No new deletion or shortened window. Pending calls, today's/tomorrow's reservations, current-day/run accounting, alert/send claims and all existing replay anchors remain. |
| IndexNow | `indexnow_batches.versions` repeats URL/hash maps. Terminal batches no longer read the map; endpoint/run/status still suppress submissions. Sending batches need their full map to finish, however old. | Clear only received/failed version maps after one day. Keep the entire receipt row and every sending map; existing page-level facts/queue/receipt timestamps stay. |
| Link facts/tool pages | `publish.link_facts.derive_link_facts` computes from retained facts; tool pages are static artifacts. Neither creates an append-only state table. Stable posted redirects, product identities/offers, attribution and SEO fact memory have active readers. | No pruning. |

An offline call to the real `verify._harvest_image` with a known ASIN and no
photo adds **64 compact-JSON bytes once**, or 211,200 raw bytes across 3,300
offers. Repeating it 100 times adds **zero** bytes. This is a logical payload
measurement, not an encrypted-size saving or a production photo-coverage claim.

### Flag, expiry and compaction

`retention.state_retention_337` is a **strict bool**, defaults **false** in
`config.py`, ships **true** in `config.yaml`. The three positive integer knobs
are `api_cache_days: 1`, `post_receipt_details_days: 2`, and
`terminal_indexnow_versions_days: 1`. They have no effect while OFF; existing
options, schemas, writers, bytes and maintenance bookkeeping remain as before.
Disabling after cleanup cannot restore discarded unused details; all readers
still work with trimmed receipts. No new table/schema migration is required.

The existing cooperative maintenance now adds three rowid keyset sweeps. They
share its one-second budget, frozen high water, round-robin cursor, 64-row
transaction and 4,096-examined/table/run allowance. Expiry is strict `<`:
recent, exact-boundary, future and API NULL clocks survive. Updates/deletes,
cursors and allowance commit together, including deadline rollback. Finishing
a sweep cannot restart it under the same run key or join another lane's newer
sweep. Fast lanes continue deferring VACUUM; the existing hourly 10%-free-page
policy is retained. Bounded draining and subsequent compaction must finish
before any projected saving can be claimed in production.

### Reproduction and size limit

`python -m scripts.state_growth_experiment` builds the largest existing 296c
fixture shape (3,300 live deals, 66,000 sightings and permanent price facts,
432,000 compact accuracy samples, eight full click-exposure days, 12 Keepa
facts/offer, 448,000 post receipts and 122,000 completed audit receipts).
It reuses the existing real schema/fixture helpers and gzip-6/OpenSSL helper
with its **public fixture password**, never a repository secret. Receipt ages
are spread uniformly over their full 30-/9-day windows; the old helper's
i-second timestamps covered only 5.2/1.4 days, which is unsuitable for a
steady-state expiry estimate. Historical cohorts are seeded transactionally;
the measured hour/day uses actual accuracy, deal-upsert and click writers.
All databases/encrypted files live in automatically removed owned `/tmp`
scratch. The model records both unvacuumed and explicitly vacuumed sizes,
bounded-drain calls, complete table/column inventories, a one-hour snapshot
delta and a further 24-hour continuation with retention.

Scenario inputs are **623 post receipts/hour**, **565 completed audits/hour**
(the existing cohort sizes divided by their retention windows, rounded up),
2,000 accuracy observations/hour, five publications/hour, and an explicit
**40 unique API validator identities/hour**. These are model inputs, not live
traffic measurements. The first snapshot follows VACUUM: packed hash indexes
split when inserts resume, and the first publication of a UTC day allocates
daily exposure blocks. That hour's page growth must not be extrapolated as
a perpetual production rate. The day continuation exposes the distinction.

Measured top five **first-hour physical growers**, including indexes:

| Table | Added rows | Raw MB/hour | Evidence |
| --- | ---: | ---: | --- |
| `posts_seen` | 623 | **2.396160** | 39,872 new hash bytes, 15,575 discovery-clock bytes, 60,001 unused uid/URL/published-clock bytes; most physical allocation is packed-index splitting, not new row data. |
| `source_audit_posts` | 565 | **1.515520** | 36,160 new hash bytes, 28,250 discovery/parse-clock bytes, scalar report counters and two indexes. |
| `deal_click_days` | 3,300 | **0.954368** | A new UTC day allocates 633,600 hourly-slot payload bytes and 3,300 key/day identities once. |
| `accuracy_samples_compact` | 2,000 | **0.499712** | New key/run integer IDs, checked/observed clocks, bucket/outcome and receipt indexes; legacy table adds zero rows. |
| `http_cache` | 40 | **0.008192** | 2,720 opaque API-key bytes plus 1,000 fetched-clock bytes; **zero response-body bytes**. |

The whole encrypted snapshot grows **571,136 bytes** in that cold allocation
hour. These numbers do **not** apportion the supplied live 0.368913 MB/hour.
In the following retained day, the largest physical net growers instead are
`price_history` **0.030037 MB/hour**, maintenance/run bookkeeping
**0.016213**, `posts_seen` **0.010411**, API validators **0.006997**, and
compact accuracy **0.002389**; click daily blocks have saturated. Physical
growth can coexist with fewer retained rows because indexes/pages rebalance.

| Snapshot | SQLite bytes | gzip bytes | encrypted bytes |
| --- | ---: | ---: | ---: |
| Largest fixture before measured hour | 280,137,728 | 84,569,438 | **84,569,456** |
| After that hour and initial bounded cleanup, before VACUUM | 285,663,232 | 81,358,287 | **81,358,304** |
| Same data after explicit VACUUM | 243,941,376 | 80,659,068 | **80,659,088** |
| Further 24-hour continuation and final drain/VACUUM | 245,538,816 | 80,430,918 | **80,430,944** |

Initial receipt cleanup needs **110** maintenance calls with the shipped
limits; the final drain needs **97 additional** calls after the 120 modeled
publications. These are same-clock fixture drains, not promised production
throughput. The 24-hour encrypted delta is **−228,144 bytes**, or
**−0.009506 MB/hour**: approximately flat for the fixed inventory/cohorts,
after catching maintenance up. Keeping 10 MB for unmodeled production state
gives **90.43 MB**, already above the 90 MB guard and far above 60 MB.
The fixture includes only a day of own checks; their full 90-day residency,
new live offers, permanent post observations and pending discoveries can
increase this estimate. It is not a finite bound for growing inventory.
VACUUM is material but modest for encryption: the first cleanup leaves **zero
freelist pages**, so the normal 10% trigger will not immediately perform the
explicit fixture VACUUM. Use **81.36 MB** as the measured initial saving's
unvacuumed endpoint, not the lower manually compacted number. No new forced
VACUUM policy is introduced.

The **below-60-MB production target is not established** by disposable-cache
expiry alone. Receipt hashes, inventory, source audit and historical facts
remain protected. The 296c stress model already measured 83.94 MB before its
10 MB allowance, and none of its largest protected cohorts can simply be
deleted. This is a conservative reduction plus a usable measurement tool;
production attribution still requires two decrypted snapshots and this report.
Do not deploy assuming the 90 MB incident is solved. Further lossless receipt
storage compaction or the separately documented R2 migration remains necessary
if production follows the stress case; no storage backend is changed here.

### Targeted verification

**261 distinct cases passed, one browser case skipped** across the requested
offline selections (262 unique collected IDs). Final overlapping runs:
162 passed/1 skipped; 100 passed; 48 passed. The initial quoting error in the
diagnostic test fixture was corrected; no failing selected case remains.
Selections cover the new rules/strict flag/config validation, exact-boundary
and future/NULL retention, OFF/missing behavior, bounds, interrupted chunk
rollback, receipt replay, all protected fact/alert/delivery/money fixtures,
source-audit readers, existing 296b/c maintenance, incremental-cache behavior,
every published file's bytes and both offline pipeline rerun variants.
Every pytest invocation used `--basetemp=/tmp/b337`, `-p no:cacheprovider`
(execution runs `-n 1`); scratch was deleted afterward. Both large model runs
completed with SQLite integrity/fact-count checks; the initial young-age run
was superseded by the uniform-age results above. No full suite, live calls,
commit or push. Runtime/production size and the 60 MB target remain unresolved.

## 296c: lossless compact writes (2026-10-18)

`retention.compact_accuracy_samples` and `retention.compact_click_exposure`
are strict booleans, default **false**, shipped **true**. No public output,
evidence checks, money caps, requests, ranking or retention windows change.
Tables are created lazily, so a missing/false knob on an old database keeps
the old schema and writer bytes. Readers recognize both encodings regardless
of the switch; turning either switch off does not hide previous observations.

### Accuracy receipts

The audit found no raw payload in `accuracy_samples`: its cost was repeated
deal URLs, run keys and timestamp strings in the table and its three indexes.
The report needs checked time, bucket and outcome. The optional sampler needs
deal keys checked within six hours. Replay and cached-reading suppression need
**both** `(run,deal,source)` and `(deal,observed time,source)` uniqueness.

`accuracy_sample_keys` interns `(deal_key,source)`; `accuracy_sample_runs`
interns run keys. `accuracy_samples_compact` stores their integer IDs, checked
and observed UTC seconds, bucket and outcome, with the same two uniqueness
constraints and an indexed checked clock. `util.iso` already truncates to
seconds, so no clock precision is lost. Store is omitted because neither
reader uses it; source remains in the identity required for deduplication.
No hashing, sampling or change to the denominator is involved. Keeping these
compact receipts, rather than just daily totals, preserves replay and the
six-hour exclusion without inventing another approximate dedupe mechanism.

Both writers check receipts in the other encoding before accepting a sample.
Reports merge both formats and still select seven **completed local days**,
including DST and timezone edges. Extra sampling reads recent keys from both.
Read-only report connections need no migration or schema write.

### Click exposure

The learner joins exposure to individually covered/capped hours and click
counts over a configurable 1–720-hour window. A daily sum or a top-screen-only
sample would change its features. `deal_click_days` instead has one row per
deal and UTC day: a 192-byte little-endian array of **24 binary64 seconds
values**. Each slot retains the old per-hour arithmetic and 3,600-second cap;
`-1` means absent (a stored zero remains distinguishable). Historical position
is omitted because features uses the current publication order.

Publication gaps, disappearing offers, fractional seconds, hour/day boundaries
and run guards behave as before. The reader combines legacy and packed hourly
seconds, counts clicks once per covered deal/hour, and excludes the current
unfinished hour. That also supports a switch back to the legacy writer halfway
through an hour. Neither empty coverage nor opt-outs become zero-click evidence.

### Migration, retention and rollback

The existing cooperative maintenance migrates legacy rows in the same 64-row
transactions and 4,096-examined/table/run, one-second shared budget as 296b.
Insert into the compact encoding, deletion of the legacy row, cursor and
allowance commit together; an interrupted chunk rolls back schema creation too.
Migration uses a bounded rowid sweep, expiring already-old rows instead of
copying them. Compact accuracy expiry uses its timestamp index; bounded key/run
sweeps delete only dictionary entries with no remaining samples. Packed click
expiry uses the day index and retains the entire boundary day (at most 23 extra
hours), so no needed partial-day slot is removed. Future observations survive.
Fast runs still defer VACUUM; hourly runs retain the existing 10%-free policy.

The switches control writing and migration, not reading. With the new code,
switching them off is safe, including for a mixed database. An **old code
rollback** must first export compact accuracy receipts and hourly slots to the
legacy tables or restore the last pre-migration snapshot; old code cannot read
these new tables. No immediate production reclamation is claimed: migration
and retention must drain, then hourly VACUUM must reclaim free pages. The
production size log remains the rollout check.

### Reproducible write measurement and remaining size gap

Run `.venv/bin/python -m scripts.state_write_experiment` offline. It creates
and removes its own `/tmp/task296c-model-*` directory, uses the real schema and
writers, and calls the existing gzip `-n -6` / OpenSSL encryption helper with
a public fixture password. No production database, secret, network or Git
write is involved. The fixture contains:

- 3,300 live deals, 20 normalized sightings and permanent post-price facts per
  deal (66,000 each), and varied current prices/outcomes.
- 2,000 accuracy readings and deal payload updates per hourly run; four fast
  publications/hour, at :01/:16/:31/:46, plus the hourly publication: **120
  runs/day**, all exposing the same live inventory. Different observed seconds,
  rotating recheck cohorts, unique run keys and hourly exposure boundaries
  exercise the receipt indexes. Each mode records exactly 48,000 samples/day.
- A saturated nine-day accuracy window (432,000 samples), eight exposure days
  plus a partial boundary day, and 12 Keepa facts per retained deal.
- Unchanged post/audit receipts calibrated to **the full 296b observed raw
  growth × retention windows**, including indexes: 159,449,088 bytes / 448,000
  `posts_seen` rows and 37,367,808 bytes / 122,000 completed audit rows before
  VACUUM. Deterministic SHA256 content hashes model incompressible receipts,
  with URLs, source names and timestamps, never post bodies.

The before column uses legacy accuracy/exposure writers and uncompressed deal
payloads; after enables both new knobs plus 296b's existing deal compaction.
The upsert test separately proves the 296b reduction through real dedupe
upserts, with identical expanded facts. This is a comparison of the legacy
writers with all three compact writers, not a production trace or physical
disk-I/O benchmark. Snapshot deltas are **net retained state growth**; every
save still uploads the entire encrypted file.

| Measurement | Before, bytes | After, bytes |
| --- | ---: | ---: |
| Deal payload bytes submitted for 2,000 hourly updates | 7,737,056 | 732,500 |
| Mean payload bytes/update | 3,868.528 | 366.250 |
| First modeled day: SQLite growth | 25,559,040 | 6,033,408 |
| First modeled day: encrypted growth | 4,094,976 | 1,478,944 |
| Mean SQLite growth/run, 120 runs/day | 212,992 | 50,278.4 |
| Mean encrypted growth/run, 120 runs/day | 34,124.8 | 12,324.53 |
| Saturated SQLite snapshot after VACUUM | 459,243,520 | 281,026,560 |
| Saturated gzip snapshot | 95,611,124 | 83,935,470 |
| Saturated encrypted snapshot | 95,611,152 | 83,935,488 |
| Encrypted estimate including 10 MB allowance for other state | 105,611,152 | **93,935,488** |

Net encrypted growth falls **63.88%**, and hourly deal payload writes fall
**90.53%**. First-use allocations are not representative: the first hourly
run creates dictionaries/day rows and grows encrypted state 179,072 → 207,568
bytes; the first fast run crosses into a new daily block and grows it
101,456 → 96,144 bytes. Amortize across the modeled day, not just one cold run.
The first-day encrypted snapshots are 11,275,616 → 8,236,880 bytes, including
the initial protected inventory (7,180,640 → 6,757,936 bytes).

**The brief's below-70-MB requirement remains unmet under its sustained-rate
stress scenario.** The measured compact model is **83.94 MB before** the
explicit 10 MB allowance, or **93.94 MB including it**. A separate real-schema
component measurement, with the same post/audit cohorts and VACUUM/encryption,
finds **60,625,264 encrypted bytes for those two unchanged tables alone**
(`posts_seen` alone: 47,863,120). Thus even zero bytes for accuracy/exposure
would leave little room below 70 MB for protected inventory/history and all
other state. The former whole-state compression proxy underestimated these
hash-heavy receipts; a lower asserted total would not be evidence-backed.

For the modeled day the current two-writer change reduces growth immediately,
but it does **not** establish that production can continue saving indefinitely.
The 20-minute production burst is not a measured long-term arrival rate, and
live inventory/permanent facts have no finite bound. Resolving the remaining
target requires either a separate lossless post/audit receipt compaction task,
or measured production arrival rates demonstrating that this burst does not
persist. Those writers and retention windows are unchanged by 296c; do not
deploy on the assumption that 70 MB has been achieved. Keep checking the
actual encrypted-size log and bounded migration progress.

### 296c targeted validation

Final targeted run: **224 passed in 86.54 s**, including both accuracy writer
formats through Keepa/Woot/store callers and optional sampler limits, exact
report/report-line parity over DST/timezones, mixed exposure formats and
partial coverage, zero/fractional slots, 168/720-hour cutoff boundaries,
same-run replay, interrupted migration rollback, missing/false writer bytes,
296b retention/compaction, protected facts/money, unchanged publication/Node
output, and both `test_e2e_offline::test_rerun_is_idempotent` modes. Selectors:
`test_state_write_less`, `test_accuracy_scoreboard`, `test_click_signal`,
`test_state_growers`, `test_lighter_state`, the rerun test above,
`test_state_size::test_unbounded_readers_evidence_and_money_are_untouched` and
`test_state_retention_publication::test_retained_publication_and_node_controller`.
Earlier focused runs: 68 passed / 1 migration-bookkeeping assertion failed
(fixed); 154 passed / 21 legacy-table assertions failed (test projection
updated, now parameterized over both formats); then 124 passed. Every run used
`-q -n 1 -p no:cacheprovider` and an owned `/tmp/task296c-*` basetemp removed
afterward. Python syntax and diff checks pass. No full suite, browser, network,
commit or push. Production migration throughput and size remain unmeasured.

## R2 option

Design only: no storage scripts, workflows, Cloudflare resources or secrets
are changed by 296c. Keep `state.db` local during the job and keep the existing
gzip/AES/PBKDF2 `state_crypt.sh` envelope and `STATE_DB_KEY`.

- **Operator setup:** create a private Standard-storage R2 bucket, for example
  `wowpriced-state`, in the existing Cloudflare account. Disable public access
  and public custom domains. Create an R2 S3 credential with **Object Read &
  Write**, restricted to this bucket. Bucket creation/lifecycle administration
  stays with the operator; the runner needs object GET/HEAD/PUT/LIST/DELETE,
  not account-wide administration. Save `R2_ACCESS_KEY_ID`,
  `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` and `R2_ENDPOINT` as GitHub repository
  secrets; retain `STATE_DB_KEY` and the existing account ID. The S3 endpoint is
  `https://<account-id>.r2.cloudflarestorage.com`, region `auto`. Existing Pages
  API credentials are not automatically R2 object credentials.
- **Restore:** a default-off storage-backend knob selects git or R2. Fetch a
  small private `latest.json` manifest and its immutable snapshot object into
  temporary files. Check encrypted byte length and SHA256 from the manifest,
  decrypt, validate the SQLite header and `PRAGMA quick_check`, then atomically
  replace local state. Remember the manifest ETag/generation. Missing object,
  authentication/network/decryption failure or invalid state must fail the job,
  never start empty or silently restore an older git copy. Bootstrap requires
  an explicit operator-controlled first seed.
- **Save:** close/checkpoint SQLite (or use its backup API for a consistent
  snapshot); reuse deterministic gzip, encryption, diagnostics and byte guard.
  Keep git's thresholds until cutover; an R2-specific warning/stop threshold
  can then be chosen from measured restore/runtime limits, with operator review,
  instead of inheriting Git's 100 MB object limit.
  Compare a plaintext snapshot digest to avoid encryption-salt-only rewrites.
  Upload under an immutable run/generation key, verify length/checksum by
  reading it back, then conditionally replace `latest.json` against the ETag
  restored by this job. A stale writer fails; it must not overwrite a newer
  generation using today's git retry's last-writer-wins behavior. Confirm the
  chosen R2 client/endpoint supports the required conditional write before
  implementation; otherwise serialize publication through a small coordinator
  with atomic compare-and-swap. Retries reuse the uploaded snapshot. All state
  writers (fast, hourly, ads guard, Pinterest connect) must share the existing
  `dealradar` concurrency group; read-only probes/reports use the same restore
  helper. Keep `sources_report.md` on git separately. Review every restore/save
  workflow caller and disable the data-squash job after cutover.
- **Retention and cost:** explicitly keep the latest 24 successful snapshots
  plus seven daily checkpoints; garbage-collect only unreferenced generations
  after publication succeeds. At 70 MB each this is about **2.17 GB** (plus
  manifest/temporary upload slack), not a snapshot forever on every run.
  Planning assumptions, **not reverified online because this task is offline**:
  Standard R2's free allowance is 10 GB-month storage, 1 million Class A and
  10 million Class B requests/month, with no internet egress fee. At 120 runs/day
  and even three saves/run, snapshot+manifest PUTs are about 21,600/month;
  restores and verification reads are similarly far below the request limits.
  With those allowances unused elsewhere in the account, modeled R2 cost is
  **$0/month**. Recheck current terms and shared-account usage before enabling:
  [R2 pricing](https://developers.cloudflare.com/r2/pricing/),
  [R2 credentials](https://developers.cloudflare.com/r2/api/tokens/),
  [S3 compatibility](https://developers.cloudflare.com/r2/api/s3/api/).
- **Cutover/rollback:** pause writers, seed R2 from the latest validated git
  snapshot, round-trip decrypt and compare SQLite bytes, then enable R2 and
  resume. Keep the final git state and deployment SHA as a checkpoint. To roll
  back later, pause writers again, export the **latest validated R2 state** to
  `data:state.db.enc` using the existing size guard, round-trip verify, switch
  back to git, and resume. Restoring the old pre-cutover git state would lose
  spend/replay receipts and is not an acceptable ordinary rollback. If state
  exceeds git's guard, reduce it safely and validate first; do not bypass the
  guard. Retain R2 checkpoints until rollback has been verified.

## 296b: actual growers and reader-safe limits (2026-10-18)

The production table sizes in `docs/codex-tasks/296b-state-growers.md` supersede
the **assumed table mix** of the older 296 experiment below. Production state
was not opened; all implementation and checks here are offline. The new
retention/compaction reduces retained work, but **a steady state below 70 MB
cannot be established from these measurements**. In particular, neither live
inventory nor deferred discoveries can safely receive a total-row cap. Do not
treat the old IndexNow-heavy synthetic saving as a production forecast.

### Writer and reader inventory

Searched `dealradar/`, `tests/`, `scripts/`, `.github/`, including publication,
growth reports and site-check helpers, for every table name, raw payload SQL,
and calls of the DB inventory methods. All dates below use strict `<` expiry:
an exact boundary and future records survive.

| Table | Writers and readers | Shipped retention and reason |
| --- | --- | --- |
| `accuracy_samples` | Sole writer `accuracy.record`; callers are `accuracy.keepa` (Keepa feed and price checks), Woot feed refresh and `store_prices`. `accuracy.report_data` reads the **seven completed local calendar days**, through yesterday; growth `report._accuracy`, private daily/Money formatting and scoreboard tests reuse it. `accuracy.extra_samples` excludes keys checked in the last **6 hours**. PK `(run_key,deal_key,source)` and unique `(deal_key,observed_at,source)` suppress repeated current/cached observations. | New `accuracy_samples_days: 9` (floor 9) replaces 35 days. Seven local days plus the current partial day, timezone/DST and report margin. No downsampling; every retained sample, outcome and bucket survives. Extra-read daily cap and run guards remain on their existing **35-day** tables. |
| `deal_click_shown` | Sole writer `clicks.observe_publication`, also via `record_shadow`/site rendering; increments hourly exposure once per run. `clicks.features` joins covered hours and clicks over `first_party_clicks.lookback_hours` (**168**, configurable through 720); its shadow sorter, `publish/click_signal` private feature export, `growth.report` and ads guard consume these features or the saved shadow. `volatility` reads **click counts**, not exposure rows, over 6 hours. | New `deal_click_shown_days: 8`, floored at **max(7 days, configured learner lookback)**. Remove the writer's old unbounded 30-day DELETE for this table when the knob is set; cooperative maintenance owns it even when click collection is off. Other click counts, coverage and run receipts keep their existing 30-day rules. |
| `keepa_recheck_history` | Sole writer `keepa_rechecks.History.record` already bounds each offer to `keepa.recheck_adaptive.history_samples` (**12**), folding repeated observations. `History` reads request/run replay guards, latest answered/request clocks and paid-request coverage. `Schedule` uses **all retained readings** for probability/cadence. `volatility.Plan` uses all retained per-mode token receipts and observations, **48-hour changes**, **7-day stability** (including arbitrarily old `stable_since` anchors) and promo list-price evidence. Shared ASIN `observation`/`counted` facts suppress duplicate yields. Pipeline/site health use these same readers; the daily/7-day growth report reads the separate `keepa_recheck_yield`, not samples. | New `keepa_recheck_history_samples: 12`, floored at the reader's configured history count. No age cutoff for retained offers: a sparse old sample can still be the latest request, cost receipt or stability anchor. Cooperative cleanup trims legacy excess entries and deletes only orphan rows, after their deal disappears. One row per retained deal; normally at most 12 samples in it. |
| `deals` | Writers: `DB.insert_deal`/`update_deal`, called by dedupe/upsert/repricing, pipeline verification/death/revival/photo/identity repairs, Woot refresh, store checks, buybox, extractor category repair and rank; constructor repairs normalize conditions/flag unbacked prices. `products.attach` and `ai_yield` update their SQL identity/lifetime anchors. Readers through `get_deal`, `all_deals`, `deals_by_store`, `deal_by_canonical_url`, `deals_for_post`: dedupe, pipeline, verify, rank, extractor, source audit, critic tiering, accuracy, clicks/click-price, buybox, store prices, non-Amazon freshness, photo recovery, Amazon reference and checkout-price rules; publication site/SEO/checker/product pages/product offers/social/Pinterest; Keepa/Woot fetchers and the buybox probe. `get_deal`/canonical matching can revive a retained death; fuzzy matching reads live rows. Rank's grey "Just missed" window is **24h**; SEO weekly hub stats need **7d**; `/deal/` pages keep noindex ended pages until the row expires, while sitemaps/IndexNow include only eligible live pages. Product offers, post price history and posted-link redirects retain their independent anchors. | Existing `deals_days: 14` **after death**, with `last_seen_at` as the legacy missing-death fallback. Never age out a live row, including hidden/suspect/long-lived used offers. Keep the standing 14-day policy and revival interval. Compact duplicate payload facts, described below, instead of shortening it. |
| `posts_seen` | `DB.record_post` is the writer; `post_is_known` is read by pipeline, RSS/pages/Keepa/Woot adapters and newsletter acknowledgement. `post_first_seen` supplies discovery time when the feed has no date. Source-audit `_history` uses 24h/7d; intake history supports configurable **1–30d**, and due-post scheduling uses the latest completed parse. | Existing `posts_seen_days: 30` retained. Shortening would re-enable paid parsing of still-present feed/email versions and truncate the supported 30-day intake reader. Only the established expired receipts are deleted, now in bounded chunks. |
| `source_audit_posts` | `DB.audit_seen` inserts once per discovered content hash; `audit_parsed` fills counters once, after processing. Source-audit `_history` uses first discovery for 24h/7d counts and **parse completion** for drop/evidence counters. `intake_deferred` uses the **oldest unprocessed discovery**, without an age bound. Growth intake reports and pipeline summaries reuse these readers. | New `source_audit_posts_days: 9` (floor 9), requiring **both** discovery and parse completion older than the cutoff. Unparsed rows survive. While enabled, `audit_seen` ignores already-known `posts_seen` hashes, so refetching a processed 10-day-old post cannot recreate its expired audit row as a new discovery. Existing 30-day parse receipts stay intact. |

The site-check JS and growth site-health helpers read generated HTML/JSON,
deal pages, sitemaps and freshness clocks; they do not query these tables or
introduce another SQL retention window. Tests of their publication inputs must
preserve full output, not just the number of offers.

### Duplicate payload removal

`deals.payload` persisted the whole `sightings_for(id)` array on every upsert,
in addition to normalized sightings and duplicated SQL columns. With
`compact_deal_payloads: true`, insert/update writes compact JSON and omits only
fields already reconstructed from SQL by `_deal_row_to_dict`: id/key, store,
item/normalized item, canonical URL, prices/savings, online flag, tier/status,
expiry and first/last-seen timestamps. All nonduplicated facts, evidence,
critic decisions, death/revival clocks, price provenance and conditions remain.

An existing JSON sightings array is replaced by the internal
`_sightings_in_table` marker **only after exact equality with the normalized
rows**. Legacy/synthetic sightings that do not match remain verbatim. Every DB
inventory reader, including read-only snapshots, reconstructs the full list
before returning the deal. No sighting, credit, post identity or history fact
is deleted to compact a live deal. The two extractor raw-payload readers only
repair category fields and preserve the marker. Existing blobs are compacted
through the bounded inventory sweep; future pipeline writes compact immediately.
Turning the knob off preserves the ability to read previously compacted rows.

The other five tables contain scalar facts/receipts rather than post bodies,
HTML or evidence copies. Every Keepa sample field has an active reader (cost,
stock/price, volatility, caching, shared-observation accounting or replay).
Dropping these fields or shortening the 12-reading reader was rejected. The
unused-looking old timestamps still support sparse-offer stability and coverage.

### Cooperative maintenance, compatibility and limits

The pipeline and CLI pass the same normalized policy into `DB.prune`.
Omitted/null new day/count knobs retain their previous windows; omitted/false
compaction preserves the old payload writer. If all are absent, the legacy bulk
prune path is used. New integer knobs accept 1–3650; reader floors prevent
unsafe short overrides. Budget and row limits are validated separately.

Enabled maintenance shares a **1.0-second cooperative budget**, bounded by the
existing pipeline deadline minus its 90-second tail reserve. Each transaction
examines at most **64 rows**; each table can examine at most **4,096 rows/run**
(including retained inventory/history/discovery rows). Accuracy/posts use their
existing expiry indexes; exposure adds `idx_click_shown_hour`. Inventory,
history and audit use rowid keyset sweeps with a frozen high-water mark. No
OFFSET or repeated full-table expired-row scan. The per-table limits include
updates and deletions; deleting a dead deal still cascades its sightings under
the existing foreign key.

Rows, per-table cursors and `run_stats.state_maintenance` counters commit
together. A timeout or exception rolls back the unfinished chunk. Resuming the
same run retains its allowance; a completed table cannot restart under that
key. A durable next-table pointer rotates work across runs if time expires
mid-round. New rows beyond a frozen high-water mark wait for the next sweep;
refreshes ahead of it are read with their current facts inside the write lock.
Logs expose counts/completion/deferred/time only. Progress uses fixed-size meta
records and the existing 60-day run-stat retention.

Fast runs defer VACUUM as in incremental validator maintenance. Hourly/manual
runs retain checkpoint + VACUUM only above 10% free pages. A SQL statement,
lock wait, large single payload/cascade or hourly VACUUM is not preempted by
this cooperative budget. Creating the new exposure index is a one-time schema
operation. Other pre-existing table cleanup and constructor migrations are
unchanged. End-to-end job time and production drain rate are not measured.

### Size estimate: the requested 70 MB target is not demonstrated

Use decimal MB and 72 twenty-minute intervals/day. The supplied encrypted
growth is 0.41 × 72 = **29.52 MB/day**. At 64.43 MB that is about **4.53 hours
to warning** and **20.79 hours to refusal**, if that burst persists. Net
page-allocation changes do not distinguish new offers, updated payloads,
freelist reuse or sample saturation, so this is a stress extrapolation.

| Dated table | Observed raw MB/day | Retained days | Rate × window, raw MB | Encrypted proxy MB |
| --- | ---: | ---: | ---: | ---: |
| accuracy_samples | 26.836992 | 9 | 241.532928 | 58.61 |
| deal_click_shown | 17.989632 | 8 | 143.917056 | 34.93 |
| posts_seen | 5.308416 | 30 | 159.252480 | 38.65 |
| source_audit_posts, completed rows only | 4.128768 | 9 | 37.158912 | 9.02 |
| **Subtotal** | | | **581.861376** | **141.20** |

The encrypted proxy uses the only supplied whole-state compression ratio,
64.43 / 265.5 = 0.242674. It is **not a measured per-table ratio, prediction or
lower bound**. Even this subtotal excludes live/dead inventory, retained Keepa
history, pending discoveries and the other tables. Applying the same proxy to
the old 35/30/30/30 windows gives about 428 MB: the new dated windows cut that
scenario by about two thirds, but cannot justify a below-70-MB claim.

`deals` has **no finite steady-state bound** while live inventory grows.
Its observed 32.735232 raw MB/day cannot be converted into a dead-row residency
estimate without births/deaths and compacted payload sizes. Keepa is already
bounded at 12 samples/retained offer; its 9.732096 raw MB/day burst cannot keep
growing at that rate for a fixed offer set. Normalized sightings and post
price history remain protected independently. Current row ages are unknown,
so zero immediate expiry benefit is the safe assumption; the new windows may
not yet contain any expired production rows.

**Remaining operational requirement:** inspect the next production table/row-age
and compressed size lines after bounded compaction and hourly VACUUM. This task
does not supply evidence that saves will remain below the warning/refusal gates.
If the burst persists, preserving these exact readers requires a further
lossless storage/aggregation or state-storage change; shorter windows or a cap
on live offers would violate this brief. The 70/90 MB gates, evidence checks,
money caps, delivery receipts and public history policy are unchanged.

### 296b offline validation

Targeted tests cover expired/exact-boundary/future rows, three local-calendar
accuracy windows including DST, configurable click learner parity, every Keepa
history consumer, dead/live/revival boundaries, source reports/pending discovery
ages and known-post refetches. They also cover bounded deletes per table,
same-run replay, interrupted chunk rollback, fair resumption, keyset progress,
query plans, CLI/pipeline wiring, legacy/missing knobs, unchanged complete
publication (including ended pages/sitemaps/Deal Check) and read-only hydration.
First focused run: **37 passed in 7.19 s**. Expanded reader/regression run:
**341 passed in 250.73 s**, selecting `test_state_growers`,
`test_accuracy_scoreboard`, `test_click_signal`, `test_value_per_token_rechecks`,
`test_unique_asin_rechecks`, `test_volatility_tiers`, `test_dedupe`,
`test_source_audit`, `test_lighter_state` and both
`test_e2e_offline::test_rerun_is_idempotent` modes. Final targeted checks:
**144 passed in 73.21 s**, selecting `test_state_growers`,
`test_incremental_cache`, `test_evidence`, the protected-reader/money fixture
in `test_state_size`, the publication/Node fixture in
`test_state_retention_publication`, both frozen fetch/publication snapshots
and both offline pipeline rerun modes. The recorded fetch fixture ignores only
the new empty maintenance bookkeeping column; its expected database facts and
publication hashes are unchanged. No failing targeted checks remain. All runs use
`-n 1 -p no:cacheprovider`, owned
`/tmp/task296b-*` basetemps removed after each run. No network, full suite,
Chromium, commit or push.

A separate **compaction-only** fixture reused `protected_facts` and `measure`
from `scripts/state_size_experiment.py`, without its IndexNow cohorts. It has
2,000 live deals, 40,000 normalized sightings and 40,000 post-price observations.
Before measurement each deal was updated with its full expanded DB row and
`sightings_for(id)`, reproducing the duplicate payload writer. One bounded
`DB.prune(..., state_retention=options(load_config()), run_key=...)` completed
the inventory sweep; every expanded deal compared equal afterwards. Actual
gzip/OpenSSL sizes (public fixture password, no secrets) were:

| Snapshot | SQLite bytes | gzip bytes | encrypted bytes |
| --- | ---: | ---: | ---: |
| Duplicate payloads | 29,614,080 | 4,755,838 | 4,755,856 |
| Compacted payloads | 29,614,080 | 4,586,849 | 4,586,880 |

The **168,976-byte encrypted saving is modest**: duplicated textual keys and
URLs already compress well. Raw allocation did not shrink because freed space
was inside occupied pages, not whole freelist pages; the existing VACUUM
threshold therefore did not fire. Future rows can reuse that space. This is
not a production-mix model or a demonstrated solution to the imminent size gate.
The fixture lived in an owned `/tmp/task296b-model-*` directory, removed on exit.

## Earlier task 296 investigation (historical synthetic model)

Offline investigation, 2026-10-18. Production state was not opened. The brief
reports 254.8 MB SQLite / 59.0 MB encrypted, growing about 4.5 MB/day. Those
totals do **not** establish the production table mix; the first deployed size
lines must confirm it. All MB below are decimal, 1,000,000 bytes.

## What grows, and what may expire

The schema is in `dealradar/db.py`; `persistence.py` checkpoints reservations
through `scripts/commit_state.sh`. The existing gzip-before-encrypt and
greater-than-10%-free-page VACUUM remain. No full posts or images are introduced.

| State / reader | Retention decision |
| --- | --- |
| `indexnow_batches.versions`: `indexnow_claim` saves up to 10,000 URL/hash pairs per run; no prior cleanup. `indexnow_finish` reads the map **only while status is `sending`**. `submit_indexnow` reads the permanent run/status receipt first. | New `retention.indexnow_batch_versions_days: 7` replaces old `received` / `failed` maps with `{}`. Never delete the endpoint/run/status/HTTP receipt or any sending map. Exact cutoff and newer maps survive. A timed-out send finished as failed retains its receipt; a sending batch can still finish years later. |
| `appeal_scores`: one row per deal/price; `appeal_score.ratings` accepts only scores within `appeal_score.ttl_h` (72 h shipped). | New `retention.appeal_scores_days: 7`; keep at least the configured TTL even when it exceeds seven days. Strictly older scores expire. Future/exact-cutoff rows, run claims and money ledgers survive. |
| `sightings`, including repeated versions of long-lived deals | Keep existing behavior. `sightings_for`, source credits, `deals_for_post`, post death/recheck mapping, photo recovery, product history and dedupe have unbounded readers. Only the existing dead-deal deletion cascades these rows. A simple age-based deletion would change facts and allow duplicate observations. |
| `price_history`, product identities/offers | Keep existing behavior: own `check:*` observations expire after 90 days; post observations persist. `price_stats` / verification use the all-time median; `product_price_history(..., None)` feeds product facts even after deal deletion. Deal Check, product offers and volatility also have bounded history readers. No shortened window, sampling or rollup. |
| `http_cache`, `post_parses` | Existing 30-day idle validator expiry and two-day parse reuse/expiry remain. HTTP stores validators only, not response bodies. Parse reuse re-verifies evidence against the current post in `parse/extractor.verify_evidence`. Incremental HTTP cleanup and its fast/hourly compaction policy are unchanged. |
| `meta` AI takes, current/past fact hashes, appeal run claims | Preserve unlimited readers / paid-call dedupe. AI takes already keep at most four prior fact/version pairs per page; removing these on age alone could rebuy the same wording. Existing legacy-cache and AI-run cleanup remains. |
| `alerts_sent`, sent/pending `outgoing`, `delivery_batches`, social history, `indexnow_pages` | Preserve permanent dedupe/quota/receipt anchors. Existing 60-day rejected-outgoing expiry remains. Current URL receipts, key-pending flags, removal tracking, retry state and metrics are unchanged. Social history also supports redirects for old posted links. |
| LLM calls/reservations, run/audit tables | Existing 35/60-day policies remain; today's/tomorrow's spend and pending calls retain their protections. No cap, evidence or reservation logic changed. |

Both new retention knobs default to `None` in code: omitted/null means the
previous unlimited behavior. Config accepts integers 1–3650; invalid values
fail closed. CLI prune and the pipeline both pass the knobs and the appeal
reader's TTL. This deliberately reclaims large completed **payloads**, retaining
the small records required for idempotency. It cannot bound all state forever.

## Size line and guard

Every `commit_state.sh` invocation with state and a key prepares one gzip/encrypted
snapshot before remote access, reports it once (including no-op runs), and reuses
that exact encrypted snapshot across raced-push retries. Temporary gzip and
encrypted files are deleted on exit; plaintext is never staged. Existing legacy
restore/migration and unchanged-state comparisons remain.
An unchanged plaintext with a differently sized remote encoding is re-encoded
once, so reusing an old compressed copy cannot bypass the measured size limit.

The line has prefix `[commit_state] size ` followed by compact JSON:

```json
{"db_bytes":253816832,"enc_bytes":58901440,"free_pages":0,"gz_bytes":58901421,"method":"dbstat","page_size":4096,"pages":61967,"tables":[{"bytes":231796736,"name":"indexnow_batches"}]}
```

Example truncated to one table. Actual output lists up to eight tables ordered
by bytes descending, then name; table sizes include their indexes/overflow pages.
`dbstat` unavailable means `method: "page_counts"`, database page counts and an
empty table list, without pretending to know per-table allocation. An unreadable
legacy SQLite diagnostic reports `method: "unavailable"`; the actual file-byte
guard still runs. No row contents, URLs, receipts, configuration values other
than numeric thresholds, or keys are printed.

`state.warn_enc_mb: 70` warns **above** 70,000,000 bytes.
`state.max_enc_mb: 90` exits 1 **at or above** 90,000,000 bytes, before even
`ls-remote`, staging, committing or pushing. Invalid thresholds/configuration
also refuse the save. Missing/null individual thresholds retain the previous
behavior for that threshold. Positive finite values are required, and when both
exist the warning must be below the maximum. The guard is not an emergency
pruner: operators must inspect the size trend before it trips.

## Reproducible synthetic measurement

Run from this worktree with existing dependencies (no network):

```sh
.venv/bin/python -m scripts.state_size_experiment
```

The script creates the real SQLite schema in its own `/tmp/task296-model-*`
directory and removes that directory in `finally`. It uses a public fixture
password, not `STATE_DB_KEY` from the environment. No git operations occur.
The fixture has 2,000 live deals, 40,000 sightings, 40,000 permanent post-price
observations, and 14 days × 101 completed IndexNow batches × 1,000 URLs. Slugs
repeat, changing version hashes are deterministic SHA256s, and every seventh
URL has a shared noindex version. Counts/compressibility are calibrated to the
brief's totals, **not measurements of production activity**. No expiry benefit
from appeal scores is assumed. No live facts or price observations expire.

Measured with the actual `gzip -n -6` and AES/PBKDF2 scripts:

| Snapshot | Raw bytes | Gzip bytes | Encrypted bytes |
| --- | ---: | ---: | ---: |
| Before new retention | 253,816,832 | 58,901,421 | 58,901,440 |
| After shipped retention + existing VACUUM | 137,183,232 | 34,598,803 | 34,598,832 |
| Add one more day, before maintenance | 153,735,168 | 38,480,920 | 38,480,944 |
| Next day's maintenance | 137,195,520 | 34,599,695 | 34,599,712 |

The initial totals are within 0.4% raw / 0.2% encrypted of the brief. The model's
largest table including indexes is IndexNow batches: 231,796,736 bytes before,
115,998,720 after; sightings are 10,977,280 before and price history 9,867,264.
Their row contents remain intact; VACUUM also packs their pages more tightly.
Old receipt anchors remain, and expired payloads release 24.30 MB encrypted.
The next retained day adds only 880 encrypted bytes of anchors in this model;
this is **not** a forecast of production's remaining unbounded tables.

For a conservative headroom calculation, transfer only the measured 24.30 MB
one-time saving to the brief's 59.0 MB snapshot and **keep the full observed
4.5 MB/day growth rate**. Projected post-maintenance size is 34.70 MB:

| Threshold | Before (days) | After (days) |
| --- | ---: | ---: |
| 70 MB warning | 2.44 | 7.84 |
| 90 MB stop | 6.89 | 12.29 |
| 100 MB host limit (save already stopped at 90) | 9.11 | 14.51 |

Thus the shipped defaults buy **about 5.4 additional days**, conditional on a
production mix like this fixture, even without credit for a lower growth rate.
The model's unpruned batch increment is 3.882 MB/day. If that much of the observed
4.5 MB/day is actually completed IndexNow payloads, residual growth after expiry
would be about 0.619 MB/day including anchors, yielding roughly **89 days to
90 MB**. Treat that as a scenario, not a promise. If production growth instead
comes from protected sightings/history, only the report/guard is assured and
the saved space could be much smaller. Inspect successive deployed size lines
before revising the estimate or changing additional retention policies.

## Verification and native browser follow-up

Targeted offline checks cover retained unbounded readers, exact cutoff/TTL
boundaries, pending and completed IndexNow finishes/replays, source evidence
cache, current/future money reservations, CLI/pipeline wiring, missing knobs,
dbstat/index bytes/fallback, size-line/no-op behavior, exact guard byte limits,
and the existing offline pipeline rerun. Local workflow fixtures use only their
temporary file origins; this worktree was never committed or pushed.

Completed targeted runs: **101 passed in 44.68 s**, **113 passed in 85.78 s**,
and **76 passed in 38.32 s** (some state/workflow checks repeated after the remote
encoding guard fix). Every run used `-q -n 1 -p no:cacheprovider` and its own
`/tmp/task296-*` basetemp, deleted after the run. Shell/Python syntax and
`git diff --check` pass. No test failures in these runs.

`tests/test_state_retention_publication.py` compares every published file byte
before/after pruning with optional product pages and Deal Check enabled, retaining
400-day-old observations. Its Node harness tests the emitted Deal Check controller
without launching Chromium. This exercises the existing dormant history mode;
production's withdrawal of public history is unchanged.

Chromium was deliberately not launched in this sandbox. Run this command natively
with the existing Playwright package and `/opt/pw-browsers/chromium` installed
(the shared `tests/checker_page_browser.cjs` harness uses that executable):

```sh
python3 - <<'PY'
import shutil
import subprocess
import tempfile
base = tempfile.mkdtemp(prefix='task296-native-', dir='/tmp')
try:
    result = subprocess.run([
        '.venv/bin/python', '-m', 'pytest', '-q', '-n', '1', '-p', 'no:cacheprovider',
        '--basetemp=' + base,
        'tests/test_state_retention_publication.py::test_retained_publication_browser',
    ])
finally:
    shutil.rmtree(base)
raise SystemExit(result.returncode)
PY
```

That test calls `node tests/checker_page_browser.cjs <temporary-site>` and checks
phone/desktop, light/dark, keyboard focus, live/ended/unknown results, credits,
price clocks and URL privacy; external traffic is intercepted. A browser skip
means native QA remains pending, not a pass. No full suite was run here.
