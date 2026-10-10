"""Offline tests: Keepa deals adapter, Keepa verify cross-check, daily spend cap."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from dealradar import verify
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.errors import AdapterNotConfigured
from dealradar.fetch.keepa import MAX_DEALS_PER_RUN, KeepaDealsAdapter
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
            # Popularity filters: only products people actually buy.
            assert sel["salesRankRange"] == [1, 30000]
            assert sel["minRating"] == 20
            if sel["priceTypes"] == [0]:  # amazon-price pass
                return httpx.Response(
                    200, json={"deals": {"dr": [row]}, "tokensLeft": 280})
            assert sel["priceTypes"] == [9]  # warehouse pass
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

        def handler(request: httpx.Request) -> httpx.Response:
            sel = json.loads(request.url.params["selection"])
            if sel["priceTypes"] == [0]:
                return httpx.Response(200, json={"deals": {"dr": []}})
            return httpx.Response(
                200, json={"deals": {"dr": [deep, shallow, no_new]}})

        adapter = KeepaDealsAdapter(http_client=_mock_client(handler, cfg))
        posts = adapter.fetch_posts(SRC, None, None, cfg, NOW, None)
        # Only the deep drop maps: 26% off and no-live-new-price are skipped.
        assert len(posts) == 1
        p = posts[0]
        assert p.uid == "B0WAREHOUSE:wh:12.07"
        assert "yours for $12.07" in p.text
        assert "New condition price: $81.55" in p.text
        assert "used/open-box" in p.text
        assert "https://www.amazon.com/dp/B0WAREHOUSE" in p.text

    def test_in_body_api_error_raises_a_source_failure(self, cfg, monkeypatch):
        monkeypatch.setenv("KEEPA_KEY", "kkey")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "error": {"type": "parameter", "message": "bad selection"}})

        adapter = KeepaDealsAdapter(http_client=_mock_client(handler, cfg))
        with pytest.raises(RuntimeError, match="keepa api error"):
            adapter.fetch_posts(SRC, None, None, cfg, NOW, None)


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
