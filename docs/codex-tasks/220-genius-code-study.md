# 220 — Learn from the best engineers' code; turn the lessons into wowpriced improvements

## Why (operator 2026-10-16)
"Study genius-level code and see how to implement it on our site ... see how the brightest minds in the
world have coded to bypass or circumvent issues that we are facing ... further build your code."
In about a week the site must run ~99.9% unattended (Opus + ChatGPT $100 plan + Keepa 60 tokens/min).

## Read (cloned, read-only) and the operator's pointers
- /home/user/redis/redis — `src/dict.c` (incremental rehashing: never a latency spike), `src/sds.c|h`
  (metadata header before the data: O(1) facts), `src/ae.c` (a tiny event loop).
- /home/user/id-software/quake-iii-arena — `code/qcommon/msg.c` (delta-compressed snapshots: send only
  what changed), `code/qcommon/common.c` (`Z_Malloc`, `Hunk_Alloc`: pre-sized zones wiped in O(1)),
  `code/game/q_math.c` (`Q_rsqrt`, and the "measure, then cheat precisely" mindset).
- /home/user/karpathy/nanogpt — `model.py` (`CausalSelfAttention`: a whole system in one readable file);
  /home/user/karpathy/llm.c — `train_gpt2.c` (`gpt2_forward`/`gpt2_backward`: no hidden magic).
- /home/user/bellard/quickjs — `quickjs.c` (JSValue NaN-boxing, organization of a monolith),
  `libregexp.c` (a small regex bytecode VM), `cutils.h` (tiny reusable utilities).

## Added by the operator (2026-10-16, deal-site engineering) — also cloned read-only
- /home/user/golang/groupcache — `singleflight/singleflight.go` (Brad Fitzpatrick: duplicate-call
  suppression). Our site is static on Cloudflare (no origin to stampede); map it to the PIPELINE: the same
  ASIN/URL/short link requested by several deals in one run (Keepa reads cost tokens = money; redirect
  follows are rate-limited per host). Find where we already dedupe and where we don't.
- /home/user/varnishcache/varnish-cache — request coalescing at the edge (`bin/varnishd/cache/cache_hash.c`
  waiting list / busy objects). Map to Cloudflare caching headers of deals.json and pages (`_headers`).
- /home/user/redis/redis — `src/t_zset.c` (hash + skiplist sorted set: O(1) by id, ranged by score) and
  `src/hyperloglog.c` (unique counts in ~12 KB). Map to: a click-driven "hot right now" score with time
  decay from our anonymous click signal (`dealradar/clicks.py`), and privacy-safe unique counts.
- /home/user/apache/lucene/lucene/monitor and /home/user/flaxsearch/luwak — reverse search: index saved
  queries, run each new document against them (presearcher term extraction). Map to future keyword deal
  alerts for NON-Amazon stores (Amazon forbids price alerts), at our scale (0 to 10k alerts) in Python.
- /home/user/quickwit-oss/tantivy — compact inverted index / fast search; map to our client-side search over
  ~2,300 deals (`dealradar/publish/templates/index.html` search) and the search freshness work.
- /home/user/bytedance/monolith — TikTok's real-time recommender (collisionless embedding tables, expirable
  features, online training). We have no accounts and must stay privacy-light: take the IDEAS (fresh
  feedback loops, feature expiry by time/frequency) for local-only personalization and the hot score, never
  the infrastructure.

- /home/user/twitter/the-algorithm/src/scala/com/twitter/simclusters_v2 — SimClusters (community
  detection over the user–producer bipartite graph via Metropolis-Hastings; sparse interest vectors;
  new items scored into communities instantly = cold start). We have no accounts, no follow graph and no
  push notifications (no service worker; Amazon forbids price alerts). Map the IDEA to our cold start
  (a brand-new deal with zero clicks): content-based "communities" from categories/brands/stores/terms
  and co-click sessions (anonymous, aggregate), so a 2 AM niche deal reaches the right local follows
  (brief 201) and category hubs immediately. Say plainly what needs scale we do not have yet.

## Our real problems to map them to (verify each in our code; cite file:line)
1. The fast lane must finish in ~12 min (`.github/workflows/fast.yml`); big work (backfills, retention,
   rechecks, photo recovery) can spike a run. (Redis incremental rehash, Quake hunk zones.)
2. Every visitor re-downloads the whole `deals.json` (~2,300 deals) every refresh/drop on phones.
   (Quake delta snapshots: e.g. a small per-publish delta file keyed by the previous publish id, with a
   full-file fallback; Cloudflare Pages static hosting only, no server.)
3. Huge files that hide bugs: `dealradar/publish/templates/index.html` (~3,450 lines),
   `dealradar/pipeline.py` (~2,850 lines). (nanoGPT/llm.c clarity: one readable unit per idea.)
4. Parsing untrusted text from dozens of sources with Python regexes: catastrophic backtracking risk,
   a run hang. (libregexp: bounded, non-backtracking matching; find our risky patterns.)
5. Repeated recomputation of the same per-deal facts across publish steps. (sds: carry computed facts
   once; but money math stays in savings.py.)
6. Container/job interruptions: work must be resumable and idempotent. (Redis/Quake state discipline.)

## Deliverables (documents only — no product code)
1. `docs/research/genius-code-study.md`: for each repo 2-4 techniques with exact file:line in THEIR code
   and a short quote; for each, the matching wowpriced problem with our file:line, a verdict ADOPT /
   ADAPT / SKIP, and the honest expected payoff (no invented numbers; say how to measure). Keep < 900 lines.
2. Up to 6 build briefs `docs/codex-tasks/221..226-<slug>.md` for the best ADOPT/ADAPT items, each small
   enough for one Codex session, behind a config.yaml switch (default off) where behavior changes, with
   offline tests, idempotency, CLAUDE.md non-negotiables, Amazon rules (no Amazon price history/alerts),
   no new third-party scripts, phone performance first.
3. A short "daily study list": 10 more world-class codebases worth one technique each (name, repo, the
   file to read, the wowpriced problem it addresses), for the daily quality loop.

No network, no commits, write only those files. Finish with a short reply: top findings + brief names.
