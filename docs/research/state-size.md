# State size and retention — task 296

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
