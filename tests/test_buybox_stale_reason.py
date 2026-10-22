"""2026-10-22: Buy Box readings Keepa takes during a run are real readings.

Hourly #319 asked for 97 Buy Box readings and only 3 counted as fresh (fast
#806: 109/110). Requests go out minutes after the run's ``now``; a reading
Keepa refreshed at request time is stamped after it and was refused as
"future" on every Buy Box path (the listing clock already clamps). Such a
stamp now counts as of ``now``; only one beyond the real clock (+5 min) is
refused. The run summary counts why a requested reading was not fresh.
"""
from datetime import timedelta

import pytest

from dealradar import buybox, click_price, util, verify
from tests.test_buybox_top_picks import cfg, record, remember, run, transport  # noqa: F401
from tests.test_price_accuracy_engine import NOW, db, product, seed  # noqa: F401


@pytest.fixture
def clock(monkeypatch):
    # The real clock a few minutes into the run.
    monkeypatch.setattr(util, "now_utc", lambda: NOW + timedelta(minutes=4))


def test_reading_at_clamps_in_run_stamps_and_refuses_the_future(clock):
    assert verify.keepa_reading_at(None, NOW) is None
    assert verify.keepa_reading_at(NOW - timedelta(minutes=9), NOW) == NOW - timedelta(minutes=9)
    assert verify.keepa_reading_at(NOW + timedelta(minutes=3), NOW) == NOW
    assert verify.keepa_reading_at(NOW + timedelta(minutes=9), NOW) == NOW  # 4 min + 5 min skew
    assert verify.keepa_reading_at(NOW + timedelta(minutes=10), NOW) is None


def test_reasons_follow_fresh_reading_in_order(db, cfg, clock):
    deal = seed(db, cfg)
    for age in (5, -3):
        reading = buybox.fresh_reading(deal, record(deal, 30, age=age), cfg, NOW)
        assert reading is not None and reading[1] == min(NOW, NOW - timedelta(minutes=age))
        assert buybox.stale_reason(deal, record(deal, 30, age=age), cfg, NOW) is None
    no_time = record(deal, 30, age=5)
    no_time["stats"].pop("lastBuyBoxUpdate", None)
    cases = {"no_buybox_price": product(2500, asin=deal["asin"]), "no_buybox_time": no_time,
             "future_stamp": record(deal, 30, age=-30),
             "older_than_interval": record(deal, 30, age=cfg.keepa_buybox_interval_min + 1)}
    for reason, p in cases.items():
        assert buybox.stale_reason(deal, p, cfg, NOW) == reason
        assert buybox.fresh_reading(deal, p, cfg, NOW) is None
    assert buybox.stale_reason(dict(deal, conditions=["used"]), record(deal, 30), cfg, NOW) == "not_eligible"


def test_click_price_keeps_an_in_run_reading_but_never_a_future_one(db, cfg, clock):
    deal = seed(db, cfg)
    click_price.remember(deal, record(deal, 30, age=-3), NOW, NOW)
    assert deal["click_price_check"]["at"] == util.iso(NOW) and deal["click_price_check"]["buybox"] == 30
    later = seed(db, cfg, asin="B000000002")
    click_price.remember(later, record(later, 30, age=-30), NOW, NOW)
    assert "click_price_check" not in later


@pytest.mark.parametrize("age,stale", [(5, {}), (-3, {}), (-30, {"future_stamp": 1})])
def test_run_summary_counts_only_truly_stale_readings(db, cfg, monkeypatch, age, stale):
    row = seed(db, cfg)
    remember(db, top=[row], wall=[row])
    _, clock, _ = transport(monkeypatch, db, {row["asin"]: record(row, 30, age=age)})
    monkeypatch.setattr(util, "now_utc", lambda: clock[0] + timedelta(minutes=4))
    result = run(db, cfg)
    assert result["buybox_top"]["requested"] == 1
    assert result["buybox_top"]["with_buybox"] == int(not stale)
    assert result["buybox_stale"] == stale
