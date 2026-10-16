# 236 — Fetch different hosts side by side (per-host slots, Scrapy-style)

## Why

Live 2026-10-16 hourly #180: fetch took 310 s of the 600 s pipeline budget,
leaving parse 159 s (it stopped with 328 posts left; money was not the limit).
`pipeline._fetch_all` (~line 199) runs every source one after another, and
`PoliteFetcher._rate_wait` (`http_client.py` ~line 474) SLEEPS whenever the next
request goes to a host it just hit (enrichment reads several article pages from
one blog at 1 request/second). While it sleeps for one host, every other host
waits too.

Study (2026-10-16, Scrapy `scrapy/core/downloader/__init__.py`, commit 53d3bc7):
`Slot` (line 46) holds one queue, `concurrency`, `delay` and `lastseen` per
domain; `_process_queue` (line 301) waits out a slot's own delay with a timer
instead of blocking, while other slots keep downloading; a global total cap
bounds everything. Same politeness per host, far less idle time overall.

## Read first

CLAUDE.md (politeness is law: robots, ≤1 request/second/host, backoff,
conditional GETs, crawl_permission scope), AGENTS.md, docs/HANDOFF.md.
`pipeline._fetch_all`, `fetch/base.run_source` (breakers, per-source budget,
deadlines, DB writes), `http_client.PoliteFetcher` (`_rate_wait`, robots cache,
validators in `db`, body cache, backoff, `_check_deadline`), `fetch/registry`,
adapters that keep state on the shared fetcher (keepa `recheck`, woot
`recheck_stats`, newsletter inbox), `store_prices.recheck_store_prices`,
tests for politeness/robots/rate limits/deadlines/idempotency.

## Task

1. New config `fetch.parallel_sources` (int, validated 1..6, default and
   shipped value `1` = exactly today's sequential behaviour and bytes).
   When > 1, `_fetch_all` runs up to that many sources at once, but never two
   sources whose hosts overlap at the same time (group sources by their
   registrable host set; a source that touches several hosts holds all of them).
   Sources with shared adapter state or paid/quota APIs stay sequential on the
   main thread: keepa (and its recheck hook), woot, the newsletter inbox, store
   APIs (bestbuy/ebay), and any adapter you cannot prove independent.
2. Politeness stays global and exact: `_rate_wait` becomes thread-safe (one
   lock per host; the wait happens outside any global lock), so at most one
   request per `min_host_interval_s` per host across ALL threads, robots checks
   and backoff unchanged. Add a test that hammers one host from several
   threads with a fake clock and asserts the spacing.
3. SQLite stays safe: every `db` call made while fetching (validators, breakers,
   source results, meta, audit) is serialized (a single lock around the DB
   object, or results handed back to the main thread). No new connection, no
   change to schema or stored values.
4. Determinism: the returned `posts` list and `errors` dict are in the same
   order as the sequential run (sort results back to source order), so
   content-hash recording, parse order and published bytes match for the same
   inputs. Deadlines: the fetch window and per-source budgets are enforced per
   source exactly as today; a source not started before the fetch deadline is
   skipped with the same log line.
5. Run summary: private `fetch_seconds` and `fetch_parallel` (value used).

## Tests (offline)

- `parallel_sources: 1` → identical posts, errors, DB rows and site bytes to the
  current code (fixture recorded from the old path).
- `parallel_sources: 4` with fake adapters on distinct hosts and per-request
  fake delays: wall time on the fake clock drops; same posts/errors in source
  order; same DB rows.
- Two sources sharing a host never overlap; per-host spacing holds across
  threads; robots disallow still blocks; a breaker trip and a deadline mid-run
  behave as today; keepa/woot/inbox/store sources run on the main thread.
- Rerun idempotency stays green.

## Rules

Ship with `parallel_sources: 1` (off). Do not change tests/test_workflows.py.
Run tests with -n 1 and your own basetemp /tmp/task236-*; delete it after each
run. Do not run the whole suite. Do not commit. Update docs/HANDOFF.md
"In flight" with one short entry.
