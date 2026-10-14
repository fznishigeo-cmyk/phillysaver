"""095: volatility facts, capacity planning and real scheduler integration."""
import hashlib
import json
from datetime import timedelta

import httpx
import pytest

from dealradar import pipeline, rank, util, verify
from dealradar.config import load_config
from dealradar.keepa_budget import KeepaBudget
from dealradar.keepa_rechecks import History
from dealradar.volatility import Plan, tier, stretch
from tests.http_helpers import mock_keepa_get
from tests.test_price_accuracy_engine import NOW, db, product, seed
from tests.test_value_per_token_rechecks import minutes
from tests.test_wall_confirmation_coverage import remember


@pytest.fixture
def cfg():
    cfg = load_config()
    # These tests exercise the volatility scheduler itself; the shipped switch
    # is off until its batching fix (2026-10-14).
    cfg.keepa_volatility["enabled"] = True
    return cfg


@pytest.mark.parametrize("changes,reason", [
    ({"conditions": [c]}, c) for c in ("used", "open_box", "refurbished")
] + [({"store": "Amazon Warehouse"}, "warehouse"),
     ({"conditions": ["limited_qty"]}, "limited_quantity_or_variant"),
     *[({"how": [s]}, "limited_quantity_or_variant") for s in (
         "Only 2 left", "single size", "one color", "only style", "size XL only")],
     ({"item": "Lightning deal"}, "lightning"),
     ({"conditions": ["clearance_aisle"]}, "clearance"),
     ({"conditions": ["pricing_error"]}, "pricing_error")])
def test_fast_rules_win_over_stock(changes, reason):
    result = tier(changes, [], NOW, plenty_of_stock=True)
    assert result["tier"] == "FAST" and reason in result["reasons"]


def test_observed_changes_boundaries_and_stability():
    observations = [{"price": 25, "observed_at": util.iso(NOW - timedelta(days=8))},
                    {"price": 24, "observed_at": util.iso(NOW - timedelta(hours=48))}]
    assert tier({}, observations, NOW)["tier"] == "FAST"
    assert tier({}, observations, NOW + timedelta(seconds=1))["tier"] == "NORMAL"
    observations[1]["price"] = 25
    assert tier({}, observations, NOW)["tier"] == "NORMAL"  # only six observed days
    observations.append({"price": 25, "observed_at": util.iso(NOW)})
    assert tier({}, observations, NOW)["reasons"] == ["observed_unchanged_7d"]
    assert tier({"first_seen_at": util.iso(NOW - timedelta(days=90))}, [], NOW)["tier"] == "NORMAL"
    assert tier({}, [{"price": 1, "observed_at": util.iso(NOW + timedelta(days=1))}], NOW)["tier"] == "NORMAL"


@pytest.mark.parametrize("promo", [{"codes": ["SAVE"]}, {"stack_steps": ["Clip the coupon"]},
                                  {"stack_steps": ["Apply the promo at checkout"]}])
def test_slow_stock_and_verified_stable_promo_basis(promo):
    assert tier({}, [], NOW, plenty_of_stock=True)["reasons"] == ["plenty_of_stock"]
    deal = {**promo, "regular_price": 100}
    points = [{"price": 25, "regular_price": 100, "observed_at": util.iso(NOW - timedelta(hours=h))}
              for h in (24, 0)]
    assert tier(deal, points, NOW)["reasons"] == ["promo_stable_list_price"]
    assert tier(deal, points[:1], NOW)["tier"] == "NORMAL"
    points[0]["regular_price"] = 110
    assert tier(deal, points, NOW)["tier"] == "NORMAL"
    assert tier({}, [{"price": 25, "observed_at": util.iso(NOW),
                     "stable_since": util.iso(NOW - timedelta(days=7))}], NOW)["tier"] == "SLOW"


def test_small_observed_change_survives_bounded_history_and_cached_reads(db, cfg):
    deal = seed(db, cfg)
    for i in range(30):
        at = NOW - timedelta(hours=30 - i)
        record = product(2500 if i == 0 else 2501)
        History(db, cfg, at).record(deal, record, True, at, "rest_new", 1, 0)
    history = History(db, cfg, NOW)
    assert len(history.samples[deal["dedupe_key"]]) == 12
    points = [dict(s, price=s["live_price"]) for s in history.samples[deal["dedupe_key"]]]
    assert tier(deal, points, NOW)["reasons"] == ["observed_price_change_48h"]
    assert tier(deal, points, NOW + timedelta(hours=20))["tier"] == "NORMAL"
    last = history.samples[deal["dedupe_key"]][-1]
    history.record(deal, product(2501), True, util.parse_iso(last["observed_at"]), "rest_new", 1, 0)
    folded = history.samples[deal["dedupe_key"]][-1]
    assert folded["stable_since"] == last["stable_since"]
    assert folded["price_changed_at"] == last["price_changed_at"]


def test_zero_capacity_and_exceptional_overload_are_reported_as_stretched():
    demand = {"FAST": 0, "NORMAL": 200, "SLOW": 5}
    factors = stretch(demand, 20, {"NORMAL": 4/3, "SLOW": 4})
    assert sum(demand[t] / factors[t] for t in demand) == pytest.approx(20)
    factors = stretch(demand, 0, {"NORMAL": 4/3, "SLOW": 4})
    assert sum(demand[t] / factors[t] for t in demand) == 0


def make_plan(db, cfg, rows, top=(), wall=(), gap=25):
    return Plan(History(db, cfg, NOW), rows, {d["dedupe_key"]: i for i, d in enumerate(top)},
                {d["dedupe_key"]: i for i, d in enumerate(wall)}, gap)


def test_stretch_slow_then_normal_then_fast_and_rate_knob(db, cfg):
    cfg.keepa_tokens_per_minute = 20  # the original plan; shipped config is 60 since 2026-10-14
    assert stretch({"FAST": 10, "NORMAL": 10, "SLOW": 10}, 25, {"SLOW": 4, "NORMAL": 4/3}) == {
        "FAST": 1, "NORMAL": 1, "SLOW": 2}
    top = [seed(db, cfg, asin=f"B0{i:08}") for i in range(190)]
    wall = [seed(db, cfg, asin=f"B1{i:08}", conditions=["used"]) for i in range(110)]
    low = make_plan(db, cfg, top + wall, top, wall)
    cfg.keepa_tokens_per_minute = 60
    high = make_plan(db, cfg, top + wall, top, wall)
    assert low.factors["FAST"] > high.factors["FAST"] == 1
    assert low.target(top[0]) == pytest.approx(1)
    assert .75 <= high.target(top[0]) <= 1 and high.target(wall[0]) == .5
    assert sum(low.demand[t] / low.factors[t] for t in low.demand) <= low.granted + 1e-9


def test_real_receipts_ignore_estimates_and_folded_costs(db, cfg):
    deal = seed(db, cfg, conditions=["used"])
    for minutes_ago, cost, estimated in ((30, 7, 0), (20, 13, 0), (10, 13, 13)):
        at = NOW - timedelta(minutes=minutes_ago)
        History(db, cfg, at).record(deal, product(2500), None, at, "wall_used", cost, estimated)
    plan = make_plan(db, cfg, [deal], wall=[deal])
    assert plan.costs[deal["dedupe_key"]] == 10
    assert plan.cost_samples["used"] == 2
    assert plan.report([deal])["tiers"]["FAST"]["classes"]["wall"]["mean_cost"] == 10


def test_costs_prefer_this_offer_and_accept_a_zero_receipt(db, cfg):
    rows = [seed(db, cfg, asin=f"B0{i:08}", conditions=["used"]) for i in range(3)]
    for deal, cost in zip(rows, (0, 7, 13)):
        History(db, cfg, NOW).record(deal, product(2500), None, NOW, "wall_used", cost, 0)
    plan = make_plan(db, cfg, rows, wall=rows)
    assert [plan.costs[d["dedupe_key"]] for d in rows] == [0, 7, 13]
    assert len(plan.order(rows, {})) == 3


def test_observed_discovery_cost_and_free_clock_are_not_paid_requests(db, cfg):
    deal = seed(db, cfg, keepa_price_seen_at=util.iso(NOW))
    with db.conn:
        for i in range(3):
            at = util.iso(NOW - timedelta(minutes=(i + 1) * 25))
            db.conn.execute("INSERT INTO efficiency_runs(run_key,started_at,observed_at,metrics,pending) VALUES (?,?,?,?,0)",
                            (str(i), at, at, json.dumps({"feed_tokens": 50})))
    plan = make_plan(db, cfg, [deal])
    assert plan.discovery == 2 and plan.discovery_source == "observed_feed_receipts"
    assert plan.last_check(deal) is None
    assert plan.report([deal])["tiers"]["NORMAL"]["classes"]["rest"]["request_age"]["unknown"] == 1


def test_value_weighted_overdue_order_crosses_condition_and_explores(db, cfg):
    younger = seed(db, cfg, price_rechecked_at=util.iso(NOW - timedelta(minutes=20)))
    older = seed(db, cfg, asin="B100000001", price_rechecked_at=util.iso(NOW - timedelta(hours=4)))
    used = seed(db, cfg, asin="B200000001", conditions=["used"],
                price_rechecked_at=util.iso(NOW - timedelta(hours=10)))
    rows = [younger, older, used]
    plan = make_plan(db, cfg, rows, wall=rows)
    assert plan.order(rows, {})[0] == older  # age / target × visibility / actual cost
    assert plan.order(rows, {used["dedupe_key"]: "top"})[0] == used


@pytest.mark.parametrize("tokens,share", [(15, .35), (1200, 0)])
def test_clicked_used_still_obeys_budget_and_share(db, cfg, monkeypatch, tokens, share):
    deal = seed(db, cfg, conditions=["used"], price_rechecked_at=util.iso(NOW - timedelta(minutes=15)))
    remember(db, wall=[deal])
    add_click(db, cfg, deal)
    cfg.keepa_used_scan_budget_share = share
    calls = transport(monkeypatch, [NOW])
    KeepaBudget(tokens, 20, NOW).save(db)
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert not calls


@pytest.mark.parametrize("tokens,expected", [(1200, 1), (400, 0)])
def test_overflow_still_buys_early_checks_after_discovery(db, cfg, monkeypatch, tokens, expected):
    from tests.test_real_run_recovery import starts
    deal = seed(db, cfg, price_rechecked_at=util.iso(NOW - timedelta(minutes=70)))
    for days in (12, 4):
        db.record_price(deal["dedupe_key"], "Amazon", 25, NOW - timedelta(days=days), str(days))
    remember(db, top=[deal])
    starts(db, [25, 25, 25])
    calls = transport(monkeypatch, [NOW])
    KeepaBudget(tokens, 20, NOW).save(db)
    def discovery(budget):
        assert not calls
        return {"pages": 0, "tokens": 0}
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False, overflow_discovery=discovery)
    assert stats["done"]["idle_top_new"] == len(calls) == expected


def test_hidden_recovery_keeps_pick_value_ranking_and_bound(db, cfg, monkeypatch):
    rows = [seed(db, cfg, asin=f"B0{i:08}", seen=NOW - timedelta(days=4),
                 brand="Known" if i % 2 else "") for i in range(6)]
    cfg.keepa_hidden_new_tokens_per_run = 2
    expected = sorted(rows, key=lambda d: (-rank.pick_score(d, cfg, NOW, require_confirmation=False),
                                           d["dedupe_key"]))[:2]
    calls = transport(monkeypatch, [NOW])
    KeepaBudget(1200, 20, NOW).save(db)
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert [a for call in calls for a in call[0]] == [d["asin"] for d in expected]
    assert stats["done"]["hidden_new"] == 2


def transport(monkeypatch, clock, *, failure=None, lag=0):
    monkeypatch.setenv("KEEPA_KEY", "offline")
    monkeypatch.setattr(util, "now_utc", lambda: clock[0])
    calls = []
    def get(url, params=None, **kwargs):
        assert url == "https://api.keepa.com/product" and str(params["history"]) == "0"
        asins = params["asin"].split(",")
        calls.append((asins, "offers" in params, "update" in params))
        if failure == "lost":
            raise httpx.ReadTimeout("offline")
        if failure == "429":
            return httpx.Response(429, json={"tokensLeft": 1000, "refillRate": 20})
        records = []
        for asin in asins:
            record = product(2500, asin=asin)
            at = minutes(clock[0] - timedelta(hours=lag))
            record["lastUpdate"] = at
            if "offers" in params:
                record.update(offers=[{"condition": 2, "offerCSV": [at, 2500, 0]}],
                              liveOffersOrder=[0], offersSuccessful=True)
                record["stats"]["lastOffersUpdate"] = at
            if failure == "inconclusive":
                record["stats"] = {}
            records.append(record)
        cost = (13 if "offers" in params else 1) + int("update" in params)
        return httpx.Response(200, json={"products": records, "tokensConsumed": cost * len(asins)})
    mock_keepa_get(monkeypatch, get)
    return calls


def test_overdue_top_beats_fast_click_suspect_and_hidden(db, cfg, monkeypatch):
    cfg.keepa_tokens_per_minute = 20  # matches the 20/min bucket below
    top = seed(db, cfg, price_rechecked_at=util.iso(NOW - timedelta(hours=2)))
    used = seed(db, cfg, asin="B100000001", conditions=["used"], gone_suspect_at=util.iso(NOW - timedelta(hours=3)))
    remember(db, top=[top], wall=[used])
    add_click(db, cfg, used)
    calls = transport(monkeypatch, [NOW])
    KeepaBudget(1200, 20, NOW).save(db)
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert calls[0][0] == [top["asin"]]
    assert result["token_plan"]["granted_per_minute"] == 20


@pytest.mark.parametrize("failure", ["lost", "inconclusive", "429"])
def test_retry_deadlines_run_keys_and_first_429(db, cfg, monkeypatch, failure):
    top = seed(db, cfg, price_rechecked_at=util.iso(NOW - timedelta(hours=3)))
    other = seed(db, cfg, asin="B100000001", conditions=["used"])
    remember(db, top=[top], wall=[other])
    clock = [NOW]
    calls = transport(monkeypatch, clock, failure=failure)
    KeepaBudget(1200, 20, NOW).save(db)
    db.start_run("one", "fast", NOW)
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    count = len(calls)
    if failure == "429":
        assert count == 1 and result["rate_limited"]
    clock[0] += timedelta(minutes=1)
    db.start_run("one", "fast", clock[0])
    pipeline._recheck_prices(db, cfg, clock[0], image_backfill=False)
    assert len(calls) == count
    clock[0] += timedelta(minutes=4)
    db.start_run("two", "fast", clock[0])
    pipeline._recheck_prices(db, cfg, clock[0], image_backfill=False)
    assert len(calls) > count


def add_click(db, cfg, deal):
    cfg.raw["first_party_clicks"]["enabled"] = True
    hour = int(NOW.timestamp() // 3600) - 1
    with db.conn:
        db.conn.execute("INSERT OR IGNORE INTO deal_click_coverage VALUES (?,0)", (hour,))
        db.conn.execute("INSERT OR REPLACE INTO deal_clicks VALUES (?,?,2)",
                        (hashlib.sha256(deal["dedupe_key"].encode()).hexdigest()[:16], hour))


@pytest.mark.parametrize("enabled", [False, True])
def test_click_boost_is_bounded_optional_and_same_run_allowance(db, cfg, monkeypatch, enabled):
    rows = [seed(db, cfg, asin=f"B0{i:08}", conditions=["used"] if i < 2 else [],
                 price_rechecked_at=util.iso(NOW - timedelta(minutes=15))) for i in range(4)]
    remember(db, wall=rows)
    for d in reversed(rows):
        add_click(db, cfg, d)
    cfg.raw["first_party_clicks"]["enabled"] = enabled
    cfg.keepa_volatility["click_boost_per_run"] = 1
    clock = [NOW]
    calls = transport(monkeypatch, clock)
    KeepaBudget(800, 20, NOW).save(db)  # no overflow: isolate the optional click signal
    db.start_run("click", "fast", NOW)
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert stats["token_plan"]["click_boosted"] == int(enabled)
    if enabled:
        assert calls[0][0] == [rows[0]["asin"]] and calls[0][1]
    else:
        assert not calls
    before = len(calls)
    clock[0] += timedelta(minutes=1)
    db.start_run("click", "fast", clock[0])
    pipeline._recheck_prices(db, cfg, clock[0], image_backfill=False)
    assert len(calls) == before


def test_tier_request_age_separates_keepa_lag(db, cfg, monkeypatch, tmp_path):
    from dealradar.publish.site import render_site
    used = seed(db, cfg, conditions=["used"], price_rechecked_at=util.iso(NOW - timedelta(hours=5)))
    remember(db, wall=[used])
    calls = transport(monkeypatch, [NOW], lag=3)
    KeepaBudget(1200, 20, NOW).save(db)
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    cell = result["token_plan"]["tiers"]["FAST"]["classes"]["wall"]
    assert cell["request_age"]["within_target"] == 1
    assert cell["keepa_age"]["within_target"] == 0
    assert calls
    summary = render_site(db, cfg, NOW, out_dir=tmp_path)
    ages = summary["confirmed_age"]["wall"]["tiers"]["FAST"]
    assert ages["request_age"]["used"]["within_1h"] == 1 and ages["used"]["within_1h"] == 0
    assert "token_plan" not in (tmp_path / "deals.json").read_text()
