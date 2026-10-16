# Lessons from systems code for wowpriced

Study date: 2026-10-16. The useful transfers are bounded work, shared results,
explicit lifetimes and conservative candidate selection. Keep Python, SQLite,
vanilla browser code and Cloudflare Pages. Neither these sources nor this study
establish that wowpriced can run 99.9% unattended; measure completed scheduled
publishes, recovery and operator interventions before making that claim.

## Scope, provenance and recommendation

Read CLAUDE.md, AGENTS.md, docs/HANDOFF.md, task 220 and the
[Clone Wars study](clone-wars-study.md). Reference repositories were read only;
no network, installs, reference execution, product execution, test suite,
commits or temporary-directory deletion occurred. Only this study and briefs
221–226 are deliverables. Task 220's explicit file restriction takes precedence
over the general HANDOFF update instruction; HANDOFF remains unchanged.

Reference paths below are relative to `/home/user/`; our paths are relative to
this worktree. A `path:line` points to an inspected source line. Quotes are short
source excerpts, not claims about live production. ADOPT means use the pattern
(sometimes already present); ADAPT means use only the restricted translation;
SKIP means do not implement it here. All configuration names in proposals are
new unless explicitly called existing. No source transplant is proposed;
copying implementation would require a separate license review.

| Checkout | Local HEAD |
|---|---|
| redis/redis | 4e4c65bc71dd |
| id-software/quake-iii-arena | dbe4ddb10315 |
| karpathy/nanogpt | 3adf61e154c3 |
| karpathy/llm.c | f1e2ace65149 |
| bellard/quickjs | 84e723f1ace5 |
| golang/groupcache | 2c02b8208cf8 |
| varnishcache/varnish-cache | 0038dd19804f |
| apache/lucene | 8476457b8752 |
| flaxsearch/luwak | 7d78a434fe6d |
| quickwit-oss/tantivy | e1acb3e0112f |
| bytedance/monolith | 135c491a52b1 |
| twitter/the-algorithm | c54bec0d4e02 |
| wowpriced | 55fb33b4ab0d |

First build candidates: unique-ASIN recheck batches, incremental cache cleanup,
a price-free freshness manifest, bounded prefilter analysis, shared link facts
within one publish, and a time-decayed click comparison kept private/shadow.
These extend existing work; they do not replace tasks 109/119–121 or the
earlier proposed 201–206. No switch is activated by this study.

## Gates inherited by every ADOPT/ADAPT and every build brief

**G1 — access.** CLAUDE.md/AGENTS.md/HANDOFF standing decisions apply. Honor
robots.txt and documented source-host-only permission, never bypass bot/login
walls, retain at most 1 request/s/host, descriptive UA, backoff and conditional
GETs. No new retailer crawling, headless partner browsing or new paid service.

**G2 — facts and money.** Preserve `dealradar/parse/extractor.py:101` evidence
verification and `dealradar/rank.py:406` publish gates. Store facts only, not
post bodies/images. All savings arithmetic stays in `dealradar/savings.py:263`.
Keep config.yaml LLM caps, model-priced reservations, Keepa reserves, receipts,
first-429 stop, observation clocks and two-reading death rules. Optimization
must never turn a reused observation into a fresh independent reading.

**G3 — Amazon.** Apply task 220's restriction as a project requirement: no new
Amazon price history, price alerts, watch targets, historical price caches or
price-change notices. Keep `amazon_history: hide`, the shared Amazon identity
predicate (`dealradar/verify.py:369`), mixed-store exclusions and current-price
as-of/disclaimer (`dealradar/publish/amazon_as_of.py:22`). Store-name-only tests
are insufficient. A new transport must not archive old Amazon snapshots.

**G4 — repeat safety and durability.** Same run/publish key, retries, overlapping
requests, reload and interrupted work must not add duplicate rows, charges,
observations, notices or sends. SQLite state stays encrypted on the data branch;
never add state.db to the product branch. Local commit is distinct from remote
encrypted checkpoint. Keep outgoing claims/uncertain-delivery handling.

**G5 — controls/privacy.** Behavior changes use validated config.yaml switches,
omitted/false = off. Disabled output and existing enabled switches retain their
contracts. No new third-party script, tracker, fingerprint, account, session ID,
service worker or notification service. No inferred unique people or purchases.
Device choices stay local; existing GPC, opt-out and internal-traffic gates stay.

**G6 — phones and release.** No framework/engine dependency. Measure generated
raw/gzip bytes, requests, parsing/query work and memory on fixed fixtures before
claiming speedups. Preserve first-card position, image priorities/dimensions,
44px targets, 16px inputs, keyboard/focus, 360px wrapping and reduced motion.
Keep native navigation/static fallback. Later implementation sessions run
focused offline checks and the required full suite before any push; Worker
changes also require `node --test cloudflare/subscribe-worker/`. This study ran
none. Ship off, review, then observe through existing private reporting.

## The six stated problems, checked against this checkout

| Problem | Evidence and qualification | Direction |
|---|---|---|
| Fast lane has about 12 minutes | `.github/workflows/fast.yml:30` sets the job limit; `config.yaml:13` gives the pipeline 600 seconds. `dealradar/pipeline.py:2201` creates its deadline; `:2493` bounds fast judging. `:2761` calls unsliced pruning; `dealradar/db.py:1330` may VACUUM the whole DB. A spike is plausible, not reproduced here. | Bound optional work; do not increase the job timeout or weaken correctness checks. |
| Whole public JSON downloads | `dealradar/publish/templates/index.html:3217` cache-busts GET with no-store; `:3383` uses it for drops, `:3457` for the five-minute fallback. `:3334` already HEAD-polls while waiting. `dealradar/publish/seo.py:90` emits no-store for JSON. The brief's ~2,300 deals is context, not a measured live count. | First skip unchanged payloads with a small price-free manifest; defer historical deltas. |
| Large files hide dependencies | `dealradar/pipeline.py:2177` starts orchestration in a 2,849-line file; home search is at `dealradar/publish/templates/index.html:1261`, auth at `:2446`, adoption at `:3283` in a 3,462-line file. `dealradar/rank.py:190` imports publisher internals. | Extract one coherent owner at a time; no framework or pipeline rewrite. |
| Untrusted-text CPU risk | `dealradar/parse/extractor.py:282` and `:291` have unanchored digit runs before required suffixes. `:327` enumerates price pairs. `:375` already cleans to the configured length (`config.yaml:37`, 6,000). | Concrete polynomial risks; no demonstrated exponential hang. Bound scanning and pair work without weakening evidence. |
| Repeated derived facts | `dealradar/rank.py:245` invokes the entire serializer just for earns; `dealradar/publish/site.py:764` computes candidate scores, `:777` serializes, `:780` scores again using earns. Some repetition is justified by different eligibility stages. | Share only pure link facts within a frozen render, not cached rank/price verdicts across mutations. |
| Interrupted work | `dealradar/db.py:644` already has transactional bounded product attachment; `dealradar/pipeline.py:2302` records handled posts only. `.github/workflows/fast.yml:86` saves after failure but excludes cancellation. `dealradar/persistence.py:31` checkpoints before sends. | Retain these contracts; distinguish replay safety from recovery of work not saved before a hard kill. |

## Techniques in the reference implementations

### Redis — four techniques

**R1. Incremental work includes a bound on unproductive scans.**
`redis/redis/src/dict.c:407`: `empty_visits = n*10`; `:429` returns when exhausted.
The time wrapper at `:449` checks elapsed time between 100-bucket batches.
`redis/redis/src/ae.c:394` chooses the earliest timer for polling.
Ours: `dealradar/db.py:1250` pruning and `dealradar/pipeline.py:2761` tail work.
**ADAPT, G:** 222 limits visited cache rows as well as deletions, remembers a
cursor and leaves publication/state-save room. This is cooperative work, not a
hard real-time guarantee: a Redis bucket can hold many keys and its timer can
overshoot one batch. Measure worst slice duration/backlog age; do not promise
“never a latency spike” or port an event loop into our synchronous pipeline.

**R2. Carry stable metadata next to its owner.**
`redis/redis/src/sds.h:33`: `uint8_t len`; `:102` reads that header directly.
Ours: `dealradar/publish/site.py:619` derives identity, `:636`/`:657` derive
earning links; `dealradar/rank.py:245` repeats projection for earns.
**ADAPT, G:** 225 extracts shared immutable link facts for one render/config.
Python strings already know their length; a C header is irrelevant. Reuse only
inputs whose lifetime is fixed, leave money math in savings.py, and count
serializer/link-helper calls plus render CPU to measure actual payoff.

**R3. Identity lookup and score order are separate views.**
`redis/redis/src/t_zset.c:1546`: `dictFind(zs->dict, member)`; `:1717` updates the
skiplist score while preserving the identity entry. Ours:
`dealradar/clicks.py:228` already sorts a private candidate order.
**ADAPT, G:** 226 builds a key→feature map and sorts once per publish for a
decayed shadow comparison. At our scale ordinary Python dict/sort is simpler
than a Redis server/skiplist. Measure sort/query time and candidate moves;
recomputing all decayed scores at a common time avoids stale sorted scores.

**R4. A small cardinality sketch does not create an identity signal.**
`redis/redis/src/hyperloglog.c:197`: `HLL_REGISTERS (1<<HLL_P)`; `:199` uses six
bits/register, `:480` hashes the supplied element. Dense registers consume
16,384 × 6 / 8 = 12,288 bytes, plus header; small sets can use sparse encoding.
Ours: `cloudflare/subscribe-worker/clicks.js:91` retains deal/hour counts only.
**SKIP visitor HLL:** no visitor identifier exists to insert, and hashing IP/UA
would introduce tracking. A sketch of deal keys counts distinct clicked deals,
which SQLite can already count exactly here. No unique-person payoff is
available; only consider sketches for a future measured high-cardinality need.

### Quake III Arena — three techniques

**Q1. A delta is relative to a specific baseline and includes removals.**
`id-software/quake-iii-arena/code/qcommon/msg.c:875`: “a NULL to is a delta remove
message”; `:894` tests changed fields, `:923` emits no-change bits.
Ours: `dealradar/publish/site.py:996` writes complete public JSON;
`dealradar/publish/templates/index.html:3217` downloads it again.
**ADAPT, G:** borrow the explicit version/fallback discipline for 223's manifest.
Defer price-bearing delta archives, particularly Amazon. A detailed possible
non-Amazon protocol is below; its bandwidth payoff requires measured changed
fractions, compressed size, extra round trips and parse cost, not game ratios.

**Q2. Scratch allocation has a phase lifetime.**
`id-software/quake-iii-arena/code/qcommon/common.c:765`: “one free block”;
`:1577` resets hunk pointers to marks. `:1604` Hunk_Clear also calls shutdown
functions; `:1707` Hunk_Alloc zeroes allocated memory. Z_Malloc at `:1009` calls
the block-list allocator (`:928`) then zeroes memory, so allocation is not O(1).
Ours: `dealradar/pipeline.py:2213` creates run state and
`dealradar/publish/site.py:777` creates render objects.
**ADAPT, G:** give 225's derived facts one render lifetime; discard on return.
Do not port manual arenas or assert Python cleanup is O(1). Persistent work
belongs in transactional SQLite checkpoints, not an arena. Measure peak memory
and retained references; no claimed job-time benefit without a profile.

**Q3. Approximation belongs only where its error is acceptable.**
`id-software/quake-iii-arena/code/game/q_math.c:561`: `0x5f3759df`; `:563` applies
one refinement. The code demonstrates an approximation, not a benchmark proving
it beats today's hardware. Ours: `dealradar/savings.py:263` owns exact project
money rules, while `dealradar/clicks.py:224` is already a heuristic score.
**SKIP the numerical trick:** never approximate checkout prices, savings or
budget accounting. 226 can evaluate a documented ranking heuristic against a
baseline; measure ranking stability/coverage, not fabricated purchase lift.

### nanoGPT — two techniques

**N1. One readable unit exposes its invariants and fallback.**
`karpathy/nanogpt/model.py:33`: `assert config.n_embd % config.n_head == 0`;
`:62`/`:65` show optimized and manual attention paths together.
Ours: `dealradar/rank.py:190` reaches into site.py for link/step facts and
`dealradar/publish/site.py:586` mixes those with the entire public projection.
**ADAPT, G:** 225 creates one small pure owner for link facts, with explicit
inputs and existing serializer wrappers. Do not reduce file size by hiding
state behind a service container. Measure helper call counts and review the
public/member byte comparison; clarity is a maintenance benefit, not measured
retention or permission to replace the existing model/parser.

**N2. Bound shapes at the entry point; compute only the required output.**
`karpathy/nanogpt/model.py:173`: `assert t <= self.config.block_size`; `:190`
projects only the final token for inference. Ours:
`dealradar/parse/extractor.py:375` already limits cleaned text, but `:327`
can compare every price pair. **ADAPT, G:** 224 bounds numeric token/pair work
and returns unknown conservatively when exhausted. Do not truncate evidence to
invent an easier valid answer. Measure operation counts, spend and unchanged
valid classifications; no PyTorch/model dependency or new LLM calls.

### llm.c — two techniques

**L1. Stages and their data are visible in order.**
`karpathy/llm.c/train_gpt2.c:863`: `layernorm_forward`; `:865` calls attention
explicitly; `:922` describes reversing the forward pass for backward.
Ours: `dealradar/pipeline.py:2496` through `:2515` run photo/link/recheck phases;
`dealradar/publish/site.py:1159` through `:1163` seal final output in order.
**ADAPT, G:** keep the orchestration readable while extracting one pure owner
in 225. Later extract one pipeline phase with explicit DB/fetcher/deadline;
do not introduce a generic workflow engine or reorder audit/IndexNow sealing.
Payoff is easier failure isolation; measure phase durations and review surface.

**L2. Allocation/reuse has an explicit compatibility contract.**
`karpathy/llm.c/train_gpt2.c:790`: `if(model->acts_memory == NULL)`; `:809`
rejects incompatible B/T reuse; `:901` requires a completed forward pass.
Ours: `dealradar/http_client.py:363` cannot reuse an API 304 without its body;
`dealradar/publish/site.py:764` and `:777` operate on different staged facts.
**ADAPT, G:** request sharing must include every semantic option and preserve
observation identity. 221 starts within one compatible request group; 225
captures one immutable render/config. Measure avoided work and assert incompatible
inputs never hit. No process-global cache, GPU buffers or persisted raw bodies.

### QuickJS — four techniques

**J1. Compact representations depend on target architecture.**
`bellard/quickjs/quickjs.h:63` enables NaN boxing only without JS_PTR64;
`:147`: `typedef uint64_t JSValue`; `:229` defines the alternative tagged struct.
Ours: `dealradar/publish/site.py:648` uses explicit public fields and `:997`
compact JSON. **SKIP NaN boxing:** no useful translation to Python/JSON/browser
values; do not bit-pack prices or obscure locked-stub fields. Existing compact
serialization is suitable. Measure actual gzip bytes before changing the wire
schema; the pointer in task 220 to quickjs.c is completed by this header evidence.

**J2. A monolith still distinguishes runtime and context ownership.**
`bellard/quickjs/quickjs.c:321`: `struct JSRuntime`; `:518`: `JSRuntime *rt` in
JSContext. Ours: `dealradar/pipeline.py:2210` creates a per-run fetcher and
`dealradar/pipeline.py:2818` closes it. **ADOPT, G, partly present:** keep that
lifetime, give 225 only render-local facts and 223 one page-local request owner.
Do not turn all Python modules into classes or share mutable caches globally.
Payoff is fewer stale references and clearer cleanup; test repeated setup and
overlapping request lifetimes rather than claiming a smaller file is faster.

**J3. This regex VM backtracks; interruption is an explicit facility.**
`bellard/quickjs/libregexp.c:2774`: `lre_exec_backtrack`; `:2958` saves split
state; `:2744` calls `lre_check_timeout`. Ours:
`dealradar/parse/extractor.py:282`, `:291`, `:327` contain CPU-risk candidates.
**ADAPT, G:** use bounded scanning/analysis in 224, not QuickJS or a claim of
linear-time regex. Python stdlib re does not gain a per-match interrupt because
we check a deadline before calling it. Measure growth on adversarial families
under an outer subprocess timeout; do not run dangerous probes inline.

**J4. Error state is explicit and belongs to the result.**
`bellard/quickjs/cutils.h:260`: `BOOL error`; `:322` reads it through dbuf_error.
Ours: `dealradar/photo_recovery.py:55` distinguishes deferred work from an empty
result; `dealradar/http_client.py:793` propagates DeadlineExceeded.
**ADOPT, G, already a useful pattern:** 224 uses unknown/exhausted rather than
false; 222 persists progress only for completed chunks. Avoid sentinel meanings
that silently mark deferred work complete. Payoff: no permanently skipped
work after an interruption; measure resumed completion and no-op replay.

### groupcache — two techniques

**F1. Equivalent in-flight work has one producer.**
`golang/groupcache/singleflight/singleflight.go:48`: `c.wg.Wait()`; `:49` returns
the producer's value and error. Ours: `dealradar/verify.py:805` appends each
deal's ASIN, and `:829` sends the resulting list; `:528` does not deduplicate.
**ADAPT, G:** 221 groups unique ASINs within identical request policy before
batch sizing, then fans the result to distinct deals. This is a sequential
batch adaptation, not an origin stampede or a reason for new threads. Measure
unique requested ASINs/receipts; Keepa's treatment of duplicate input is not
established here, so token savings are not guaranteed.

**F2. Singleflight is not a lasting cache.**
`golang/groupcache/singleflight/singleflight.go:60`: `delete(g.m, key)` after
completion. Ours: `dealradar/photo_recovery.py:69` already shares a completed
short-link cache; `dealradar/pipeline.py:2167` still resolves per extracted deal.
**ADAPT, G:** distinguish overlapping-call suppression from deliberate run-local
memoization. 221 handles one recheck invocation only; a later redirect-sharing
brief must key HEAD-only/GET fallback, permission and hop policy separately.
Measure repeat keys across phases first. No cross-run cache of Amazon prices,
and no sharing an error in a way that defeats the first-429 stop.

### Varnish — two techniques

**V1. Busy objects coalesce requests with compatible identity.**
`varnishcache/varnish-cache/bin/varnishd/cache/cache_hash.c:527` checks Vary;
`:669`: `VTAILQ_INSERT_TAIL(&oh->waitinglist, req, w_list)` parks waiters.
Ours: `dealradar/publish/seo.py:90` emits JSON no-store and
`dealradar/publish/templates/index.html:3217` creates timestamped URLs.
**ADAPT, G:** 223 uses one stable price-free manifest URL and one in-flight
browser promise; leave price JSON no-store. This does not configure Varnish or
prove Cloudflare coalesces anything. Measure bytes/requests and later inspect
actual Pages validators/cache behavior before proposing longer edge freshness.

**V2. Grace and wake-up policy are explicit tradeoffs.**
`varnishcache/varnish-cache/bin/varnishd/cache/cache_hash.c:659`: “we do not wait
on the busy object if in grace”; `:717` bounds waiter release by rush_exponent.
Ours: `dealradar/publish/templates/index.html:3330` polls for a real publish;
`dealradar/publish/seo.py:93` revalidates HTML.
**SKIP stale-price grace:** no stale-while-revalidate policy or long immutable
price URL. Adopt only bounded fan-out/fallback in 223. A stale manifest must
not indefinitely suppress price updates. Measure freshness under deploy races,
failed validators and outages; do not equate cache-hit ratio with correctness.

### Lucene Monitor — two techniques

**M1. Reverse the search: index queries, select with document terms.**
`apache/lucene/lucene/monitor/src/java/org/apache/lucene/monitor/TermFilteredPresearcher.java:51`:
“terms extracted from queries”; `:136` adds ANYTOKEN fallback for queries that
cannot be narrowed safely. Ours: `dealradar/publish/templates/index.html:1310`
matches a query against deals; `dealradar/publish/templates/engagement.js:287`
gates existing price-watch behavior. **ADAPT, G, future non-Amazon only:** use a
Python term→alert-ID map, an always-check set, then exact evaluation. At zero
alerts do nothing. No JVM, alert sending or new subscription collection in this
study/briefs. Measure candidate reduction and zero false negatives versus brute
force at 100/1,000/10,000 synthetic alerts; worst case remains all queries.

**M2. Presearcher candidates must still pass the full matcher.**
`apache/lucene/lucene/monitor/src/java/org/apache/lucene/monitor/Monitor.java:299`:
`matcher.matchQuery(id, query.matchQuery, query.metadata)`; `:301` records errors
per candidate. Ours: `dealradar/rank.py:406` owns publication eligibility and
`dealradar/publish/members.py:136` owns existing delivery paths.
**ADOPT, G, future:** exact category/store/condition/keyword evaluation follows
candidate retrieval, then current public eligibility and Amazon exclusion again
at delivery. A candidate is never an alert. Payoff: safety despite lossy
preselection; measure exact result equality and per-query error isolation.

### Luwak — two techniques

**W1. Separate query analysis from indexing and matching.**
`flaxsearch/luwak/luwak/src/main/java/uk/co/flax/luwak/presearcher/TermFilteredPresearcher.java:176`:
`extractor.buildTree(query, weightor)`; `:197` collects terms into an indexed
query document. Ours: `dealradar/publish/templates/index.html:1268` normalizes
search words; `dealradar/publish/templates/engagement.js:329` restricts watches.
**ADAPT, G, future:** a small validated literal keyword grammar with versioned
normalization, not user regex or Lucene syntax. Rebuild postings on query edits;
unextractable/negative-only queries enter always-check. Payoff: inspectable
matching without a service; measure all supported grammar cases against a
reference evaluator. This and Lucene Monitor are related designs, not independent
evidence of two different breakthroughs.

**W2. Unbounded query complexity is a bad fit for unattended jobs.**
`flaxsearch/luwak/luwak/src/main/java/uk/co/flax/luwak/presearcher/TermFilteredPresearcher.java:72`:
`BooleanQuery.setMaxClauseCount(Integer.MAX_VALUE)`.
Ours: `dealradar/pipeline.py:2201` sets a finite run budget;
`dealradar/parse/extractor.py:327` already illustrates combinatorial work.
**SKIP that limit policy:** future queries need bounded terms/length and finite
candidate slices; 224 applies the same lesson to today's parser heuristics.
The delivery checkpoint needs a cursor if matching is deferred. Payoff: bounded
work under broad queries; measure worst-case candidates and backlog, never
claim sublinear matching for an all-query/very-common-term workload.

### Tantivy — three techniques

**T1. Inverted postings retrieve candidates by term.**
`quickwit-oss/tantivy/src/index/inverted_index_reader.rs:293`: `pub fn read_postings`;
`:298` resolves term information before reading postings.
Ours: `dealradar/publish/templates/index.html:1269` already caches token sets per
object, but `:1359` scans rows for each filter/recovery pass.
**ADAPT, G, defer pending profile:** a browser Map token→deal keys could narrow
candidates, built from the current public snapshot only. Reuse today's exact
model tokens, ordinary prefixes and one-edit semantics; retain a linear oracle.
No Tantivy/WASM/index download. Measure build time, per-keystroke CPU, heap and
query-result equality at ~2,300 and 5,000 fixture deals before choosing it.

**T2. Do not compute scoring machinery when callers only need a set.**
`quickwit-oss/tantivy/src/query/term_query/term_weight.rs:193` tests
`!self.scoring_enabled`; `:194` can return AllMatch rather than a term scorer.
Ours: `dealradar/publish/templates/index.html:1310` needs a boolean match and
`:1391` reuses candidates for recovery counts. **ADOPT, G, partly present:**
keep boolean retrieval separate from established deal order. If postings are
justified, cache the candidate set for one normalized query/publish, not a new
BM25 relevance score. Measure repeated scan counts and p95 input-to-render;
do not override checkout spacing, photo/confirmation grouping or category choice.

**T3. Bound token processing on unconstrained input.**
`quickwit-oss/tantivy/src/tokenizer/remove_long.rs:36` uses
`token.text.len() < self.token_length_limit` (strictly less, in UTF-8 bytes).
Ours: `dealradar/publish/templates/index.html:1263` tokenizes strings and
`dealradar/parse/extractor.py:375` caps cleaned post characters.
**ADAPT, G:** 224 has finite numeric-token/pair analysis. A future browser index
must bound work without dropping legitimate long model identifiers; fall back
to today's matcher for unsupported tokens. Measure adversarial long input and
real identifier fixtures; bytes and Unicode characters are different limits.

### Monolith — three techniques

**B1. Exact-key feature ownership avoids shared hash-bucket identities.**
`bytedance/monolith/monolith/native_training/runtime/hash_table/cuckoohash/cuckoo_embedding_hash_table.cc:165`:
`m_.find_fn(id, find_fn)`; `:169` zero-fills a missing embedding.
Ours: `dealradar/clicks.py:202` uses public deal keys;
`dealradar/publish/templates/index.html:1270` caches by deal object.
**ADAPT, G:** 226 keeps exact deal-key maps and explicit missing values.
“Collisionless” does not mean a hash function never collides: full keys retain
identity. No embedding table/training infrastructure; never map a missing
observation to measured zero. Measure key integrity and missing-coverage cases.

**B2. Features expire using their own update timestamps.**
`bytedance/monolith/monolith/native_training/runtime/hash_table/cuckoohash/cuckoo_embedding_hash_table.cc:243`:
`entry.SetTimestamp(update_time)`; `:261` checks slot-specific expiry.
Ours: `dealradar/clicks.py:167` retains bounded hourly tables, but `:188` sums a
flat lookback window. **ADAPT, G:** 226 decays completed click buckets by event
hour and excludes expired/unknown/capped coverage. A later explicit local
preference record can have a TTL/reset; implicit learning needs a separate
decision. Feature expiry never kills a deal or refreshes a price. Measure old
spike fade-out and bounded storage, not a hypothetical recommender lift.

**B3. Admit features only when enough feedback exists.**
`bytedance/monolith/monolith/native_training/runtime/hash_filter/hash_filter.h:164`:
`return add(fid, count) < slot_occurrence_threshold`.
Ours: `dealradar/clicks.py:224` smooths with prior hours but has no explicit
minimum-support rule for the proposed decayed view. **ADAPT, G:** 226 reports
insufficient sample separately and caps shadow movement; preserve the original
content/quality prior for new deals. These are click counts, not independent
users, and can still be gamed. Measure coverage/support distributions and a
single-key burst fixture. No probabilistic filter or feedback-based suppression
of niche deals with zero clicks.

### SimClusters — three techniques

**S1. Learned communities need a real graph.**
`twitter/the-algorithm/src/scala/com/twitter/simclusters_v2/scalding/update_known_for/UpdateKnownForSBFRunner.scala:546`:
`val optimizedZ = algo.optimize` uses the MHAlgorithm created at `:545`; the
algorithm is not implemented inside this wrapper. The repo README explains the
producer-similarity graph.
Ours: `cloudflare/subscribe-worker/clicks.js:91` discards surface/position and
stores no joint-session signal. **SKIP learned graph now:** no follow graph,
accounts, or co-click sessions exist to train it. Counts by hour cannot recover
which deals one session clicked. Content facets can solve today's cold start
without this infrastructure; measure category/brand metadata coverage first.

**S2. Sparse, timestamped interest dimensions limit feedback work.**
`twitter/the-algorithm/src/scala/com/twitter/simclusters_v2/summingbird/common/SimClustersInterestedInUtil.scala:58`
filters by a support threshold; `:61`: `thriftDecayedValueMonoid.build` attaches
event time. Ours: `dealradar/publish/templates/index.html:1276` already combines
item/brand/store/category/conditions; proposed 201 follows categories locally.
**ADAPT, G:** initialize content “communities” from those explicit facets as
soon as a public deal appears, including a zero-click niche deal at 2 AM. This
is our content-based translation, not Twitter's community detector. No upload
of follows. Measure relevant-deal coverage in Followed/hubs and local query cost.

**S3. Align decay time and bound the retained dimensions.**
`twitter/the-algorithm/src/scala/com/twitter/simclusters_v2/summingbird/common/Monoids.scala:330`
chooses the latest timestamp; `:364`: `.sortBy(-_._2)`; `:365` takes topK.
The merge keeps the larger decayed value for duplicate dimensions, not a sum.
Ours: `dealradar/clicks.py:110` imports absolute buckets and `:228` sorts once.
**ADAPT, G:** 226 recomputes sums from absolute completed hourly counts at one
evaluation time, never adds a downloaded bucket twice. Separately,
`twitter/the-algorithm/src/scala/com/twitter/simclusters_v2/summingbird/storm/TweetJob.scala:60`
joins favorites to existing interests: that path needs engagement, so it does
not itself solve a zero-engagement item. Measure replay equality/decay; no
Heron, embeddings, immediate public hot claims or automatic alerts.

## Engineering decisions the patterns actually support

### Deduplication inventory: retain what already works

| Existing boundary | What it does | Remaining limitation |
|---|---|---|
| `dealradar/pipeline.py:2307` | Per-batch content hashes and known-post checks. | A content hash does not dedupe different deals' equivalent external requests. |
| `dealradar/fetch/keepa.py:607` and `:1095` | Per-lane seen ASIN sets and unique sweep ASINs. | Sweep product transport is separate from verify.keepa_products. |
| `dealradar/pipeline.py:2508` | Reuses the early recheck summary rather than blindly repeating that phase. | Not a cache of equivalent products for every later phase. |
| `dealradar/pipeline.py:1646` | attempted deal-key guard across recheck lanes. | Distinct promo/condition/URL keys can share an ASIN. |
| `dealradar/photo_recovery.py:69` and `:92` | One run-local short-link cache across new posts/existing deals. | General `_resolve_product_urls` (`dealradar/pipeline.py:2167`), `_resolve_short_links` (`:842`) and `_links_from_posts` (`:952`) have separate calls. |
| `dealradar/http_client.py:351` | Conditional API validators and in-memory bodies; bodyless 304 retries at `:363`. | Still issues a request; not semantic ASIN dedupe or persistent product reuse. |
| `dealradar/verify.py:528` and `:805` | Limits product batch size and groups new/used policy. | Duplicate ASINs can enter a list and consume batch slots. 221 handles this narrow gap. |

Do not dedupe offers by ASIN alone: used-offer scans, stats, Buy Box and
update=0/update=1 have different meaning/cost. A future shared response key
would include domain, ASIN, stats window, offers, live/update, Buy Box and policy
version; credentials remain private. Results retain request/observation identity.
One transport reservation/receipt is charged once, then apportioned to all deal
consumers without double-counting useful readings. Missing/429/error is not a
dead deal. Cross-phase caching and redirect-policy consolidation need separate
briefs after a counts-only repeat-key profile; 221 does not pretend to solve them.

### Delta snapshots versus a safe first transport improvement

A hypothetical static delta needs `schema`, exact `from`/`to` content IDs,
upserts, removals, complete order and all relevant metadata/spotlight changes.
Client rules: require the exact baseline/schema; build a temporary key map;
validate the reconstructed envelope; remove absent/locked/ended access promptly;
adopt atomically. Missing patch, missed deploy, wrong hash, malformed data,
login/access change or unsupported client falls back to full public JSON. Never
mix member data into a public patch. One retained edge patch still misses tabs
several deploys behind; unbounded chains/archives are unacceptable.

This would require retaining a previous public baseline across stateless jobs,
and old patches can preserve old Amazon price facts. **SKIP a price-bearing
Amazon delta archive under this task's rule.** Even non-Amazon patches need
measured payoff: compare compressed full size against patch + manifest + order
bytes and extra latency for quiet/heavy/reprice/removal/access-change publishes.
If most fields or metadata change, a patch can lose. Do not infer bandwidth
reduction from Quake's bit packing or cache byte size instead of transfer size.

223 therefore proposes only a small, current, price-free manifest describing the
current JSON's hash/size/publish/version. Keep the ordinary full-file fallback
and no-store price path. An unchanged manifest avoids the large GET for an
already loaded page; each changed publish still downloads the whole JSON.
This cannot solve every phone transfer, but needs no old price archive, new
origin, KV, Worker, API, durable browser cache or service worker. Preserve
existing HTML no-cache headers. A later deployment observation must establish
Pages validator behavior; local source cannot establish live cache hits.

### Incremental maintenance and interruption limits

Start with HTTP-validator retention and fast-run compaction policy (222), not
all pruning and not paid scheduling. Bound rows examined even if none expire;
advance a stable key cursor over examined rows in the same transaction as
deletion and a per-run allowance. Stop before the tail reserve, then resume on
the next eligible run. Never delete current spend reservations, sent outgoing
claims, live offers, pending IndexNow receipts or product-history owners.
Unbounded deletes in other tables remain explicit follow-up work.

The remote recovery boundary matters: `dealradar/db.py:644` checkpoints locally;
`dealradar/persistence.py:49` additionally saves encrypted state before sends.
`dealradar/db.py:1081` settles calls and `:1095` releases reservations locally. The workflow saves
failed pipeline state, but a cancellation/hard kill before that save can lose
recent local paid-call accounting. **No 99.9% or exactly-once claim follows.**
A future bounded checkpoint cadence for paid work needs a separate cost/latency
design; do not restore the rejected per-LLM-call Git push recorded in HANDOFF.
Replay of deleted cache rows is harmless; replaying external sends is not.

### Untrusted-text work: concrete risks, not an invented exploit

| Candidate | Static concern | Scope and safe translation |
|---|---|---|
| `dealradar/parse/extractor.py:282` (`\d+` before percent) and `:291` | Long digit strings without a suffix permit repeated suffix scans from successive starts; polynomial worst-case work is plausible. | 224 scans digit runs once and distinguishes complete tokens; no exponential claim or live hang established. |
| `dealradar/parse/extractor.py:327` | Pair enumeration is m(m−1)/2 for m prices; not a regex problem at all. | Bound candidate/pair work; exhausted analysis returns unknown, never permission to skip publication evidence/critic. |
| `dealradar/parse/claude_client.py:69` | Multiple whitespace repetitions around a lazy DOTALL capture can revisit whitespace/fence positions. | Lower priority: model output is token-bounded; later replace fence stripping with delimiter/string logic after fixtures. Outside 224. |
| `dealradar/util.py:144` | Unanchored `<[^>]+>` on many `<` without `>` can repeatedly scan suffixes. | Only the HTMLParser-exception fallback; reachability is conditional, not a normal-path reproduced hang. Later bounded scanner; outside 224. |

Escaped literal evidence searches at `dealradar/parse/extractor.py:89` and
`:154` are not arbitrary user regex. No broad RE2/QuickJS migration is justified.
Existing 6,000-character cleaning lowers exposure but is not a time-complexity
proof; larger configured limits and many posts still matter. Future adversarial
checks run child processes with outer timeouts, compare size growth, and use
deterministic work counters for gating rather than flaky wall-clock assertions.

### Future non-Amazon keyword alerts: a Python design at 0–10k

No alert delivery is authorized by these briefs. If later requested, begin with
literal all/any/excluded terms plus store/category/condition constraints, finite
length/term counts and no user regex. Maintain query-ID→validated query, positive
term→query-ID postings and an always-check set. For OR include every branch;
negative-only/unknown extraction enters always-check. Candidate union must be a
superset of true matches; exact evaluator checks every condition afterward.

Zero subscriptions means zero index/matching work. At small counts a brute-force
loop may be cheapest. At 10k measure candidate fraction, CPU/memory, broad-query
worst cases and p95 per-new-public-deal latency against that loop. Candidate
filtering is not permission to lose matches. Query edits replace postings by
version; deletes remove them. Matching may use bounded run slices/cursors.
Delivery needs a separately designed consent/unsubscribe/outbox lifecycle and
idempotency key `(query ID, query version, deal identity, admission version)`;
revalidate current public status and non-Amazon identity immediately before any
send. Neither an email list nor a price-watch browser record supplies consent
for this new service. No Amazon offers, price thresholds or hidden offers.

### Search, hot scores and cold start without a recommender platform

Search already has cached exact tokens, prefixes, conservative typo matching,
model/number boundaries and recovery choices. Tantivy suggests candidate
postings, not replacing these semantics. Prefix/fuzzy query terms need a safe
superset of matching vocabulary; exact-only postings would miss today's matches.
Build or replace the index when the current public snapshot changes; remove
deleted/locked items immediately and keep it in memory. Profile first; the
previous study's phone budgets and stable-current-facts work remain higher
priority than a Rust/WASM engine or a second downloadable search dataset.

For 226 evaluate hourly click decay privately. For completed, observed,
uncapped comparable hours h, choose common evaluation time t and configured
half-life H; `w(h) = 2 ** (-(t - end_of_hour(h)) / H)`. Derive weighted clicks
`E = sum(w*C)` and generated-publication hours `T = sum(w*shown_seconds/3600)`
over the same hours; when T>0 use `log1p(E/(T + prior_hours))`. Missing coverage,
capped hours and no comparable exposure are unknown, not zero. Exposure here
means generated publication time, not impressions. Recompute from absolute
buckets each time; use support thresholds, a bounded movement and original
order as the prior/tie-breaker. No live rank, social or Keepa priority changes.
Compare fade-out, burst sensitivity, cold-start preservation, coverage and CPU;
click bias/abuse remain. Current flat-window shadow stays available unchanged.

For a brand-new zero-click deal, content facets work immediately: current
category membership first, then verified brand/store/title terms. Route to its
existing category hub and, when 201 is implemented, the locally Followed view
on the next real publish. Do not delay niche inventory for minimum clicks or
override broad-appeal top-pick rules. Explicit local category follows need no
learned interest vector; later opt-in local-only brand/term preferences can add
bounded dimensions with TTL/reset, no prices or uploaded identifiers.

Co-click communities would require joint-session observations that we neither
collect nor can infer from marginal counts. If later justified, a separate
privacy design could aggregate bounded category-pair counts on device before
any collection, suppress rare combinations and discard session identity; it
would still be a new signal needing approval and enough diverse observations
for stable held-out results. No specific traffic threshold or retention gain
can be asserted now. Skip Metropolis-Hastings, distributed graphs, online
training and push infrastructure at today's unmeasured scale.

## Ranked build briefs and release checks

Rank reflects bounded scope, direct operational/phone benefit and failure risk,
not invented ROI or measured speedups. Each brief stands alone and ships off.

| Order | Brief | Expected payoff and measurement | Main limit |
|---|---|---|---|
| 1 | [221 — Unique-ASIN recheck batches](../codex-tasks/221-unique-asin-rechecks.md) | Fewer duplicate batch slots/requests; unique ASINs, receipts, fan-out verdict equality. | Within one request-policy group only; actual token saving unknown. |
| 2 | [222 — Incremental cache maintenance](../codex-tasks/222-incremental-cache-maintenance.md) | Smaller optional tail slices; examined/deleted rows, slice CPU, backlog age, job tail. | Other bulk pruning remains; no end-to-end deadline guarantee. |
| 3 | [223 — Price-free publish manifest](../codex-tasks/223-price-free-publish-manifest.md) | Avoid unchanged full JSON transfers; response bytes, latency and request count. | Changed publishes still need full JSON; live Pages behavior unmeasured. |
| 4 | [224 — Bounded prefilter analysis](../codex-tasks/224-bounded-prefilter-analysis.md) | Finite numeric/pair work; adversarial operation counts and valid-input parity. | Does not replace every regex/HTML parser. |
| 5 | [225 — Reuse publish link facts](../codex-tasks/225-publish-link-facts.md) | Avoid full serialization for an earns check; call counts, render CPU/memory. | Render-local only; exact output and routing required. |
| 6 | [226 — Decayed click shadow](../codex-tasks/226-decayed-click-shadow.md) | Evaluate fresher feedback without public changes; support/coverage/moves/decay. | Parent signal is off; no live sample, uniqueness or causal lift. |

Implement one reviewed change at a time. 223 overlaps the request/adoption area
of proposed 202; coordinate without taking over visible ordering. 225 must
preserve the serializer's task-121 Amazon decisions and task-119 photos. 222
does not change task-109 parsing reserves or paid recheck scheduling. 226 does
not activate first-party collection or the separate volatility click boost.
All six need a same-checkout disabled baseline and enabled offline fixtures;
preserve four access modes and existing enabled features. No snapshot repin to
hide a failure. Switch-off takes effect on the next successful deploy and cannot
recall code already downloaded by a browser.

The unattended-operation scorecard should name the scheduled-run denominator,
on-time publish/deploy success, stale-publish duration, deferred backlog age,
hard kills, checkpoint failures, uncertain charges/sends and operator actions.
Use existing private run/receipt data where available; missing fields stay
unknown. Synthetic replay can prove specified failure behavior, not a week's
availability. Phone runtime/traffic/accuracy/retention remain unmeasured here.

## Daily study list: ten next reading leads

These are additional codebases, not studied findings or dependencies. They are
not cloned here; paths are suggested upstream reading targets to verify during
a later authorized study. No network was used to validate their current HEADs.

| Codebase / repository | File to read | One technique / wowpriced problem |
|---|---|---|
| SQLite — sqlite/sqlite | `src/pager.c` | Transaction/journal recovery boundaries; distinguish durable local state from remote snapshot survival. |
| PostgreSQL — postgres/postgres | `src/backend/access/transam/xlog.c` | WAL/checkpoint ordering; design a measured checkpoint cadence without per-call Git pushes. |
| curl — curl/curl | `lib/multi.c` | Explicit transfer states and cancellation; bounded polite fetch lifetimes. |
| RE2 — google/re2 | `re2/nfa.cc` | Bounded-state matching; assess linear scanners versus a new regex dependency. |
| ripgrep — BurntSushi/ripgrep | `crates/searcher/src/searcher/mod.rs` | Separate candidate scanning from match reporting; browser search and parser CPU. |
| Zstandard — facebook/zstd | `lib/compress/zstd_compress.c` | Bounded streaming compression workspaces; encrypted-state transfer/peak memory. |
| RocksDB — facebook/rocksdb | `db/db_impl/db_impl_write.cc` | Group commit and write ownership; avoid duplicated durable bookkeeping. |
| Git — git/git | `lockfile.c` | Lockfile/atomic replacement lifecycle; safe local publish/checkpoint artifacts. |
| systemd — systemd/systemd | `src/basic/ratelimit.c` | Small rate-limit state with time windows; polite retries and bounded admin notices. |
| Prometheus — prometheus/prometheus | `tsdb/head.go` | Bounded time-series head/retention; private operational samples without raw-event growth. |

## Documentary validation and remaining work

Document checks cover local citation paths/line bounds, source excerpts and
revisions, all relative links, 32 techniques (2–4 in each of 12 repositories),
the six-brief mapping and the under-900-line limit. The tracked-file diff is
empty; only these seven deliverables were written, preserving the existing task
and background documents. No product test results or benchmark values are
claimed. Remaining work: review/select briefs, later implement and run their
offline/full checks, then observe actual run completion, token costs, phone
transfers and freshness before activation or an unattended-availability claim.
