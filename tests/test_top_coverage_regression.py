"""Task 092: deadlines survive cached/inconclusive hourly responses."""

from datetime import timedelta

import httpx
import pytest

from dealradar import pipeline, util, verify
from dealradar.keepa_budget import KeepaBudget
from dealradar.keepa_rechecks import History
from dealradar.publish import site
from tests.http_helpers import mock_keepa_get
from tests.test_price_accuracy_engine import NOW, db, product, seed
from tests.test_real_run_recovery import checks, hidden, starts
from tests.test_value_per_token_rechecks import cfg, minutes
from tests.test_wall_confirmation_coverage import remember


def test_hourly_then_fast_18_minutes_later_retries_all_overdue_top(db, cfg, monkeypatch):
    top = [seed(db, cfg, asin=f"B0{i:08}", price_rechecked_at=util.iso(
        NOW - timedelta(hours=3 if i < 63 else 1))) for i in range(192)]
    remember(db, top=top, wall=top[:181])
    for i in range(456):
        hidden(db, cfg, i, used=True)
    starts(db, [15, 20, 18] * 4)
    clock = [NOW]
    blocked = {d["dedupe_key"] for d in top[:63]}
    cached = {d["dedupe_key"] for d in top[63:127]}

    def snapshot(record, deal):
        # An answered hourly request cannot interpret 63 prices. Other
        # records have ordinary Keepa cache lag of up to one hour.
        if clock[0] == NOW and deal["dedupe_key"] in blocked:
            record["stats"] = {}
        elif deal["dedupe_key"] in cached:
            record["lastUpdate"] = minutes(clock[0] - timedelta(minutes=60))

    calls = checks(monkeypatch, clock, modify=snapshot)
    KeepaBudget(1092, 20, NOW).save(db)
    db.start_run("hourly", "hourly", NOW)
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    before = len(calls)
    clock[0] += timedelta(minutes=18)
    db.start_run("fast", "fast", clock[0])
    result = pipeline._recheck_prices(db, cfg, clock[0], image_backfill=False)
    selected = {key for batch in calls[before:] for key in batch}
    assert blocked <= selected
    ages = result["confirmed_age"]
    assert ages["top_candidates"]["amazon"] == 192
    assert ages["wall"]["amazon"] == 181
    assert ages["top_candidates"]["within_2h"] == 1
    assert ages["top_candidates"]["request_age"]["within_45m"] == 1


def test_deadlines_precede_every_lower_class_and_hidden_recovery(db, cfg, monkeypatch):
    top = seed(db, cfg, asin="B000000001", price_rechecked_at=util.iso(NOW - timedelta(hours=2)))
    wall = seed(db, cfg, asin="B000000002", price_rechecked_at=util.iso(NOW - timedelta(hours=3)))
    used = seed(db, cfg, asin="B000000003", conditions=["used"],
                price_rechecked_at=util.iso(NOW - timedelta(hours=5)))
    seed(db, cfg, asin="B000000004", gone_suspect_at=util.iso(NOW - timedelta(hours=4)))
    seed(db, cfg, asin="B000000005")
    hidden(db, cfg)
    hidden(db, cfg, 1, used=True)
    remember(db, top=[top], wall=[top, wall, used])
    calls = checks(monkeypatch, [NOW])
    KeepaBudget(1200, 20, NOW).save(db)
    def discover(budget):
        calls.append(["discovery"])
        return {"pages": 0, "tokens": 0}
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False, overflow_discovery=discover)
    assert [key for batch in calls for key in batch][:3] == [d["dedupe_key"] for d in (top, wall, used)]


@pytest.mark.parametrize("failure,lane", [(failure, lane) for failure in ("timeout", "missing_product")
                                        for lane in ("top", "rest", "hidden")] + [("inconclusive", "top")])
def test_failed_request_retries_next_run_and_same_run_is_idempotent(db, cfg, monkeypatch, failure, lane):
    deal = seed(db, cfg, seen=NOW - timedelta(days=4),
                price_rechecked_at=util.iso(NOW - timedelta(hours=30 if lane == "hidden" else 9)))
    remember(db, top=[deal] if lane == "top" else [])
    clock = [NOW]
    monkeypatch.setattr(util, "now_utc", lambda: clock[0])
    monkeypatch.setenv("KEEPA_KEY", "offline")
    calls = []
    def get(url, params=None, **kwargs):
        calls.append(params)
        assert str(params["history"]) == "0"
        if failure == "timeout":
            raise httpx.ReadTimeout("offline timeout")
        record = dict(product(2500), stats={} if failure == "inconclusive" else product(2500)["stats"])
        return httpx.Response(200, json={"products": [] if failure == "missing_product" else [record],
                                         "tokensConsumed": 1})
    mock_keepa_get(monkeypatch, get)
    KeepaBudget(1000, 20, NOW).save(db)
    db.start_run("hourly", "hourly", NOW)
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    count = len(calls)
    clock[0] += timedelta(minutes=1)
    db.start_run("hourly", "hourly", clock[0])
    pipeline._recheck_prices(db, cfg, clock[0], image_backfill=False)
    assert len(calls) == count
    clock[0] += timedelta(minutes=4)
    db.start_run("fast", "fast", clock[0])
    pipeline._recheck_prices(db, cfg, clock[0], image_backfill=False)
    assert len(calls) > count


def test_overdue_hidden_top_does_not_wait_for_recovery_allowance(db, cfg, monkeypatch):
    deal = hidden(db, cfg)
    remember(db, top=[deal])
    cfg.keepa_hidden_new_tokens_per_run = 0
    KeepaBudget(15, 20, NOW).save(db)
    calls = checks(monkeypatch, [NOW])
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert calls == [[deal["dedupe_key"]]]
    assert result["done"]["top_new"] == 1
    assert result["hidden_recovery"]["tokens"] == 0
    assert not verify.price_unconfirmed(db.get_deal(deal["dedupe_key"]), cfg, NOW)


def test_request_age_distinguishes_recent_request_from_old_keepa_data(db, cfg, tmp_path):
    old = NOW - timedelta(minutes=90)
    deal = seed(db, cfg, price_rechecked_at=util.iso(old))
    never = seed(db, cfg, asin="B000000002", price_rechecked_at=util.iso(NOW))
    remember(db, top=[deal, never])
    History(db, cfg, NOW - timedelta(minutes=30)).record(deal, product(2500), True, old, "top_new", 1, 0)
    cfg.feature_flags["keepa"] = False
    measured = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)["confirmed_age"]["top_candidates"]
    assert measured["within_1h"] == .5
    assert measured["request_age"] == {"amazon": 2, "unrequested": 1, "within_45m": .5,
        "within_1h": .5, "within_2h": .5, "within_3h": .5, "within_12h": .5, "oldest_h": .5}
    rendered = site.render_site(db, cfg, NOW, out_dir=tmp_path)
    assert rendered["confirmed_age"]["top_candidates"]["request_age"] == measured["request_age"]


@pytest.mark.parametrize("tokens,expected", [(15, 2), (900, 130)])
def test_live_top_batches_use_two_tokens_and_observed_gap_overflow(db, cfg, monkeypatch, tokens, expected):
    cfg.keepa_recheck_top_live = True
    cfg.keepa_recheck_gap.update(ema_alpha=1, margin_minutes=10)
    starts(db, [18])  # forecast 28 min: 900 + 560 - 1200 = 260 tokens
    age = 2 if tokens == 15 else .5
    top = [seed(db, cfg, asin=f"B0{i:08}", price_rechecked_at=util.iso(
        NOW - timedelta(hours=age))) for i in range(192)]
    remember(db, top=top)
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    monkeypatch.setenv("KEEPA_KEY", "offline")
    calls = []
    def get(url, params=None, **kwargs):
        assert str(params["history"]) == "0" and str(params["update"]) == "0"
        assert "offers" not in params
        asins = params["asin"].split(",")
        calls.extend(asins)
        return httpx.Response(200, json={"products": [product(2500, asin=a) for a in asins],
                                         "tokensConsumed": 2 * len(asins)})
    mock_keepa_get(monkeypatch, get)
    KeepaBudget(tokens, 20, NOW).save(db)
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert result["run_gap"]["minutes"] == 28
    assert len(calls) == expected
    assert result["tokens"]["requested"] == result["yield"]["overall"]["tokens"] == 2 * expected
    assert result["tokens"]["bucket_end"] == tokens - 2 * expected
    assert result["tokens"]["bucket_end"] >= 10


def test_live_switch_only_forces_saved_top_candidates(db, cfg, monkeypatch):
    cfg.keepa_recheck_top_live = True
    rows = [seed(db, cfg, asin=f"B0{i:08}", seen=NOW - timedelta(days=4),
                 price_rechecked_at=util.iso(NOW - timedelta(hours=age)))
            for i, age in enumerate((2, 3, 9, 30))]
    remember(db, top=rows[:1], wall=rows[:2])
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    monkeypatch.setenv("KEEPA_KEY", "offline")
    calls = []
    def get(url, params=None, **kwargs):
        calls.append(params)
        asins = params["asin"].split(",")
        live = "update" in params
        assert live == (asins == [rows[0]["asin"]])
        assert str(params["history"]) == "0" and "offers" not in params
        return httpx.Response(200, json={"products": [product(2500, asin=a) for a in asins],
                                         "tokensConsumed": len(asins) * (2 if live else 1)})
    mock_keepa_get(monkeypatch, get)
    KeepaBudget(1200, 20, NOW).save(db)
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert len(calls) == 4 and result["tokens"]["requested"] == 5
    assert result["hidden_recovery"]["tokens"] == 1


def test_regressed_cache_cooldown_cannot_suppress_next_deadline(db, cfg, monkeypatch):
    # A 091 early check can receive a snapshot older than a free confirmation.
    deal = seed(db, cfg, price_rechecked_at=util.iso(NOW - timedelta(minutes=35)))
    remember(db, top=[deal])
    clock = [NOW]
    calls = checks(monkeypatch, clock, modify=lambda record, _: record.update(lastUpdate=minutes(NOW - timedelta(minutes=60))))
    KeepaBudget(1200, 20, NOW).save(db)
    db.start_run("hourly", "hourly", NOW)
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert db.get_deal(deal["dedupe_key"])["recheck_tried_at"] == util.iso(NOW)
    clock[0] += timedelta(minutes=30)
    db.start_run("fast", "fast", clock[0])
    result = pipeline._recheck_prices(db, cfg, clock[0], image_backfill=False)
    assert len(calls) == 2
    ages = result["confirmed_age"]["top_candidates"]
    assert ages["request_age"]["within_45m"] == 1
    assert ages["within_1h"] == 0  # requesting old data is not a new confirmation


def test_recent_success_does_not_override_deadline_in_next_run(db, cfg, monkeypatch):
    deal = seed(db, cfg, price_rechecked_at=util.iso(NOW - timedelta(hours=2)))
    remember(db, top=[deal])
    clock = [NOW]
    calls = checks(monkeypatch, clock, modify=lambda record, _: record.update(lastUpdate=minutes(NOW - timedelta(hours=2))))
    KeepaBudget(1200, 20, NOW).save(db)
    db.start_run("hourly", "hourly", NOW)
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    clock[0] += timedelta(minutes=1)
    db.start_run("hourly", "hourly", clock[0])
    pipeline._recheck_prices(db, cfg, clock[0], image_backfill=False)
    assert len(calls) == 1
    db.start_run("fast", "fast", clock[0] + timedelta(minutes=1))
    clock[0] += timedelta(minutes=1)
    pipeline._recheck_prices(db, cfg, clock[0], image_backfill=False)
    assert len(calls) == 2
