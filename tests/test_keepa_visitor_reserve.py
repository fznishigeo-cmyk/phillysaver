"""311: every pipeline lane leaves an extra floor for visitor lookups."""

import json
from types import SimpleNamespace

import httpx
import pytest
import yaml

from dealradar import accuracy, compare, pipeline, util, verify
from dealradar.config import AppConfig, load_config
from dealradar.fetch.keepa import KeepaDealsAdapter, _Lane, _Pull, DEAL_PAGE_SIZE
from dealradar.keepa_budget import (KeepaBudget, MIN_FOR_DEAL_QUERY,
                                    MIN_FOR_RECHECK, MIN_FOR_IMAGE_BACKFILL)
from tests.test_keepa import FakeKeepa, SRC, _adapter
from tests.test_price_accuracy_engine import NOW, api, bucket, cfg, db, product, seed
from tests.test_photos_every_deal import search, search_run, seed as seed_photo


@pytest.mark.parametrize("value", [True, False, None, "150", -1, float("nan"),
                                  float("inf"), -float("inf"), [], {}])
def test_visitor_reserve_config_is_strict(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"keepa": {"visitor_reserve_tokens": value}}))
    with pytest.raises(ValueError, match="keepa.visitor_reserve_tokens"):
        load_config(path)


def test_visitor_reserve_defaults_and_shipped_setting(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("{}")
    assert AppConfig().keepa_visitor_reserve_tokens == 0
    assert load_config(path).keepa_visitor_reserve_tokens == 0
    assert load_config().keepa_visitor_reserve_tokens == 150
    for value in (0, 150, 150.5):
        path.write_text(yaml.safe_dump({"keepa": {"visitor_reserve_tokens": value}}))
        assert load_config(path).keepa_visitor_reserve_tokens == value


@pytest.mark.parametrize("reserve", [0, 150])
@pytest.mark.parametrize("need", [MIN_FOR_DEAL_QUERY, MIN_FOR_RECHECK,
                                  MIN_FOR_IMAGE_BACKFILL, 305, 711])
def test_floor_is_additive_and_counts_only_reserve_deferrals(reserve, need):
    budget = KeepaBudget(need + reserve, 0, NOW, visitor_reserve=reserve)
    assert budget.allows(need, NOW)
    assert budget.available(NOW) == need + reserve
    assert budget.spendable(NOW) == need
    budget.reserve(1, NOW)
    assert not budget.allows(need, NOW)
    assert budget.visitor_deferred_calls == int(bool(reserve))
    budget.note({"tokensLeft": need - 1}, NOW)
    assert not budget.allows(need, NOW)
    assert budget.visitor_deferred_calls == int(bool(reserve))


def test_photos_yield_first_and_deal_lane_last():
    budget = KeepaBudget(164, 0, NOW, visitor_reserve=150)
    assert not budget.allows(MIN_FOR_IMAGE_BACKFILL, NOW)
    assert not budget.allows(MIN_FOR_RECHECK, NOW)
    assert budget.allows(MIN_FOR_DEAL_QUERY, NOW)
    budget.reserve(5, NOW)
    assert budget.available(NOW) == 159
    assert not budget.allows(MIN_FOR_DEAL_QUERY, NOW)


def test_readings_stay_raw_and_deferrals_are_run_local(db, cfg):
    cfg.keepa_visitor_reserve_tokens = 150
    bucket(db, 149)
    raw = json.loads(db.meta_get("keepa_budget"))
    for _ in range(2):
        budget = KeepaBudget.load(db, cfg)
        assert not budget.allows(10, NOW)
        budget.save(db)
    assert db.keepa_visitor_deferred_calls == 2
    assert json.loads(db.meta_get("keepa_budget")) == raw
    assert KeepaBudget.load(db, cfg).available(NOW) == 149
    assert KeepaBudget.load(db, cfg).visitor_deferred_calls == 0


@pytest.mark.parametrize("reserve,expected", [(0, 2), (150, 0)])
def test_first_deal_pages_stop_before_visitors_floor(db, cfg, monkeypatch, reserve, expected):
    monkeypatch.setenv("KEEPA_KEY", "offline")
    cfg.keepa_visitor_reserve_tokens = reserve
    cfg.keepa_focus_categories = []
    cfg.keepa_popular_affordable_page = False
    bucket(db, 154)
    fake = FakeKeepa(tokens=154)
    _adapter(fake, cfg).fetch_posts(SRC, None, db, cfg, NOW, None)
    assert len(fake.calls) == expected
    assert fake.tokens >= (150 if reserve else 0)
    assert getattr(db, "keepa_visitor_deferred_calls", 0) == int(bool(reserve))


@pytest.mark.parametrize("reserve,expected", [(0, 20), (150, 5)])
def test_product_batches_use_spendable_balance(db, cfg, monkeypatch, reserve, expected):
    cfg.keepa_visitor_reserve_tokens = reserve
    deals = [seed(db, cfg, asin=f"B{i:09}") for i in range(20)]
    calls = api(monkeypatch, {d["asin"]: product(2500, asin=d["asin"]) for d in deals}, tokens=165)
    budget = KeepaBudget(165, 0, NOW, db=db)
    result = verify.recheck_amazon_prices(deals, cfg, budget=budget)
    assert len(result) == expected
    assert sum(len(call["asin"].split(",")) for call in calls) == expected
    assert budget.available(NOW) == 165 - expected
    assert budget.visitor_deferred_calls == int(bool(reserve))


@pytest.mark.parametrize("volatility", [False, True])
@pytest.mark.parametrize("reserve,expected", [(0, 1), (150, 0)])
def test_pipeline_recheck_plan_reports_reserve_deferral(
        db, cfg, monkeypatch, caplog, volatility, reserve, expected):
    cfg.keepa_visitor_reserve_tokens = reserve
    cfg.keepa_volatility["enabled"] = volatility
    deal = seed(db, cfg)
    db.meta_set("published_picks", json.dumps([deal["dedupe_key"]]))
    db.meta_set("published_wall", json.dumps([deal["dedupe_key"]]))
    bucket(db, 164)
    calls = api(monkeypatch, {deal["asin"]: product(2500)}, tokens=164)
    with caplog.at_level("INFO", logger="dealradar.pipeline"):
        summary = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert len(calls) == expected
    if reserve:
        assert summary["visitor_reserve_deferred_calls"] == 1
        assert "visitor_reserve_deferred_calls=1" in caplog.text
    else:
        assert "visitor_reserve_deferred_calls" not in summary


def test_full_run_summary_includes_discovery_reserve_deferrals(tmp_path, monkeypatch, caplog):
    from pathlib import Path

    from dealradar.db import DB
    from tests.test_e2e_offline import _offline_env, _FakeClaudeClient, _handler, NOW as RUN_NOW

    env = _offline_env(tmp_path, monkeypatch)
    path = Path(env["cfg"])
    config = yaml.safe_load(path.read_text())
    config["keepa"].update(visitor_reserve_tokens=150, focus_categories=[])
    path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv("KEEPA_KEY", "offline")
    database = DB(env["db"])
    try:
        KeepaBudget(154, 0, RUN_NOW).save(database)
        database.conn.commit()
    finally:
        database.close()

    def handler(request):
        assert request.url.host != "api.keepa.com", "visitor floor must defer discovery before send"
        return _handler(request)

    with caplog.at_level("INFO", logger="dealradar.pipeline"):
        summary = pipeline.run(db_path=env["db"], config_path=env["cfg"],
            transport=httpx.MockTransport(handler), sources_override=[SRC],
            dry_run_telegram=True, skip_validation=True, claude_client=_FakeClaudeClient())
    assert summary["price_recheck"]["visitor_reserve_deferred_calls"] == 1
    assert "Keepa budget: visitor_reserve=150, visitor_reserve_deferred_calls=1" in caplog.text


def test_cross_check_defers_without_marking_answered(db, cfg, monkeypatch):
    cfg.keepa_visitor_reserve_tokens = 150
    deal = seed(db, cfg)
    calls = api(monkeypatch, {deal["asin"]: product()}, tokens=164)
    budget = KeepaBudget(164, 0, NOW, db=db)
    answered = {}
    assert verify.keepa_check(deal, cfg, budget=budget, answered=answered) is None
    assert calls == [] and answered == {}
    assert budget.visitor_deferred_calls == 1


@pytest.mark.parametrize("lane,cost,keep", [("extra", 5, 300), ("sweep_finder", 11, 700),
                                          ("sweep_products", 50, 700), ("category", 1, 300)])
def test_optional_discovery_gates_preserve_extra_floor(db, lane, cost, keep):
    budget = KeepaBudget(keep + cost + 149, 0, NOW, db=db, visitor_reserve=150)
    pull = _Pull(client=None, key="offline", budget=budget, db=db, now=NOW,
                 deadline=None, reserve=keep, interval_s=0, margin_s=0, sleep=lambda _: None,
                 category_names={}, categories_tried=set())
    adapter = KeepaDealsAdapter()
    if lane == "extra":
        assert not adapter._may_fetch(pull, lane, extra=True)
    elif lane == "category":
        adapter._lookup_categories(pull, ["123"])
        assert pull.categories_tried == set()
    else:
        assert not adapter._sweep_affordable(pull, cost, keep, lane)
    assert pull.requests == 0
    assert budget.visitor_deferred_calls == 1


@pytest.mark.parametrize("reserve,tokens,expected", [(0, 454, 12), (150, 454, 0), (150, 455, 1)])
def test_task_319_focus_pages_keep_rotation_and_add_visitor_floor(db, reserve, tokens, expected):
    config = load_config()
    config.keepa_visitor_reserve_tokens = reserve
    # Exercise the focus lane's separate standing 300-token minimum.
    config.keepa_reserve_tokens = 0
    budget = KeepaBudget(tokens, 0, NOW, db=db, visitor_reserve=reserve)
    queries = []

    def get(url, *, params, **kwargs):
        queries.append(json.loads(params["selection"]))
        return httpx.Response(200, json={"deals": {"dr": [{}] * DEAL_PAGE_SIZE},
            "tokensLeft": tokens - 5 * len(queries)}, request=httpx.Request("GET", url))

    names = {str(i + 10): name for i, name in enumerate(config.keepa_focus_categories)}
    pull = _Pull(client=SimpleNamespace(api_get=get), key="offline", budget=budget, db=db,
                 now=NOW, deadline=None, reserve=0, interval_s=0, margin_s=0,
                 sleep=lambda _: None, category_names=names, categories_tried=set())
    KeepaDealsAdapter()._run_focus(pull, config, {"priceTypes": [0]}, set(),
                                  lambda _: None, [], _Lane(100, db))
    offset = int(NOW.timestamp() // (60 * config.keepa_recheck_interval_minutes)) % len(names)
    ids = list(map(int, names))
    rotated = ids[offset:] + ids[:offset]
    order = [(cid, page) for page in range(2) for cid in rotated]
    assert [(q["includeCategories"][0], q["page"]) for q in queries] == order[:expected]
    assert not config.amazon_used_enabled
    assert all(q["priceTypes"] == [0] for q in queries)
    assert budget.available(NOW) == tokens - 5 * expected
    assert budget.available(NOW) >= 300 + reserve
    assert bool(budget.visitor_deferred_calls) == bool(reserve)


def test_task_319_overflow_discovery_applies_floor_to_shared_budget(db):
    config = load_config()
    config.keepa_recheck_interval_minutes = 60
    floor = max(MIN_FOR_DEAL_QUERY, config.keepa_reserve_tokens,
                config.keepa_recheck_surplus_reserve)
    # Rechecks may hand discovery a freshly loaded budget without config.
    budget = KeepaBudget(floor + 5 + 149, 20, NOW, db=db)
    pull = _Pull(client=None, key="offline", budget=budget, db=db, now=NOW,
                 deadline=None, reserve=config.keepa_reserve_tokens,
                 interval_s=0, margin_s=0, sleep=lambda _: None)
    passes = [({"priceTypes": [0]}, 1, set(), lambda _: None, [], _Lane(100, db), None)]
    result = KeepaDealsAdapter()._run_overflow(pull, config, passes)
    assert result["pages"] == result["tokens"] == pull.requests == 0
    assert budget.visitor_reserve == 150
    assert budget.visitor_deferred_calls == 1


@pytest.mark.parametrize("reserve,expected", [(0, 2), (150, 1)])
def test_categories_without_token_receipts_cannot_reuse_reserve(db, reserve, expected):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return httpx.Response(200, json={"categories": {}}, request=httpx.Request("GET", url))

    budget = KeepaBudget(151, 0, NOW, db=db, visitor_reserve=reserve)
    pull = _Pull(client=SimpleNamespace(api_get=get), key="offline", budget=budget,
                 db=db, now=NOW, deadline=None, reserve=0, interval_s=0, margin_s=0,
                 sleep=lambda _: None, category_names={}, categories_tried=set())
    KeepaDealsAdapter()._lookup_categories(pull, [str(i) for i in range(20)])
    assert len(calls) == expected
    assert budget.available(NOW) >= reserve
    assert budget.visitor_deferred_calls == int(bool(reserve))


def test_photo_finder_does_not_mark_attempt_or_spend_quota(search):
    database, config, fetcher, calls, _ = search
    config.keepa_visitor_reserve_tokens = 150
    seed_photo(database, regular_price=100, final_price=10)
    # Existing photo floor (650), finder (11), and all 50 possible products.
    KeepaBudget(860, 0, NOW).save(database)
    assert search_run(search)["searched"] == 0
    assert calls == []
    assert not database.get_deal("a").get("photo_search_tried")
    assert database.keepa_visitor_deferred_calls == 1


def test_accuracy_extra_sample_keeps_visitor_floor(db, cfg, monkeypatch):
    from tests.test_accuracy_scoreboard import visible

    cfg.keepa_visitor_reserve_tokens = 150
    cfg.accuracy_extra_samples_per_day = 1
    visible(db, [seed(db, cfg)])
    monkeypatch.setenv("KEEPA_KEY", "offline")
    KeepaBudget(max(cfg.keepa_reserve_tokens, cfg.keepa_recheck_surplus_reserve) + 4 + 149,
                0, NOW).save(db)
    calls = []
    monkeypatch.setattr(verify, "recheck_amazon_prices", lambda *a, **k: calls.append(a) or {})
    accuracy.extra_samples(db, cfg, NOW)
    assert calls == []
    assert db.conn.execute("SELECT COUNT(*) FROM accuracy_extra_reads").fetchone()[0] == 0
    assert db.keepa_visitor_deferred_calls == 1


@pytest.mark.parametrize("money_cap", [0, 100])
def test_compare_finder_adds_floor_after_money_caps(db, cfg, monkeypatch, tmp_path, money_cap):
    from tests.test_compare import TERM

    cfg.keepa_visitor_reserve_tokens = 150
    cfg.feature_flags["keepa"] = True
    path = tmp_path / "terms.yaml"
    path.write_text(yaml.safe_dump([TERM]))
    cfg.compare.update(refresh=True, terms_file=str(path), tokens_per_run=money_cap,
                       tokens_per_day=100, min_bucket_share=0)
    monkeypatch.setenv("KEEPA_KEY", "offline")
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    floor = max(cfg.keepa_reserve_tokens, cfg.keepa_recheck_surplus_reserve)
    budget = KeepaBudget(floor + cfg.compare["finder_reserve_tokens"] + 149, 60, NOW, db=db)
    result = compare.refresh(db, cfg, NOW, None, None, "reserve-test", budget=budget)
    assert result["finders"] == result["products"] == result["tokens"] == 0
    assert budget.visitor_deferred_calls == int(bool(money_cap))
