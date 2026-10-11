"""Offline tests: Keepa deals adapter, Keepa verify cross-check, the shared
Keepa token budget, daily spend cap."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dealradar import verify
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.errors import AdapterNotConfigured
from dealradar.fetch.base import run_source
from dealradar.fetch.keepa import (MAX_DEALS_PER_RUN,
                                   WAREHOUSE_MAX_DEALS_PER_RUN,
                                   KeepaDealsAdapter)
from dealradar.keepa_budget import (META_KEY, MIN_FOR_DEAL_QUERY,
                                    KeepaBudget)
from dealradar.models import SourceConfig
from dealradar.parse.claude_client import ClaudeParser

NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)

SRC = SourceConfig(name="keepa_amazon_drops", method="api",
                   url="https://api.keepa.com/deal", adapter="keepa")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("KEEPA_KEY", raising=False)


@pytest.fixture()
def cfg() -> AppConfig:
    return load_config()


def _mock_client(handler, cfg: AppConfig) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler),
                        headers={"User-Agent": cfg.resolved_user_agent()})


def _deals_payload():
    def row(asin, title, current, avg):
        return {"asin": asin, "title": title,
                "current": [current], "avg": [avg]}  # integer cents

    rows = [
        row("B0TESTASIN1", "Mega Blender 9000", 2999, 14999),   # 80% below avg
        row("B0TESTASIN2", "No-drop Gadget", 9999, 9999),       # current == avg -> skip
        {"asin": "", "title": "broken row"},                     # skipped
    ]
    rows += [row(f"B0FILLER{i:03d}", f"Filler {i}", 1000, 5000)
             for i in range(30)]  # exercises the per-run cap
    return {"deals": {"dr": rows}}


class TestKeepaAdapter:
    def test_missing_key_raises(self, cfg):
        with pytest.raises(AdapterNotConfigured):
            KeepaDealsAdapter().fetch_posts(SRC, None, None, cfg, NOW, None)

    def test_maps_drops_with_verbatim_prices(self, cfg, monkeypatch):
        monkeypatch.setenv("KEEPA_KEY", "kkey")

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "api.keepa.com"
            assert request.url.path == "/deal"
            assert request.url.params["key"] == "kkey"
            assert "domainId" in request.url.params["selection"]
            return httpx.Response(200, json=_deals_payload())

        adapter = KeepaDealsAdapter(http_client=_mock_client(handler, cfg))
        posts = adapter.fetch_posts(SRC, None, None, cfg, NOW, None)

        assert 1 <= len(posts) <= MAX_DEALS_PER_RUN
        first = posts[0]
        assert first.title == "Mega Blender 9000"
        # Verbatim price lines the evidence check will match against.
        assert "On sale for $29.99" in first.text
        assert "Regular price (90-day average on Amazon): $149.99" in first.text
        # Amazon product link present so dedupe gets a canonical ASIN URL.
        assert "https://www.amazon.com/dp/B0TESTASIN1" in first.text
        # Credit link goes to Keepa's own product page.
        assert first.url.startswith("https://keepa.com/")
        # The flat-average row and the broken row were skipped.
        assert all("No-drop Gadget" != p.title for p in posts)

    def test_maps_real_api_shape(self, cfg, monkeypatch):
        """Live /deal rows: ``current`` is indexed by price type (with -1
        gaps) and ``avg`` is [dateRange][priceType]. Regression for the
        launch bug where avg (a list of lists) parsed as no-data and every
        row was silently dropped ("mapped 0 price-drop posts")."""
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        row = {
            "asin": "B0REALSHAPE", "title": "Robot Vacuum X",
            # index 0 = Amazon's price; later entries are other price types.
            "current": [4999, 5999, -1, 120000],
            # [day, week, month, 90d] x [priceType] — 90-day window preferred.
            "avg": [[5200, -1], [9900, -1], [15000, -1], [19999, 6000]],
            # Keepa serializes the image filename as an int array of chars.
            "image": [ord(c) for c in "61vacuumIMG._AC_.jpg"],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            sel = json.loads(request.url.params["selection"])
            assert sel["dateRange"] == 3  # drop vs the 90-day average
            assert sel["minRating"] == 20
            if sel["priceTypes"] == [0]:  # amazon-price pass
                # Strict popularity net for the new-price lane.
                assert sel["salesRankRange"] == [1, 30000]
                return httpx.Response(
                    200, json={"deals": {"dr": [row]}, "tokensLeft": 280})
            assert sel["priceTypes"] == [9]  # warehouse pass
            # Much wider net: mispriced open-box hides on mid-rank items.
            assert sel["salesRankRange"] == [1, 200000]
            return httpx.Response(
                200, json={"deals": {"dr": []}, "tokensLeft": 275})

        adapter = KeepaDealsAdapter(http_client=_mock_client(handler, cfg))
        posts = adapter.fetch_posts(SRC, None, None, cfg, NOW, None)
        assert len(posts) == 1
        assert "On sale for $49.99" in posts[0].text
        assert "Regular price (90-day average on Amazon): $199.99" in posts[0].text
        # Thumbnail: Amazon's own public CDN URL, decoded from the int array.
        assert posts[0].image_url == \
            "https://m.media-amazon.com/images/I/61vacuumIMG._AC_.jpg"

    def test_sparse_history_rows_are_dropped(self, cfg, monkeypatch):
        """Live incident: OEM parts with one stale $460 listing in 90 days
        showed as '97% off $13'. No week/month data for the Amazon price
        type -> the 'average' is an artifact -> the row never becomes a
        post."""
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        sparse = {
            "asin": "B0SPARSEOEM", "title": "Brake Rotor OEM 09.X999.11",
            "current": [1305, -1],
            "avg": [[-1, -1], [-1, -1], [-1, -1], [46000, -1]],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"deals": {"dr": [sparse]}, "tokensLeft": 250})

        adapter = KeepaDealsAdapter(http_client=_mock_client(handler, cfg))
        assert adapter.fetch_posts(SRC, None, None, cfg, NOW, None) == []

    def test_warehouse_pass_maps_used_vs_live_new_price(self, cfg, monkeypatch):
        """The Brembo-rotor lesson: Amazon Resale open-box offers ARE real
        deals when compared against the LIVE new price ($12.07 used vs
        $81.55 new = Amazon's own -85%)."""
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        deep = {"asin": "B0WAREHOUSE", "title": "Brembo Disc Brake Rotor",
                "current": [8155, 8155, -1, -1, -1, -1, -1, -1, -1, 1207]}
        shallow = {"asin": "B0SHALLOWWH", "title": "Mild Discount Gadget",
                   "current": [8155, 8155, -1, -1, -1, -1, -1, -1, -1, 6000]}
        no_new = {"asin": "B0NONEWPRIC", "title": "No New Offer Item",
                  "current": [-1, -1, -1, -1, -1, -1, -1, -1, -1, 1207]}
        junk = {"asin": "B0JUNKCASEX", "title": "$12 Phone Case",
                "current": [1200, 1200, -1, -1, -1, -1, -1, -1, -1, 199]}

        def handler(request: httpx.Request) -> httpx.Response:
            sel = json.loads(request.url.params["selection"])
            if sel["priceTypes"] == [0]:
                return httpx.Response(200, json={"deals": {"dr": []}})
            return httpx.Response(
                200, json={"deals": {"dr": [deep, shallow, no_new, junk]}})

        adapter = KeepaDealsAdapter(http_client=_mock_client(handler, cfg))
        posts = adapter.fetch_posts(SRC, None, None, cfg, NOW, None)
        # Only the deep drop maps: 26%-off, no-live-new-price, and
        # low-value junk (new price under $25) are all skipped.
        assert len(posts) == 1
        p = posts[0]
        assert p.uid == "B0WAREHOUSE:wh:12.07"
        assert "yours for $12.07" in p.text
        assert "New condition price: $81.55" in p.text
        assert "used/open-box" in p.text
        assert "https://www.amazon.com/dp/B0WAREHOUSE" in p.text

    def test_self_throttle_to_15_minutes_inside_the_5_minute_lane(
            self, cfg, monkeypatch, tmp_path):
        """The lane fires every 5 minutes; Keepa pulls at most every ~14 so
        the token allowance holds. A pull inside the window is a clean
        no-op (no HTTP), and a pull after it goes through again."""
        from datetime import timedelta

        monkeypatch.setenv("KEEPA_KEY", "kkey")
        db = DB(tmp_path / "state.db")
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.params["selection"])
            return httpx.Response(200, json={"deals": {"dr": []}})

        adapter = KeepaDealsAdapter(http_client=_mock_client(handler, cfg))
        adapter.fetch_posts(SRC, None, db, cfg, NOW, None)
        assert len(calls) == 2  # amazon + warehouse queries ran
        # 5 minutes later: inside the window -> no HTTP at all.
        assert adapter.fetch_posts(SRC, None, db, cfg,
                                   NOW + timedelta(minutes=5), None) == []
        assert len(calls) == 2
        # 15 minutes later: due again.
        adapter.fetch_posts(SRC, None, db, cfg,
                            NOW + timedelta(minutes=15), None)
        assert len(calls) == 4
        db.close()

    def test_in_body_api_error_raises_a_source_failure(self, cfg, monkeypatch):
        monkeypatch.setenv("KEEPA_KEY", "kkey")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "error": {"type": "parameter", "message": "bad selection"}})

        adapter = KeepaDealsAdapter(http_client=_mock_client(handler, cfg))
        with pytest.raises(RuntimeError, match="keepa api error"):
            adapter.fetch_posts(SRC, None, None, cfg, NOW, None)

    def test_warehouse_pass_maps_up_to_40_while_amazon_keeps_20(
            self, cfg, monkeypatch):
        """The warehouse pass is the only used/open-box supply, so it maps
        twice the Amazon-price pass's cap per pull."""
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        amazon = [{"asin": f"B0AMZ{i:06d}", "title": f"New {i}",
                   "current": [1000], "avg": [5000]} for i in range(30)]
        warehouse = [{"asin": f"B0WHS{i:06d}", "title": f"Open box {i}",
                      "current": [8155, 8155, -1, -1, -1, -1, -1, -1, -1, 1207]}
                     for i in range(50)]

        def handler(request: httpx.Request) -> httpx.Response:
            sel = json.loads(request.url.params["selection"])
            rows = amazon if sel["priceTypes"] == [0] else warehouse
            return httpx.Response(200, json={"deals": {"dr": rows}})

        adapter = KeepaDealsAdapter(http_client=_mock_client(handler, cfg))
        posts = adapter.fetch_posts(SRC, None, None, cfg, NOW, None)
        assert MAX_DEALS_PER_RUN == 20 and WAREHOUSE_MAX_DEALS_PER_RUN == 40
        assert sum(":wh:" not in p.uid for p in posts) == 20
        assert sum(":wh:" in p.uid for p in posts) == 40

    def test_429_keeps_mapped_posts_and_never_trips_the_breaker(
            self, cfg, monkeypatch, tmp_path):
        """Live incident: three 429s (empty token bucket, refills by the
        minute, costs nothing) tripped the breaker for six hours and killed
        the only used/open-box supply. A 429 now ends the pull with what
        was mapped, records a SUCCESS for the breaker, and leaves
        keepa_last_fetch_at unset so the next tick retries."""
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        db = DB(tmp_path / "state.db")
        row = {"asin": "B0TESTASIN1", "title": "Mega Blender 9000",
               "current": [2999], "avg": [14999]}

        def handler(request: httpx.Request) -> httpx.Response:
            sel = json.loads(request.url.params["selection"])
            if sel["priceTypes"] == [0]:
                return httpx.Response(200, json={
                    "deals": {"dr": [row]}, "tokensLeft": 50, "refillRate": 5})
            return httpx.Response(429, json={
                "tokensLeft": -2, "refillIn": 31000, "refillRate": 5,
                "error": {"type": "tokens", "message": "Too Many Requests"}})

        adapter = KeepaDealsAdapter(http_client=_mock_client(handler, cfg))
        for i in range(4):  # well past circuit_breaker_failures (3)
            # Three minutes apart: the 429 body (-2 tokens, 5/min) lets the
            # budget clear the next pull's query again by then.
            now = NOW + timedelta(minutes=3 * i)
            posts, ok, error = run_source(SRC, adapter, None, db, cfg, now)
            assert (ok, error) == (True, "")
            assert [p.title for p in posts] == ["Mega Blender 9000"]
        health = {r["source_name"]: r for r in db.all_source_health()}
        assert health[SRC.name]["consecutive_failures"] == 0
        assert not db.is_tripped(SRC.name, now)
        assert db.meta_get("keepa_last_fetch_at") is None
        # The 429 body's token state is what the next run starts from.
        saved = json.loads(db.meta_get(META_KEY))
        assert saved["tokens_left"] == -2 and saved["refill_rate"] == 5
        db.close()

    def test_429_on_first_query_defers_until_the_bucket_refills(
            self, cfg, monkeypatch, tmp_path):
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        db = DB(tmp_path / "state.db")
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(429, json={
                "tokensLeft": -2, "refillIn": 24000, "refillRate": 5})

        adapter = KeepaDealsAdapter(http_client=_mock_client(handler, cfg))
        assert adapter.fetch_posts(SRC, None, db, cfg, NOW, None) == []
        assert len(calls) == 1  # the pull stopped at the first 429
        assert db.meta_get("keepa_last_fetch_at") is None
        # One minute on: -2 + 5 = 3 tokens < 10 -> no HTTP at all.
        assert adapter.fetch_posts(SRC, None, db, cfg,
                                   NOW + timedelta(minutes=1), None) == []
        assert len(calls) == 1
        # Three minutes on: 13 tokens -> the query is tried again.
        adapter.fetch_posts(SRC, None, db, cfg, NOW + timedelta(minutes=3),
                            None)
        assert len(calls) == 2
        db.close()

    def test_budget_gate_skips_queries_without_http(self, cfg, monkeypatch,
                                                    tmp_path, caplog):
        """The persisted budget from any earlier Keepa response (product
        calls included) holds a query back while the estimate is under
        MIN_FOR_DEAL_QUERY; a pull cut short keeps keepa_last_fetch_at
        unset so the next tick retries once the bucket refills."""
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        db = DB(tmp_path / "state.db")
        db.meta_set(META_KEY, json.dumps({
            "tokens_left": 4, "refill_rate": 1, "seen_at": NOW.isoformat()}))
        row = {"asin": "B0TESTASIN1", "title": "Mega Blender 9000",
               "current": [2999], "avg": [14999]}
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(json.loads(request.url.params["selection"])["priceTypes"])
            return httpx.Response(200, json={
                "deals": {"dr": [row]}, "tokensLeft": 8, "refillRate": 1})

        adapter = KeepaDealsAdapter(http_client=_mock_client(handler, cfg))
        with caplog.at_level("INFO", logger="dealradar.fetch.keepa"):
            assert adapter.fetch_posts(SRC, None, db, cfg, NOW, None) == []
        assert calls == []
        assert "skipping the query" in caplog.text
        assert db.meta_get("keepa_last_fetch_at") is None
        # Ten minutes of refill: 14 tokens -> the Amazon pass runs; its
        # response says 8 are left (< 10) -> the warehouse pass waits.
        posts = adapter.fetch_posts(SRC, None, db, cfg,
                                    NOW + timedelta(minutes=10), None)
        assert calls == [[0]]
        assert [p.title for p in posts] == ["Mega Blender 9000"]
        assert db.meta_get("keepa_last_fetch_at") is None
        assert MIN_FOR_DEAL_QUERY == 10
        db.close()


class TestKeepaBudget:
    def test_estimate_adds_refill_capped_at_one_hour(self):
        b = KeepaBudget()
        assert b.available(NOW) is None       # never seen a response ...
        assert b.allows(10 ** 6, NOW)         # ... means no gating at all
        b.note({"tokensLeft": 12, "refillRate": 5, "refillIn": 7000}, NOW)
        assert b.available(NOW) == pytest.approx(12.0)
        assert b.available(NOW + timedelta(minutes=10)) == pytest.approx(62.0)
        # Keepa's bucket never holds more than an hour of refill.
        assert b.available(NOW + timedelta(hours=3)) == pytest.approx(312.0)
        # A reading from the future (clock skew) credits nothing.
        assert b.available(NOW - timedelta(minutes=5)) == pytest.approx(12.0)
        assert b.allows(60, NOW + timedelta(minutes=10))
        assert not b.allows(60, NOW + timedelta(minutes=9))

    def test_note_ignores_bodies_without_tokens_and_keeps_the_rate(self):
        b = KeepaBudget()
        b.note({"error": {"type": "parameter"}}, NOW)
        assert b.available(NOW) is None
        b.note(None, NOW)
        b.note({"tokensLeft": 30, "refillRate": 5}, NOW)
        # A 429 body: tokens overdrawn, no rate field -> the rate is kept.
        b.note({"tokensLeft": -3, "refillIn": 24000},
               NOW + timedelta(minutes=2))
        assert b.available(NOW + timedelta(minutes=2)) == pytest.approx(-3.0)
        assert not b.allows(10, NOW + timedelta(minutes=2))
        assert b.available(NOW + timedelta(minutes=5)) == pytest.approx(12.0)

    def test_persists_across_runs_in_db_meta(self, tmp_path):
        db = DB(tmp_path / "state.db")
        assert KeepaBudget.load(db).available(NOW) is None
        b = KeepaBudget()
        b.save(db)                            # nothing learned: no row
        assert db.meta_get(META_KEY) is None
        b.note({"tokensLeft": 12, "refillRate": 5}, NOW)
        b.save(db)
        again = KeepaBudget.load(db)
        assert again.available(NOW + timedelta(minutes=2)) == pytest.approx(22.0)
        # Junk in meta reads as "unknown", never as a crash or a gate.
        db.meta_set(META_KEY, "not json")
        assert KeepaBudget.load(db).available(NOW) is None
        db.meta_set(META_KEY, json.dumps([1, 2]))
        assert KeepaBudget.load(db).allows(999, NOW)
        db.close()


class TestKeepaVerify:
    def _deal(self, **kw):
        base = {"dedupe_key": "url|https://www.amazon.com/dp/B0TESTASIN1",
                "asin": "B0TESTASIN1", "final_price": 29.99,
                "pct_at_checkout": 80.0}
        base.update(kw)
        return base

    def test_check_true_when_far_below_avg(self, cfg, monkeypatch):
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        cfg.feature_flags["keepa"] = True

        def fake_get(url, params=None, timeout=None):
            assert "api.keepa.com/product" in url
            assert params["asin"] == "B0TESTASIN1"
            return httpx.Response(
                200, json={"products": [{
                    "stats": {"avg90": [14999, -1]},
                    "imagesCSV": "71productIMG.jpg,41otherIMG.jpg",
                }]},
                request=httpx.Request("GET", url))

        monkeypatch.setattr(verify.httpx if hasattr(verify, "httpx") else httpx,
                            "get", fake_get)
        deal = self._deal()
        assert verify.keepa_check(deal, cfg) is True
        # Free ride on the same response: the product's first image.
        assert deal["image_url"] == \
            "https://m.media-amazon.com/images/I/71productIMG.jpg"

    def test_check_false_when_near_avg(self, cfg, monkeypatch):
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        cfg.feature_flags["keepa"] = True

        def fake_get(url, params=None, timeout=None):
            return httpx.Response(
                200, json={"products": [{"stats": {"avg90": [3999]}}]},
                request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "get", fake_get)
        assert verify.keepa_check(self._deal(), cfg) is False

    def test_no_key_or_flag_means_no_opinion(self, cfg):
        cfg.feature_flags["keepa"] = False
        assert verify.keepa_check(self._deal(), cfg) is None
        cfg.feature_flags["keepa"] = True  # flag on, key still unset
        assert verify.keepa_check(self._deal(), cfg) is None

    def test_429_is_no_opinion_and_every_response_feeds_the_budget(
            self, cfg, monkeypatch, caplog):
        """An empty token bucket is 'no opinion' for both product calls —
        never a verdict that marks a deal dead or unverified-for-good —
        and the 429 body's token state reaches the shared budget, as does
        a normal response's."""
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        cfg.feature_flags["keepa"] = True
        req = httpx.Request("GET", "https://api.keepa.com/product")
        monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(
            429, json={"tokensLeft": -1, "refillIn": 5000, "refillRate": 5},
            request=req))
        budget = KeepaBudget()
        deal = self._deal(conditions=[])
        with caplog.at_level("WARNING", logger="dealradar.verify"):
            assert verify.keepa_check(deal, cfg, budget=budget) is None
            assert verify.recheck_amazon_price(deal, cfg, budget=budget) is None
        assert "429" in caplog.text and "refillIn=5000ms" in caplog.text
        assert budget.tokens_left == -1 and budget.refill_rate == 5
        monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(
            200, json={"products": [{"stats": {"current": [2999, -1],
                                               "avg90": [14999]}}],
                       "tokensLeft": 44, "refillRate": 5},
            request=req))
        assert verify.recheck_amazon_price(deal, cfg, budget=budget) is True
        assert budget.tokens_left == 44
        assert verify.keepa_check(deal, cfg, budget=budget) is True


class TestDailySpendCap:
    def test_spend_sums_for_utc_day(self, tmp_path):
        db = DB(tmp_path / "state.db")
        for i, spend in enumerate((1.25, 2.00)):
            key = f"2026-10-10T{10 + i:02d}"
            db.start_run(key, "hourly", NOW)
            db.finish_run(key, now=NOW, items_fetched=1, posts_new=1,
                          deals_parsed=1, published=0, spend_usd=spend)
        other = datetime(2026, 10, 9, 23, 0, tzinfo=timezone.utc)
        db.start_run("2026-10-09T23", "hourly", other)
        db.finish_run("2026-10-09T23", now=other, items_fetched=1, posts_new=1,
                      deals_parsed=1, published=0, spend_usd=9.0)
        assert db.spend_usd_for_day(NOW) == pytest.approx(3.25)
        db.close()

    def test_run_cap_is_tightened_by_daily_remainder(self, cfg):
        class FakeModels:
            def list(self):
                class M:  # noqa: N801 - tiny stub
                    id = "claude-haiku-5-5"
                    created_at = "2026-01-01"
                return [M()]

        class FakeClient:
            models = FakeModels()

        parser = ClaudeParser(cfg, client=FakeClient(), run_cap_usd=0.10)
        assert parser.run_cap_usd == pytest.approx(0.10)
        parser_full = ClaudeParser(cfg, client=FakeClient())
        assert parser_full.run_cap_usd == pytest.approx(cfg.spend_cap_usd_per_run)
