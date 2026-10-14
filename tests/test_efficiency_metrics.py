"""Offline receipts, daily math, missing history and zero extra requests (088)."""

import json
from datetime import timedelta

import httpx
import pytest

from dealradar import ai_yield, efficiency, pipeline, util
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.growth import report
from dealradar.keepa_budget import KeepaBudget, TokenUsage
from dealradar.keepa_rechecks import History, report_data
from tests.http_helpers import mock_keepa_get
from tests.test_price_accuracy_engine import NOW, ASIN, seed, product
from tests.test_e2e_offline import env, _run


def coverage(age=2, one=.5, three=1, twelve=1):
    return {"new": {"amazon": 10, "unconfirmed": 0, "oldest_h": age,
                    "within_1h": one, "within_3h": three, "within_12h": twelve},
            "used": {"amazon": 5, "unconfirmed": 0, "oldest_h": age + 1,
                     "within_1h": 0, "within_3h": .4, "within_12h": 1}}


def summary(**overrides):
    return {"ai_yield": {"unique_new_public_deals": 10, "spend_usd": 2},
            "intake": {"unique_public_by_source": {"keepa": 4, "partner": 6}},
            "audit": {"critic_calls_avoided": 3, "would_avoid_calls": 99},
            "price_recheck": {"yield": {"overall": {"tokens": 20, "estimated_tokens": 0, "useful": 2},
                                       "classes": {}},
                              "confirmed_age": {"wall": coverage()},
                              "tokens": {"requested": 1000}, "dead": 999}, **overrides}


def metrics(**overrides):
    return efficiency.run_metrics(summary(**overrides), TokenUsage(1, 0, 10), {"keepa"})


def save(db, key, now=NOW, values=None):
    efficiency.start(db, now, key)
    efficiency.record(db, now, key, values or metrics())


def test_run_math_uses_task_087_yield_and_unique_public_sources():
    values = metrics()
    assert values["tokens_per_new_verified_deal"] == 2.5
    assert values["new_verified_deals_per_token"] == .4
    assert values["tokens_per_useful_recheck"] == 10
    assert values["useful_rechecks_per_token"] == .1
    assert values["llm_usd_per_published_deal"] == .2
    assert values["critic_calls_avoided"] == 3


@pytest.mark.parametrize("value", [None, -1, True, "5", float("nan"), float("inf")])
def test_missing_and_invalid_receipts_stay_unavailable_despite_conservative_debits(value):
    usage = TokenUsage()
    usage.note({"tokensConsumed": 5, "tokensLeft": 1000})
    usage.note({"tokensConsumed": value, "tokensLeft": 950}, estimate=13)
    assert usage.total is None and usage.missing == 1
    assert usage.consumed == 5 and usage.estimated == 13 and usage.accounted == 18
    combined = TokenUsage()
    combined.add(usage)
    assert combined == usage
    assert efficiency.ratio(value, 2) is None


def test_absence_zero_denominators_and_measured_zero_are_distinct():
    values = efficiency.run_metrics({}, TokenUsage(), set())
    assert values["feed_tokens"] == 0
    assert values["published_deals"] is None
    assert values["recheck_tokens"] is None
    assert values["critic_calls_avoided"] is None
    assert efficiency.ratio(0, 2) == 0
    assert efficiency.ratio(2, 0) is None
    assert efficiency.ratio(0, 0) is None
    assert metrics(ai_yield={"unavailable": "failed", "unique_new_public_deals": 0})["feed_new_deals"] is None


def test_daily_totals_weighted_ratios_utc_and_worst_freshness(tmp_path):
    db = DB(tmp_path / "state.db")
    cfg = AppConfig(timezone="America/New_York")
    day = NOW.replace(hour=0)
    db.meta_set("efficiency_since", util.iso(day))
    ai_yield.start(db, cfg, day, "baseline")
    first = metrics()
    second = metrics()
    second.update(feed_tokens=90, feed_new_deals=6, recheck_tokens=80, useful_rechecks=3,
                  critic_calls_avoided=1)
    second["freshness"] = {"latest": coverage(.5, 1), "worst": coverage(.5, 1)}
    save(db, "one", day + timedelta(hours=1), first)
    save(db, "two", day + timedelta(hours=23), second)
    save(db, "outside", day - timedelta(seconds=1))
    save(db, "today", day + timedelta(days=1))
    for i in range(5):
        db.conn.execute("INSERT INTO ai_yield_events VALUES (?,?,?,?,?,?,?)",
                        (str(i), str(i), "new", util.iso(NOW), "one", "partner", "home"))
    db.conn.execute("INSERT INTO llm_legacy_spend(run_key,billed_at,usd) VALUES (?,?,?)",
                    ("failed-run", util.iso(NOW), 1.5))
    db.conn.commit()
    values = efficiency.daily(db, cfg, day + timedelta(days=1, hours=1))
    assert values["day"] == day.date().isoformat()  # still yesterday locally
    assert values["tokens_per_new_verified_deal"] == 10  # 100 / 10, not mean(2.5, 15)
    assert values["tokens_per_useful_recheck"] == 20
    assert values["useful_rechecks_per_token"] == .05
    assert values["critic_calls_avoided"] == 4
    assert values["llm_usd_per_published_deal"] == .3
    assert values["freshness"]["latest"]["new"]["oldest_h"] == .5
    assert values["freshness"]["worst"]["new"]["oldest_h"] == 2
    assert values["freshness"]["worst"]["new"]["within_1h"] == .5
    assert values["coverage"] == {"samples": 2, "from": util.iso(day), "partial": False}
    # A known failed public measurement must not turn a partial event count
    # into a seemingly complete dollars/deal ratio.
    save(db, "failed-public", day + timedelta(hours=23, minutes=5),
         metrics(ai_yield={"unavailable": "public yield measurement failed"}))
    assert efficiency.daily(db, cfg, day + timedelta(days=1))["llm_usd_per_published_deal"] is None
    db.close()


def test_retry_keeps_real_costs_without_recounting_deals_or_adding_rows():
    db = DB(":memory:")
    save(db, "same")
    retry = metrics()
    retry.update(feed_tokens=5, feed_new_deals=0, published_deals=0, llm_usd=0,
                 useful_rechecks=0, recheck_tokens=0, critic_calls_avoided=0)
    save(db, "same", NOW + timedelta(minutes=10), retry)
    # Repeated bookkeeping within an attempt is a no-op.
    efficiency.record(db, NOW, "same", retry)
    rows = db.conn.execute("SELECT * FROM efficiency_runs").fetchall()
    assert len(rows) == 1
    values = json.loads(rows[0]["metrics"])
    assert values["feed_tokens"] == 15
    assert values["feed_new_deals"] == 4
    assert values["tokens_per_new_verified_deal"] == 3.75
    assert values["published_deals"] == 10
    db.close()


def test_legacy_empty_partial_failed_and_missing_run_history():
    db = DB(":memory:")
    cfg = AppConfig()
    tomorrow = NOW + timedelta(days=1)
    empty = efficiency.daily(db, cfg, tomorrow)
    assert empty["feed_tokens"] is None
    assert empty["llm_usd_per_published_deal"] is None
    save(db, "new")
    partial = efficiency.daily(db, cfg, tomorrow)
    assert partial["coverage"]["partial"]
    assert partial["llm_usd_per_published_deal"] is None
    efficiency.start(db, NOW, "crash")
    assert efficiency.daily(db, cfg, tomorrow)["recheck_tokens"] is None
    save(db, "crash")  # retry cannot hide the lost receipts
    assert efficiency.daily(db, cfg, tomorrow)["feed_tokens"] is None
    db.start_run("unrecorded", "fast", NOW)
    assert efficiency.daily(db, cfg, tomorrow)["freshness"]["worst"]["new"]["oldest_h"] is None
    db.conn.execute("DROP TABLE efficiency_runs")
    legacy = efficiency.daily(db, cfg, tomorrow)
    assert legacy["feed_tokens"] is None
    db.conn.execute("INSERT INTO legitimacy_batches VALUES (?,?,?,?,?,?,?)",
                    ("batch", util.iso(NOW), "enforce", "fixture", 8, 2, .01))
    assert efficiency.daily(db, cfg, tomorrow)["critic_calls_avoided"] == 2
    db.close()


def test_unknown_clocks_and_empty_shown_conditions():
    sample = coverage()
    sample["new"]["unconfirmed"] = 1
    sample["used"] = {"amazon": 0, "unconfirmed": 0}
    values = efficiency.freshness(sample)
    assert values["new"]["oldest_h"] is None
    assert values["new"]["within_1h"] == .5
    assert values["used"]["oldest_h"] is None
    worst = efficiency.worst_freshness([values, efficiency.freshness(coverage())])
    assert worst["new"]["oldest_h"] is None
    assert worst["used"]["oldest_h"] == 3


def useful_reading(deal, record, cfg):
    """Exercise the same history feeding scheduling, efficiency and reports."""
    db = DB(":memory:")
    try:
        history = History(db, cfg, NOW)
        history.record(dict(deal, dedupe_key="fixture"), record, True, NOW, "wall_new", 1, 0)
        return bool(history.yield_stats["overall"]["useful"])
    finally:
        db.close()


@pytest.mark.parametrize("cents,expected", [(2500, False), (2524, False), (2525, True), (2475, True)])
def test_useful_threshold_both_directions_and_stale_or_hidden_prices(cents, expected):
    cfg = AppConfig(keepa_reprice_min_pct=1)
    deal = {"final_price": 25, "first_seen_at": util.iso(NOW - timedelta(hours=2))}
    assert useful_reading(deal, product(cents), cfg) is expected
    assert not useful_reading(dict(deal, codes=["SAVE"]), product(cents), cfg)
    stale = product(cents)
    stale["lastUpdate"] -= 24 * 60
    assert not useful_reading(deal, stale, cfg)


def test_paid_rechecks_count_changes_and_confirmed_deaths_once(monkeypatch):
    db = DB(":memory:")
    cfg = load_config()
    cfg.feature_flags["keepa"] = True
    monkeypatch.setenv("KEEPA_KEY", "offline-key")
    monkeypatch.setattr(util, "now_utc", lambda: NOW + timedelta(minutes=1))
    changed = seed(db, cfg)
    dead = seed(db, cfg, asin="B000000002", gone_suspect_at=util.iso(NOW - timedelta(hours=2)))
    seed(db, cfg, asin="B000000003")
    seed(db, cfg, asin="B000000004")
    records = {ASIN: product(2525), "B000000002": product(-1, asin="B000000002"),
               "B000000003": product(2500, asin="B000000003"),
               "B000000004": {"asin": "B000000004"}}
    records[ASIN]["lastUpdate"] += 1  # a live read made after the run began
    requests = []

    def get(url, params, timeout):
        requests.append(params)
        asins = params["asin"].split(",")
        return httpx.Response(200, json={"products": [records[a] for a in asins],
            "tokensConsumed": len(asins), "tokensLeft": 500, "refillRate": 20})

    mock_keepa_get(monkeypatch, get)
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    counts = stats["yield"]["overall"]
    assert counts["tokens"] == 4 and counts["estimated_tokens"] == 0
    assert counts["useful"] == 2 and counts["tokens_per_useful_reading"] == 2
    measured = efficiency.run_metrics({"price_recheck": stats}, TokenUsage(), set())
    assert measured["tokens_per_useful_recheck"] == 2
    assert measured["useful_rechecks_per_token"] == .5
    assert db.get_deal(changed["dedupe_key"])["final_price"] == 25.25
    assert db.get_deal(dead["dedupe_key"])["status"] == "dead"
    assert all(r["history"] == "0" for r in requests)
    db.close()


@pytest.mark.parametrize("status,receipt,tokens,useful", [
    (200, None, None, 1), (429, 0, 0, 0), (429, None, 0, 0),
    (429, 99, 0, 0), (500, 1, 1, 0),
])
def test_recheck_receipts_on_incomplete_failed_and_throttled_calls(monkeypatch, status, receipt, tokens, useful):
    db = DB(":memory:")
    cfg = load_config()
    cfg.feature_flags["keepa"] = True
    seed(db, cfg)
    monkeypatch.setenv("KEEPA_KEY", "offline-key")
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    mock_keepa_get(monkeypatch, lambda *a, **kw: httpx.Response(status, json={
        "tokensConsumed": receipt, "products": [product(2600)]}))
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    measured = efficiency.run_metrics({"price_recheck": stats}, TokenUsage(), set())
    assert measured["recheck_tokens"] == tokens
    assert measured["useful_rechecks"] == useful
    assert stats["yield"]["overall"]["estimated_tokens"] == (1 if receipt is None and status != 429 else 0)
    db.close()


def test_photo_only_tokens_are_not_recheck_tokens(monkeypatch):
    db = DB(":memory:")
    cfg = load_config()
    cfg.feature_flags["keepa"] = True
    deal = seed(db, cfg, price_rechecked_at=util.iso(NOW - timedelta(minutes=10)))
    db.update_deal(deal["dedupe_key"], payload=dict(deal, image_url=""))
    monkeypatch.setenv("KEEPA_KEY", "offline-key")
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    mock_keepa_get(monkeypatch, lambda *a, **kw: httpx.Response(200, json={
        "tokensConsumed": 1, "products": [product(2600)]}))
    stats = pipeline._recheck_prices(db, cfg, NOW)
    assert stats["image_backfill"] == 1
    assert stats["yield"]["overall"]["tokens"] == 0
    assert stats["yield"]["overall"]["useful"] == 0
    db.close()


def test_feed_receipts_include_sweep_but_exclude_categories(monkeypatch):
    from dealradar.fetch.keepa import CATEGORY_URL, _Pull
    from tests.test_keepa import _adapter, SRC

    cfg = load_config()
    cfg.keepa_focus_categories = []
    cfg.keepa_sweep_enabled = True
    db = DB(":memory:")
    KeepaBudget(1200, 20, NOW).save(db)
    monkeypatch.setenv("KEEPA_KEY", "offline-key")
    requests = []
    costs = {"/deal": 5, "/query": 11, "/product": 1, "/category": 1}

    def handler(request):
        path = request.url.path
        requests.append(path)
        return httpx.Response(200, json={"tokensConsumed": costs[path], "tokensLeft": 1000,
            "refillRate": 20, "deals": {"dr": []}, "asinList": [ASIN], "products": [product()]})

    adapter = _adapter(handler, cfg)
    adapter.fetch_posts(SRC, None, db, cfg, NOW, None)
    assert "/query" in requests and "/product" in requests
    assert adapter.token_usage.total == sum(costs[p] for p in requests if p != "/category")
    from types import SimpleNamespace
    pull = _Pull(client=SimpleNamespace(api_get=lambda *a, **kw: httpx.Response(
        200, json={"tokensConsumed": 1}, request=httpx.Request("GET", CATEGORY_URL))),
        key="offline", budget=KeepaBudget(), db=db, now=NOW, deadline=None,
        reserve=0, interval_s=1, margin_s=0, sleep=lambda _: None)
    before = adapter.token_usage.total
    adapter._get(pull, CATEGORY_URL, {}, "categories")
    assert adapter.token_usage.total == before
    db.close()


def test_used_changes_require_successful_current_offers_of_used_condition():
    from dealradar.verify import KEEPA_EPOCH_MINUTES

    stamp = int(NOW.timestamp() / 60) - KEEPA_EPOCH_MINUTES
    deal = {"final_price": 25, "conditions": ["used"],
            "price_since": util.iso(NOW - timedelta(hours=1))}
    record = {"stats": {"lastOffersUpdate": stamp}, "offersSuccessful": True,
              "liveOffersOrder": [0, 1], "offers": [
                  {"condition": 1, "offerCSV": [stamp, 2500, 0]},
                  {"condition": 2, "offerCSV": [stamp, 2400, 200]}]}
    cfg = AppConfig()
    assert useful_reading(deal, record, cfg)  # $26 including shipping
    assert not useful_reading(deal, dict(record, offersSuccessful=False), cfg)
    assert not useful_reading(deal, dict(record, liveOffersOrder=[0]), cfg)
    record["offers"].append({"condition": 2, "offerCSV": [stamp, -1, 0]})
    record["liveOffersOrder"].append(2)
    assert not useful_reading(deal, record, cfg)


def test_rendering_has_verdict_first_labels_units_and_unavailable():
    cfg = AppConfig(growth_expired_click_hours=6)
    _, text = report.build_report(cfg, {"efficiency": {"day": "2026-10-13", **metrics()}}, {}, NOW.date())
    assert "== Efficiency (2026-10-13 UTC) ==\nEach Keepa token found 0.40 newly verified deals" in text
    assert "Tokens per useful recheck: 10.00; useful rechecks per token: 0.10" in text
    assert "death within 6 h after click): unavailable" in text
    assert "LLM dollars per first published deal: $0.2000" in text
    _, empty = report.build_report(cfg, {}, {}, NOW.date())
    assert "Keepa tokens per newly verified deal: unavailable" in empty
    assert "Critic calls avoided: unavailable" in empty
    assert "within 1 / 3 / 12 h: latest unavailable / unavailable / unavailable" in empty


def test_collection_is_read_only_and_makes_identical_external_requests(tmp_path, monkeypatch, caplog):
    from tests.test_growth_report import FakeApis, client_for, ENV_NAMES

    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOOGLE_SA_JSON", "offline-credentials")
    from tests.test_growth_report import GOOGLE_TOKEN
    monkeypatch.setattr(report.google, "access_token", lambda *_: GOOGLE_TOKEN)
    monkeypatch.setattr(report.site_health.time, "sleep", lambda _: None)
    cfg = AppConfig(growth_ga4_property_id="123", custom_domain="wowpriced.com")
    path = tmp_path / "snapshot.db"
    db = DB(path)
    save(db, "yesterday", NOW - timedelta(days=1))
    db.close()
    before = path.read_bytes()
    one, two = FakeApis(), FakeApis()
    report.collect(cfg, NOW, client_for(one))
    data, status = report.collect(cfg, NOW, client_for(two), db_path=path)
    assert status["efficiency"] == "ok"
    assert path.read_bytes() == before
    assert [(r.method, str(r.url), r.content) for r in one.requests] == [
        (r.method, str(r.url), r.content) for r in two.requests]
    assert all("keepa" not in str(r.url) and "anthropic" not in str(r.url) for r in two.requests)
    assert "Each Keepa token" not in caplog.text


def test_pipeline_rerun_preserves_first_publish_counts_and_public_payload(env):
    first = _run(env)
    db = DB(env["db"])
    count = db.conn.execute("SELECT COUNT(*) FROM efficiency_runs").fetchone()[0]
    db.close()
    second = _run(env)
    db = DB(env["db"])
    assert db.conn.execute("SELECT COUNT(*) FROM efficiency_runs").fetchone()[0] == count == 1
    values = json.loads(db.conn.execute("SELECT metrics FROM efficiency_runs").fetchone()[0])
    assert first["efficiency"]["published_deals"] > 0
    assert second["efficiency"]["published_deals"] == 0
    assert values["published_deals"] == first["efficiency"]["published_deals"]
    assert "efficiency" not in (env["site"] / "deals.json").read_text()
    db.close()


def test_config_knob(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("growth:\n  expired_click_hours: 8\n")
    assert load_config(path).growth_expired_click_hours == 8


@pytest.mark.parametrize("lost_page", [False, True])
def test_open_pull_receipts_are_shared_without_counting_rechecks_or_photos_as_discovery(monkeypatch, lost_page):
    import time
    from dataclasses import replace
    from dealradar.http_client import PoliteFetcher
    from tests.test_keepa import SRC
    from tests.test_keepa_overflow import setup_pull
    from tests.test_wall_confirmation_coverage import remember

    db = DB(":memory:")
    cfg = load_config()
    fake = setup_pull(cfg, db, monkeypatch)
    due = seed(db, cfg, price_rechecked_at=util.iso(NOW - timedelta(hours=2)))
    idle = seed(db, cfg, asin="B000000002", price_rechecked_at=util.iso(NOW - timedelta(minutes=35)))
    photo = seed(db, cfg, asin="B000000003", price_rechecked_at=util.iso(NOW - timedelta(minutes=10)))
    db.update_deal(photo["dedupe_key"], payload=dict(photo, image_url=""))
    remember(db, top=[due, idle, photo])
    fake.products = {d["asin"]: product(2600, asin=d["asin"]) for d in (due, idle, photo)}
    if lost_page:
        fake.fail[("amazon", 1)] = httpx.Response(200, text="unreadable")
    receipts = []

    def handler(request):
        response = fake(request)
        cost = len(request.url.params["asin"].split(",")) if request.url.path == "/product" else 5
        receipts.append((request.url.path, cost))
        try:
            payload = response.json()
        except ValueError:
            return response
        return httpx.Response(response.status_code, json={**payload, "tokensConsumed": cost})

    fetcher = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handler), sleep_fn=lambda _: None)
    fetcher.keepa_feed_usage = TokenUsage()
    stats = {}
    try:
        pipeline._fetch_all([replace(SRC, fast_lane=True)], fetcher, db, cfg, NOW, time.monotonic() + 1000,
                            True, price_recheck=stats)
        usage = fetcher.keepa_feed_usage
        feed_cost = sum(cost for path, cost in receipts if path == "/deal")
        assert usage.accounted == feed_cost
        assert usage.total == (None if lost_page else feed_cost)
        assert usage.estimated == (5 if lost_page else 0)
        assert usage.requests == sum(path == "/deal" for path, _ in receipts)
        assert stats["overflow_discovery"]["tokens"] == feed_cost - 15  # three ordinary pages
        assert stats["overflow_discovery"]["estimated_tokens"] == usage.estimated
        assert sum(cost for path, cost in receipts if path == "/product") == 3
        assert stats["image_backfill"] == 1
        assert stats["yield"]["overall"]["tokens"] == 2
        assert stats["yield"]["overall"]["useful"] == 2
        measured = efficiency.run_metrics({"price_recheck": stats}, usage, {"keepa"})
        assert measured["recheck_tokens"] == 2
        assert measured["tokens_per_useful_recheck"] == 1
        assert measured["feed_tokens"] == usage.total
        saved = report_data(db, NOW + timedelta(days=1))["yesterday"]
        assert saved == stats["yield"]
    finally:
        fetcher.close()
        db.close()


def test_recheck_report_does_not_turn_absent_periods_into_zero():
    db = DB(":memory:")
    cfg = AppConfig()
    try:
        deal = {"dedupe_key": "fixture", "final_price": 25}
        History(db, cfg, NOW).record(deal, product(2600), True, NOW, "top_new", 1, 0)
        data = report_data(db, NOW)
        assert "unavailable" in data["yesterday"] and "unavailable" in data["week"]
        _, text = report.build_report(cfg, {"price_recheck": data}, {}, NOW.date())
        assert "yesterday: unavailable" in text and "week: unavailable" in text
    finally:
        db.close()
