"""Offline source concurrency, ownership, politeness and compatibility checks."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import threading
import time

import httpx
import pytest
import yaml

from dealradar import http_client, pipeline
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.errors import DeadlineExceeded
from dealradar.fetch.parallel import source_hosts
from dealradar.http_client import PoliteFetcher
from dealradar.models import RawPost, SourceConfig
from tests.fetch_parallel_fixture import db_rows, snapshot
from tests import test_e2e_offline as offline

NOW = offline.NOW
REAL_SLEEP = time.sleep


def source(name, **kwargs):
    return SourceConfig(name, "rss", f"https://{name}.example/feed", **kwargs)


def post(s):
    return RawPost(s.name, s.name, s.url, "Widget", "Widget now $10", NOW, NOW)


def fetch(sources, fetcher, db, cfg, window=1000, **kwargs):
    return pipeline._fetch_all(sources, fetcher, db, cfg, NOW,
                               time.monotonic() + pipeline._RESERVE_FOR_PARSE_S + window,
                               False, **kwargs)


def test_config_default_and_shipped_off(tmp_path):
    assert AppConfig().parallel_sources == load_config().parallel_sources == 1
    path = tmp_path / "config.yaml"
    path.write_text("{}")
    assert load_config(path).parallel_sources == 1
    for value in range(1, 7):
        path.write_text(yaml.safe_dump({"fetch": {"parallel_sources": value}}))
        assert load_config(path).parallel_sources == value


@pytest.mark.parametrize("value", [0, 7, -1, True, False, 1.5, 4.0, "4", None, [], {}])
def test_config_rejects_non_integer_or_out_of_range(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"fetch": {"parallel_sources": value}}))
    with pytest.raises(ValueError, match="fetch.parallel_sources"):
        load_config(path)


@pytest.mark.parametrize("parallel", [1, 4])
def test_matches_pre_change_posts_errors_database_and_public_bytes(tmp_path, monkeypatch, parallel):
    from dealradar import store_prices

    main = threading.get_ident()
    recheck = store_prices.recheck_store_prices
    checked = []

    def check(*args, **kwargs):
        assert threading.get_ident() == main
        assert not any(t.name.startswith("fetch_") for t in threading.enumerate())
        checked.append(True)
        return recheck(*args, **kwargs)

    monkeypatch.setattr(store_prices, "recheck_store_prices", check)
    expected = json.loads((Path(__file__).parent / "fixtures/fetch_parallel_baseline.json").read_text())
    assert expected.pop("revision") == "ca35c7c33ed2c66ba7deda6323c6a508bb9c4a00"
    actual, summary = snapshot(tmp_path, monkeypatch, parallel)
    assert actual == expected
    assert checked == [True]
    assert summary["fetch_parallel"] == parallel
    assert summary["fetch_seconds"] >= 0
    public = json.loads((tmp_path / "site/deals.json").read_text())
    assert "fetch_parallel" not in public["meta"]["last_run"]
    assert "fetch_seconds" not in public["meta"]["last_run"]


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.t

    def sleep(self, seconds):
        # Yield while sleeping to expose an unlocked check/update race.
        REAL_SLEEP(0.001)
        with self.lock:
            self.t += seconds


def test_threads_share_one_host_spacing_and_one_robots_read():
    clock, requests = Clock(), []
    cfg = AppConfig(min_host_interval_s=1.25)
    barrier = threading.Barrier(8)

    def handler(req):
        requests.append((req.url.path, clock()))
        if req.url.path == "/robots.txt":
            REAL_SLEEP(0.005)
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, text="ok")

    with closing(DB(":memory:")) as db, closing(PoliteFetcher(
            db, cfg, clock=clock, sleep_fn=clock.sleep,
            transport=httpx.MockTransport(handler))) as client:
        def hammer(i):
            barrier.wait(timeout=5)
            for n in range(5):
                # No DB calls in this direct fetcher concurrency check.
                assert client.get(f"https://one.example/{i}/{n}", conditional=False).status == 200
            assert client.get("https://one.example/private", conditional=False).blocked_by_robots

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(hammer, range(8)))
    assert len(requests) == 41
    assert sum(path == "/robots.txt" for path, _ in requests) == 1
    assert all(b[1] - a[1] >= 1.25 for a, b in zip(requests, requests[1:]))


def test_rate_wait_itself_is_thread_safe():
    clock, stamps = Clock(), []
    barrier = threading.Barrier(6)

    class Slots(dict):
        def __setitem__(self, host, value):
            stamps.append(value)
            super().__setitem__(host, value)

    with closing(DB(":memory:")) as db, closing(PoliteFetcher(
            db, AppConfig(), clock=clock, sleep_fn=clock.sleep,
            transport=httpx.MockTransport(lambda req: httpx.Response(404)))) as client:
        client._last_request_at = Slots()
        def hammer(_):
            barrier.wait(timeout=5)
            for _ in range(6):
                client._rate_wait("one.example")
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(hammer, range(6)))
        assert stamps == list(range(1000, 1036))


def test_waiting_for_one_host_does_not_block_another():
    waiting, release = threading.Event(), threading.Event()
    clock = Clock()

    def sleep(seconds):
        waiting.set()
        assert release.wait(5)
        clock.sleep(seconds)

    with closing(DB(":memory:")) as db, closing(PoliteFetcher(
            db, AppConfig(), clock=clock, sleep_fn=sleep,
            transport=httpx.MockTransport(lambda req: httpx.Response(200)))) as client:
        client._robots.update({"one.example": True, "two.example": True})
        client.get("https://one.example/first", conditional=False)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(client.get, "https://one.example/second", conditional=False)
            try:
                assert waiting.wait(5)
                b = pool.submit(client.get, "https://two.example/", conditional=False)
                assert b.result(timeout=5).status == 200
            finally:
                release.set()
            assert a.result(timeout=5).status == 200


def test_distinct_sources_overlap_and_preserve_results_and_db(monkeypatch):
    main = threading.get_ident()
    sources = [source(f"s{i}") for i in range(4)]
    captures = []
    for parallel in (1, 4):
        clock = Clock()
        # Each barrier represents concurrent 4-second requests completing.
        # Sequential mode has one participant and pays every request in full.
        tick = threading.Barrier(parallel, action=lambda: clock.sleep(4))
        finished = []
        release = [threading.Event() for _ in sources]
        calls, db_threads = [], []

        class Adapter:
            def fetch_posts(self, s, client, db, cfg, now, deadline):
                calls.append(threading.get_ident())
                for _ in range(3):
                    tick.wait(timeout=5)
                i = int(s.name[1:])
                if parallel > 1 and i < 3:
                    assert release[i + 1].wait(5)
                db.meta_set("adapter:" + s.name, "done")
                client.db.store_validators(s.url, s.name, "", now)
                db.audit_seen([post(s)], now)
                assert db.meta_get("adapter:" + s.name) == "done"
                finished.append(s.name)
                release[i].set()
                if i in (0, 2):
                    raise RuntimeError("fixture_failure")
                return [post(s)]

        with monkeypatch.context() as patch, closing(DB(":memory:")) as db:
            patch.setattr(pipeline.time, "monotonic", clock)
            patch.setattr("dealradar.fetch.registry.get_adapter", lambda *a, **k: Adapter())
            db.conn.set_trace_callback(lambda sql: db_threads.append(threading.get_ident()))
            cfg = AppConfig(parallel_sources=parallel)
            with closing(PoliteFetcher(db, cfg, clock=clock,
                                      transport=httpx.MockTransport(lambda r: httpx.Response(404)))) as client:
                posts, errors = fetch(sources, client, db, cfg)
                assert client.db is db
            assert [p.source_name for p in posts] == ["s1", "s3"]
            assert list(errors) == ["s0", "s2"]
            assert set(db_threads) == {main}
            assert all((t == main) == (parallel == 1) for t in calls)
            assert finished == ([s.name for s in sources] if parallel == 1 else [s.name for s in reversed(sources)])
            captures.append((posts, errors, db_rows(db), clock() - 1000))
    assert captures[0][:3] == captures[1][:3]
    assert (captures[0][3], captures[1][3]) == (48, 12)


def test_registrable_host_sets_and_unknown_adapters():
    assert source_hosts(replace(source("a"), url="https://feed.shop.co.uk/rss",
                                site="https://www.partner.com/")) == {"shop.co.uk", "partner.com"}
    assert source_hosts(replace(source("a"), url="https://x.blogspot.com/")) == {"x.blogspot.com"}
    for url in ("", "garbage", "https://[bad", "https://127.0.0.1/", "https://localhost/"):
        assert source_hosts(replace(source("a"), url=url)) is None
    for adapter in ("keepa", "woot", "newsletter", "bestbuy", "ebay", "reddit", "meh",
                    "telegram", "threads", "instagram", "future_adapter"):
        assert source_hosts(replace(source("a"), method="api", adapter=adapter)) is None


def test_shared_and_multiple_hosts_serialize_without_blocking_other_hosts(monkeypatch):
    sources = [
        replace(source("first"), url="https://feed.shop.co.uk/rss", site="https://www.partner.com/"),
        replace(source("second"), url="https://other.partner.com/feed", site="https://second.example/"),
        source("independent"),
        replace(source("third"), url="https://www.shop.co.uk/other", site="https://second.example/"),
    ]
    started = threading.Event()
    other_started = threading.Event()
    active, order = set(), []
    lock = threading.Lock()

    class Adapter:
        def fetch_posts(self, s, *args):
            with lock:
                assert not active.intersection(source_hosts(s))
                active.update(source_hosts(s))
                order.append(s.name)
            if s.name == "first":
                started.set()
                assert other_started.wait(5)
            elif s.name == "independent":
                assert started.wait(5)
                other_started.set()
            with lock:
                active.difference_update(source_hosts(s))
            return [post(s)]

    monkeypatch.setattr("dealradar.fetch.registry.get_adapter", lambda *a, **k: Adapter())
    with closing(DB(":memory:")) as db:
        posts, errors = fetch(sources, None, db, AppConfig(parallel_sources=4))
    assert not errors
    assert [p.source_name for p in posts] == [s.name for s in sources]
    assert order.index("independent") < order.index("second")
    assert order.index("first") < order.index("second") < order.index("third")


@pytest.mark.parametrize("parallel", [1, 4])
def test_deadline_breaker_and_filtering(monkeypatch, caplog, parallel):
    clock, calls = Clock(), []
    sources = [source("tripped"), source("first"), source("second"), source("disabled", enabled=False)]
    # Share a host so exactly one reader starts before the fetch window ends.
    sources[2] = replace(sources[2], url=sources[1].url)
    cfg = AppConfig(parallel_sources=parallel, per_source_budget_s=3)

    class Adapter:
        def fetch_posts(self, s, client, db, cfg, now, deadline):
            calls.append((s.name, deadline))
            clock.sleep(6)
            client._check_deadline(deadline)
            return [post(s)]

    monkeypatch.setattr(pipeline.time, "monotonic", clock)
    monkeypatch.setattr("dealradar.fetch.registry.get_adapter", lambda *a, **k: Adapter())
    with closing(DB(":memory:")) as db, closing(PoliteFetcher(
            db, cfg, clock=clock, sleep_fn=clock.sleep,
            transport=httpx.MockTransport(lambda r: httpx.Response(404)))) as client:
        db.meta_set("source_sig:tripped", "rss||" + sources[0].url)
        for _ in range(3):
            db.record_source_result("tripped", ok=False, error="http_403", items=0, now=NOW)
        before = db.all_source_health()
        posts, errors = fetch(sources, client, db, cfg, window=5)
        assert calls == [("first", 1003)]
        assert posts == [] and errors == {"tripped": "tripped", "first": "deadline"}
        assert next(r for r in db.all_source_health() if r["source_name"] == "tripped") == before[0]
        assert db.meta_get("source_sig:second") is None
        assert db.meta_get("source_sig:disabled") is None
    assert caplog.text.count("fetch window exhausted; skipping remaining sources from second") == 1


def test_stateful_sources_and_their_hooks_are_exclusive_on_main_thread(monkeypatch):
    from dealradar.keepa_budget import TokenUsage

    main = threading.get_ident()
    active = set()
    calls, made = [], []
    sources = [source("web1"), source("web2")]
    for name in ("keepa", "woot", "newsletter", "newsletter", "bestbuy", "ebay", "meh",
                 "reddit", "telegram", "threads", "instagram", "future"):
        sources.append(SourceConfig(str(len(sources)), "email" if name == "newsletter" else "api",
                                    f"https://{name}.example/", adapter=name))
        sources.append(source("web" + str(len(sources))))
    configured = sources + [source("paused", enabled=False)]
    monkeypatch.setattr(pipeline, "load_sources", lambda: (configured, []))
    barrier = threading.Barrier(2)

    def recheck(db, cfg, now, **kwargs):
        assert threading.get_ident() == main and not active
        assert kwargs["overflow_discovery"] == "discover"
        assert kwargs["rate_limited"] is False
        return {"checked": 7}

    monkeypatch.setattr(pipeline, "_recheck_prices", recheck)

    class Adapter:
        def fetch_posts(self, s, client, db, cfg, now, deadline):
            if s.adapter:
                assert threading.get_ident() == main and not active
                # Main-thread adapters may still use their existing raw cursor.
                db.conn.execute("SELECT 1").fetchone()
            else:
                assert threading.get_ident() != main
                active.add(s.name)
                if s.name in {"web1", "web2"}:
                    barrier.wait(timeout=5)
                active.remove(s.name)
            calls.append(s.name)
            if s.adapter == "keepa":
                self.rate_limited = False
                self.recheck("discover", deadline)
                self.token_usage = TokenUsage(requests=1, consumed=5)
                self.feed_confirmed = {"new": {"keepa-confirmed"}}
            elif s.adapter == "woot":
                self.recheck_stats = {"confirmed": 3}
            elif s.adapter == "newsletter":
                assert {s.name for s in self.sources} == {s.name for s in configured}
                assert self._http is client._client
                assert client.newsletter_inbox is self
            return [post(s)]

    def adapter(s, *args, **kwargs):
        made.append(s.adapter)
        return Adapter()

    monkeypatch.setattr("dealradar.fetch.registry.get_adapter", adapter)
    cfg = AppConfig(parallel_sources=4)
    confirmed, rechecked, stores = {"new": set(), "used": set()}, {}, {}
    with closing(DB(":memory:")) as db, closing(PoliteFetcher(
            db, cfg, transport=httpx.MockTransport(lambda r: httpx.Response(404)))) as client:
        client.keepa_feed_usage = TokenUsage()
        posts, errors = fetch(sources, client, db, cfg, feed_confirmed=confirmed,
                               price_recheck=rechecked, store_checks=stores)
        assert client.keepa_feed_usage.total == 5
        assert client.db is db
    assert not errors
    assert [p.source_name for p in posts] == [s.name for s in sources]
    assert calls[2:] == [s.name for s in sources[2:]]
    assert made.count("newsletter") == 1
    assert rechecked == {"checked": 7}
    assert confirmed == {"new": {"keepa-confirmed"}, "used": set()}
    assert stores == {"woot": {"confirmed": 3}}


def test_unannounced_enrichment_host_is_held_until_source_finishes(monkeypatch):
    clock = Clock()
    first_read, second_waiting, first_finished = (threading.Event() for _ in range(3))
    local, requests = threading.local(), []
    sources = [source("first"), source("second")]

    def handler(req):
        requests.append((req.url.path, clock()))
        if req.url.path == "/second":
            assert first_finished.is_set()
        return httpx.Response(404 if req.url.path == "/robots.txt" else 200, text="ok")

    class Adapter:
        def fetch_posts(self, s, client, db, cfg, now, deadline):
            local.name = s.name
            if s.name == "second":
                assert first_read.wait(5)
            result = client.get("https://shared.example/" + s.name, deadline=deadline)
            assert result.status == 200
            if s.name == "first":
                first_read.set()
                assert second_waiting.wait(5)
                assert not any(path == "/second" for path, _ in requests)
                first_finished.set()
            return [post(s)]

    monkeypatch.setattr(pipeline.time, "monotonic", clock)
    monkeypatch.setattr("dealradar.fetch.registry.get_adapter", lambda *a, **k: Adapter())
    cfg = AppConfig(parallel_sources=4)
    with closing(DB(":memory:")) as db, closing(PoliteFetcher(
            db, cfg, clock=clock, sleep_fn=clock.sleep,
            transport=httpx.MockTransport(handler))) as client:
        reserve = client._reserve_host

        def claim(host, deadline):
            if local.name == "second":
                second_waiting.set()
            reserve(host, deadline)

        monkeypatch.setattr(client, "_reserve_host", claim)
        posts, errors = fetch(sources, client, db, cfg)
    assert not errors and len(posts) == 2
    assert [path for path, _ in requests] == ["/robots.txt", "/first", "/second"]
    assert all(b[1] - a[1] >= 1 for a, b in zip(requests, requests[1:]))


def test_cyclic_redirect_claims_expire_without_sending_or_deadlocking(monkeypatch):
    clock = Clock()
    barrier = threading.Barrier(2)
    claims, requests = [], []
    cfg = AppConfig(parallel_sources=4, per_source_budget_s=3)

    class Adapter:
        def fetch_posts(self, s, client, db, cfg, now, deadline):
            barrier.wait(timeout=5)
            # Each needs the other's reserved site. No real request can start.
            target = "second" if s.name == "first" else "first"
            return client.get(f"https://{target}.example/", deadline=deadline)

    monkeypatch.setattr(pipeline.time, "monotonic", clock)
    monkeypatch.setattr("dealradar.fetch.registry.get_adapter", lambda *a, **k: Adapter())
    with closing(DB(":memory:")) as db, closing(PoliteFetcher(
            db, cfg, clock=clock, sleep_fn=clock.sleep,
            transport=httpx.MockTransport(lambda req: requests.append(req)))) as client:
        reserve = client._reserve_host

        def claim(host, deadline):
            claims.append(host)
            if len(claims) == 2:
                clock.sleep(4)
            reserve(host, deadline)

        monkeypatch.setattr(client, "_reserve_host", claim)
        posts, errors = fetch([source("first"), source("second")], client, db, cfg)
        assert client.db is db
    assert len(claims) == 2 and not requests
    assert posts == [] and errors == {"first": "deadline", "second": "deadline"}


def test_a_queued_source_gets_its_budget_only_when_started(monkeypatch):
    clock, deadlines = Clock(), []
    cfg = AppConfig(parallel_sources=4, per_source_budget_s=3)
    sources = [source("first"), replace(source("second"), url=source("first").url)]

    class Adapter:
        def fetch_posts(self, s, client, db, cfg, now, deadline):
            deadlines.append(deadline)
            clock.sleep(2)
            return [post(s)]

    monkeypatch.setattr(pipeline.time, "monotonic", clock)
    monkeypatch.setattr("dealradar.fetch.registry.get_adapter", lambda *a, **k: Adapter())
    with closing(DB(":memory:")) as db:
        assert len(fetch(sources, None, db, cfg)[0]) == 2
    assert deadlines == [1003, 1005]


@pytest.mark.parametrize("parallel", [2, 4, 6])
def test_global_source_limit(monkeypatch, parallel):
    lock = threading.Lock()
    barrier = threading.Barrier(parallel)
    active, peak = 0, 0

    class Adapter:
        def fetch_posts(self, s, *args):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            barrier.wait(timeout=5)
            with lock:
                active -= 1
            return [post(s)]

    monkeypatch.setattr("dealradar.fetch.registry.get_adapter", lambda *a, **k: Adapter())
    sources = [source(f"s{i}") for i in range(parallel * 2)]
    with closing(DB(":memory:")) as db:
        posts, errors = fetch(sources, None, db, AppConfig(parallel_sources=parallel))
    assert not errors and len(posts) == parallel * 2
    assert peak == parallel


def test_parallel_fast_lane_filter_and_expired_window_skip_without_db_work(monkeypatch, caplog):
    calls = []

    class Adapter:
        def fetch_posts(self, s, *args):
            calls.append(s.name)
            return [post(s)]

    monkeypatch.setattr("dealradar.fetch.registry.get_adapter", lambda *a, **k: Adapter())
    cfg = AppConfig(parallel_sources=4)
    sources = [source("hourly"), source("disabled", fast_lane=True, enabled=False),
               source("fast", fast_lane=True)]
    with closing(DB(":memory:")) as db:
        posts, errors = pipeline._fetch_all(sources, None, db, cfg, NOW,
                                           time.monotonic() + 1000, True)
        assert not errors and [p.source_name for p in posts] == ["fast"]
        before = db_rows(db)
        posts, errors = pipeline._fetch_all(sources, None, db, cfg, NOW,
                                           time.monotonic() + pipeline._RESERVE_FOR_PARSE_S - 1, True)
        assert posts == [] and errors == {} and db_rows(db) == before
    assert calls == ["fast"]
    assert "fetch window exhausted; skipping remaining sources from fast" in caplog.text


def test_unexpected_worker_error_drains_sqlite_calls_and_restores_fetcher(monkeypatch):
    ready = threading.Event()

    class Adapter:
        def fetch_posts(self, s, client, db, cfg, now, deadline):
            ready.set()
            for i in range(4):
                db.meta_set("drained", str(i))
            return [post(s)]

    def build(s, *args, **kwargs):
        if s.name == "broken":
            assert ready.wait(5)
            raise RuntimeError("unexpected constructor failure")
        return Adapter()

    monkeypatch.setattr("dealradar.fetch.registry.get_adapter", build)
    cfg = AppConfig(parallel_sources=4)
    with closing(DB(":memory:")) as db, closing(PoliteFetcher(
            db, cfg, transport=httpx.MockTransport(lambda r: httpx.Response(404)))) as client:
        with pytest.raises(RuntimeError, match="unexpected constructor failure"):
            fetch([source("broken"), source("healthy")], client, db, cfg)
        assert client.db is db and db.meta_get("drained") == "3"
    assert not any(t.name.startswith("fetch_") for t in threading.enumerate())


@pytest.mark.parametrize("audit_mode", ["shadow", "enforce"])
def test_parallel_rerun_is_idempotent(tmp_path, monkeypatch, capsys, audit_mode):
    env = offline._offline_env(tmp_path, monkeypatch)
    path = Path(env["cfg"])
    cfg = yaml.safe_load(path.read_text())
    cfg["fetch"]["parallel_sources"] = 4
    cfg["parse"]["critic_tiering"]["enabled"] = False
    cfg["audit"]["mode"] = audit_mode
    path.write_text(yaml.safe_dump(cfg))
    # Reuse the full existing assertions on rows, spend and outgoing receipts.
    offline.test_rerun_is_idempotent(env, capsys, audit_mode)
