"""Pinterest growth uses offline API fixtures; no live boards or pins are made."""

import json
from datetime import timedelta, timezone

import httpx
import pytest

from dealradar import util
from dealradar.db import DB
from dealradar.growth import pinterest as analytics, report
from dealradar.publish import pinterest
from tests.test_pinterest import (ACCESS, NOW, candidate, cfg, db, published, seed_auth)
from tests.test_social_stock import seed


def test_shipped_board_mapping_pace_and_ramp(cfg):
    names = cfg.pinterest["category_boards"]
    assert len(set(names.values())) == 8
    for category in ("electronics", "computers", "gaming"):
        assert pinterest.board_name(cfg, category) == "Tech Deals"
    assert pinterest.board_name(cfg, "kitchen") == "Kitchen Deals"
    assert pinterest.board_name(cfg, "unknown") == cfg.pinterest["board_name"]
    assert pinterest._pace(cfg, NOW) == (20, 2)
    cfg.pinterest["ramp"]["schedule"] = [
        {"from": (NOW + timedelta(days=2)).date().isoformat(), "daily_max": 30, "pins_per_run": 3},
        {"from": NOW.date().isoformat(), "daily_max": 25},
    ]
    assert pinterest._pace(cfg, NOW + timedelta(days=10)) == (20, 2)
    cfg.pinterest["ramp"]["enabled"] = True
    assert pinterest._pace(cfg, NOW - timedelta(days=1)) == (20, 2)
    assert pinterest._pace(cfg, NOW) == (25, 2)
    assert pinterest._pace(cfg, NOW + timedelta(days=2)) == (30, 3)


def test_lazy_boards_cache_across_environments_and_reopen(db, cfg):
    calls = []
    def handle(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"items": []})
        data = json.loads(request.content)
        assert data["privacy"] == "PUBLIC"
        return httpx.Response(201, json={"id": request.url.host + data["name"]})
    for env in ("sandbox", "production", "sandbox", "production"):
        cfg.pinterest["environment"] = env
        api = pinterest._API(cfg, transport=httpx.MockTransport(handle))
        try:
            for category in ("electronics", "computers", "gaming", "home"):
                name = pinterest.board_name(cfg, category)
                assert pinterest._board(db, cfg, api, {}, name) == api.client.base_url.join(api.base).host + name
        finally:
            api.client.close()
    assert len(calls) == 8  # two names, in two environments, lookup + create each
    assert {json.loads(r.content)["name"] for r in calls if r.method == "POST"} == {"Tech Deals", "Home Deals"}
    reopened = DB(db.path)
    api = pinterest._API(cfg, transport=httpx.MockTransport(lambda r: pytest.fail("cached board request")))
    try:
        assert pinterest._board(reopened, cfg, api, {}, "Tech Deals").endswith("Tech Deals")
    finally:
        reopened.close()
        api.client.close()


def test_legacy_board_migrates_without_network(db, cfg):
    legacy = {"name": cfg.pinterest["board_name"], "environment": "sandbox", "id": "old-board"}
    db.meta_set("pinterest_board", json.dumps(legacy))
    api = pinterest._API(cfg, transport=httpx.MockTransport(lambda r: pytest.fail("legacy request")))
    try:
        assert pinterest._board(db, cfg, api, {}) == "old-board"
        assert json.loads(db.meta_get("pinterest_boards"))["sandbox"][legacy["name"]] == "old-board"
    finally:
        api.client.close()


def test_secret_board_is_never_reused_or_renamed(db, cfg):
    calls = []
    def handle(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"items": [
                {"name": "Kitchen Deals", "id": "secret", "privacy": "SECRET"}]})
        assert request.method == "POST"
        name = json.loads(request.content)["name"]
        if name == "Kitchen Deals":
            return httpx.Response(400, json={"code": 58})
        assert name == "Kitchen Deals (wowpriced)"
        return httpx.Response(201, json={"id": "public"})
    api = pinterest._API(cfg, transport=httpx.MockTransport(handle))
    try:
        assert pinterest._board(db, cfg, api, {}, "Kitchen Deals") == "public"
        before = len(calls)
        assert pinterest._board(db, cfg, api, {}, "Kitchen Deals") == "public"
        assert len(calls) == before
    finally:
        api.client.close()


def test_board_collision_lookup_stops_on_rate_limit(db, cfg):
    calls = []
    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"items": []})
        if len(calls) == 2:
            return httpx.Response(400, json={"code": 58})
        assert len(calls) == 3 and request.url.params["privacy"] == "ALL"
        return httpx.Response(429, json={})
    api = pinterest._API(cfg, transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(pinterest._APIError) as error:
            pinterest._board(db, cfg, api, {}, "Home Deals")
        assert error.value.status == 429
        assert db.meta_get("pinterest_boards") is None
    finally:
        api.client.close()


@pytest.mark.parametrize("disclosure", ["", "#ad $10", "Partner #ad note", "a " * 300])
def test_copy_facts_brand_category_and_limits(disclosure):
    deal = candidate("copy", 1, item="Air Fryer 4 Qt $19.99 USD 50 , Blue", brand="Ninja",
                     category="kitchen")
    pin = pinterest.pin_payload(deal, "/deal/item/", "123", disclosure)
    assert pin["title"] == "Ninja Air Fryer 4 Qt | Kitchen deal"
    assert pin["description"].startswith("Ninja Air Fryer 4 Qt. Kitchen deal on wowpriced.")
    assert pin["description"].split()[-1] == "#ad"
    assert pin["description"].count("#ad") == 1
    assert pin["alt_text"] == "Air Fryer 4 Qt , Blue"
    for key, maximum in (("title", 100), ("description", 500), ("alt_text", 500)):
        assert 0 < len(pin[key]) <= maximum
        assert not any(word in pin[key] for word in ("$", "USD", "19.99", "50", "%", "verified", "Amazon"))
    assert pin["link"] == "https://wowpriced.com/deal/item/"
    assert pin["media_source"]["url"] == deal["image_url"]


def test_long_alt_text_and_category_are_bounded():
    pin = pinterest.pin_payload(candidate("x", 1, item="Product word " * 100, category="$99 invented"),
                                "/deal/x/", "1", "#ad")
    assert len(pin["alt_text"]) <= 500
    assert pin["alt_text"].split()[-1] in {"Product", "word"}
    assert "invented" not in pin["title"] + pin["description"]


def test_spread_preserves_rank_within_boards_and_obeys_same_board_mapping(cfg):
    rows = [(candidate(str(i), i, category=c), "/deal/x/") for i, c in enumerate(
        ["electronics", "computers", "gaming", "home", "kitchen", "beauty"], 1)]
    chosen = pinterest._spread(rows, cfg, {}, 5)
    assert [d["dedupe_key"] for d, _ in chosen] == ["1", "2", "4", "3", "5"]
    cfg.pinterest["max_consecutive_board"] = 1
    assert [d["dedupe_key"] for d, _ in pinterest._spread(rows, cfg, {}, 5)] == ["1", "4", "2", "5", "3"]
    assert pinterest._spread(rows[:3], cfg, {"board": "Tech Deals", "count": 1}, 2) == []


def board_pin_handler(calls, *, pin_status=201):
    def handle(request):
        calls.append(request)
        if request.url.path == "/v5/boards":
            if request.method == "GET":
                return httpx.Response(200, json={"items": []})
            return httpx.Response(201, json={"id": json.loads(request.content)["name"]})
        assert request.url.path == "/v5/pins"
        return httpx.Response(pin_status, json={"id": str(len(calls))})
    return handle


def test_spread_crosses_runs_and_frozen_retry_does_not_refill(db, cfg):
    seed_auth(db)
    cfg.pinterest["pins_per_run"] = 3
    published(db, cfg, [candidate(str(i), i, category=c) for i, c in enumerate(
        ["kitchen", "kitchen", "kitchen", "home", "kitchen", "beauty"], 1)])
    calls = []
    transport = httpx.MockTransport(board_pin_handler(calls))
    pinterest.post(db, cfg, transport=transport, now=NOW, run_key="first-run")
    pins = [json.loads(r.content) for r in calls if r.url.path == "/v5/pins"]
    assert [p["board_id"] for p in pins] == ["Kitchen Deals", "Kitchen Deals", "Home Deals"]
    before = len(calls)
    pinterest.post(db, cfg, transport=transport, now=NOW + timedelta(minutes=1), run_key="first-run")
    assert len(calls) == before
    pinterest.post(db, cfg, transport=transport, now=NOW + timedelta(hours=1), run_key="next-run")
    pins = [json.loads(r.content) for r in calls if r.url.path == "/v5/pins"]
    assert [p["board_id"] for p in pins][3:] == ["Kitchen Deals", "Kitchen Deals", "Beauty Deals"]
    entry = json.loads(db.meta_get("pinterest_posted"))["1"]
    assert (entry["category"], entry["environment"], entry["board"]) == ("kitchen", "sandbox", "Kitchen Deals")


@pytest.mark.parametrize("status,expected", [(500, 2), (429, 0), (401, 0)])
def test_unknown_delivery_keeps_streak_but_rejection_rolls_it_back(db, cfg, status, expected):
    seed_auth(db)
    published(db, cfg)
    pinterest.post(db, cfg, transport=httpx.MockTransport(board_pin_handler([], pin_status=status)), now=NOW)
    assert json.loads(db.meta_get("pinterest_spread"))["sandbox"].get("count", 0) == expected


def test_stock_unconfirmed_dead_and_suspect_exclusions(db, cfg):
    cfg.social_require_stock_evidence = True
    rows = []
    for i, (key, changes) in enumerate([
        ("good", {}), ("one", {"item": "Air fryer only 1 left"}),
        ("unconfirmed", {"price_unconfirmed": True}), ("dead", {"status": "dead"}),
        ("suspect", {"gone_suspect_at": util.iso(NOW)}), ("msrp", {"flags": ["suspect_msrp"]}),
        ("used", {"conditions": ["used"]}), ("stale-page", {}), ("no-evidence", {}),
    ], 1):
        if key != "no-evidence":
            seed(db, key)
        if key == "stale-page":
            row = db.get_deal(key)
            row["gone_suspect_at"] = util.iso(NOW)
            db.conn.execute("UPDATE deals SET payload=? WHERE dedupe_key=?", (json.dumps(row), key))
            db.conn.commit()
        rows.append(candidate(key, i, pct_at_checkout=60, **changes))
    published(db, cfg, rows)
    assert [d["dedupe_key"] for d, _ in pinterest._pin_candidates(db, cfg, {})] == ["good"]


def seed_pins(db, now=NOW, n=2):
    entries = {str(i): {"pin_id": str(100 + i), "environment": "production", "board": "Kitchen Deals" if i % 2 == 0 else "Home Deals",
                       "category": "kitchen" if i % 2 == 0 else "home",
                       "at": util.iso(now - timedelta(days=3, hours=i))} for i in range(n)}
    db.meta_set("pinterest_posted", json.dumps(entries))
    return entries


def response_days(first, last, *, impressions=100, clicks=5, status="READY"):
    return {"ALL": {"daily_metrics": [
        {"date": (first + timedelta(days=i)).isoformat(), "data_status": status,
         "metrics": {"IMPRESSION": impressions, "SAVE": 2, "PIN_CLICK": 8, "OUTBOUND_CLICK": clicks}}
        for i in range((last - first).days + 1)], "lifetime_metrics": {"IMPRESSION": 999999999}}}


def collect(db, cfg, handle, now=NOW):
    cfg.pinterest["environment"] = "production"
    api = pinterest._API(cfg, transport=httpx.MockTransport(handle))
    try:
        analytics.collect_daily(db, cfg, api, {"Authorization": f"Bearer {ACCESS}"}, now)
    finally:
        api.client.close()


def count_rows(db):
    return db.conn.execute("SELECT COUNT(*) FROM pinterest_pin_daily").fetchone()[0]


def test_analytics_daily_claim_absolute_upserts_and_utc_boundary(db, cfg):
    seed_pins(db)
    calls = []
    def handle(request):
        calls.append(request)
        assert request.url.host == "api.pinterest.com" and request.method == "GET"
        assert request.url.params["metric_types"] == "IMPRESSION,SAVE,PIN_CLICK,OUTBOUND_CLICK"
        assert request.url.params["split_field"] == "NO_SPLIT"
        assert request.headers["Authorization"] == f"Bearer {ACCESS}"
        return httpx.Response(200, json=response_days(NOW.date() - timedelta(days=3), NOW.date() - timedelta(days=1)))
    collect(db, cfg, handle)
    assert count_rows(db) == 6 and len(calls) == 2
    reopened = DB(db.path)
    try:
        collect(reopened, cfg, lambda r: pytest.fail("same UTC day"), NOW.astimezone(timezone(timedelta(hours=-8))))
    finally:
        reopened.close()
    collect(db, cfg, handle, NOW + timedelta(days=1))
    assert len(calls) == 4 and count_rows(db) == 6
    assert db.conn.execute("SELECT SUM(impressions) FROM pinterest_pin_daily").fetchone()[0] == 600
    data = analytics.report_data(db, cfg, NOW)
    assert data["totals"] == {"impressions": 600, "saves": 12, "pin_clicks": 48, "outbound_clicks": 30}
    assert data["outbound_per_100"] == 5
    assert data["observed_pins"] == 2


def test_analytics_bounds_rotation_and_no_wrong_environment_or_legacy_requests(db, cfg):
    entries = seed_pins(db, n=3)
    entries.update({"legacy": {"pin_id": "300", "at": util.iso(NOW - timedelta(days=1))},
                    "sandbox": {"pin_id": "301", "environment": "sandbox", "at": util.iso(NOW - timedelta(days=1))},
                    "old": {"pin_id": "302", "environment": "production", "at": util.iso(NOW - timedelta(days=31))},
                    "today": {"pin_id": "303", "environment": "production", "at": util.iso(NOW)},
                    "unsafe": {"pin_id": "../oauth/token", "environment": "production", "at": util.iso(NOW - timedelta(days=1))}})
    db.meta_set("pinterest_posted", json.dumps(entries))
    cfg.pinterest["analytics"]["max_requests_per_day"] = 1
    calls = []
    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=response_days(NOW.date() - timedelta(days=3), NOW.date() - timedelta(days=1)))
    for i in range(3):
        collect(db, cfg, handle, NOW + timedelta(days=i))
    assert calls == ["/v5/pins/102/analytics", "/v5/pins/101/analytics", "/v5/pins/100/analytics"]
    state = json.loads(db.meta_get("pinterest_analytics_production"))
    assert state["status"] == "partial" and state["requested"] == 1


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_analytics_http_failure_stops_without_public_data_or_repeat(db, cfg, caplog, status):
    seed_pins(db)
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={"Retry-After": "172800"}, json={"message": "private metric 9123456"})
    collect(db, cfg, handle)
    collect(db, cfg, handle)
    collect(db, cfg, handle, NOW + timedelta(days=1))
    assert len(calls) == 1 and count_rows(db) == 0
    assert "9123456" not in caplog.text and ACCESS not in caplog.text
    state = json.loads(db.meta_get("pinterest_analytics_production"))
    assert state["status"] == ("rate_limited" if status == 429 else "error")
    assert state["requested"] == 1


def test_transport_failure_and_time_budget_do_not_break_run(db, cfg):
    seed_pins(db)
    def fail(request):
        raise httpx.ReadTimeout("private response", request=request)
    collect(db, cfg, fail)
    assert json.loads(db.meta_get("pinterest_analytics_production"))["status"] == "error"
    cfg.pinterest["analytics"]["max_seconds"] = 0
    collect(db, cfg, lambda r: pytest.fail("time budget exhausted"), NOW + timedelta(days=1))
    assert count_rows(db) == 0


@pytest.mark.parametrize("change", ["processing", "missing", "negative", "fraction", "boolean", "future", "before", "duplicate", "shape"])
def test_missing_bad_or_processing_counts_never_become_zero_rows(db, cfg, change):
    seed_pins(db, n=1)
    first = NOW.date() - timedelta(days=1)
    response = response_days(first, first)
    row = response["ALL"]["daily_metrics"][0]
    if change == "processing":
        row["data_status"] = "PROCESSING"
    elif change == "missing":
        del row["metrics"]["SAVE"]
    elif change in {"negative", "fraction", "boolean"}:
        row["metrics"]["SAVE"] = {"negative": -1, "fraction": 1.5, "boolean": True}[change]
    elif change == "future":
        row["date"] = NOW.date().isoformat()
    elif change == "before":
        row["date"] = (NOW - timedelta(days=31)).date().isoformat()
    elif change == "duplicate":
        response["ALL"]["daily_metrics"].append(row.copy())
    else:
        response = {"ALL": {"summary_metrics": row["metrics"]}}
    collect(db, cfg, lambda r: httpx.Response(200, json=response))
    assert count_rows(db) == 0
    data = analytics.report_data(db, cfg, NOW)
    assert data["outbound_per_100"] is None and data["pin_days"] == 0


def test_ready_zero_is_observed_and_old_rows_pruned_without_dedupe_loss(db, cfg):
    entries = seed_pins(db, n=1)
    db.conn.execute("INSERT INTO pinterest_pin_daily VALUES (?,?,?,?,?,?,?,?,?,?)", (
        "production", "old", (NOW - timedelta(days=31)).date().isoformat(), "Old", "home",
        util.iso(NOW - timedelta(days=32)), 5, 0, 0, 1))
    db.conn.commit()
    yesterday = NOW.date() - timedelta(days=1)
    collect(db, cfg, lambda r: httpx.Response(200, json=response_days(yesterday, yesterday, impressions=0, clicks=0)))
    assert count_rows(db) == 1
    assert json.loads(db.meta_get("pinterest_posted")) == entries
    data = analytics.report_data(db, cfg, NOW)
    assert data["pin_days"] == 1 and data["outbound_per_100"] is None
    assert data["shadow_weights"]["kitchen"] == 1


def test_analytics_disabled_and_sandbox_make_no_calls(db, cfg):
    seed_pins(db)
    api = pinterest._API(cfg, transport=httpx.MockTransport(lambda r: pytest.fail("disabled analytics")))
    try:
        analytics.collect_daily(db, cfg, api, {}, NOW)  # sandbox
        assert "unavailable" in analytics.report_data(db, cfg, NOW)
        cfg.pinterest["environment"] = "production"
        cfg.pinterest["analytics"]["enabled"] = False
        analytics.collect_daily(db, cfg, api, {}, NOW)
        assert db.meta_get("pinterest_analytics_production") is None
    finally:
        api.client.close()


def test_post_collects_even_when_no_new_candidates_and_analytics_failure_is_safe(db, cfg):
    cfg.pinterest["environment"] = "production"
    seed_auth(db, environment="production")
    seed_pins(db)
    published(db, cfg, [])
    calls = []
    def handle(request):
        calls.append(request)
        assert request.url.path.endswith("/analytics")
        return httpx.Response(429, json={"message": "private"})
    assert pinterest.post(db, cfg, transport=httpx.MockTransport(handle), now=NOW) == 0
    assert len(calls) == 1


def test_private_report_read_only_top_groups_hours_and_shadow(db, cfg, monkeypatch, caplog):
    seed_pins(db)
    day = NOW.date() - timedelta(days=1)
    def handle(request):
        clicks = 10 if "/100/" in request.url.path else 1
        return httpx.Response(200, json=response_days(day, day, impressions=100, clicks=clicks))
    collect(db, cfg, handle)
    before = db.conn.total_changes
    data = report._pinterest(db.path, cfg, NOW)
    assert db.conn.total_changes == before
    assert data["boards"][0]["name"] == "Kitchen Deals"
    assert data["categories"][0]["name"] == "kitchen"
    assert data["hours"][0]["name"] == f"{NOW.hour:02d}:00 UTC"
    assert data["shadow_weights"]["kitchen"] > data["shadow_weights"]["home"]
    assert all(.5 <= v <= 2 for v in data["shadow_weights"].values())
    assert data["shadow_weights"]["pets"] == 1
    subject, text = report.build_report(cfg, {"pinterest": data}, {"pinterest": "ok"}, day)
    for word in ("Pinterest", "Top boards", "Top categories", "posting hours", "per 100 impressions", "not applied", "partial"):
        assert word in text
    assert "pinterest" not in subject.lower()
    assert "Kitchen Deals" not in caplog.text
    assert ACCESS not in text
    # Large fake feedback cannot change the posting order or the quotas.
    published(db, cfg, [candidate("first", 1, category="home"), candidate("second", 2, category="kitchen")])
    selected = pinterest._spread(pinterest._pin_candidates(db, cfg, {}), cfg, {}, 2)
    assert [d["dedupe_key"] for d, _ in selected] == ["first", "second"]
    assert pinterest._pace(cfg, NOW) == (20, 2)


def test_private_collection_reads_snapshot_without_network_or_writes(db, cfg, monkeypatch, caplog):
    cfg.pinterest["environment"] = "production"
    monkeypatch.setattr(report, "get_secret", lambda name: "")
    cfg.custom_domain = ""
    before = db.conn.total_changes
    with caplog.at_level("INFO", logger="dealradar.growth"):
        data, status = report.collect(cfg, NOW, None, db_path=db.path)
    assert status["pinterest"] == "ok"
    assert "pinterest" in data and data["pinterest"]["pin_days"] == 0
    assert db.conn.total_changes == before
    assert "pinterest: ok" in caplog.text


def test_api_pacing_and_analytics_timeout_bound(db, cfg, monkeypatch):
    seed_pins(db)
    clock = [0.]
    waits, starts = [], []
    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr(pinterest.time, "sleep", sleep)
    monkeypatch.setattr(pinterest.time, "monotonic", lambda: clock[0])
    cfg.min_host_interval_s = .01
    cfg.pinterest["analytics"]["max_seconds"] = 2
    yesterday = NOW.date() - timedelta(days=1)
    def handle(request):
        starts.append(clock[0])
        assert request.extensions["timeout"]["read"] <= 1
        return httpx.Response(200, json=response_days(yesterday, yesterday))
    collect(db, cfg, handle)
    assert starts == [0., 1.]
    assert waits == [1.]


def test_partial_results_survive_later_throttle_and_daily_corrections_replace(db, cfg):
    seed_pins(db, n=3)
    day = NOW.date() - timedelta(days=1)
    calls = []
    def handle(request):
        calls.append(request)
        if len(calls) == 2:
            return httpx.Response(429, json={})
        # Pinterest's unsplit group is accepted case-insensitively.
        return httpx.Response(200, json={"all": response_days(day, day)["ALL"]})
    collect(db, cfg, handle)
    assert len(calls) == 2 and count_rows(db) == 1
    assert db.conn.execute("SELECT SUM(impressions) FROM pinterest_pin_daily").fetchone()[0] == 100
    collect(db, cfg, lambda r: httpx.Response(200, json=response_days(day, day, impressions=80, clicks=1)),
            NOW + timedelta(days=1))
    assert count_rows(db) == 3
    assert db.conn.execute("SELECT SUM(impressions) FROM pinterest_pin_daily").fetchone()[0] == 240


def test_bad_storage_never_breaks_poster_and_claim_survives_interruption(db, cfg, monkeypatch):
    seed_pins(db, n=1)
    def fail(request):
        raise RuntimeError("unexpected private API body")
    collect(db, cfg, fail)
    collect(db, cfg, lambda r: pytest.fail("already claimed the daily collection"))
    assert count_rows(db) == 0
    cfg.pinterest["environment"] = "production"
    seed_auth(db, environment="production")
    published(db, cfg, [])
    db.meta_set("pinterest_analytics_production", "not JSON")
    assert pinterest.post(db, cfg, transport=httpx.MockTransport(lambda r: pytest.fail("invalid state")), now=NOW) == 0


def test_analytics_claim_is_checkpointed_before_requests_and_failure_skips_them(db, cfg, monkeypatch):
    seed_pins(db, n=1)
    saved = []
    def checkpoint(database):
        state = json.loads(database.meta_get("pinterest_analytics_production"))
        assert state["day"] == NOW.date().isoformat() and state["requested"] == 0
        saved.append(True)
        raise OSError("checkpoint unavailable")
    monkeypatch.setattr(analytics, "checkpoint_state", checkpoint)
    collect(db, cfg, lambda r: pytest.fail("no durable daily claim"))
    assert saved == [True] and count_rows(db) == 0
    collect(db, cfg, lambda r: pytest.fail("daily claim already exists"))
    assert saved == [True]
