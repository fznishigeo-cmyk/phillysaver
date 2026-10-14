"""Task 091: observed cron gaps and bounded, honest hidden-price recovery."""

from datetime import timedelta

import httpx
import pytest

from dealradar import pipeline, rank, util, verify
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.fetch.keepa import _FeedConfirmations
from dealradar.keepa_budget import KeepaBudget, run_gap_forecast
from dealradar.keepa_rechecks import History
from dealradar.publish.social import plenty_of_stock
from tests.http_helpers import mock_keepa_get
from tests.test_keepa_overflow import setup_pull, pull
from tests.test_price_accuracy_engine import NOW, db, product, seed
from tests.test_value_per_token_rechecks import cfg
from tests.test_wall_confirmation_coverage import remember


def starts(db, gaps, end=NOW):
    at = end - timedelta(minutes=sum(gaps))
    for i, gap in enumerate([0, *gaps]):
        at += timedelta(minutes=gap)
        db.start_run(f"run-{i}", "fast" if i % 2 else "hourly", at)


def hidden(db, cfg, i=0, *, used=False, **changes):
    return seed(db, cfg, asin=f"B9{i:08}", seen=NOW - timedelta(days=4),
                conditions=["used"] if used else [], **changes)


def checks(monkeypatch, clock, *, modify=None):
    calls = []

    def check(deals, cfg, budget, **options):
        now = clock[0]
        result = verify.PriceRechecks()
        cost = sum(13 if verify.is_used_deal(d) else 1 for d in deals)
        assert not options.get("live")
        budget.reserve(cost, now)
        budget.usage.note({"tokensConsumed": cost})
        assert budget.available(now) >= 10
        calls.append([d["dedupe_key"] for d in deals])
        for deal in deals:
            key = deal["dedupe_key"]
            at = int(now.timestamp() / 60) - verify.KEEPA_EPOCH_MINUTES
            record = product(2500, asin=deal["asin"])
            record["lastUpdate"] = at
            if verify.is_used_deal(deal):
                record.update(offers=[{"condition": 2, "offerCSV": [at, 2500, 0]}],
                              liveOffersOrder=[0], offersSuccessful=True)
                record["stats"]["lastOffersUpdate"] = at
            if modify:
                modify(record, deal)
            result.products[key] = record
            result.costs[key] = (13 if verify.is_used_deal(deal) else 1, 0)
            result[key] = verify.judge_live_price(deal, record, cfg.keepa_recheck_tolerance_pct)
        return result

    monkeypatch.setattr(verify, "recheck_amazon_prices", check)
    return calls


def test_gap_ema_uses_both_lanes_and_current_start_once(db, cfg):
    cfg.keepa_recheck_gap.update(ema_alpha=.5, margin_minutes=0)
    starts(db, [15, 35, 20])
    expected = 22.5  # 15 -> 25 -> 22.5
    assert run_gap_forecast(db, cfg, NOW)["minutes"] == expected
    assert run_gap_forecast(db, cfg, NOW)["samples"] == 3
    db.start_run("duplicate", "fast", NOW)
    db.start_run("unrelated", "probe", NOW - timedelta(minutes=2))
    db.start_run("future", "hourly", NOW + timedelta(minutes=60))
    assert run_gap_forecast(db, cfg, NOW)["minutes"] == expected
    # A read never appends EMA samples or changes the next result.
    assert run_gap_forecast(db, cfg, NOW) == run_gap_forecast(db, cfg, NOW)


def test_gap_floor_bounded_outages_and_sample_window(db, cfg):
    assert run_gap_forecast(db, cfg, NOW)["minutes"] == 10
    cfg.keepa_recheck_gap.update(samples=2, ema_alpha=1, margin_minutes=0, max_minutes=40)
    starts(db, [15, 35, 500])
    assert run_gap_forecast(db, cfg, NOW)["minutes"] == 40
    assert run_gap_forecast(db, cfg, NOW)["samples"] == 2
    db.start_run("short", "fast", NOW + timedelta(minutes=1))
    assert run_gap_forecast(db, cfg, NOW + timedelta(minutes=1))["minutes"] == 10
    cfg.keepa_recheck_interval_minutes = 60  # the explicit floor still wins
    assert run_gap_forecast(db, cfg, NOW)["minutes"] == 60


def test_observed_gap_buys_discovery_where_ten_minutes_forecast_no_waste(db, cfg, monkeypatch):
    fake = setup_pull(cfg, db, monkeypatch, tokens=900)
    starts(db, [15, 35, 30])
    _, stats, _ = pull(cfg, db, fake)
    assert stats["run_gap"]["minutes"] > 25
    assert stats["overflow_discovery"]["pages"] == 8
    assert stats["tokens"]["bucket_end"] == 845
    assert KeepaBudget(885, 20, NOW).overflow(10, NOW) == 0


def test_hidden_reason_partition_covers_each_condition(db, cfg):
    cfg.keepa_recheck_adaptive.update(max_rest_h=48, max_used_h=48)
    cfg.keepa_show_max_unconfirmed_h = cfg.keepa_show_max_unconfirmed_used_h = 1
    cfg.keepa_recheck_adaptive["enabled"] = False
    # Fixed cadence makes a price hidden at 2 h but not due until 8 h.
    for used in (False, True):
        offset = 100 if used else 0
        hidden(db, cfg, offset, used=used)
        hidden(db, cfg, offset + 1, used=used, recheck_tried_at=util.iso(NOW - timedelta(minutes=20)))
        hidden(db, cfg, offset + 2, used=used, gone_suspect_at=util.iso(NOW - timedelta(hours=2)))
        hidden(db, cfg, offset + 3, used=used, price_rechecked_at=util.iso(NOW - timedelta(hours=2)))
        tried = hidden(db, cfg, offset + 4, used=used)
        History(db, cfg, NOW).record(tried, None, None, NOW, "hidden_used" if used else "hidden_new", 1, 1)
        hidden(db, cfg, offset + 5, used=used, price_rechecked_at=util.iso(NOW - timedelta(hours=30)))
    cfg.feature_flags["keepa"] = False
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    reasons = stats["hidden_unconfirmed"]["reasons"]
    assert stats["hidden_unconfirmed"]["total"] == 12
    for reason in reasons:
        expected = {"new": 1, "used": 1, "total": 2}
        if reason in {"adaptive_requested_recently", "missing_asin", "current_price_never_confirmed"}:
            expected = {"new": 0, "used": 0, "total": 0}
        elif reason == "new_hidden_recovery_due":
            expected = {"new": 1, "used": 0, "total": 1}
        elif reason == "used_hidden_recovery_due":
            expected = {"new": 0, "used": 1, "total": 1}
        assert reasons[reason] == expected
    cfg.keepa_recheck_adaptive["enabled"] = True
    recent = hidden(db, cfg, 999)
    History(db, cfg, NOW - timedelta(minutes=5)).record(recent, product(), None, NOW, "hidden_new", 1, 1)
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert stats["hidden_unconfirmed"]["reasons"]["adaptive_requested_recently"]["total"] == 1


def test_recovery_new_is_bounded_value_ranked_and_same_start_idempotent(db, cfg, monkeypatch):
    rows = [hidden(db, cfg, i, brand="Known" if i % 2 else "") for i in range(10)]
    cfg.keepa_hidden_new_tokens_per_run = 3
    cfg.keepa_recheck_top_live = True  # hidden stats checks must still cost 1
    # Off-wall recovery stays bounded; remembered top deadlines now lead it.
    expected = sorted(rows, key=lambda d: (-rank.pick_score(d, cfg, NOW, require_confirmation=False), d["dedupe_key"]))[:3]
    KeepaBudget(1000, 20, NOW).save(db)
    calls = checks(monkeypatch, [NOW])
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert calls == [[d["dedupe_key"] for d in expected]]
    assert stats["done"]["hidden_new"] == 3 and stats["done"]["top_new"] == 0
    assert stats["hidden_recovery"] == {"tokens": 3, "estimated_tokens": 0, "requested": 3,
                                         "recovered": {"new": 3, "used": 0, "total": 3}}
    assert stats["hidden_unconfirmed_before"]["new"] == 10
    assert stats["hidden_unconfirmed"]["new"] == 7
    again = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert len(calls) == 1 and again["hidden_recovery"]["tokens"] == 0
    assert len(db.all_deals()) == 10


@pytest.mark.parametrize("used,cap,cost", [(False, 150, 150), (True, 10, 130)])
def test_default_caps_and_internal_used_scores_leave_public_gates_alone(db, cfg, monkeypatch, used, cap, cost):
    rows = [hidden(db, cfg, i, used=used, brand="Known" if i % 2 else "") for i in range(cap + 5)]
    expected = sorted(rows, key=lambda d: (-rank.pick_score(d, cfg, NOW, require_confirmation=False,
                                                            allow_used=True), d["dedupe_key"]))[:cap]
    KeepaBudget(1200, 20, NOW).save(db)
    calls = checks(monkeypatch, [NOW])
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert [key for batch in calls for key in batch] == [d["dedupe_key"] for d in expected]
    assert stats["hidden_recovery"]["tokens"] == cost
    assert stats["hidden_recovery"]["recovered"]["total"] == cap
    if used:
        current = db.get_deal(expected[0]["dedupe_key"])
        assert rank.pick_score(current, cfg, NOW) is None
        assert not plenty_of_stock(db, current, cfg)
        assert stats["done"]["rest_used"] == 0


@pytest.mark.parametrize("tokens,share,new_count,used_count", [(302, 1, 2, 0), (313, 1, 5, 0),
    (331, 1, 5, 2), (1200, .01, 5, 0), (299, 1, 0, 0), (None, 1, 0, 0)])
def test_recovery_keeps_reserves_and_used_share(db, cfg, monkeypatch, tokens, share, new_count, used_count):
    for i in range(5):
        hidden(db, cfg, i)
        hidden(db, cfg, i + 100, used=True)
    cfg.keepa_used_scan_budget_share = share
    if tokens is not None:
        KeepaBudget(tokens, 20, NOW).save(db)
    calls = checks(monkeypatch, [NOW])
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert stats["done"]["hidden_new"] == new_count
    assert stats["done"]["hidden_used"] == used_count
    if tokens is not None and tokens >= 300:
        assert stats["tokens"]["bucket_end"] >= 300


@pytest.mark.parametrize("used,hours", [(False, 1), (True, 6)])
def test_inconclusive_recovery_waits_full_retry_window(db, cfg, monkeypatch, used, hours):
    deal = hidden(db, cfg, used=used)
    clock = [NOW]
    def inconclusive(record, deal):
        record["stats"] = {}
        record.pop("offers", None)
        record.pop("liveOffersOrder", None)
    calls = checks(monkeypatch, clock, modify=inconclusive)
    KeepaBudget(1000, 20, NOW).save(db)
    first = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert first["hidden_recovery"]["recovered"]["total"] == 0
    assert db.get_deal(deal["dedupe_key"])["recheck_tried_at"] == util.iso(NOW)
    clock[0] = NOW + timedelta(hours=hours, seconds=-1)
    again = pipeline._recheck_prices(db, cfg, clock[0], image_backfill=False)
    assert len(calls) == 1
    assert again["hidden_unconfirmed"]["reasons"]["retry_window"]["total"] == 1
    clock[0] += timedelta(seconds=1)
    pipeline._recheck_prices(db, cfg, clock[0], image_backfill=False)
    assert len(calls) == 2


@pytest.mark.parametrize("used", [False, True])
@pytest.mark.parametrize("time_kind", ["missing", "future", "old", "before_price"])
def test_hidden_recovery_never_turns_stale_or_missing_clocks_into_now(db, cfg, monkeypatch, used, time_kind):
    deal = hidden(db, cfg, used=used)
    def timestamp(record, deal):
        stamp = (None if time_kind == "missing" else
                 int((NOW + timedelta(hours=1) if time_kind == "future" else
                      NOW - timedelta(days=2 if time_kind == "old" else 5)).timestamp() / 60)
                 - verify.KEEPA_EPOCH_MINUTES)
        record["lastUpdate"] = record["stats"]["lastOffersUpdate"] = stamp
    checks(monkeypatch, [NOW], modify=timestamp)
    KeepaBudget(1000, 20, NOW).save(db)
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert stats["hidden_recovery"]["recovered"]["total"] == 0
    current = db.get_deal(deal["dedupe_key"])
    assert not current.get("price_rechecked_at")
    assert verify.price_unconfirmed(current, cfg, NOW)
    assert current["recheck_tried_at"] == util.iso(NOW)


def test_discovery_then_recovery_then_idle_and_free_warehouse_match(db, cfg, monkeypatch):
    due = seed(db, cfg, asin="B000000001")
    idle = seed(db, cfg, asin="B000000002", price_rechecked_at=util.iso(NOW - timedelta(minutes=35)))
    new = hidden(db, cfg)
    used = hidden(db, cfg, 1, used=True)
    remember(db, top=[due, idle])
    events = checks(monkeypatch, [NOW])
    KeepaBudget(1200, 20, NOW).save(db)
    def discover(budget):
        events.append("discovery")
        budget.reserve(5, NOW)
        confirms = _FeedConfirmations(db, NOW)
        confirms.note([{"asin": used["asin"], "current": [2500] * 10,
                        "lastUpdate": int(NOW.timestamp() / 60) - verify.KEEPA_EPOCH_MINUTES}], used=True)
        return {"pages": 1, "tokens": 5}
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False, overflow_discovery=discover)
    assert events == [[due["dedupe_key"]], "discovery", [new["dedupe_key"]], [idle["dedupe_key"]]]
    assert stats["hidden_unconfirmed"]["total"] == 0
    assert stats["hidden_recovery"]["tokens"] == 1
    assert not verify.price_unconfirmed(db.get_deal(used["dedupe_key"]), cfg, NOW)


@pytest.mark.parametrize("used", [False, True])
@pytest.mark.parametrize("failure", ["429", "unreadable", "transport"])
def test_real_transport_recovery_history_zero_costs_and_first_429(db, cfg, monkeypatch, used, failure):
    for i in range(151 if not used else 11):
        hidden(db, cfg, i, used=used)
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    monkeypatch.setenv("KEEPA_KEY", "offline")
    cfg.keepa_recheck_top_live = True
    calls = []
    def get(url, params=None, **kwargs):
        assert url == "https://api.keepa.com/product"
        assert str(params["history"]) == "0" and "update" not in params
        assert ("offers" in params) == used
        calls.append(params)
        if failure == "transport":
            raise httpx.ReadError("lost response")
        return httpx.Response(429, json={"tokensLeft": 1100, "refillRate": 20}) if failure == "429" else httpx.Response(200, text="truncated")
    mock_keepa_get(monkeypatch, get)
    KeepaBudget(1200, 20, NOW).save(db)
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    if failure == "429":
        assert len(calls) == 1 and stats["rate_limited"]
        assert stats["hidden_recovery"]["tokens"] == 0
    else:
        expected = 130 if used else 150
        assert stats["hidden_recovery"]["tokens"] == stats["hidden_recovery"]["estimated_tokens"] == expected
        assert stats["tokens"]["bucket_end"] == 1200 - expected
    before = len(calls)
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert len(calls) == before


def test_recovery_configuration_loads_and_zero_disables(tmp_path, db, cfg, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("keepa:\n  hidden_new_tokens_per_run: -1\n  hidden_used_scans_per_run: 0\n  recheck_gap: {ema_alpha: 0.5}\n")
    loaded = load_config(config)
    assert loaded.keepa_hidden_new_tokens_per_run == loaded.keepa_hidden_used_scans_per_run == 0
    assert loaded.keepa_recheck_gap == {"ema_alpha": .5}
    cfg.keepa_hidden_new_tokens_per_run = cfg.keepa_hidden_used_scans_per_run = 0
    hidden(db, cfg)
    hidden(db, cfg, 1, used=True)
    KeepaBudget(1200, 20, NOW).save(db)
    assert checks(monkeypatch, [NOW]) == []
    assert pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)["hidden_recovery"]["tokens"] == 0


def simulate_irregular_day(cfg, monkeypatch, *, observed):
    database = DB(":memory:")
    # Enough real inventory to use the refill, like the supplied live backlog.
    # Fixed public membership avoids claiming newly recovered rows are top picks.
    top = [seed(database, cfg, asin=f"B0{i:08}", price_rechecked_at=util.iso(NOW)) for i in range(200)]
    wall_new = [seed(database, cfg, asin=f"B1{i:08}", price_rechecked_at=util.iso(NOW)) for i in range(100)]
    wall_used = [seed(database, cfg, asin=f"B2{i:08}", conditions=["used"],
                      price_rechecked_at=util.iso(NOW - timedelta(hours=i / 40))) for i in range(100)]
    for i in range(263):
        hidden(database, cfg, i)
    for i in range(516):
        hidden(database, cfg, 1000 + i, used=True)
    remember(database, top=top, wall=top + wall_new + wall_used)
    starts(database, [15, 35, 20, 30, 25] * 3)
    if not observed:
        cfg.keepa_recheck_gap.update(max_minutes=10, margin_minutes=0)
    clock = [NOW]
    checks(monkeypatch, clock)
    KeepaBudget(1000, 20, NOW).save(database)
    spent = lost = recovered_new = recovered_used = 0
    worst_top = worst_wall = 1
    elapsed = tick = 0
    first_recovered = None
    try:
        while elapsed < 1440:
            now = clock[0] = NOW + timedelta(minutes=elapsed)
            database.start_run(f"day-{tick}", "hourly" if tick % 3 == 0 else "fast", now)
            budget = KeepaBudget.load(database)
            refill = max(0, (now - budget.seen_at).total_seconds() / 60) * 20
            lost += max(0, budget.tokens_left + refill - 1200)
            # Ordinary /deal pages cost 25; further pages retain their real
            # 8-page bound and 300-token reserve. No invented public supply.
            budget.reserve(25, now)
            budget.save(database)
            spent += 25
            def discover(budget):
                pages = 0
                gap = run_gap_forecast(database, cfg, now)["minutes"]
                while pages < cfg.keepa_overflow_extra_pages_max and budget.overflow(gap, now) >= 5 \
                        and budget.available(now) >= 305:
                    budget.reserve(5, now)
                    pages += 1
                return {"pages": pages, "tokens": pages * 5}
            stats = pipeline._recheck_prices(database, cfg, now, image_backfill=False,
                                             overflow_discovery=discover)
            spent += stats["tokens"]["requested"] + stats["overflow_discovery"]["tokens"]
            recovered_new += stats["hidden_recovery"]["recovered"]["new"]
            recovered_used += stats["hidden_recovery"]["recovered"]["used"]
            if stats["hidden_unconfirmed"]["new"] == 0 and first_recovered is None:
                first_recovered = elapsed
            assert stats["done"]["hidden_new"] <= 150 and stats["done"]["hidden_used"] <= 10
            assert 10 <= stats["tokens"]["bucket_end"] <= 1200
            ages = stats["confirmed_age"]
            worst_top = min(worst_top, ages["top_candidates"]["within_1h"])
            worst_wall = min(worst_wall, ages["wall"]["new"]["within_2h"])
            assert all(not verify.price_unconfirmed(d, cfg, now) for d in rank.site_deals(database, cfg, now))
            tick += 1
            elapsed += [15, 35, 20, 30, 25][(tick - 1) % 5]
        assert first_recovered is not None and recovered_new >= 263
        assert spent <= 1000 + 20 * (clock[0] - NOW).total_seconds() / 60
        return {"runs": tick, "tokens": spent, "unused_refill": lost,
                "recovered_new": recovered_new, "recovered_used": recovered_used,
                "hidden_new": stats["hidden_unconfirmed"]["new"],
                "hidden_used": stats["hidden_unconfirmed"]["used"],
                "first_all_new_recovered_min": first_recovered,
                "min_top_1h": worst_top, "min_wall_new_2h": worst_wall}
    finally:
        database.close()


def test_irregular_15_to_35_minute_day_uses_refill_and_recovers_hidden_new(cfg, monkeypatch):
    import copy
    fixed = simulate_irregular_day(copy.deepcopy(cfg), monkeypatch, observed=False)
    observed = simulate_irregular_day(copy.deepcopy(cfg), monkeypatch, observed=True)
    print({"fixed_10_minutes": fixed, "observed_gaps": observed})
    assert observed["unused_refill"] < 20 * 1440 * .02
    assert observed["unused_refill"] < fixed["unused_refill"] * .2
    assert observed["hidden_new"] == 0
    assert observed["first_all_new_recovered_min"] <= 60
    assert observed["min_top_1h"] >= .95 and observed["min_wall_new_2h"] >= .98


def test_retry_of_same_run_key_cannot_buy_another_recovery_allowance(db, cfg, monkeypatch):
    for i in range(8):
        hidden(db, cfg, i)
    cfg.keepa_hidden_new_tokens_per_run = 3
    clock = [NOW]
    calls = checks(monkeypatch, clock)
    KeepaBudget(1000, 20, NOW).save(db)
    db.start_run("same-run", "fast", NOW)
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    clock[0] += timedelta(minutes=12)
    db.start_run("same-run", "fast", clock[0])
    stats = pipeline._recheck_prices(db, cfg, clock[0], image_backfill=False)
    assert len(calls) == 1 and stats["done"]["hidden_new"] == 0


def test_hidden_suspect_still_needs_two_readings_an_hour_apart(db, cfg, monkeypatch):
    deal = hidden(db, cfg)
    clock = [NOW]
    def no_offer(record, deal):
        record["stats"]["current"] = [-1, -1]
    calls = checks(monkeypatch, clock, modify=no_offer)
    KeepaBudget(1000, 20, NOW).save(db)
    first = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert first["suspect"] == 1 and first["dead"] == 0
    clock[0] += timedelta(minutes=59)
    pipeline._recheck_prices(db, cfg, clock[0], image_backfill=False)
    assert len(calls) == 1
    clock[0] += timedelta(minutes=1)
    final = pipeline._recheck_prices(db, cfg, clock[0], image_backfill=False)
    assert final["dead"] == 1 and final["hidden_recovery"]["recovered"]["total"] == 0
    assert db.get_deal(deal["dedupe_key"])["death"] == "confirmed"


def test_failed_used_scan_does_not_recover_matching_offer(db, cfg, monkeypatch):
    deal = hidden(db, cfg, used=True)
    checks(monkeypatch, [NOW], modify=lambda record, _: record.update(offersSuccessful=False))
    KeepaBudget(1000, 20, NOW).save(db)
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert stats["hidden_recovery"]["recovered"]["total"] == 0
    assert db.get_deal(deal["dedupe_key"])["recheck_tried_at"] == util.iso(NOW)


def test_expired_deadline_and_recent_history_skip_hidden_calls(db, cfg, monkeypatch):
    import time
    deal = hidden(db, cfg)
    History(db, cfg, NOW - timedelta(minutes=5)).record(deal, product(), None, NOW, "hidden_new", 1, 1)
    hidden(db, cfg, 1)
    KeepaBudget(1200, 20, NOW).save(db)
    calls = checks(monkeypatch, [NOW])
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False, deadline=time.monotonic() - 1)
    assert calls == [] and result["hidden_recovery"]["tokens"] == 0
    assert result["hidden_unconfirmed"]["reasons"]["adaptive_requested_recently"]["total"] == 1
