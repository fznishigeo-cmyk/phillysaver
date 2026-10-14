"""095 capacity experiment: production scheduler, clocks, history and budgets.

Run: .venv/bin/python -m pytest -q -s tests/test_volatility_simulation.py
The printed JSON is the source of the task HANDOFF table. No network, free
matches, arrivals, price changes or forced-live used readings are modeled.
"""
import json
from collections import defaultdict
from datetime import timedelta

import pytest

from dealradar import pipeline, util, verify
from dealradar.config import load_config
from dealradar.keepa_budget import KeepaBudget, run_gap_forecast
from dealradar.volatility import DEFAULT_TARGETS
from tests.test_price_accuracy_engine import NOW, db, product, seed
from tests.test_real_run_recovery import starts
from tests.test_value_per_token_rechecks import minutes
from tests.test_wall_confirmation_coverage import remember


def simulate(db, cfg, monkeypatch, *, gaps=(15, 35, 20, 30, 25), duration=48 * 60, warmup=24 * 60):
    rows, cohorts, initial = [], {}, []
    prototype = seed(db, cfg, asin="B000000000", seen=NOW - timedelta(days=4))
    def fixture_seed(asin, **changes):
        # The experiment concerns scheduling, not fuzzy ingestion. Copy a
        # verified fixture through the DB API instead of O(n²) upserts.
        key = prototype["dedupe_key"] if asin == "B000000000" else "sim-" + asin
        deal = dict(prototype, dedupe_key=key, asin=asin, item="Widget Pro " + asin,
                    canonical_url="https://www.amazon.com/dp/" + asin, **changes)
        if asin == "B000000000":
            db.update_deal(key, payload=deal)
        else:
            db.insert_deal(dedupe_key=key, store="Amazon", item=deal["item"],
                normalized_item=util.normalize_item(deal["item"]), canonical_url=deal["canonical_url"],
                payload=deal, final_price=25, pct_at_checkout=75, abs_savings=75,
                online_only="true", expires_at="", now=NOW - timedelta(days=4))
        return db.get_deal(key)
    def add(count, name, visibility, used=False):
        group = []
        for i in range(count):
            index = len(rows)
            age = (30 + 48 * i / count) if visibility == "hidden" else i % 20 / 60
            stamp = NOW - timedelta(hours=age)
            deal = fixture_seed(asin=f"B{index:09}",
                        price_rechecked_at=util.iso(stamp),
                        conditions=["used"] if used else ["lightning"] if name == "FAST" else [])
            if name == "SLOW":
                for days in (12, 4):
                    db.record_price(deal["dedupe_key"], "Amazon", 25,
                                    NOW - timedelta(days=days), f"stable-{index}-{days}")
            live = visibility == "top"
            initial.append((deal["dedupe_key"], json.dumps([{
                "requested_at": util.iso(stamp), "observed_at": util.iso(stamp),
                "reading": True, "answered": True, "changed": False, "death": False,
                "shown_price": 25, "live_price": 25, "last_change_at": "",
                "tokens": (13 if used else 1) + live,
                "request_tokens": (13 if used else 1) + live, "estimated_tokens": 0,
                "used": used, "live": live}])) )
            rows.append(deal)
            cohorts[deal["dedupe_key"]] = name, visibility
            group.append(deal)
        return group
    top = add(40, "FAST", "top") + add(120, "NORMAL", "top") + add(30, "SLOW", "top")
    # 170 wall rows: 30 top overlaps, 30 other new, 110 used.
    wall = top[:10] + top[40:50] + top[-10:]
    wall += add(10, "FAST", "wall") + add(10, "NORMAL", "wall") + add(10, "SLOW", "wall")
    wall += add(110, "FAST", "wall", used=True)
    add(100, "FAST", "rest")
    add(800, "NORMAL", "rest")
    add(400, "SLOW", "rest")
    add(520, "FAST", "hidden", used=True)
    for i in range(280):
        deal = fixture_seed(asin=f"C{i:09}", price_rechecked_at=util.iso(NOW))
        db.update_deal(deal["dedupe_key"], payload=dict(deal, asin=""))
    with db.conn:
        db.conn.executemany("INSERT INTO keepa_recheck_history VALUES (?,?)", initial)
    remember(db, top, wall)
    starts(db, list(gaps) * 3)
    clock = [NOW]
    used_tokens = 0
    def check(deals, cfg, budget, **options):
        nonlocal used_tokens
        response = verify.PriceRechecks()
        for deal in deals:
            used = verify.is_used_deal(deal)
            cost = (13 if used else 1) + int(options.get("live", False))
            used_tokens += cost
            budget.reserve(cost, clock[0])
            assert budget.available(clock[0]) >= 10
            budget.usage.note({"tokensConsumed": cost})
            key = deal["dedupe_key"]
            at = minutes(clock[0])
            record = product(2500, asin=deal["asin"])
            record["lastUpdate"] = at
            if used:
                record.update(offers=[{"condition": 2, "offerCSV": [at, 2500, 0]}] * 20,
                              liveOffersOrder=list(range(20)), offersSuccessful=True)
                record["stats"]["lastOffersUpdate"] = at
            response[key] = True
            response.products[key], response.costs[key] = record, (cost, 0)
        return response
    monkeypatch.setattr(verify, "recheck_amazon_prices", check)
    monkeypatch.setattr(util, "now_utc", lambda: clock[0])
    rate = cfg.keepa_tokens_per_minute
    from dealradar.fetch.keepa import DEAL_PAGE_COST
    ordinary_pages = (cfg.keepa_amazon_pages_per_pull
        + cfg.keepa_warehouse_pages_per_pull + len(cfg.keepa_focus_categories))
    KeepaBudget(rate * 60, rate, NOW).save(db)
    tally = defaultdict(lambda: dict(max_h=0, covered=0, samples=0, unknown=0))
    elapsed = tick = discovery_tokens = 0
    worst_top = 1
    needed = None
    while elapsed < duration:
        now = clock[0] = NOW + timedelta(minutes=elapsed)
        # Measure immediately BEFORE a run: includes the whole gap in which
        # a shopper could click, not just the freshly reset post-run ages.
        if elapsed >= warmup:
            for row in db.conn.execute("SELECT deal_key,samples FROM keepa_recheck_history"):
                if row["deal_key"] not in cohorts:
                    continue
                name, visibility = cohorts[row["deal_key"]]
                sample = max(json.loads(row["samples"]), key=lambda s: s["requested_at"])
                for kind, field in (("request", "requested_at"), ("keepa", "observed_at")):
                    at = util.parse_iso(sample[field])
                    age = (now - at).total_seconds() / 3600
                    cell = tally[name, visibility, kind]
                    cell["max_h"] = max(cell["max_h"], age)
                    cell["covered"] += age <= DEFAULT_TARGETS[name][visibility] + 1e-9
                    cell["samples"] += 1
        db.start_run(f"sim-{tick}", "hourly" if tick % 3 == 0 else "fast", now)
        budget = KeepaBudget.load(db)
        feed_before = discovery_tokens
        # Current configured page ceiling: 6 Amazon + 12 Warehouse + 2 focus.
        # The two entry pages keep the deal reserve; further pages keep the
        # product reserve. Short pages/post caps can reduce this workload.
        for page in range(ordinary_pages):
            need = 10 if page < 2 else cfg.keepa_reserve_tokens + DEAL_PAGE_COST
            if not budget.allows(need, now):
                break
            budget.reserve(DEAL_PAGE_COST, now)
            discovery_tokens += DEAL_PAGE_COST
        budget.save(db)
        def discover(budget):
            nonlocal discovery_tokens
            pages = 0
            gap = run_gap_forecast(db, cfg, now)["minutes"]
            while (pages < cfg.keepa_overflow_extra_pages_max and budget.overflow(gap, now) >= 5
                   and budget.available(now) >= cfg.keepa_reserve_tokens + 5):
                budget.reserve(5, now)
                pages += 1
            discovery_tokens += pages * 5
            return {"pages": pages, "tokens": pages * 5}
        summary = pipeline._recheck_prices(db, cfg, now, image_backfill=False, overflow_discovery=discover)
        with db.conn:
            db.conn.execute("INSERT INTO efficiency_runs(run_key,started_at,observed_at,pending,metrics) "
                            "VALUES (?,?,?,0,?)", (f"sim-{tick}", util.iso(now), util.iso(now),
                            json.dumps({"feed_tokens": discovery_tokens - feed_before})))
        assert summary["hidden_recovery"]["requested"] <= 150 + 10 * 13
        assert 10 <= summary["tokens"]["bucket_end"] <= rate * 60
        needed = summary["token_plan"]["needed_per_minute"] if needed is None else needed
        if elapsed >= 120:
            normal = summary["confirmed_age"]["top_candidates"]["tiers"]["NORMAL"]
            worst_top = min(worst_top, normal["within_1h"])
        tick += 1
        if tick % 24 == 0:
            print(f"SIM_PROGRESS rate={rate} runs={tick} elapsed_h={elapsed / 60:.1f}", flush=True)
        elapsed += gaps[(tick - 1) % len(gaps)]
    result = {"rate": rate, "runs": tick, "needed_per_minute_initial": needed,
              "recheck_tokens": used_tokens, "discovery_tokens": discovery_tokens,
              "min_normal_top_post_run_1h": worst_top,
              "cells": {"/".join(key): {"max_h": round(c["max_h"], 3),
                         "coverage": round(c["covered"] / c["samples"], 4)} for key, c in tally.items()}}
    print("VOLATILITY_SIM " + json.dumps(result, sort_keys=True), flush=True)
    return result


@pytest.mark.slow  # ~50 min: five 48-hour capacity runs; run with -m slow
@pytest.mark.parametrize("rate", [20, 60, 100, 150, 250])
def test_sweet_spot(db, monkeypatch, rate):
    cfg = load_config()
    cfg.keepa_volatility["enabled"] = True
    cfg.keepa_tokens_per_minute = rate
    result = simulate(db, cfg, monkeypatch)
    assert result["min_normal_top_post_run_1h"] == 1
    # All modeled answers have fresh clocks; reporting must expose equality.
    for key, value in result["cells"].items():
        if key.endswith("/request"):
            assert value == result["cells"][key.removesuffix("request") + "keepa"]
    # The 13-token scan gate and recovered used inventory leave 100/min
    # with a 50-minute wall maximum. Require the higher rates to meet the goal.
    if rate >= 150:
        for name in ("FAST/top/request", "FAST/wall/request"):
            assert result["cells"][name]["max_h"] <= 40 / 60
        assert result["cells"]["NORMAL/top/request"]["max_h"] <= 1
    else:
        assert result["cells"]["FAST/wall/request"]["max_h"] > 40 / 60


def test_144_runs_at_20_keep_normal_top_within_one_hour(db, monkeypatch):
    cfg = load_config()
    cfg.keepa_volatility["enabled"] = True  # the scheduler under test; shipped switch is off for now
    result = simulate(db, cfg, monkeypatch, gaps=(10,), duration=1440, warmup=120)
    assert result["runs"] == 144
    assert result["min_normal_top_post_run_1h"] == 1
