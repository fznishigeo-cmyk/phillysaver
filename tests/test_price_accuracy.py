"""Price accuracy on the 20 token/min Keepa plan (operator, 2026-10-13):
the site showed Amazon Essentials leggings at $3.65, -77% vs $16.02, while
Amazon showed $4.11 with "Typical price: $6.01" (-32%)."""

from __future__ import annotations

import json
from datetime import timedelta

import httpx

from dealradar import pipeline, verify
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.fetch.keepa import KeepaDealsAdapter
from dealradar.publish.site import PICKS_META
from tests.http_helpers import mock_keepa_get
from tests.test_keepa import SRC
from tests.test_quality_trio import NOW, _seed_amazon


def _leggings(month: int, ninety: int):
    # avg is [day, week, month, 90-day] x [priceType]; index 0 = Amazon.
    return KeepaDealsAdapter._map_row(
        {"asin": "B0LEGGINGS", "title": "Amazon Essentials Women's Leggings",
         "current": [365], "avg": [[380], [450], [month], [ninety]]}, SRC, NOW)


def test_regular_price_is_the_lower_of_the_30_and_90_day_averages():
    post = _leggings(601, 1602)
    assert "Regular price (30-day average on Amazon): $6.01." in post.text
    assert post.structured["regular_price"] == 6.01
    assert post.structured["regular_price_basis"] == "30-day average on Amazon"
    assert post.structured["evidence"]["regular_price_quote"] in post.text
    # A month above the 90-day average leaves the 90-day basis (and its
    # hash wording) exactly as before.
    steady = _leggings(1700, 1602)
    assert "Regular price (90-day average on Amazon): $16.02." in steady.text
    assert steady.structured["regular_price_basis"] == "90-day average on Amazon"


def test_no_drop_against_the_30_day_average_is_no_post():
    assert _leggings(360, 1602) is None


def _product(live_cents: int) -> dict:
    return {"asin": "B0LEGGINGS", "lastUpdate": 99999999,
            "stats": {"current": [live_cents, -1]}}


def test_live_price_tolerance_is_a_config_knob():
    deal = {"asin": "B0LEGGINGS", "final_price": 3.65, "conditions": [],
            "first_seen_at": "2026-10-10T03:00:00+00:00"}
    assert load_config().keepa_recheck_tolerance_pct == 5
    assert verify.judge_live_price(deal, _product(411), 5) is False
    assert verify.judge_live_price(deal, _product(411)) is False  # 10% too
    assert verify.judge_live_price(deal, _product(383), 5) is True
    assert verify.judge_live_price(deal, _product(395), 5) is False
    assert verify.judge_live_price(deal, _product(395), 10) is True


def test_published_picks_are_rechecked_hourly_and_first(tmp_path, monkeypatch):
    db = DB(tmp_path / "state.db")
    seen = (NOW - timedelta(hours=2)).isoformat()
    last = (NOW - timedelta(hours=1, minutes=5)).isoformat()
    for key in ("url|pick", "url|plain", "url|usedpick"):
        _seed_amazon(db, key, "B0" + key[4:].upper().ljust(8, "0"),
                     used=key == "url|usedpick", last=last, first_seen_at=seen)
    _seed_amazon(db, "url|never", "B0NEVER000", first_seen_at=seen)
    db.meta_set(PICKS_META, json.dumps(["url|pick", "url|usedpick"]))
    cfg = load_config()
    cfg.keepa_click_price_rule = "off"  # recorded before task 275
    # Predates the Buy Box top picks and surplus scheduling.
    cfg.keepa_buybox_top_n = 0
    cfg.keepa_recheck_surplus = cfg.keepa_recheck_top_live_adaptive = False
    cfg.keepa_volatility["enabled"] = False  # legacy visibility cadence
    cfg.feature_flags["keepa"] = True
    assert cfg.keepa_recheck_top_h == .75
    calls = []

    def fake_recheck(deals, _cfg, budget=None, used_offers=True, fetcher=None, live=False):
        calls.append([d["dedupe_key"] for d in deals])
        return {d["dedupe_key"]: True for d in deals}

    monkeypatch.setattr(verify, "recheck_amazon_prices", fake_recheck)
    pipeline._recheck_prices(db, cfg, NOW)
    # The new-condition pick is due after an hour and leads the never-
    # checked deal; a non-pick waits recheck_fresh_h, a used pick its
    # offers-scan cadence (13 tokens each).
    assert calls == [["url|pick"], ["url|never"]]
    cfg.keepa_recheck_top_h = 0
    calls.clear()
    pipeline._recheck_prices(db, cfg, NOW + timedelta(hours=1, minutes=5))
    # Zero disables cadence acceleration, not the adaptive hard deadline.
    assert calls == [["url|pick"]]
    cfg.keepa_recheck_adaptive["enabled"] = False
    calls.clear()
    pipeline._recheck_prices(db, cfg, NOW + timedelta(hours=2, minutes=10))
    assert calls == []
    db.close()


def _priced(current: int, listed: int, month: int, ninety: int):
    # current is indexed by Keepa price type: 0 = Amazon, 4 = List Price.
    return KeepaDealsAdapter._map_row(
        {"asin": "B0TIDEPODS", "title": "Tide Pods Laundry Detergent",
         "current": [current, -1, -1, -1, listed],
         "avg": [[current], [2300], [month], [ninety]]}, SRC, NOW)


def test_list_price_amazon_really_sold_at_is_the_regular_price():
    # Amazon's page: $22.53, "List Price: $24.94", -10%.
    post = _priced(2253, 2494, 2350, 2300)
    assert "Regular price (List Price on Amazon): $24.94." in post.text
    assert post.structured["regular_price"] == 24.94
    assert post.structured["regular_price_basis"] == "List Price on Amazon"
    assert post.structured["evidence"]["regular_price_quote"] in post.text


def test_list_price_the_item_never_sold_near_is_ignored():
    # The leggings: a $22 list price, but Amazon's 90-day average ($16.02)
    # never came near it; Amazon showed "Typical price: $6.01".
    post = _priced(365, 2200, 601, 1602)
    assert "Regular price (30-day average on Amazon): $6.01." in post.text
    assert "List Price" not in post.text
    # No list price at all: the averages, as before.
    assert "List Price" not in _priced(2253, -1, 2350, 2300).text
    assert load_config().keepa_list_price_min_avg_ratio == 0.9


def test_rechecks_skip_price_history_and_flag_a_cut_off_answer(monkeypatch, caplog):
    # Live 2026-10-14: a 62-ASIN recheck passed the 5 MB body cap; Keepa
    # charged for every ASIN and no deal got an answer, silently.
    seen = []

    def get(url, params=None, timeout=None):
        seen.append(params)
        return httpx.Response(200, content=b'{"products": [{"asin": "B0LEG',
                              request=httpx.Request("GET", url))

    mock_keepa_get(monkeypatch, get)
    with caplog.at_level("WARNING", logger="dealradar.verify"):
        assert verify.keepa_products("k", ["B0LEGGINGS"], 1, None) == {}
    assert seen[0]["history"] == "0"  # no history arrays: same tokens, small answer
    assert "keepa product x1: unreadable answer" in caplog.text
