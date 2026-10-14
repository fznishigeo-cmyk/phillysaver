"""101: the real scheduler and transport under a fast run's wall-clock limit."""
import json
from datetime import timedelta

import httpx
import pytest

from dealradar import pipeline, util, verify
from dealradar.keepa_budget import KeepaBudget
from dealradar.keepa_rechecks import CallHistory, History
from dealradar.volatility import Plan
from tests.http_helpers import mock_keepa_get
from tests.test_price_accuracy_engine import NOW, db, product, seed
from tests.test_volatility_tiers import cfg
from tests.test_value_per_token_rechecks import minutes
from tests.test_wall_confirmation_coverage import remember
from tests.test_real_run_recovery import starts


def inventory(db, cfg, *, used=85, rest=980, top=190):
    prototype = seed(db, cfg, asin="B000000000")
    rows = []
    for i in range(top + used + rest):
        is_used = top <= i < top + used
        age = 2 if i < top else 4 if is_used else 8 + 8 * (i - top - used) / max(1, rest)
        asin = f"B{i:09}"
        key = prototype["dedupe_key"] if i == 0 else "batch-" + asin
        deal = dict(prototype, dedupe_key=key, asin=asin, item="Widget " + asin,
                    canonical_url="https://www.amazon.com/dp/" + asin,
                    conditions=["used"] if is_used else [],
                    price_rechecked_at=util.iso(NOW - timedelta(hours=age)))
        if i == 0:
            db.update_deal(key, payload=deal)
        else:
            db.insert_deal(dedupe_key=key, store="Amazon", item=deal["item"],
                normalized_item=util.normalize_item(deal["item"]), canonical_url=deal["canonical_url"],
                payload=deal, final_price=25, pct_at_checkout=75, abs_savings=75,
                online_only="true", expires_at="", now=NOW - timedelta(days=4))
        rows.append(db.get_deal(key))
    picks, wall = rows[:top], rows[:min(top, 100)] + rows[top:top + used]
    remember(db, picks, wall)
    return rows, picks, wall


def timed_transport(monkeypatch, clock, *, offers_seconds=(4, 5, 6), cost=7, balance=None):
    calls, elapsed = [], [0.0]
    monkeypatch.setenv("KEEPA_KEY", "offline")
    monkeypatch.setattr(util, "now_utc", lambda: clock[0])
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: elapsed[0])

    def get(url, params=None, **kwargs):
        assert str(params["history"]) == "0"
        asins = params["asin"].split(",")
        used, live = "offers" in params, "update" in params
        assert len(asins) <= (20 if used else 100)
        # Includes host pacing: successive starts are always >=1 s apart.
        seconds = offers_seconds[len(calls) % len(offers_seconds)] if used else 1
        calls.append((asins, used, live, elapsed[0]))
        elapsed[0] += seconds
        records = []
        for asin in asins:
            record = product(2500, asin=asin)
            at = minutes(clock[0])
            record["lastUpdate"] = at
            if used:
                record.update(offers=[{"condition": 2, "offerCSV": [at, 2500, 0]}],
                              liveOffersOrder=[0], offersSuccessful=True)
                record["stats"]["lastOffersUpdate"] = at
            records.append(record)
        tokens = len(asins) * ((cost if used else 1) + live)
        receipt = {"tokensConsumed": tokens}
        if balance is not None:
            balance[0] -= tokens
            assert balance[0] >= 10
            receipt.update(tokensLeft=balance[0], refillRate=60)
        return httpx.Response(200, json={"products": records, **receipt})

    fetcher = mock_keepa_get(monkeypatch, get)
    fetcher._clock = lambda: elapsed[0]
    fetcher._sleep = lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds)
    return calls, elapsed


def test_fast_lane_fills_offers_across_interleaved_value_groups(db, cfg, monkeypatch):
    cfg.keepa_recheck_top_live = False
    rows, picks, wall = inventory(db, cfg)
    calls, elapsed = timed_transport(monkeypatch, [NOW])
    KeepaBudget(3545, 60, NOW).save(db)
    summary = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False, deadline=80)
    offered = [asins for asins, used, _, _ in calls if used]
    assert summary["used_checked"] == 85
    assert [len(batch) for batch in offered] == [20, 20, 20, 20, 5]
    assert elapsed[0] <= 80
    assert summary["confirmed_age"]["top_candidates"]["within_1h"] == 1


@pytest.mark.parametrize("live", [False, True])
def test_labels_share_batches_but_keep_their_accounting(db, cfg, monkeypatch, live):
    cfg.keepa_recheck_top_live = live
    rows, picks, wall = inventory(db, cfg, used=0, rest=120, top=0)
    for deal in rows:
        db.update_deal(deal["dedupe_key"], payload=dict(deal, price_rechecked_at=util.iso(NOW - timedelta(hours=8))))
    remember(db, top=rows[:30] if live else [], wall=rows[30:70])
    calls, _ = timed_transport(monkeypatch, [NOW])
    KeepaBudget(3545, 60, NOW).save(db)
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False, deadline=80)
    assert [len(c[0]) for c in calls] == ([30, 90] if live else [100, 20])
    assert [c[2] for c in calls] == ([True, False] if live else [False, False])
    assert result["done"]["wall_new"] == 40
    assert result["done"]["rest_new"] == (50 if live else 80)


def test_time_budget_sizes_selection_and_learns_slow_calls(db, cfg, monkeypatch):
    cfg.keepa_recheck_top_live = False
    rows, _, _ = inventory(db, cfg, used=85, rest=0, top=0)
    calls, elapsed = timed_transport(monkeypatch, [NOW], offers_seconds=(12,))
    CallHistory(db, NOW - timedelta(minutes=20)).record(
        used=True, live=False, count=20, seconds=12, tokens=140)
    KeepaBudget(3545, 60, NOW).save(db)
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False, deadline=35)
    assert [len(c[0]) for c in calls] == [20, 20]  # 2 * 14.4s + 5s margin
    assert result["used_checked"] == 40 and elapsed[0] == 24
    assert result["tokens_short"] == 45
    assert sum(History(db, cfg, NOW).attempted(d["dedupe_key"]) for d in rows) == 40
    # Selected, unsent rows receive no clock and remain eligible next run.
    assert sum(db.get_deal(d["dedupe_key"])["price_rechecked_at"] == util.iso(NOW) for d in rows) == 40


def test_hourly_receipts_survive_empty_fast_deal_history(db, cfg, monkeypatch):
    cfg.keepa_recheck_top_live = False
    deal = seed(db, cfg, conditions=["used"])
    clock = [NOW - timedelta(minutes=25)]
    timed_transport(monkeypatch, clock, cost=7)
    db.start_run("hourly-receipt", "hourly", clock[0])
    KeepaBudget(3545, 60, clock[0]).save(db)
    verify.recheck_amazon_prices([deal], cfg, budget=KeepaBudget.load(db))
    assert not History(db, cfg, NOW).samples  # transport receipt is independent
    db.start_run("fast-receipt", "fast", NOW)
    plan = Plan(History(db, cfg, NOW), [deal], {}, {deal["dedupe_key"]: 0}, 25)
    assert plan.costs[deal["dedupe_key"]] == 7
    assert plan.cost_sources["used"] == "shared_call_receipts"
    assert plan.cost_samples["used"] == 1
    assert plan.demand["FAST"] == pytest.approx(7 / 30)


def test_shared_samples_exclude_missing_future_expired_and_wrong_modes(db, cfg):
    deal = seed(db, cfg, conditions=["used"])
    for delta, used, live, tokens in [(-180, True, False, 20), (10, True, False, 40),
                                     (-1, False, False, 20), (-1, True, True, 40),
                                     (-1, True, False, None)]:
        at = NOW + timedelta(hours=delta)
        CallHistory(db, at).record(used=used, live=live, count=20, seconds=5, tokens=tokens)
    plan = Plan(History(db, cfg, NOW), [deal], {}, {}, 25)
    assert plan.costs[deal["dedupe_key"]] == 13
    assert plan.cost_sources["used"] == "ceiling"


def test_overdue_top_selection_wins_when_only_one_call_fits(db, cfg, monkeypatch):
    cfg.keepa_recheck_top_live = False
    rows, picks, wall = inventory(db, cfg, used=85, rest=100, top=100)
    calls, elapsed = timed_transport(monkeypatch, [NOW])
    KeepaBudget(3545, 60, NOW).save(db)
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False, deadline=6.3)
    assert len(calls) == 1 and set(calls[0][0]) == {d["asin"] for d in picks}
    assert result["used_checked"] == 0
    assert result["confirmed_age"]["top_candidates"]["within_1h"] == 1


@pytest.mark.parametrize("failure", ["lost", "429"])
def test_failed_calls_only_teach_latency(db, cfg, monkeypatch, failure):
    from tests.test_volatility_tiers import transport
    deal = seed(db, cfg, conditions=["used"])
    transport(monkeypatch, [NOW], failure=failure)
    KeepaBudget(3545, 60, NOW).save(db)
    verify.recheck_amazon_prices([deal], cfg, budget=KeepaBudget.load(db))
    saved = CallHistory(db, NOW).samples
    assert len(saved) == 1 and saved[0]["tokens"] is None
    plan = Plan(History(db, cfg, NOW), [deal], {}, {}, 25)
    assert plan.costs[deal["dedupe_key"]] == 13


def test_call_receipts_are_bounded_and_zero_is_a_real_receipt(db, cfg):
    deal = seed(db, cfg, conditions=["used"])
    for i in range(30):
        CallHistory(db, NOW - timedelta(minutes=30-i)).record(
            used=True, live=False, count=20, seconds=5, tokens=0)
    plan = Plan(History(db, cfg, NOW), [deal], {}, {}, 25)
    assert len(CallHistory(db, NOW).samples) == 24
    assert plan.costs[deal["dedupe_key"]] == 0
    assert plan.cost_sources["used"] == "shared_call_receipts"


def test_no_call_starts_without_time_for_the_margin(db, cfg, monkeypatch):
    seed(db, cfg)
    calls, _ = timed_transport(monkeypatch, [NOW])
    KeepaBudget(3545, 60, NOW).save(db)
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False, deadline=6)
    assert not calls  # one stats call needs 1.2 s + 5 s to apply/save


def test_slow_response_reduces_remaining_calls_in_the_same_run(db, cfg, monkeypatch):
    cfg.keepa_recheck_top_live = False
    inventory(db, cfg, used=85, rest=0, top=0)
    calls, elapsed = timed_transport(monkeypatch, [NOW], offers_seconds=(20,))
    KeepaBudget(3545, 60, NOW).save(db)
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False, deadline=40)
    assert len(calls) == 1 and elapsed[0] == 20
    assert result["used_checked"] == 20


def fast_day(db, cfg, monkeypatch, *, duration=1440, offer_cost=13, used=110):
    # Same 190-top / 110-used-wall load as the existing 095 capacity test.
    # The 85-used-wall live regression above remains a separate fixture.
    rows, picks, wall = inventory(db, cfg, used=used)
    clock, balance = [NOW], [3545]
    calls, elapsed = timed_transport(monkeypatch, clock, balance=balance, cost=offer_cost)
    gaps = (15, 35, 20, 30, 25)
    starts(db, list(gaps) * 3)
    KeepaBudget(3545, 60, NOW).save(db)
    tick = minute = 0
    lost = max_seconds = 0
    post_used, post_top, before_used, before_top = [], [], [], []
    while minute < duration:
        clock[0] = NOW + timedelta(minutes=minute)
        elapsed[0] = minute * 60
        budget = KeepaBudget.load(db)
        if minute >= 120:
            before_used.append(verify.confirmation_coverage(
                [db.get_deal(d["dedupe_key"]) for d in wall if verify.is_used_deal(d)], clock[0])["within_1h"])
            before_top.append(verify.confirmation_coverage(
                [db.get_deal(d["dedupe_key"]) for d in picks], clock[0])["within_1h"])
        db.start_run(f"batch-sim-{tick}", "fast", clock[0])
        # Ordinary feed ceiling: 20 pages, no free confirmations. At 1s/page
        # these precede the live run's remaining 80-second recheck window.
        budget.reserve(100, clock[0])
        budget.save(db)
        balance[0] = budget.available(clock[0])
        def discover(budget):
            from dealradar.keepa_budget import run_gap_forecast
            pages = 0
            gap = run_gap_forecast(db, cfg, clock[0])["minutes"]
            while pages < 8 and budget.overflow(gap, clock[0]) >= 5 and budget.available(clock[0]) >= 305:
                if elapsed[0] + 1 > minute * 60 + 80:
                    break
                budget.reserve(5, clock[0])
                balance[0] -= 5
                elapsed[0] += 1
                pages += 1
            return {"pages": pages, "tokens": pages * 5}
        summary = pipeline._recheck_prices(db, cfg, clock[0], image_backfill=False,
            deadline=minute * 60 + 80, overflow_discovery=discover)
        seconds = elapsed[0] - minute * 60
        max_seconds = max(max_seconds, seconds)
        assert seconds <= 80
        assert summary["hidden_recovery"]["requested"] <= 150 + 10 * 13
        assert 10 <= summary["tokens"]["bucket_end"] <= 3600
        if minute >= 120:
            ages = summary["confirmed_age"]
            post_used.append(ages["wall"]["used"]["within_1h"])
            post_top.append(ages["top_candidates"]["within_1h"])
            lost += KeepaBudget.load(db).overflow(gaps[tick % len(gaps)], clock[0])
        with db.conn:
            db.conn.execute("INSERT INTO efficiency_runs(run_key,started_at,observed_at,pending,metrics) VALUES (?,?,?,0,?)",
                (f"batch-sim-{tick}", util.iso(clock[0]), util.iso(clock[0]),
                 json.dumps({"feed_tokens": 100 + summary.get("overflow_discovery", {}).get("tokens", 0)})))
        minute += gaps[tick % len(gaps)]
        tick += 1
    result = dict(runs=tick, max_seconds=max_seconds, min_used_1h=min(post_used), min_top_1h=min(post_top),
                  mean_used_1h=sum(post_used) / len(post_used), mean_top_1h=sum(post_top) / len(post_top),
                  min_before_used_1h=min(before_used), min_before_top_1h=min(before_top),
                  unused_per_hour=lost * 60 / (minute - 120), calls=len(calls))
    print("BATCH_SIM " + json.dumps({"volatility": cfg.keepa_volatility["enabled"], "offer_cost": offer_cost, **result}), flush=True)
    return result


@pytest.mark.parametrize("enabled", [False, True], ids=["legacy", "batched"])
def test_fast_day_with_latency_and_irregular_gaps(db, cfg, monkeypatch, enabled):
    cfg.keepa_volatility["enabled"] = enabled
    cfg.keepa_tokens_per_minute = 60
    cfg.keepa_recheck_top_live = False
    result = fast_day(db, cfg, monkeypatch)
    assert result["min_used_1h"] >= .95
    assert result["min_top_1h"] >= .95
    assert result["min_before_used_1h"] >= .95
    assert result["min_before_top_1h"] >= .95
    if enabled:
        assert result["unused_per_hour"] <= 120  # <=2 minutes' refill/hour (3.3%)
