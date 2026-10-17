"""221: one product per compatible ASIN, with separate offer judgments."""

from copy import deepcopy
from contextlib import closing
from datetime import timedelta
import hashlib
import json

import httpx
import pytest

from dealradar import accuracy, pipeline, util, verify
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.keepa_budget import KeepaBudget
from dealradar.keepa_rechecks import CallHistory, History
from dealradar.publish import site
from tests.http_helpers import mock_keepa_get
from tests.test_amazon_as_of import sample_deals, NOW as OUTPUT_NOW
from tests.test_e2e_offline import env, test_rerun_is_idempotent as check_rerun
from tests.test_price_accuracy_engine import NOW, cfg, db, product, seed
from tests.test_value_per_token_rechecks import minutes
from tests.test_wall_confirmation_coverage import remember
from tests.history_pins import pre_history_removal


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def offer(key, asin="B000000001", **changes):
    return dict(dedupe_key=key, asin=asin, final_price=25, regular_price=100,
                first_seen_at=util.iso(NOW - timedelta(hours=8)), conditions=[], **changes)


def transport(monkeypatch, cfg, *, db=None, tokens=500, answer=None):
    """Real polite client and reservation/receipt path; no sockets or sleeps."""
    monkeypatch.setenv("KEEPA_KEY", "offline")
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    calls, reserves = [], []
    budget = KeepaBudget(tokens, 0, NOW, db=db)
    reserve = budget.reserve

    def reserve_once(cost, now=None):
        reserves.append(cost)
        reserve(cost, now)

    monkeypatch.setattr(budget, "reserve", reserve_once)

    def get(url, params=None, **kwargs):
        assert url == "https://api.keepa.com/product"
        calls.append({k: v for k, v in params.items() if k != "key"})
        if answer is not None:
            return answer(calls[-1], len(calls))
        asins = params["asin"].split(",")
        cost = len(asins) * ((13 if "offers" in params else 1)
                            + int("update" in params) + 2 * int("buybox" in params))
        return httpx.Response(200, json={"products": [product(2500, asin=a) for a in asins],
                                         "tokensConsumed": cost})

    fetcher = mock_keepa_get(monkeypatch, get)
    fetcher.cfg = cfg
    return fetcher, budget, calls, reserves


def test_validated_switch_is_off_unless_explicitly_true(tmp_path):
    assert not AppConfig().keepa_unique_asin_rechecks_enabled
    assert not load_config().keepa_unique_asin_rechecks_enabled
    path = tmp_path / "config.yaml"
    for value in ({}, {"enabled": False}, {"enabled": True}):
        path.write_text(json.dumps({"keepa": {"unique_asin_rechecks": value}}))
        assert load_config(path).keepa_unique_asin_rechecks_enabled is value.get("enabled", False)
    for invalid in (None, [], True, {"enabled": "false"}, {"enabled": 1}):
        path.write_text(json.dumps({"keepa": {"unique_asin_rechecks": invalid}}))
        with pytest.raises(ValueError, match="unique_asin_rechecks.enabled"):
            load_config(path)


@pytest.mark.parametrize("mode,expected", [
    ("support", "62e2167ac53f97249f8c8c49865d883993754bcdb0c81aca9b58da82cb3b0a5c"),
    ("open", "a2dc0ccc78a71e67c743d12a68c2810cc08cb97cf73a69450bff3a83ba213ff3"),
    ("locked", "8ea4b01db5d4216acc9f664fe66e72d7925cb142e9edb2b8db987068400f68ac"),
    ("member", "8ea4b01db5d4216acc9f664fe66e72d7925cb142e9edb2b8db987068400f68ac"),
])
@pytest.mark.usefixtures("pre_extension_privacy")  # pinned before task 244's privacy section
@pytest.mark.parametrize("enabled", [False, True])
def test_all_published_files_and_member_payload_match_before_editing(
        tmp_path, monkeypatch, mode, expected, enabled):
    # Compared against this checkout's original sources, with all previously
    # enabled switches retained: 177 files plus the complete member payload.
    monkeypatch.setattr(site, "util_now", lambda: OUTPUT_NOW)
    config = pre_history_removal(load_config())
    config.dark_polish = False  # recorded before task 254
    config.search_amazon = False  # recorded before task 259
    config.front_page.get("everyday_price", {})["enabled"] = False  # recorded before 2026-10-17 tilt
    config.join_prompt_delay_s = 15  # recorded before the 35 s delay
    config.keepa_buybox_rule = "shadow"  # recorded before the Buy Box rule went on
    config.non_amazon_stores = {}  # recorded before the Woot freshness override
    config.front_page.get("top_picks", {}).update(non_amazon_max_age_h=24) if "non_amazon_max_age_h" in config.front_page.get("top_picks", {}) else None  # recorded before 8 h
    config.keepa_unique_asin_rechecks_enabled = enabled
    config.support_enabled = mode == "support"
    config.paywall_open_access = mode == "open"
    config.paywall_teaser_count = 2
    config.seo_hub_min_items = 1
    with closing(DB(":memory:")) as database:
        rendered = site.render_site(database, config, OUTPUT_NOW, out_dir=tmp_path,
                                    deals=sample_deals(), last_run={"run_key": "task221"})
    files = {str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in tmp_path.rglob("*") if p.is_file()}
    assert digest({"files": files, "full_payload": rendered["full_payload"]}) == expected
    public = json.loads((tmp_path / "deals.json").read_text())["deals"]
    assert any(row.get("locked") for row in public) == (mode in {"locked", "member"})
    assert all(not row.get("locked") for row in rendered["full_payload"]["deals"])


def test_disabled_transport_and_pipeline_match_pre_edit_baseline(db, cfg, monkeypatch):
    cfg.keepa_hidden_new_tokens_per_run = cfg.keepa_hidden_used_scans_per_run = 0
    for i in range(3):
        seed(db, cfg, asin=f"B{i:09}")
    fetcher, budget, calls, _ = transport(monkeypatch, cfg, db=db)
    rows = db.all_deals()
    rows.append(dict(rows[0], dedupe_key="promo", codes=["SAVE"], image_url=""))
    verdicts = verify.recheck_amazon_prices(rows, cfg, budget, fetcher=fetcher)
    assert not hasattr(verdicts, "diagnostics")
    snapshot = {"calls": calls[:], "verdicts": verdicts, "costs": verdicts.costs,
                "products": verdicts.products, "usage": vars(budget.usage), "bucket": budget.available(NOW)}
    assert digest(snapshot) == "e24faa8be7f9e1e7f19c02d6a7f3b3df4b0020e7c9aef60cd3426eb93506ae5a"
    calls.clear()
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False, fetcher=fetcher)
    # Tasks 253 and 255 added fields after this pin; compare the recorded fields.
    later = {"product_identity_url", "product_identity_tried", "keepa_popularity"}
    rows = [{k: v for k, v in d.items() if k not in later} for d in db.all_deals()]
    assert digest({"calls": calls, "stats": stats, "rows": rows,
                   "history": History(db, cfg, NOW).samples}) == "5b91008419009dfd1bfd455a4fb7705f3a9523f1325c43e25182542c0e3ca379"


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("duplicates", [False, True])
def test_batches_count_unique_asins_and_cover_every_selected_consumer(cfg, monkeypatch, enabled, duplicates):
    cfg.keepa_unique_asin_rechecks_enabled = enabled
    rows = [offer(f"offer-{i}", f"B{i:09}") for i in range(103)]
    if duplicates:
        rows = [item for row in rows for item in (row, dict(row, dedupe_key=row["dedupe_key"] + "-promo"))]
        rows.append(dict(rows[0], dedupe_key="late-consumer"))
    fetcher, budget, calls, reserves = transport(monkeypatch, cfg)
    result = verify.recheck_amazon_prices(rows, cfg, budget, fetcher=fetcher)
    requested = [a for call in calls for a in call["asin"].split(",")]
    assert requested == ([f"B{i:09}" for i in range(103)] if enabled else [d["asin"] for d in rows])
    assert len(calls) == (len(requested) + 99) // 100
    assert max(len(call["asin"].split(",")) for call in calls) <= 100
    assert result == {d["dedupe_key"]: True for d in rows}
    assert budget.usage.requests == len(calls) == len(reserves)
    assert sum(reserves) == budget.usage.consumed == len(requested)
    assert sum(c[0] for c in result.costs.values()) == pytest.approx(len(requested))
    assert sum(c[1] for c in result.costs.values()) == 0
    if enabled:
        assert result.diagnostics == dict(selected_consumers=len(rows), unique_asins=103,
            duplicate_slots_avoided=len(rows) - 103, product_requests=2)
        # Equal shares per unique ASIN, then equal shares among its offers.
        assert result.costs["offer-0"] == (pytest.approx(1 / 3 if duplicates else 1), 0)


def test_normalization_invalid_asins_and_affordability(cfg, monkeypatch):
    cfg.keepa_unique_asin_rechecks_enabled = True
    rows = [offer("a", " b000000001 "), offer("b"), offer("c", "B000000002")]
    rows += [offer(str(i), asin) for i, asin in enumerate((None, "", "bad", "B00000000!", "B0000000001"))]
    rows += [dict(offer("no-price"), final_price=None)]
    fetcher, budget, calls, reserves = transport(monkeypatch, cfg, tokens=15)
    result = verify.recheck_amazon_prices(rows, cfg, budget, fetcher=fetcher)
    assert [c["asin"] for c in calls] == ["B000000001,B000000002"]
    assert result == {"a": True, "b": True, "c": True}
    assert reserves == [2] and budget.available(NOW) == 13
    assert result.diagnostics == dict(selected_consumers=3, unique_asins=2,
                                     duplicate_slots_avoided=1, product_requests=1)


@pytest.mark.parametrize("used", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("volatility", [False, True])
def test_pipeline_packs_unique_asins_before_its_own_batch_cap(
        db, cfg, monkeypatch, used, enabled, volatility):
    cfg.keepa_unique_asin_rechecks_enabled = enabled
    cfg.keepa_volatility["enabled"] = volatility
    cfg.keepa_hidden_new_tokens_per_run = cfg.keepa_hidden_used_scans_per_run = 0
    count = 21 if used else 103
    rows = []
    for i in range(count * 2):
        row = seed(db, cfg, asin=f"B{i:09}")
        row.update(asin=f"B{i // 2:09}", conditions=["used"] if used else [])
        db.update_deal(row["dedupe_key"], payload=row)
        rows.append(db.get_deal(row["dedupe_key"]))
    remember(db, top=[], wall=rows)
    def answer(params, _):
        records = [product(2500, asin=a) for a in params["asin"].split(",")]
        if used:
            for record in records:
                record.update(offers=[{"condition": 2, "offerCSV": [minutes(NOW), 2500, 0]}],
                              liveOffersOrder=[0], offersSuccessful=True)
                record["stats"]["lastOffersUpdate"] = minutes(NOW)
        return httpx.Response(200, json={"products": records,
                              "tokensConsumed": len(records) * (13 if used else 1)})
    fetcher, _, calls, _ = transport(monkeypatch, cfg, answer=answer)
    KeepaBudget(4000, 0, NOW).save(db)
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False, fetcher=fetcher)
    requested = [a for call in calls for a in call["asin"].split(",")]
    assert len(requested) == (count if enabled else count * 2)
    assert len(calls) == (2 if enabled else 3)
    assert stats["checked"] == count * 2
    assert stats["tokens"]["requested"] == stats["yield"]["overall"]["tokens"] == len(requested) * (13 if used else 1)
    assert stats["yield"]["overall"]["readings"] == (count if enabled else count * 2)
    assert all(d["final_price"] == 25 and d["status"] == "alive" for d in db.all_deals())
    assert len(db.all_deals()) == count * 2


@pytest.mark.parametrize("used", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_volatility_affordability_and_used_share_charge_unique_groups(
        db, cfg, monkeypatch, used, enabled):
    cfg.keepa_unique_asin_rechecks_enabled = enabled
    cfg.keepa_volatility["enabled"] = True
    cfg.keepa_hidden_new_tokens_per_run = cfg.keepa_hidden_used_scans_per_run = 0
    count = 2 if used else 8
    rows = []
    for i in range(count):
        row = seed(db, cfg, asin=f"B{i:09}")
        row.update(asin="B000000001", conditions=["used"] if used else [])
        db.update_deal(row["dedupe_key"], payload=row)
        rows.append(db.get_deal(row["dedupe_key"]))
    remember(db, top=[], wall=rows)
    def answer(params, _):
        record = product(2500)
        record["stats"]["current"].append(2500)  # used slot may confirm, never kill
        return httpx.Response(200, json={"products": [record],
            "tokensConsumed": len(params["asin"].split(",")) * (13 if used else 1)})
    fetcher, _, calls, _ = transport(monkeypatch, cfg, answer=answer)
    tokens = 50 if used else 15  # one used scan under the share, or the new-read minimum
    KeepaBudget(tokens, 0, NOW).save(db)
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False, fetcher=fetcher)
    assert result["checked"] == (count if enabled else 1)
    assert result["tokens"]["requested"] == (13 if used else 1)
    assert len(calls) == 1
    assert KeepaBudget.load(db).available(NOW) == tokens - result["tokens"]["requested"]
    if enabled:
        from dealradar.volatility import Plan
        history = History(db, cfg, NOW)
        plan = Plan(history, db.all_deals(), {}, {r["dedupe_key"]: i for i, r in enumerate(rows)}, 10)
        assert set(plan.costs.values()) == {13 if used else 1}
        assert plan.cost_samples["used" if used else "new"] == 1
        assert plan.cost_sources["used" if used else "new"] == "deal_receipts"


def test_fanout_keeps_distinct_promo_verdicts_images_and_mutable_results(cfg, monkeypatch):
    cfg.keepa_unique_asin_rechecks_enabled = True
    rows = [offer("plain"), dict(offer("coupon"), final_price=10, codes=["SAVE"]),
            dict(offer("too-low"), final_price=10)]
    record = dict(product(2500), imagesCSV="one.jpg,two.jpg")
    fetcher, budget, calls, _ = transport(monkeypatch, cfg, answer=lambda *_:
        httpx.Response(200, json={"products": [record], "tokensConsumed": 1}))
    result = verify.recheck_amazon_prices(rows, cfg, budget, fetcher=fetcher)
    assert result == {"plain": True, "coupon": True, "too-low": False}
    assert len(calls) == 1 and calls[0]["asin"] == "B000000001"
    assert all(row["image_url"].endswith("one.jpg") for row in rows)
    assert all(verify.keepa_observed_at(p, NOW) == NOW for p in result.products.values())
    result.products["plain"]["stats"]["current"][0] = 99999
    assert result.products["coupon"] == result.products["too-low"] == record
    assert result.products["plain"] is not result.products["coupon"]


@pytest.mark.parametrize("live,conditional,buybox", [(False, False, False), (True, False, False),
                                                    (True, True, True), (False, False, True)])
def test_new_used_and_request_options_never_share_transport(cfg, monkeypatch, live, conditional, buybox):
    cfg.keepa_unique_asin_rechecks_enabled = True
    rows = [dict(offer("used-a"), conditions=["used"]), offer("new-a"),
            dict(offer("used-b"), conditions=["open_box"]), offer("new-b")]
    fetcher, budget, calls, reserves = transport(monkeypatch, cfg)
    result = verify.recheck_amazon_prices(rows, cfg, budget, live=live, buybox=buybox,
                                         conditional_live=conditional, fetcher=fetcher)
    assert [c["asin"] for c in calls] == ["B000000001", "B000000001"]
    assert "offers" not in calls[0] and calls[1]["offers"] == verify.RECHECK_OFFERS
    assert reserves == [1 + live + 2 * buybox, 13 + live + 2 * buybox]
    for call in calls:
        assert call["stats"] == 1 and call["history"] == "0"
        assert call.get("update") == (str(int(conditional)) if live else None)
        assert call.get("buybox") == ("1" if buybox else None)
    assert result["new-a"] is result["new-b"] is True
    assert result["used-a"] is result["used-b"] is None
    # Stats-only used checks are a separate invocation and cannot kill a unit.
    before = len(calls)
    other = verify.recheck_amazon_prices(rows, cfg, budget, used_offers=False, fetcher=fetcher)
    assert len(calls) == before + 1 and "offers" not in calls[-1]
    assert other["used-a"] is None


@pytest.mark.parametrize("failure", ["partial", "missing", "malformed", "unknown", "lost", "429"])
def test_incomplete_answers_and_failure_account_once_without_death(db, cfg, monkeypatch, failure):
    cfg.keepa_unique_asin_rechecks_enabled = True
    rows = [offer("a"), offer("b"), offer("other", "B000000002")]
    rows += [dict(offer("used"), conditions=["used"])]
    def answer(params, count):
        if count > 1:
            return httpx.Response(200, json={"products": [], "tokensConsumed": 13})
        if failure == "lost":
            raise httpx.ReadTimeout("offline lost response")
        if failure == "429":
            return httpx.Response(429, content=b"not json")
        if failure == "malformed":
            return httpx.Response(200, content=b'{"products": [')
        records = {"partial": [product(2500)], "missing": [],
                   "unknown": [{"asin": "B999999999", "stats": {}}]}[failure]
        return httpx.Response(200, json={"products": records, "tokensConsumed": 2})
    fetcher, budget, calls, reserves = transport(monkeypatch, cfg, db=db, answer=answer)
    result = verify.recheck_amazon_prices(rows, cfg, budget, fetcher=fetcher)
    assert all(v is not False for v in result.values())
    assert set(result) == ({"a", "b"} if failure == "partial" else set())
    assert result.rate_limited == (failure == "429")
    assert len(calls) == (1 if failure == "429" else 2)
    assert len(reserves) == budget.usage.requests == len(calls)
    assert len(CallHistory(db, NOW).samples) == len(calls)
    assert sum(v[0] for v in result.costs.values()) == pytest.approx(budget.usage.accounted)
    assert sum(v[1] for v in result.costs.values()) == pytest.approx(budget.usage.estimated)
    assert budget.usage.estimated == (2 if failure in {"lost", "malformed"} else 0)
    assert budget.usage.consumed == (0 if failure == "429" else 15 - budget.usage.estimated)


def seed_shared(db, cfg, *, used=False, second_promo=False):
    rows = [seed(db, cfg, asin=f"B{i:09}") for i in (1, 2)]
    for i, row in enumerate(rows):
        row.update(asin="B000000001", image_url="", conditions=["used"] if used else [],
                   codes=["SAVE"] if i and second_promo else [])
        db.update_deal(row["dedupe_key"], payload=row)
    return [db.get_deal(d["dedupe_key"]) for d in rows]


def test_shared_response_reprices_only_the_eligible_offer_and_keeps_credits(db, cfg, monkeypatch):
    cfg.keepa_unique_asin_rechecks_enabled = True
    cfg.keepa_hidden_new_tokens_per_run = cfg.keepa_hidden_used_scans_per_run = 0
    rows = seed_shared(db, cfg, second_promo=True)
    remember(db, top=[], wall=rows)
    record = dict(product(3000), imagesCSV="one.jpg")
    fetcher, _, calls, _ = transport(monkeypatch, cfg, answer=lambda *_:
        httpx.Response(200, json={"products": [record], "tokensConsumed": 1}))
    KeepaBudget(500, 0, NOW).save(db)
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False, fetcher=fetcher)
    after = [db.get_deal(row["dedupe_key"]) for row in rows]
    assert [d["final_price"] for d in after] == [30, 25]
    assert result["drift"]["repriced"] == result["yield"]["overall"]["useful"] == 1
    assert result["checked"] == 2 and len(calls) == 1
    for old, new in zip(rows, after):
        assert new["image_url"].endswith("one.jpg") and new["status"] == "alive"
        assert new["price_rechecked_at"] == util.iso(NOW)
        for field in ("dedupe_key", "canonical_url", "codes", "conditions", "sightings", "critic"):
            assert new[field] == old[field]
    before = {table: db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in ("deals", "sightings", "price_history", "alerts_sent")}
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False, fetcher=fetcher)
    assert len(calls) == 1 and db.all_deals() == after
    assert before == {table: db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in before}


@pytest.mark.parametrize("used", [False, True])
@pytest.mark.parametrize("failure", [None, "lost", "429"])
def test_pipeline_same_key_cached_clocks_spend_deaths_and_private_counts(db, cfg, monkeypatch, used, failure):
    cfg.keepa_unique_asin_rechecks_enabled = True
    cfg.keepa_hidden_new_tokens_per_run = cfg.keepa_hidden_used_scans_per_run = 0
    rows = seed_shared(db, cfg, used=used)
    remember(db, top=rows, wall=rows)
    clock = [NOW]
    observed = [NOW]
    def answer(params, _):
        if failure == "lost":
            raise httpx.ReadTimeout("offline")
        if failure == "429":
            return httpx.Response(429, json={"tokensLeft": 0})
        record = dict(product(-1), lastUpdate=minutes(observed[0]), imagesCSV="one.jpg")
        if used:
            record.update(offers=[], liveOffersOrder=[], offersSuccessful=True)
            record["stats"]["lastOffersUpdate"] = minutes(observed[0])
        return httpx.Response(200, json={"products": [record], "tokensConsumed": 13 if used else 1})
    fetcher, _, calls, _ = transport(monkeypatch, cfg, answer=answer)
    monkeypatch.setattr(util, "now_utc", lambda: clock[0])
    KeepaBudget(500, 0, NOW).save(db)
    db.start_run("shared", "fast", NOW)
    first = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False, fetcher=fetcher)
    assert first["unique_asin_rechecks"] == dict(selected_consumers=2, unique_asins=1,
                                                duplicate_slots_avoided=1, product_requests=1)
    assert first["yield"]["overall"]["tokens"] == (0 if failure == "429" else 13 if used else 1)
    assert first["yield"]["overall"]["readings"] == int(failure is None)
    assert all(db.get_deal(r["dedupe_key"])["status"] == "alive" for r in rows)
    original = deepcopy(db.all_deals())
    allowance = db.meta_get("keepa_hidden_recovery")
    count = len(calls)
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False, fetcher=fetcher)
    assert len(calls) == count and db.all_deals() == original
    assert db.meta_get("keepa_hidden_recovery") == allowance
    if failure:
        return
    assert all(r["gone_suspect_at"] == util.iso(NOW) for r in original)
    assert all(r["price_rechecked_at"] == util.iso(NOW) for r in original)
    clock[0] += timedelta(hours=1)
    cached = pipeline._recheck_prices(db, cfg, clock[0], image_backfill=False, fetcher=fetcher)
    assert cached["dead"] == cached["yield"]["overall"]["readings"] == 0
    assert all(db.get_deal(r["dedupe_key"])["price_rechecked_at"] == util.iso(NOW) for r in rows)
    observed[0] = clock[0] = NOW + timedelta(hours=2)
    fresh = pipeline._recheck_prices(db, cfg, clock[0], image_backfill=False, fetcher=fetcher)
    assert fresh["dead"] == 2 and fresh["yield"]["overall"]["readings"] == 1
    assert fresh["yield"]["overall"]["deaths"] == fresh["yield"]["overall"]["useful"] == 1
    assert len(db.all_deals()) == 2


@pytest.mark.parametrize("enabled", [False, True])
def test_pipeline_used_share_reserve_and_deadline_are_unchanged(db, cfg, monkeypatch, enabled):
    cfg.keepa_unique_asin_rechecks_enabled = enabled
    cfg.keepa_hidden_new_tokens_per_run = cfg.keepa_hidden_used_scans_per_run = 0
    rows = seed_shared(db, cfg, used=True)
    remember(db, top=rows, wall=rows)
    fetcher, _, calls, _ = transport(monkeypatch, cfg)
    KeepaBudget(40, 0, NOW).save(db)
    # 35% of 30 spendable tokens cannot afford even one used-offer scan.
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False, fetcher=fetcher)
    assert not calls and result["used_skipped_budget"] == 2
    KeepaBudget(500, 0, NOW).save(db)
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: 10)
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False, fetcher=fetcher, deadline=9)
    assert not calls and result["tokens_short"] == 2


def test_shared_paid_observation_keeps_per_offer_accuracy_idempotent(db, cfg, monkeypatch):
    cfg.keepa_unique_asin_rechecks_enabled = True
    rows = seed_shared(db, cfg)
    for row in rows:
        row["status"] = "alive"
    accuracy.publish(db, rows, NOW - timedelta(minutes=1), "previous")
    db.accuracy_run_key = "task221"
    fetcher, budget, calls, _ = transport(monkeypatch, cfg, db=db)
    for _ in range(2):
        result = verify.recheck_amazon_prices(rows, cfg, budget, fetcher=fetcher)
        assert len(result) == 2
    assert len(calls) == 2
    # Existing accuracy sampling is per published offer, once per observation,
    # independent of the number of consumers or repeated cached transports.
    samples = list(db.conn.execute("SELECT * FROM accuracy_samples"))
    assert len(samples) == 2 and {s["deal_key"] for s in samples} == {r["dedupe_key"] for r in rows}
    assert all(s["observed_at"] == util.iso(NOW) for s in samples)


@pytest.mark.parametrize("open_access", [False, True])
def test_enabled_offline_pipeline_rerun_in_both_access_modes(env, capsys, open_access):
    from pathlib import Path
    path = Path(env["cfg"])
    text = path.read_text().replace("unique_asin_rechecks:\n    enabled: false", "unique_asin_rechecks:\n    enabled: true")
    if not open_access:
        text = text.replace("open_access: true", "open_access: false")
    path.write_text(text)
    check_rerun(env, capsys, "shadow")
