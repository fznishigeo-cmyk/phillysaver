"""112: shopper-price evidence and bounded surplus, using only mocked Keepa."""

from copy import deepcopy
from datetime import timedelta

import httpx
import pytest

from dealradar import buybox, dedupe, pipeline, rank, savings, util, verify
from dealradar.config import AppConfig, load_config
from dealradar.keepa_budget import KeepaBudget
from dealradar.keepa_rechecks import History
from dealradar.parse.extractor import verify_evidence
from dealradar.publish import site
from dealradar.volatility import Plan
from tests.http_helpers import mock_keepa_get
from tests.test_price_accuracy_engine import NOW, db, product, seed
from tests.test_wall_confirmation_coverage import remember
from tests.test_value_per_token_rechecks import minutes


@pytest.fixture
def cfg():
    cfg = load_config()
    cfg.feature_flags["keepa"] = True
    cfg.keepa_hidden_new_tokens_per_run = cfg.keepa_hidden_used_scans_per_run = 0
    return cfg


def transport(monkeypatch, db, records, *, tokens=3500, seconds=1, failure=None):
    clock, elapsed, calls, balance = [NOW], [0], [], [tokens]
    monkeypatch.setenv("KEEPA_KEY", "offline")
    monkeypatch.setattr(util, "now_utc", lambda: clock[0])
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: elapsed[0])
    KeepaBudget(tokens, 60, NOW).save(db)

    def get(url, params=None, **kwargs):
        assert url == "https://api.keepa.com/product"
        assert params["history"] == "0" and params["stats"] == 1
        calls.append(dict(params))
        elapsed[0] += seconds
        asins = params["asin"].split(",")
        cost = len(asins) * ((13 if "offers" in params else 1)
                            + (params.get("update") == "0") + 2 * (params.get("buybox") == "1"))
        balance[0] -= cost
        assert balance[0] >= 10
        if failure == "lost":
            raise httpx.ReadTimeout("offline")
        if failure == "429":
            return httpx.Response(429, json={"tokensLeft": balance[0], "refillRate": 60})
        return httpx.Response(200, json={"products": [records[a] for a in asins if a in records],
                              "tokensConsumed": cost, "tokensLeft": balance[0], "refillRate": 60})

    fetcher = mock_keepa_get(monkeypatch, get)
    fetcher._clock = lambda: elapsed[0]
    fetcher._sleep = lambda s: elapsed.__setitem__(0, elapsed[0] + s)
    return calls, clock, elapsed


def record(deal, total=30, *, age=5):
    return product(2500, asin=deal["asin"], buyBoxPrice=round(total * 100) - 200,
                   buyBoxShipping=200, lastBuyBoxUpdate=minutes(NOW - timedelta(minutes=age)))


def run(db, cfg, now=NOW, **kwargs):
    return pipeline._recheck_prices(db, cfg, now, image_backfill=False, **kwargs)


def test_shipping_config_and_safe_defaults():
    cfg = load_config()
    assert (cfg.keepa_buybox_top_n, cfg.keepa_buybox_interval_min) == (120, 60)
    assert cfg.keepa_recheck_surplus and cfg.keepa_recheck_top_live_adaptive
    assert cfg.keepa_recheck_surplus_reserve >= 140 + 120 * 4
    assert AppConfig().keepa_buybox_top_n == 0
    assert not AppConfig().keepa_recheck_surplus
    assert not AppConfig().keepa_recheck_top_live_adaptive


@pytest.mark.parametrize("total", [20, 25, 30, 90])
def test_buybox_reprices_with_evidence_savings_history_and_idempotency(db, cfg, monkeypatch, total):
    row = seed(db, cfg)
    remember(db, top=[row], wall=[row])
    calls, _, _ = transport(monkeypatch, db, {row["asin"]: record(row, total)})
    checked, reprice = [], dedupe.reprice_existing

    def correction(db, deal, post, cfg, now, **kwargs):
        checked.append(post)
        return reprice(db, deal, post, cfg, now, **kwargs)

    monkeypatch.setattr(dedupe, "reprice_existing", correction)
    result = run(db, cfg)
    after = db.get_deal(row["dedupe_key"])
    assert after["final_price"] == total and after["status"] == "alive"
    assert after["critic"] == row["critic"] and after["sightings"] == row["sightings"]
    observed = NOW - timedelta(minutes=5)
    assert after["price_rechecked_at"] == util.iso(observed)
    assert after["buybox_check"] == {"asin": row["asin"], "final": total,
                                    "at": util.iso(observed), "source": "buybox", "price": total}
    assert not buybox.would_unconfirm(after, NOW)
    report = result["buybox_top"]
    assert report["requested"] == report["with_buybox"] == 1 and report["tokens"] == 4
    assert report["repriced_up" if total > 25 else "repriced_down" if total < 25 else "unchanged"] == 1
    assert calls[0]["buybox"] == "1" and calls[0]["update"] == "0" and "offers" not in calls[0]
    sample = History(db, cfg, NOW).latest_request(row["dedupe_key"])
    assert sample["live_price"] == total and sample["observed_at"] == util.iso(observed)
    if total != 25:
        from dealradar.parse.schema import LLMDeal, to_extracted
        post = checked[0]
        extracted = to_extracted(LLMDeal.model_validate(post.structured), post, "UTC", NOW)
        assert verify_evidence(extracted, post.text)[0]
        assert "including shipping" in extracted.evidence.final_price_quote
        computed = savings.compute_savings(extracted)
        assert after["pct_at_checkout"] == computed.pct_at_checkout
        assert after["abs_savings"] == computed.abs_savings
        observations = db.price_observations(row["dedupe_key"], NOW - timedelta(days=1), NOW)
        assert any(p["price"] == total and p["observed_at"] == util.iso(observed) for p in observations)
    if total == 90:
        assert not rank.is_publishable(after, cfg)[0]
    snapshot, count = deepcopy(db.all_deals()), len(calls)
    assert run(db, cfg)["buybox_top"]["requested"] == 0
    assert db.all_deals() == snapshot and len(calls) == count


@pytest.mark.parametrize("invalid", ["missing", "clock_missing", "unqualified", "stale", "future", "before_price", "older_confirmation"])
def test_invalid_buybox_falls_back_to_existing_reprice(db, cfg, monkeypatch, invalid):
    row = seed(db, cfg)
    p = record(row, 40)
    p["stats"]["current"][0] = 2600
    if invalid == "missing":
        del p["stats"]["buyBoxPrice"]
    elif invalid == "clock_missing":
        del p["stats"]["lastBuyBoxUpdate"]
    elif invalid == "unqualified":
        p["stats"]["buyBoxIsUnqualified"] = True
    elif invalid in ("stale", "future", "before_price"):
        p["stats"]["lastBuyBoxUpdate"] = minutes(NOW + timedelta(minutes={"stale": -61, "future": 1, "before_price": -600}[invalid]))
    else:
        row["price_rechecked_at"] = util.iso(NOW - timedelta(minutes=1))
        db.update_deal(row["dedupe_key"], payload=row)
    remember(db, top=[row], wall=[row])
    transport(monkeypatch, db, {row["asin"]: p})
    result = run(db, cfg)
    assert db.get_deal(row["dedupe_key"])["final_price"] == 26
    assert result["buybox_top"]["with_buybox"] == 0


def test_only_published_top_positions_plain_new_request_buybox(db, cfg, monkeypatch):
    changes = [{}, {"codes": ["SAVE"]}, {"conditions": ["used"]},
               {"conditions": ["open_box"]}, {"conditions": ["subscribe_save"]},
               {"stack_steps": ["Clip the coupon"]}, {}, {}]
    rows = [seed(db, cfg, asin=f"B{i:09d}", **change) for i, change in enumerate(changes)]
    cfg.keepa_buybox_top_n = 6
    # Candidate order disagrees with publication; positions 6+ never upgrade.
    remember(db, top=list(reversed(rows)), wall=rows)
    calls, _, _ = transport(monkeypatch, db, {d["asin"]: record(d) for d in rows})
    run(db, cfg)
    assert [c["asin"] for c in calls if "buybox" in c] == [rows[0]["asin"]]
    assert all(db.get_deal(d["dedupe_key"])["final_price"] == 25 for d in rows[1:6])
    assert all("buybox" not in c for c in calls if "offers" in c)


def test_interval_uses_paid_attempt_even_for_missing_buybox(db, cfg, monkeypatch):
    row = seed(db, cfg)
    remember(db, top=[row], wall=[row])
    calls, clock, _ = transport(monkeypatch, db, {row["asin"]: product(2500)})
    for minute in (0, 30, 59, 60):
        clock[0] = NOW + timedelta(minutes=minute)
        run(db, cfg, clock[0])
        assert sum("buybox" in c for c in calls) == (2 if minute == 60 else 1)


@pytest.mark.parametrize("tokens", [15, 16, 500, 3500])
def test_cost_planner_fallback_and_adaptive_update(db, cfg, monkeypatch, tokens):
    rows = [seed(db, cfg, asin=f"B{i:09d}") for i in range(3)]
    remember(db, top=rows, wall=rows)
    calls, _, _ = transport(monkeypatch, db, {d["asin"]: record(d) for d in rows}, tokens=tokens)
    result = run(db, cfg)
    expected_box = tokens >= 22
    assert all(("buybox" in c) == expected_box for c in calls)
    assert all(c["update"] == ("0" if tokens == 3500 else "1") for c in calls)
    count = sum(len(c["asin"].split(",")) for c in calls)
    assert result["tokens"]["requested"] == count * (4 if expected_box else 2)
    assert result["live_reads"] == {"forced": count if tokens == 3500 else 0,
                                   "conditional": 0 if tokens == 3500 else count}
    assert result["buybox_top"]["requested"] == (count if expected_box else 0)
    plan = Plan(History(db, cfg, NOW), rows, {d["dedupe_key"]: i for i, d in enumerate(rows)},
                {d["dedupe_key"]: i for i, d in enumerate(rows)}, 10)
    # Conditional stats receipts teach the base cost; Buy Box receipts must
    # not pollute it and then have their two-token premium counted twice.
    assert all(cost == (4 if expected_box else 3) for cost in plan.costs.values())


@pytest.mark.parametrize("enabled,tokens", [(True, 3500), (True, 1000), (False, 3500)])
def test_surplus_value_order_oldest_rest_and_no_overflow_no_extra_reads(db, cfg, monkeypatch, enabled, tokens):
    cfg.keepa_recheck_surplus = enabled
    cfg.keepa_volatility["targets"]["FAST"]["wall"] = 2
    top = seed(db, cfg, asin="B000000001")
    used = seed(db, cfg, asin="B000000002", conditions=["used"],
                price_rechecked_at=util.iso(NOW - timedelta(minutes=35)))
    newer = seed(db, cfg, asin="B000000003", price_rechecked_at=util.iso(NOW - timedelta(hours=1)))
    older = seed(db, cfg, asin="B000000004", price_rechecked_at=util.iso(NOW - timedelta(hours=3)))
    remember(db, top=[top], wall=[top, used])
    records = {d["asin"]: record(d, 25) for d in (top, used, newer, older)}
    p = records[used["asin"]]
    p.update(offers=[{"condition": 2, "offerCSV": [minutes(NOW), 2500, 0]}], liveOffersOrder=[0])
    p["stats"]["lastOffersUpdate"] = minutes(NOW)
    calls, _, _ = transport(monkeypatch, db, records, tokens=tokens)
    result = run(db, cfg)
    wanted = [top["asin"], used["asin"], older["asin"], newer["asin"]] if enabled and tokens == 3500 else [top["asin"]]
    assert [a for c in calls for a in c["asin"].split(",")] == wanted
    if enabled and tokens == 3500:
        assert result["tokens"]["surplus_spent"] == {"buybox_top": 4, "wall_used": 13, "rest": 2}
        assert result["tokens"]["surplus_granted"] == 500
        assert result["tokens"]["overflow_estimate"] == 481
        assert result["token_plan"]["surplus_granted"] == 500
    else:
        assert result["tokens"]["surplus_granted"] == 0


@pytest.mark.parametrize("failure", [None, "lost", "429"])
def test_surplus_stops_at_reserve_and_failed_calls_do_not_rebuy(db, cfg, monkeypatch, failure):
    cfg.keepa_recheck_surplus_reserve = 3480
    rows = [seed(db, cfg, asin=f"B{i:09d}", price_rechecked_at=util.iso(NOW - timedelta(hours=2))) for i in range(50)]
    remember(db, wall=rows[:1])
    calls, _, _ = transport(monkeypatch, db, {d["asin"]: record(d, 25) for d in rows}, failure=failure)
    result = run(db, cfg)
    assert result["tokens"]["bucket_end"] >= 3480
    assert sum(len(c["asin"].split(",")) for c in calls) <= 17
    count = len(calls)
    run(db, cfg)
    assert len(calls) == count


@pytest.mark.parametrize("volatility", [False, True])
def test_surplus_respects_time_guard(db, cfg, monkeypatch, volatility):
    cfg.keepa_volatility["enabled"] = volatility
    rows = [seed(db, cfg, asin=f"B{i:09d}", conditions=["used"] if i else []) for i in range(45)]
    remember(db, top=rows[:1], wall=rows)
    calls, _, elapsed = transport(monkeypatch, db, {d["asin"]: record(d, 25) for d in rows}, seconds=2)
    result = run(db, cfg, deadline=6.3)
    assert len(calls) == 1 and "buybox" in calls[0]
    assert result["used_checked"] == 0 and elapsed[0] < 6.3


@pytest.mark.parametrize("tokens,expected", [(3500, [100, 20]), (250, [100, 20])])
def test_top_120_pack_into_bounded_calls_and_degrade_as_a_batch(db, cfg, monkeypatch, tokens, expected):
    rows = [seed(db, cfg, asin=f"B{i:09d}") for i in range(120)]
    remember(db, top=rows, wall=rows)
    calls, _, _ = transport(monkeypatch, db, {d["asin"]: record(d, 25) for d in rows}, tokens=tokens)
    result = run(db, cfg)
    assert [len(c["asin"].split(",")) for c in calls] == expected
    assert all(("buybox" in c) == (tokens == 3500) for c in calls)
    assert result["buybox_top"]["requested"] == (120 if tokens == 3500 else 0)
    assert result["tokens"]["bucket_end"] >= (650 if tokens == 3500 else 10)


def test_surplus_consumes_all_available_overflow_without_spending_reserve(db, cfg, monkeypatch):
    rows = [seed(db, cfg, asin=f"B{i:09d}", price_rechecked_at=util.iso(NOW - timedelta(hours=2))) for i in range(110)]
    cfg.keepa_buybox_top_n = 0
    calls, _, _ = transport(monkeypatch, db, {d["asin"]: product(2500, asin=d["asin"]) for d in rows}, tokens=3100)
    result = run(db, cfg)
    assert result["tokens"]["surplus_granted"] == 100
    assert result["tokens"]["surplus_spent"] == {"buybox_top": 0, "wall_used": 0, "rest": 100}
    assert result["tokens"]["overflow_estimate"] == 0
    assert [len(c["asin"].split(",")) for c in calls] == [100]


def test_older_rest_condition_wins_selection_before_packing_modes(db, cfg, monkeypatch):
    # Fifteen tokens of overflow: oldest used (13) must get selected before
    # newer stats rows (1), even when another old stats row leads the list.
    cfg.keepa_buybox_top_n = 0
    rows = [seed(db, cfg, asin=f"B{i:09d}", conditions=["used"] if i == 1 else [],
                 price_rechecked_at=util.iso(NOW - timedelta(hours=4 - i / 20))) for i in range(10)]
    calls, _, _ = transport(monkeypatch, db, {d["asin"]: product(2500, asin=d["asin"]) for d in rows}, tokens=3015)
    result = run(db, cfg)
    requested = {a for c in calls for a in c["asin"].split(",")}
    assert requested == {d["asin"] for d in rows[:3]}
    assert result["tokens"]["surplus_spent"]["rest"] == 15


def test_shadow_outside_top_only_counts_and_keeps_public_price(db, cfg, monkeypatch, tmp_path):
    cfg.keepa_buybox_top_n = 1
    rows = [seed(db, cfg, asin=f"B{i:09d}") for i in range(2)]
    remember(db, top=rows, wall=rows)
    records = {d["asin"]: record(d, 40) for d in rows}
    for p in records.values():
        p["stats"].pop("avg30")  # outside top retains today's AMAZON-based correction requirements
    records[rows[0]["asin"]]["stats"]["avg30"] = [10000]
    transport(monkeypatch, db, records)
    run(db, cfg)
    assert db.get_deal(rows[0]["dedupe_key"])["final_price"] == 40
    assert db.get_deal(rows[1]["dedupe_key"])["final_price"] == 25
    published = site.render_site(db, cfg, NOW, out_dir=tmp_path)
    report = buybox.shadow_summary(db, cfg, NOW, published["public_deals"])
    assert report["would_label"] == 1 and report["would_hide"] == 0
    assert {d["dedupe_key"] for d in published["public_deals"]} == {d["dedupe_key"] for d in rows}


@pytest.mark.parametrize("listing", ["missing", "stale", "future"])
def test_fresh_buybox_uses_its_own_clock_and_not_the_listing_clock(db, cfg, monkeypatch, listing):
    row = seed(db, cfg)
    p = record(row, 30)
    if listing == "missing":
        p["stats"]["current"] = []
    else:
        p["lastUpdate"] = minutes(NOW + timedelta(hours=-10 if listing == "stale" else 10))
    remember(db, top=[row], wall=[row])
    transport(monkeypatch, db, {row["asin"]: p})
    result = run(db, cfg)
    after = db.get_deal(row["dedupe_key"])
    assert after["final_price"] == 30
    assert after["price_since"] == after["price_rechecked_at"] == util.iso(NOW - timedelta(minutes=5))
    assert result["buybox_top"]["repriced_up"] == 1


def test_no_bucket_reading_never_invents_surplus_or_buybox_budget(db, cfg, monkeypatch):
    row = seed(db, cfg)
    remember(db, top=[row], wall=[row])
    calls, _, _ = transport(monkeypatch, db, {row["asin"]: product(2500)})
    db.meta_set("keepa_budget", "{}")
    result = run(db, cfg)
    assert result["tokens"]["surplus_granted"] == 0
    assert len(calls) == 1 and "buybox" not in calls[0] and calls[0]["update"] == "1"
