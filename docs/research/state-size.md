# State size and retention — tasks 296 / 296b / 296c

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
