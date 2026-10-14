"""093 measurements retained by 095; the interrupted idle ordering is superseded."""

from datetime import timedelta

import httpx
import pytest

from dealradar import pipeline, util, verify
from dealradar.keepa_budget import KeepaBudget
from dealradar.keepa_rechecks import History
from dealradar.publish import site
from tests.http_helpers import mock_keepa_get
from tests.test_price_accuracy_engine import NOW, db, product, seed
from tests.test_value_per_token_rechecks import cfg, minutes
from tests.test_wall_confirmation_coverage import remember


def transport(monkeypatch, clock, *, used_lag=0, fail=False):
    monkeypatch.setenv("KEEPA_KEY", "offline")
    monkeypatch.setattr(util, "now_utc", lambda: clock[0])
    calls = []

    def get(url, params=None, **kwargs):
        assert url == "https://api.keepa.com/product"
        assert str(params["history"]) == "0"
        used, live = "offers" in params, "update" in params
        assert not used or not live  # used wall scans never force update=0
        asins = params["asin"].split(",")
        calls.append((asins, used, live))
        if fail:
            return httpx.Response(429, json={"tokensLeft": 1100, "refillRate": 20})
        records = []
        for asin in asins:
            record = product(2500, asin=asin)
            record["lastUpdate"] = minutes(clock[0])
            if used:
                at = minutes(clock[0] - timedelta(hours=used_lag))
                record.update(offers=[{"condition": 2, "offerCSV": [at, 2500, 0]}],
                              liveOffersOrder=[0], offersSuccessful=True)
                record["stats"]["lastOffersUpdate"] = at
            records.append(record)
        return httpx.Response(200, json={"products": records,
            "tokensConsumed": len(asins) * ((verify.USED_RECHECK_TOKENS if used else 1) + live)})

    mock_keepa_get(monkeypatch, get)
    return calls


def test_wall_request_age_by_condition_survives_final_publish(db, cfg, tmp_path):
    new = seed(db, cfg, price_rechecked_at=util.iso(NOW))
    used = seed(db, cfg, asin="B000000002", conditions=["used"],
                price_rechecked_at=util.iso(NOW - timedelta(hours=3)))
    never = seed(db, cfg, asin="B000000003", conditions=["used"],
                 keepa_warehouse_seen_at=util.iso(NOW), keepa_price_seen_at=util.iso(NOW))
    lost = seed(db, cfg, asin="B000000004", conditions=["open_box"],
                price_rechecked_at=util.iso(NOW))
    rows = [new, used, never, lost]
    remember(db, top=[new], wall=rows)
    history = History(db, cfg, NOW - timedelta(minutes=30))
    history.record(new, product(2500), True, NOW, "top_new", 2, 0)
    history.record(used, product(2500), True, NOW - timedelta(hours=3), "wall_used", 13, 0)
    history.record(lost, None, None, NOW, "wall_used", 13, 13)
    cfg.feature_flags["keepa"] = False
    measured = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)["confirmed_age"]["wall"]
    requests = measured["request_age"]
    assert requests["amazon"] == 4 and requests["unrequested"] == 1
    assert requests["within_2h"] == .75
    assert requests["new"]["within_45m"] == 1
    assert requests["used"]["within_2h"] == pytest.approx(2 / 3)
    assert requests["used"]["unrequested"] == 1  # a free feed is not a paid request
    assert measured["used"]["within_2h"] == pytest.approx(2 / 3)
    rendered = site.render_site(db, cfg, NOW, out_dir=tmp_path)
    assert rendered["confirmed_age"]["wall"]["request_age"] == requests
    assert "request_age" not in (tmp_path / "deals.json").read_text()


def test_recent_used_request_does_not_refresh_keepa_offer_clock(db, cfg, monkeypatch):
    deal = seed(db, cfg, conditions=["used"],
                price_rechecked_at=util.iso(NOW - timedelta(hours=5)))
    remember(db, wall=[deal])
    transport(monkeypatch, [NOW], used_lag=3)
    KeepaBudget(1200, 20, NOW).save(db)
    ages = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)["confirmed_age"]["wall"]
    assert ages["used"]["within_2h"] == 0
    assert ages["request_age"]["used"]["within_45m"] == 1
    assert verify.price_confirmed_at(db.get_deal(deal["dedupe_key"])) == NOW - timedelta(hours=3)


def test_request_coverage_excludes_future_dead_and_non_amazon_rows(db, cfg):
    used = seed(db, cfg, conditions=["used"])
    History(db, cfg, NOW + timedelta(minutes=1)).record(used, None, None, NOW, "wall_used", 13, 13)
    history = History(db, cfg, NOW)
    empty = history.request_coverage([])
    assert empty["within_2h"] is empty["new"]["oldest_h"] is empty["used"]["within_1h"] is None
    rows = [used, dict(used, status="dead"), dict(used, store="Target", canonical_url="https://target.com/p/x")]
    ages = history.request_coverage(rows)
    assert ages["amazon"] == ages["unrequested"] == ages["used"]["amazon"] == 1
    assert ages["within_2h"] == 0 and ages["oldest_h"] is None
