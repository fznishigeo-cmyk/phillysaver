"""Offline tests: Keepa deals adapter, Keepa verify cross-check, the shared
Keepa token budget, daily spend cap."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dealradar import verify
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.errors import AdapterNotConfigured
from dealradar.fetch.base import run_source
from dealradar.fetch.keepa import KeepaDealsAdapter
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
    cfg = load_config()
    cfg.keepa_focus_categories = []  # these tests cover the general lanes
    return cfg


def _mock_client(handler, cfg: AppConfig) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler),
                        headers={"User-Agent": cfg.resolved_user_agent()})


def _adapter(handler, cfg: AppConfig) -> KeepaDealsAdapter:
    """Adapter on a mock transport; the 1 req/s gap is recorded, not slept."""
    return KeepaDealsAdapter(http_client=_mock_client(handler, cfg),
                             sleep_fn=lambda s: None)


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


def _amazon_row(i: int) -> dict:
    """An Amazon-price row that maps: 80% under its 90-day average."""
    return {"asin": f"B0AMZ{i:05d}", "title": f"New gadget {i}",
            "current": [1000], "avg": [5000]}


def _wh_row(i: int, prefix: str = "B0WHS") -> dict:
    """A warehouse row that maps: $12.07 used vs Amazon's own $81.55."""
    return {"asin": f"{prefix}{i:05d}", "title": f"Open box {prefix} {i}",
            "current": [8155, 8155, -1, -1, -1, -1, -1, -1, -1, 1207]}


class FakeKeepa:
    """A paging /deal endpoint: each query shape serves its list in pages of
    150, keyed by the selection exactly as the adapter sends it. Also the
    warehouse sweep's product finder (/query: ``finder`` ASINs, best rank
    first, in pages of perPage) and /product (``products`` by ASIN)."""

    def __init__(self, amazon=(), fresh=(), deep=(), tokens=None, fail=None,
                 finder=(), products=None):
        self.lists = {"amazon": list(amazon), "fresh": list(fresh),
                      "deep": list(deep)}
        self.tokens = tokens          # None: responses carry no token fields
        # (shape, page) -> httpx.Response; ("product", None) = any /product
        self.fail = dict(fail or {})
        self.calls: list[tuple[str, int]] = []
        self.finder = list(finder)
        self.products = dict(products or {})
        self.finder_sels: list[dict] = []
        self.product_params: list[dict] = []
        self.costs = {"deal": 5, "query": 11}  # /product: 1 per ASIN

    @staticmethod
    def shape(sel: dict) -> str:
        if sel["priceTypes"] == [0]:
            assert (sel["dateRange"], sel["sortType"]) == (3, 4)
            assert sel["salesRankRange"] == [1, 30000]
            assert sel["deltaPercentRange"] == [35, 100]
            return "amazon"
        assert sel["priceTypes"] == [9]
        assert sel["salesRankRange"] == [1, 200000]
        assert sel["minRating"] == 20 and 283155 in sel["excludeCategories"]
        # Keepa's documented minimum: the client-side honest gate filters.
        assert sel["deltaPercentRange"] == [10, 100]
        if sel["sortType"] == 1:      # newest first, last 24 hours
            assert sel["dateRange"] == 0
            return "fresh"
        assert (sel["dateRange"], sel["sortType"]) == (3, 2)
        return "deep"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/query":
            sel = json.loads(request.url.params["selection"])
            self.finder_sels.append(sel)
            page, per = sel["page"], sel["perPage"]
            self.calls.append(("query", page))
            if ("query", page) in self.fail:
                return self.fail[("query", page)]
            body = {"asinList": self.finder[page * per:(page + 1) * per],
                    "totalResults": len(self.finder)}
            cost = self.costs["query"]
        elif request.url.path == "/product":
            params = dict(request.url.params)
            self.product_params.append(params)
            asins = params["asin"].split(",")
            self.calls.append(("product", len(asins)))
            if ("product", None) in self.fail:
                return self.fail[("product", None)]
            body = {"products": [self.products[a] for a in asins
                                 if a in self.products]}
            cost = len(asins)
        else:
            sel = json.loads(request.url.params["selection"])
            shape, page = self.shape(sel), sel["page"]
            self.calls.append((shape, page))
            if (shape, page) in self.fail:
                return self.fail[(shape, page)]
            body = {"deals": {"dr": self.lists[shape][page * 150:(page + 1) * 150]}}
            cost = self.costs["deal"]
        if self.tokens is not None:
            self.tokens -= cost
            body.update(tokensLeft=self.tokens, refillRate=20)
        return httpx.Response(200, json=body)


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

        adapter = _adapter(handler, cfg)
        posts = adapter.fetch_posts(SRC, None, None, cfg, NOW, None)

        assert 1 <= len(posts) <= cfg.keepa_amazon_max_new_posts_per_pull
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
            assert sel["minRating"] == 20
            if sel["priceTypes"] == [0]:  # amazon-price pass
                assert sel["dateRange"] == 3  # drop vs the 90-day average
                # Strict popularity net for the new-price lane.
                assert sel["salesRankRange"] == [1, 30000]
                return httpx.Response(
                    200, json={"deals": {"dr": [row]}, "tokensLeft": 280})
            assert sel["priceTypes"] == [9]  # warehouse pass
            # Much wider net: mispriced open-box hides on mid-rank items.
            assert sel["salesRankRange"] == [1, 200000]
            return httpx.Response(
                200, json={"deals": {"dr": []}, "tokensLeft": 275})

        adapter = _adapter(handler, cfg)
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

        adapter = _adapter(handler, cfg)
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

        adapter = _adapter(handler, cfg)
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

        adapter = _adapter(handler, cfg)
        adapter.fetch_posts(SRC, None, db, cfg, NOW, None)
        assert len(calls) == 3  # amazon + warehouse fresh + deep queries ran
        # 5 minutes later: inside the window -> no HTTP at all.
        assert adapter.fetch_posts(SRC, None, db, cfg,
                                   NOW + timedelta(minutes=5), None) == []
        assert len(calls) == 3
        # 15 minutes later: due again.
        adapter.fetch_posts(SRC, None, db, cfg,
                            NOW + timedelta(minutes=15), None)
        assert len(calls) == 6
        db.close()

    def test_in_body_api_error_raises_a_source_failure(self, cfg, monkeypatch):
        monkeypatch.setenv("KEEPA_KEY", "kkey")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "error": {"type": "parameter", "message": "bad selection"}})

        adapter = _adapter(handler, cfg)
        with pytest.raises(RuntimeError, match="keepa api error"):
            adapter.fetch_posts(SRC, None, None, cfg, NOW, None)

    def test_post_caps_come_from_config(self, cfg, monkeypatch):
        """Each lane emits at most its configured NEW posts per pull (the
        LLM bound), and paging stops once the cap is full: no tokens are
        spent on rows that could not be emitted."""
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        cfg.keepa_amazon_max_new_posts_per_pull = 10
        cfg.keepa_warehouse_max_new_posts_per_pull = 20
        fake = FakeKeepa(amazon=[_amazon_row(i) for i in range(1000)],
                         deep=[_wh_row(i) for i in range(1000)])
        posts = _adapter(fake, cfg).fetch_posts(SRC, None, None, cfg, NOW, None)
        assert sum(":wh:" not in p.uid for p in posts) == 10
        assert sum(":wh:" in p.uid for p in posts) == 20
        # Amazon's cap filled on page 0, the warehouse cap on deep page 0.
        assert fake.calls == [("amazon", 0), ("fresh", 0), ("deep", 0)]

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

        adapter = _adapter(handler, cfg)
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

        adapter = _adapter(handler, cfg)
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

        adapter = _adapter(handler, cfg)
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


def _junk_row(i: int, prefix: str = "B0JNK") -> dict:
    """A row that never maps (no drop, no warehouse price): pages stay full
    without the post caps stepping in."""
    return {"asin": f"{prefix}{i:05d}", "title": f"Flat {i}",
            "current": [5000], "avg": [5000]}


class TestKeepaPaging:
    """The /deal lane uses the token plan: further pages per pull while the
    reserve for product calls holds, a rotating DEEP warehouse window."""

    @pytest.fixture(autouse=True)
    def _key(self, monkeypatch):
        monkeypatch.setenv("KEEPA_KEY", "kkey")

    def test_knob_defaults(self, cfg):
        assert (cfg.keepa_amazon_pages_per_pull,
                cfg.keepa_warehouse_pages_per_pull,
                cfg.keepa_reserve_tokens) == (6, 12, 300)

    def test_pages_up_to_the_caps_one_second_apart(self, cfg, tmp_path):
        """Plenty of tokens and endless lists: 6 Amazon pages, 4 FRESH and
        8 DEEP warehouse pages (12 = a third fresh, the rest deep), never
        more, each request a polite second after the last."""
        db = DB(tmp_path / "state.db")
        fake = FakeKeepa(amazon=[_junk_row(i, "B0AAA") for i in range(2000)],
                         fresh=[_junk_row(i, "B0FFF") for i in range(2000)],
                         deep=[_junk_row(i, "B0DDD") for i in range(3000)],
                         tokens=1200)
        slept: list[float] = []
        adapter = KeepaDealsAdapter(http_client=_mock_client(fake, cfg),
                                    sleep_fn=slept.append)
        adapter.fetch_posts(SRC, None, db, cfg, NOW, None)
        # 1110 tokens left after 18 pages: idle, so the sweep's finder
        # runs last (an empty finder list: no product check).
        assert fake.calls == ([("amazon", 0)]
                              + [("fresh", p) for p in range(4)]
                              + [("deep", p) for p in range(8)]
                              + [("amazon", p) for p in range(1, 6)]
                              + [("query", 0)])
        assert slept == [cfg.min_host_interval_s] * 18
        assert db.meta_get("keepa_deep_page") == "8"
        assert db.meta_get("keepa_last_fetch_at") is not None
        db.close()

    def test_short_page_ends_each_query(self, cfg, tmp_path):
        db = DB(tmp_path / "state.db")
        fake = FakeKeepa(amazon=[_junk_row(i, "B0AAA") for i in range(200)],
                         fresh=[_junk_row(i, "B0FFF") for i in range(10)],
                         deep=[_junk_row(i, "B0DDD") for i in range(160)])
        _adapter(fake, cfg).fetch_posts(SRC, None, db, cfg, NOW, None)
        assert fake.calls == [("amazon", 0), ("fresh", 0), ("deep", 0),
                              ("deep", 1), ("amazon", 1)]
        assert db.meta_get("keepa_deep_page") == "0"  # list ended: wrap
        db.close()

    def test_reserve_is_honoured_with_a_fake_budget(self, cfg, tmp_path,
                                                    monkeypatch):
        """The Amazon and FRESH first pages need only MIN_FOR_DEAL_QUERY; an
        extra page (DEEP's first included) never starts unless the estimate
        minus its ~5 tokens stays at or above reserve_tokens (300).
        Stopping there is a normal end of the pull, not a cut-short one."""
        import dealradar.fetch.keepa as keepa_mod

        class FakeBudget:
            tokens = 318.0
            saved = False

            def available(self, now=None):
                return self.tokens

            def allows(self, need, now=None):
                return self.tokens >= need

            def note(self, payload, now=None):
                self.tokens -= 5  # what each page costs

            def save(self, db):
                self.saved = True

        budget = FakeBudget()
        monkeypatch.setattr(keepa_mod.KeepaBudget, "load",
                            classmethod(lambda cls, db: budget))
        started: list[tuple[tuple[str, int], float]] = []
        fake = FakeKeepa(amazon=[_junk_row(i, "B0AAA") for i in range(900)],
                         fresh=[_junk_row(i, "B0FFF") for i in range(900)],
                         deep=[_junk_row(i, "B0DDD") for i in range(900)])

        def handler(request):
            resp = fake(request)
            started.append((fake.calls[-1], budget.tokens))
            return resp

        db = DB(tmp_path / "state.db")
        _adapter(handler, cfg).fetch_posts(SRC, None, db, cfg, NOW, None)
        # 303 after fresh p1: DEEP's first page is an extra page too.
        assert fake.calls == [("amazon", 0), ("fresh", 0), ("fresh", 1)]
        first_pages = {("amazon", 0), ("fresh", 0)}
        for call, tokens in started:
            if call not in first_pages:
                assert tokens - 5 >= cfg.keepa_reserve_tokens
        assert budget.saved
        assert db.meta_get("keepa_last_fetch_at") is not None
        db.close()

    def test_real_budget_learns_tokens_left_and_stops_at_the_reserve(
            self, cfg, tmp_path):
        db = DB(tmp_path / "state.db")
        fake = FakeKeepa(amazon=[_junk_row(i, "B0AAA") for i in range(900)],
                         fresh=[_junk_row(i, "B0FFF") for i in range(900)],
                         deep=[_junk_row(i, "B0DDD") for i in range(900)],
                         tokens=330)
        _adapter(fake, cfg).fetch_posts(SRC, None, db, cfg, NOW, None)
        # 325 after amazon p0, 320 after fresh p0; extras while >= 305.
        assert fake.calls == [("amazon", 0), ("fresh", 0), ("fresh", 1),
                              ("fresh", 2), ("fresh", 3), ("deep", 0)]
        assert json.loads(db.meta_get(META_KEY))["tokens_left"] == 300
        db.close()

    def test_429_mid_paging_keeps_posts_and_never_trips_the_breaker(
            self, cfg, tmp_path):
        db = DB(tmp_path / "state.db")
        deep = ([_wh_row(i, "B0DEP") for i in range(5)]
                + [_junk_row(i, "B0DDD") for i in range(400)])
        fake = FakeKeepa(
            amazon=[_amazon_row(i) for i in range(5)],
            fresh=[_wh_row(i, "B0FRS") for i in range(3)], deep=deep,
            fail={("deep", 1): httpx.Response(429, json={
                "tokensLeft": -3, "refillIn": 9000, "refillRate": 20})})
        posts, ok, error = run_source(SRC, _adapter(fake, cfg), None, db,
                                      cfg, NOW)
        assert (ok, error) == (True, "")
        assert len(posts) == 5 + 3 + 5
        assert fake.calls == [("amazon", 0), ("fresh", 0), ("deep", 0),
                              ("deep", 1)]
        assert db.meta_get("keepa_deep_page") == "1"  # p1 retried next pull
        assert db.meta_get("keepa_last_fetch_at") is None
        assert json.loads(db.meta_get(META_KEY))["tokens_left"] == -3
        db.close()

    def test_deep_window_rotates_across_pulls_and_wraps(self, cfg, tmp_path):
        """Coverage, not repetition: each pull reads the NEXT deep pages
        (db meta keepa_deep_page) and a short page wraps back to 0."""
        cfg.keepa_warehouse_pages_per_pull = 3  # 1 fresh + 2 deep
        db = DB(tmp_path / "state.db")
        fake = FakeKeepa(deep=[_junk_row(i, "B0DDD") for i in range(760)])
        adapter = _adapter(fake, cfg)
        seen = []
        for n in range(4):
            fake.calls.clear()
            adapter.fetch_posts(SRC, None, db, cfg,
                                NOW + timedelta(minutes=10 * n), None)
            seen.append(([p for s, p in fake.calls if s == "deep"],
                         db.meta_get("keepa_deep_page")))
        assert seen == [([0, 1], "2"), ([2, 3], "4"), ([4, 5], "0"),
                        ([0, 1], "2")]
        db.close()

    def test_rows_deduped_by_asin_across_pages_and_shapes(self, cfg):
        cfg.keepa_warehouse_max_new_posts_per_pull = 1000
        rows = [_wh_row(i) for i in range(160)]
        fake = FakeKeepa(fresh=rows[:20],                   # also in deep p0
                         deep=rows[:150] + rows[:5] + rows[150:155])
        posts = _adapter(fake, cfg).fetch_posts(SRC, None, None, cfg, NOW,
                                                None)
        uids = [p.uid for p in posts]
        assert len(uids) == len(set(uids)) == 155
        assert ("deep", 1) in fake.calls

    def test_a_failing_later_page_never_fails_the_pull(self, cfg, tmp_path):
        """A refused later page (in-body error, 4xx: past the end) ends the
        list and wraps the window; a hiccup (5xx) stops paging and retries
        that page next pull. Either way the source stays healthy — a
        stuck window could otherwise trip the breaker every pull."""
        db = DB(tmp_path / "state.db")
        deep = [_wh_row(i, "B0DEP") for i in range(3)] + \
            [_junk_row(i, "B0DDD") for i in range(1500)]
        refused = httpx.Response(200, json={"error": {"type": "page"}})
        fake = FakeKeepa(deep=deep, fail={("deep", 1): refused})
        adapter = _adapter(fake, cfg)
        posts, ok, _ = run_source(SRC, adapter, None, db, cfg, NOW)
        assert ok and len(posts) == 3
        assert db.meta_get("keepa_deep_page") == "0"
        fake.fail = {("deep", 1): httpx.Response(503, text="busy")}
        posts, ok, _ = run_source(SRC, adapter, None, db, cfg,
                                  NOW + timedelta(minutes=10))
        assert ok and db.meta_get("keepa_deep_page") == "1"
        # A window left past the end of a shrunken list wraps, not fails.
        db.meta_set("keepa_deep_page", "7")
        fake.fail = {("deep", 7): refused}
        posts, ok, _ = run_source(SRC, adapter, None, db, cfg,
                                  NOW + timedelta(minutes=20))
        assert ok and db.meta_get("keepa_deep_page") == "0"
        health = {r["source_name"]: r for r in db.all_source_health()}
        assert health[SRC.name]["consecutive_failures"] == 0
        db.close()


#: The live wart: a $5.70 open-box phone case "90% off" a $58.95 lone
#: marketplace NEW offer; its 90-day average new price is $12.99.
BURGA = {"asin": "B0BURGA001", "title": "BURGA Phone Case (open box)",
         "current": [-1, 5895, -1, -1, -1, -1, -1, -1, -1, 570],
         "avg": [[-1, 1450], [-1, 1320], [-1, 1305], [-1, 1299]]}


def _wh(row: dict, **kw):
    return KeepaDealsAdapter._map_warehouse_row(row, SRC, NOW, **kw)


class TestWarehouseNewPriceBasis:
    def test_amazon_own_price_is_the_basis_while_nobody_undercuts_it(self):
        row = {"asin": "B0AMZBASIS", "title": "Robot Vacuum",
               "current": [8155, 8155, -1, -1, -1, -1, -1, -1, -1, 1207],
               "avg": [[8000, 3600], [8100, 3550], [8150, 3500],
                       [8155, 3500]]}
        p = _wh(row)
        assert "New condition price: $81.55." in p.text
        assert "New price basis: Amazon's own new price." in p.text

    def test_a_cheaper_new_offer_beats_amazons_own_price(self):
        """Amazon $100 while others sell it new for $60: a shopper pays
        $60, so $45 used is 25% off, not 55% (it never maps); $25 used is
        a real 58% off the $60 offer."""
        row = {"asin": "B0UNDERCUT", "title": "Espresso Machine",
               "current": [10000, 6000, -1, -1, -1, -1, -1, -1, -1, 4500],
               "avg": [[-1, 6200]] * 4}
        assert _wh(row) is None
        p = _wh(dict(row, current=[10000, 6000] + [-1] * 7 + [2500]))
        assert "yours for $25.00." in p.text
        assert "New condition price: $60.00." in p.text
        assert "New price basis: lowest new price (current offer)." in p.text
        assert "100.00" not in p.text

    def test_burga_case_uses_the_90_day_average_new_price(self, cfg,
                                                          monkeypatch):
        p = _wh(BURGA, min_new_price=10.0)
        assert p is not None and p.uid == "B0BURGA001:wh:5.70"
        assert "yours for $5.70." in p.text
        assert "New condition price: $12.99." in p.text
        assert "New price basis: lowest new price (90-day average)." in p.text
        assert "58.95" not in p.text  # the inflated lone offer is gone
        assert round(100 * (1 - 5.70 / 12.99)) == 56  # >= 45: mapped
        # At the default $25 value floor the honest basis ($12.99) shows
        # it for the low-value item it is: never mapped.
        assert cfg.keepa_warehouse_min_new_price == 25.0
        assert _wh(BURGA,
                   min_new_price=cfg.keepa_warehouse_min_new_price) is None
        # The knob reaches the adapter.
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        cfg.keepa_warehouse_min_new_price = 10.0
        posts = _adapter(FakeKeepa(deep=[BURGA]), cfg).fetch_posts(
            SRC, None, None, cfg, NOW, None)
        assert [q.uid for q in posts] == ["B0BURGA001:wh:5.70"]

    def test_lone_offer_with_a_long_inflated_history_is_no_basis(self):
        """One marketplace seller at $58.95 for 90+ days: a dense history
        of its own inflated price. The new-offer count (current[11]) says
        it is alone -> no basis; with real competition it is one."""
        lone = dict(BURGA, current=[-1, 5895] + [-1] * 7 + [570, -1, 1],
                    avg=[[-1, 5895]] * 4)
        assert _wh(lone, min_new_price=10.0) is None
        crowd = dict(lone, current=[-1, 5895] + [-1] * 7 + [570, -1, 4])
        p = _wh(crowd, min_new_price=10.0)
        assert "New condition price: $58.95." in p.text

    def test_lone_third_party_offer_without_history_is_no_basis(self):
        lone = {k: v for k, v in BURGA.items() if k != "avg"}
        assert _wh(lone, min_new_price=10.0) is None
        sparse = dict(BURGA, avg=[[-1, -1], [-1, -1], [-1, -1], [-1, 1299]])
        assert _wh(sparse, min_new_price=10.0) is None
        nothing_new = dict(BURGA, current=[-1] * 9 + [570])
        assert _wh(nothing_new, min_new_price=1.0) is None

    def test_current_offer_under_its_average_is_the_basis(self):
        row = {"asin": "B0OFFERLOW", "title": "Stand Mixer",
               "current": [-1, 3000, -1, -1, -1, -1, -1, -1, -1, 1000],
               "avg": [[-1, 5200], [-1, 5100], [-1, 5000], [-1, 4999]]}
        p = _wh(row)
        assert "New condition price: $30.00." in p.text
        assert "New price basis: lowest new price (current offer)." in p.text

    def test_drop_gate_is_a_knob(self):
        row = {"asin": "B0GATEKNOB", "title": "Blender",
               "current": [10000, 10000, -1, -1, -1, -1, -1, -1, -1, 5200]}
        assert _wh(row) is not None                  # 48% off clears 45
        assert _wh(row, min_drop_pct=50.0) is None


class TestKeepaContentHash:
    """An unchanged offer hashes identically pull after pull: Keepa's
    time-weighted averages drift by cents and must not re-parse it."""

    @staticmethod
    def _amazon(current, avg):
        return KeepaDealsAdapter._map_row(
            {"asin": "B0HASHTEST", "title": "Mega Blender",
             "current": [current], "avg": [avg]}, SRC, NOW)

    def test_amazon_average_drift_keeps_the_hash(self):
        a, b = self._amazon(2999, 14999), self._amazon(2999, 15003)
        assert "Regular price (90-day average on Amazon): $150.03." in b.text
        assert a.content_hash == b.content_hash
        # A real move re-parses: a new price, or a new whole-percent drop.
        assert self._amazon(2899, 14999).content_hash != a.content_hash
        assert self._amazon(2999, 9999).content_hash != a.content_hash

    def test_warehouse_average_basis_drift_keeps_the_hash(self):
        drifted = dict(BURGA, avg=[[-1, 1450], [-1, 1320], [-1, 1305],
                                   [-1, 1301]])
        a, b = _wh(BURGA, min_new_price=10), _wh(drifted, min_new_price=10)
        assert "New condition price: $13.01." in b.text
        assert a.content_hash == b.content_hash
        assert _wh(BURGA, min_new_price=10).content_hash == a.content_hash


class TestKeepaWootConfig:
    def test_knobs_load_with_defaults_when_absent(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text("publish:\n  min_pct: 40\n")
        c = load_config(path)
        assert (c.keepa_warehouse_pages_per_pull, c.keepa_amazon_pages_per_pull,
                c.keepa_reserve_tokens,
                c.keepa_warehouse_max_new_posts_per_pull,
                c.keepa_amazon_max_new_posts_per_pull,
                c.keepa_warehouse_min_drop_pct,
                c.keepa_warehouse_min_new_price,
                c.keepa_sweep_enabled, c.keepa_sweep_asins_per_pull,
                c.keepa_sweep_reserve_tokens,
                c.woot_max_items_per_run, c.woot_max_new_items_per_run) == (
            12, 6, 300, 20, 10, 45, 25, True, 100, 400, 300, 20)

    def test_knobs_load_from_their_sections(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(
            "keepa:\n  warehouse_pages_per_pull: 20\n"
            "  amazon_pages_per_pull: 0\n  reserve_tokens: 500\n"
            "  warehouse_max_new_posts_per_pull: 90\n"
            "  amazon_max_new_posts_per_pull: 30\n  warehouse_min_drop_pct: 50\n"
            "  warehouse_min_new_price: 40\n  sweep_enabled: false\n"
            "  sweep_asins_per_pull: 50\n  sweep_reserve_tokens: 250\n"
            "woot:\n  max_items_per_run: 120\n  max_new_items_per_run: 7\n")
        c = load_config(path)
        assert (c.keepa_warehouse_pages_per_pull, c.keepa_amazon_pages_per_pull,
                c.keepa_reserve_tokens,
                c.keepa_warehouse_max_new_posts_per_pull,
                c.keepa_amazon_max_new_posts_per_pull,
                c.keepa_warehouse_min_drop_pct,
                c.keepa_warehouse_min_new_price,
                c.keepa_sweep_enabled, c.keepa_sweep_asins_per_pull,
                c.keepa_sweep_reserve_tokens,
                c.woot_max_items_per_run, c.woot_max_new_items_per_run) == (
            20, 1, 500, 90, 30, 50, 40, False, 50, 250, 120, 7)

    def test_first_run_burst_is_bounded_by_the_shipped_config(self, cfg):
        """The release blocker: the first pulls after a deploy re-hash every
        Keepa post and select a new Woot list (~510 new posts at the old
        caps; the fast run's untimed tail then overran its 12-minute job).
        The shipped caps admit at most 80 NEW posts per run from Keepa and
        Woot together (live: 75 posts parsed in ~80 s once link resolution
        stopped sitting in store backoff); the backlog arrives later."""
        burst = (cfg.keepa_amazon_max_new_posts_per_pull
                 + cfg.keepa_warehouse_max_new_posts_per_pull
                 + cfg.woot_max_new_items_per_run)
        assert burst <= 80


class TestFirstRunBurst:
    """The release blocker: the first pulls after a deploy re-hash every
    Keepa post. Each lane emits at most its cap of NEW posts per pull;
    posts the pipeline already handled ride free; the rest arrive later."""

    @pytest.fixture(autouse=True)
    def _key(self, monkeypatch):
        monkeypatch.setenv("KEEPA_KEY", "kkey")

    def test_new_posts_are_capped_and_the_rest_arrive_on_later_pulls(
            self, cfg, tmp_path):
        cfg.keepa_amazon_max_new_posts_per_pull = 10
        cfg.keepa_warehouse_max_new_posts_per_pull = 20
        db = DB(tmp_path / "state.db")
        fake = FakeKeepa(amazon=[_amazon_row(i) for i in range(300)],
                         deep=[_wh_row(i, "B0DEP") for i in range(300)])
        adapter = _adapter(fake, cfg)
        emitted, new_split, handled = [], [], set()
        for n in range(3):
            posts = adapter.fetch_posts(SRC, None, db, cfg,
                                        NOW + timedelta(minutes=10 * n), None)
            new = [p for p in posts if not db.post_is_known(p.content_hash)]
            assert not handled & {p.uid for p in new}
            handled |= {p.uid for p in new}
            emitted.append(len(posts))
            new_split.append((sum(":wh:" not in p.uid for p in new),
                              sum(":wh:" in p.uid for p in new)))
            for p in new:  # what the pipeline records once it parsed them
                db.record_post(p, NOW)
        assert new_split == [(10, 20)] * 3      # never more than the caps
        assert emitted == [30, 60, 90]          # handled posts ride free
        # DEEP page 0 still holds new rows: the window stays on it.
        assert db.meta_get("keepa_deep_page") == "0"
        db.close()


class TestPagingFindings:
    @pytest.fixture(autouse=True)
    def _key(self, monkeypatch):
        monkeypatch.setenv("KEEPA_KEY", "kkey")

    def test_deep_first_page_keeps_the_reserve(self, cfg, tmp_path):
        """Only the Amazon and FRESH first pages may run under the reserve:
        near an empty bucket a pull spends 10 tokens, not 15."""
        db = DB(tmp_path / "state.db")
        fake = FakeKeepa(amazon=[_junk_row(i, "B0AAA") for i in range(900)],
                         fresh=[_junk_row(i, "B0FFF") for i in range(900)],
                         deep=[_junk_row(i, "B0DDD") for i in range(900)],
                         tokens=125)
        _adapter(fake, cfg).fetch_posts(SRC, None, db, cfg, NOW, None)
        assert fake.calls == [("amazon", 0), ("fresh", 0)]
        assert json.loads(db.meta_get(META_KEY))["tokens_left"] == 115
        assert db.meta_get("keepa_deep_page") is None
        db.close()

    def test_a_capped_deep_page_is_read_again_until_every_row_mapped(
            self, cfg, tmp_path):
        """The window never moves past rows the cap held back: 450
        mappable deep rows, 100 new posts a pull, every row mapped."""
        cfg.keepa_warehouse_max_new_posts_per_pull = 100
        db = DB(tmp_path / "state.db")
        rows = [_wh_row(i, "B0DEP") for i in range(450)]
        fake = FakeKeepa(deep=rows)
        adapter = _adapter(fake, cfg)
        mapped: set[str] = set()
        cursors = []
        for n in range(5):
            posts = adapter.fetch_posts(SRC, None, db, cfg,
                                        NOW + timedelta(minutes=10 * n), None)
            for p in posts:
                mapped.add(p.uid.split(":")[0])
                db.record_post(p, NOW)
            cursors.append(db.meta_get("keepa_deep_page"))
        assert cursors == ["0", "1", "2", "2", "0"]
        assert mapped == {r["asin"] for r in rows}
        db.close()

    def test_rotating_windows_lead_the_emitted_posts(self, cfg):
        """A heavy run's parse may stop early; the posts left over must be
        ones re-read next pull (FRESH, Amazon), not DEEP ones."""
        fake = FakeKeepa(amazon=[_amazon_row(i) for i in range(2)],
                         fresh=[_wh_row(i, "B0FRS") for i in range(2)],
                         deep=[_wh_row(i, "B0DEP") for i in range(2)])
        posts = _adapter(fake, cfg).fetch_posts(SRC, None, None, cfg, NOW,
                                                None)
        assert [p.uid[:5] for p in posts] == (["B0DEP"] * 2 + ["B0FRS"] * 2
                                              + ["B0AMZ"] * 2)

    def test_time_budget_stops_paging_after_the_first_request(self, cfg,
                                                               tmp_path):
        """Inside the last request_timeout_s of the per-source budget no
        further page starts: a clean end (keepa_last_fetch_at set), the
        DEEP window left on its unfetched page."""
        db = DB(tmp_path / "state.db")
        db.meta_set("keepa_deep_page", "3")
        fake = FakeKeepa(amazon=[_junk_row(i, "B0AAA") for i in range(900)],
                         fresh=[_junk_row(i, "B0FFF") for i in range(900)],
                         deep=[_junk_row(i, "B0DDD") for i in range(900)],
                         tokens=1200)
        deadline = time.monotonic() + cfg.request_timeout_s / 2
        _adapter(fake, cfg).fetch_posts(SRC, None, db, cfg, NOW, deadline)
        assert fake.calls == [("amazon", 0)]
        assert db.meta_get("keepa_last_fetch_at") is not None
        assert db.meta_get("keepa_deep_page") == "3"
        db.close()


class TestFloorDriftHash:
    def test_a_drift_across_a_publish_floor_re_parses(self, cfg,
                                                     monkeypatch):
        """The hash keeps an unchanged deal stable, but a 90-day average
        drifting a stored 40.3% to an honest 39.8% must re-parse (or it
        stays published at the 40% floor)."""
        floors = (40.0, 50.0)

        def amazon(avg, cur=5970, fl=floors):
            return KeepaDealsAdapter._map_row(
                {"asin": "B0FLOORHSH", "title": "Air Fryer",
                 "current": [cur], "avg": [avg]}, SRC, NOW, floors=fl)

        at_403 = amazon(10000)
        assert amazon(9920).content_hash != at_403.content_hash   # 39.8%
        assert amazon(10005).content_hash == at_403.content_hash  # 40.33%
        # A floor that is no whole percent: only the floor marker moves.
        assert (amazon(10000, 5720, (42.5,)).content_hash      # 42.8%
                != amazon(9900, 5720, (42.5,)).content_hash)   # 42.2%
        assert (amazon(10000, 5720, (42.5,)).content_hash
                == amazon(9990, 5720, (42.5,)).content_hash)   # 42.7%
        # Warehouse rows against the used floor, on a 90-day-average basis.
        row = {"asin": "B0WHFLOOR1", "title": "Stand Mixer",
               "current": [-1, 12000] + [-1] * 7 + [4970, -1, 5],
               "avg": [[-1, 10000]] * 4}
        drifted = dict(row, avg=[[-1, 10000]] * 3 + [[-1, 9920]])
        assert (_wh(row, floors=floors).content_hash          # 50.3%
                != _wh(drifted, floors=floors).content_hash)  # 49.9%
        # The adapter hashes against the configured publish floors.
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        fake = FakeKeepa(amazon=[{"asin": "B0FLOORHSH", "title": "Air Fryer",
                                  "current": [5970], "avg": [10000]}])
        posts = _adapter(fake, cfg).fetch_posts(SRC, None, None, cfg, NOW,
                                                None)
        assert posts[0].content_hash == at_403.content_hash


def _product(asin: str, *, amazon=-1, new=-1, wh=-1, offers=None,
             avg90_new=-1, image="71sweepIMG.jpg") -> dict:
    """A Keepa /product record (stats=1): ``stats.current`` by price type,
    ``stats.avg90`` flat by price type, the photo in ``images``."""
    current = [amazon, new] + [-1] * 7 + [wh]
    if offers is not None:
        current += [-1, offers]
    return {"asin": asin, "title": f"Sweep item {asin}",
            "stats": {"current": current, "avg90": [-1, avg90_new]},
            "images": [{"l": image, "m": "m" + image}]}


class TestWarehouseSweep:
    """Open-box items far under the NEW price whose warehouse price never
    moved never show up on /deal: the product finder + /product sweep
    finds them, on idle tokens only."""

    @pytest.fixture(autouse=True)
    def _key(self, monkeypatch):
        monkeypatch.setenv("KEEPA_KEY", "kkey")

    @staticmethod
    def _pull(fake, cfg, db, n=0):
        return _adapter(fake, cfg).fetch_posts(
            SRC, None, db, cfg, NOW + timedelta(minutes=10 * n), None)

    def test_finder_request_shape_and_honest_mapping(self, cfg, tmp_path,
                                                     caplog):
        db = DB(tmp_path / "state.db")
        products = {
            # $12.07 vs Amazon's own $81.55: maps.
            "B0SWAMZ001": _product("B0SWAMZ001", amazon=8155, new=8155,
                                   wh=1207),
            # Amazon $100 but a $60 new offer: $45 is 25% off -> skipped.
            "B0SWUNDER1": _product("B0SWUNDER1", amazon=10000, new=6000,
                                   wh=4500, offers=6),
            # A lone marketplace offer, however long its history -> skipped.
            "B0SWLONE01": _product("B0SWLONE01", new=5895, wh=570,
                                   offers=1, avg90_new=5895),
            # $15 vs the $40 90-day average new price: maps.
            "B0SWAVG001": _product("B0SWAVG001", new=5000, wh=1500,
                                   offers=3, avg90_new=4000),
            # Honest new price under the $25 value floor -> skipped.
            "B0SWCHEAP1": _product("B0SWCHEAP1", amazon=1800, new=1800,
                                   wh=300),
        }
        fake = FakeKeepa(tokens=1200, finder=list(products),
                         products=products)
        with caplog.at_level("INFO", logger="dealradar.fetch.keepa"):
            posts = self._pull(fake, cfg, db)
        assert fake.calls == [("amazon", 0), ("fresh", 0), ("deep", 0),
                              ("query", 0), ("product", 5)]
        assert fake.finder_sels == [{
            "current_WAREHOUSE_gte": 1, "current_NEW_gte": 2500,
            "current_SALES_gte": 1, "current_SALES_lte": 200000,
            "current_RATING_gte": 20,
            "categories_exclude": [283155, 133140011],
            "sort": [["current_SALES", "asc"]], "perPage": 100, "page": 0}]
        params = fake.product_params[0]
        assert (params["key"], params["domain"], params["stats"]) == \
            ("kkey", "1", "1")
        assert params["asin"] == ",".join(products)
        by_asin = {p.uid.split(":")[0]: p for p in posts}
        assert set(by_asin) == {"B0SWAMZ001", "B0SWAVG001"}
        # The same post a /deal row with the same prices makes.
        amz = by_asin["B0SWAMZ001"]
        twin = _wh({"asin": "B0SWAMZ001", "title": "Sweep item B0SWAMZ001",
                    "current": products["B0SWAMZ001"]["stats"]["current"]},
                   floors=(40.0, 50.0))
        assert (amz.uid, amz.text, amz.content_hash) == \
            (twin.uid, twin.text, twin.content_hash)
        assert amz.image_url == \
            "https://m.media-amazon.com/images/I/71sweepIMG.jpg"
        avg = by_asin["B0SWAVG001"]
        assert "yours for $15.00." in avg.text
        assert "New condition price: $40.00." in avg.text
        assert "New price basis: lowest new price (90-day average)." in avg.text
        assert db.meta_get("keepa_sweep_page") == "0"  # a short page: wrap
        assert (f"keepa sweep: page 0, 5 asins -> 2 mapped "
                f"(tokensLeft={fake.tokens})") in caplog.text
        db.close()

    def test_window_rotates_across_pulls_and_wraps(self, cfg, tmp_path):
        db = DB(tmp_path / "state.db")
        fake = FakeKeepa(tokens=5000,
                         finder=[f"B0ROT{i:05d}" for i in range(250)])
        seen = []
        for n in range(4):
            fake.finder_sels.clear()
            self._pull(fake, cfg, db, n)
            seen.append(([s["page"] for s in fake.finder_sels],
                         db.meta_get("keepa_sweep_page")))
        assert seen == [([0], "1"), ([1], "2"), ([2], "0"), ([0], "1")]
        db.close()

    def test_a_page_whose_new_posts_were_held_is_read_again(self, cfg,
                                                          tmp_path):
        cfg.keepa_warehouse_max_new_posts_per_pull = 1
        db = DB(tmp_path / "state.db")
        held = [f"B0HLD0000{i}" for i in range(1, 4)]
        products = {a: _product(a, amazon=8155, new=8155, wh=1207)
                    for a in held}
        fake = FakeKeepa(tokens=5000, products=products,
                         finder=held + [f"B0PAD{i:05d}" for i in range(97)])
        pages = []
        for n in range(3):
            posts = self._pull(fake, cfg, db, n)
            for p in posts:
                db.record_post(p, NOW)
            pages.append((sorted(p.uid[:10] for p in posts),
                          db.meta_get("keepa_sweep_page")))
        assert pages == [(held[:1], "0"), (held[:2], "0"), (held, "1")]
        db.close()

    def test_runs_only_on_idle_tokens(self, cfg, tmp_path):
        """reserve_tokens (300) + sweep_reserve_tokens (400) must still be
        in the bucket after the estimated finder (11) + product (100)
        cost: the sweep never starves rechecks or the deal pages."""
        finder = [f"B0IDL{i:05d}" for i in range(100)]
        # The deal pages cost 15: 826 leaves 811 = 700 + 111.
        for start, swept in ((825, False), (826, True)):
            db = DB(tmp_path / f"s{start}.db")
            fake = FakeKeepa(tokens=start, finder=finder)
            self._pull(fake, cfg, db)
            assert (("query", 0) in fake.calls) is swept
            if swept:
                assert fake.tokens == 700  # never below the two reserves
            db.close()
        # The product check is re-gated on the finder's own reading.
        db = DB(tmp_path / "drained.db")
        fake = FakeKeepa(tokens=826, finder=finder)
        fake.costs["query"] = 50  # something else drained the bucket
        self._pull(fake, cfg, db)
        assert ("query", 0) in fake.calls
        assert not [c for c in fake.calls if c[0] == "product"]
        assert db.meta_get("keepa_sweep_page") is None  # same page next pull
        db.close()
        # An unknown bucket is not an idle one.
        fake = FakeKeepa(finder=finder)
        self._pull(fake, cfg, None)
        assert ("query", 0) not in fake.calls

    def test_skips_asins_the_deal_pages_already_saw(self, cfg, tmp_path):
        db = DB(tmp_path / "state.db")
        products = {a: _product(a, amazon=8155, new=8155, wh=1207)
                    for a in ("B0DUP00001", "B0NEW00001")}
        fake = FakeKeepa(tokens=1200, deep=[_wh_row(1, "B0DUP")],
                         finder=list(products), products=products)
        posts = self._pull(fake, cfg, db)
        assert fake.product_params[0]["asin"] == "B0NEW00001"
        # One post per ASIN, the DEEP window's first, then the sweep's.
        assert [p.uid for p in posts] == ["B0DUP00001:wh:12.07",
                                          "B0NEW00001:wh:12.07"]
        db.close()

    def test_429_defers_the_sweep_without_failing_the_pull(self, cfg,
                                                          tmp_path):
        db = DB(tmp_path / "state.db")
        db.meta_set("keepa_sweep_page", "2")
        too_many = httpx.Response(429, json={
            "tokensLeft": -1, "refillIn": 3000, "refillRate": 20})
        fake = FakeKeepa(tokens=1200, amazon=[_amazon_row(0)],
                         finder=[f"B0DEF{i:05d}" for i in range(400)],
                         fail={("query", 2): too_many})
        adapter = _adapter(fake, cfg)
        posts, ok, error = run_source(SRC, adapter, None, db, cfg, NOW)
        assert (ok, error) == (True, "")
        assert [p.uid for p in posts] == ["B0AMZ00000:10.00"]
        assert db.meta_get("keepa_sweep_page") == "2"     # same page next pull
        assert db.meta_get("keepa_last_fetch_at") is not None  # deal pages done
        assert json.loads(db.meta_get(META_KEY))["tokens_left"] == -1
        # A 429 on the product check defers the same way.
        fake.fail, fake.tokens = {("product", None): too_many}, 1200
        posts, ok, error = run_source(SRC, adapter, None, db, cfg,
                                      NOW + timedelta(minutes=10))
        assert (ok, error) == (True, "") and ("product", 100) in fake.calls
        assert db.meta_get("keepa_sweep_page") == "2"
        health = {r["source_name"]: r for r in db.all_source_health()}
        assert health[SRC.name]["consecutive_failures"] == 0
        db.close()

    def test_refused_later_page_wraps_and_a_hiccup_retries(self, cfg,
                                                           tmp_path):
        db = DB(tmp_path / "state.db")
        db.meta_set("keepa_sweep_page", "7")
        refused = httpx.Response(200, json={"error": {"type": "page"}})
        fake = FakeKeepa(tokens=5000, finder=["B0X0000001"],
                         fail={("query", 7): refused})
        adapter = _adapter(fake, cfg)
        _, ok, _ = run_source(SRC, adapter, None, db, cfg, NOW)
        assert ok and db.meta_get("keepa_sweep_page") == "0"
        db.meta_set("keepa_sweep_page", "3")
        fake.fail = {("query", 3): httpx.Response(503, text="busy")}
        _, ok, _ = run_source(SRC, adapter, None, db, cfg,
                              NOW + timedelta(minutes=10))
        assert ok and db.meta_get("keepa_sweep_page") == "3"
        db.close()

    def test_disabled_or_a_full_lane_skips_the_sweep(self, cfg, tmp_path):
        finder = [f"B0SKP{i:05d}" for i in range(100)]
        cfg.keepa_sweep_enabled = False
        db = DB(tmp_path / "a.db")
        fake = FakeKeepa(tokens=1200, finder=finder)
        self._pull(fake, cfg, db)
        assert ("query", 0) not in fake.calls
        db.close()
        cfg.keepa_sweep_enabled = True
        cfg.keepa_warehouse_max_new_posts_per_pull = 1
        db = DB(tmp_path / "b.db")
        fake = FakeKeepa(tokens=1200, finder=finder,
                         deep=[_wh_row(0, "B0DEP")])
        posts = self._pull(fake, cfg, db)
        assert len(posts) == 1 and ("query", 0) not in fake.calls
        db.close()
