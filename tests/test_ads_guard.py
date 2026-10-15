"""Offline safety regressions: real requests through MockTransport, never live ads."""
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import yaml

from dealradar import cli
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.growth import ads_guard as guard, google, google_ads, report

NOW = datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc)  # still Oct 13 in the account
CID = "4412275803"
ROOT = Path(__file__).resolve().parents[1]


def config(cap=10):
    return AppConfig(growth_google_ads_customer_id=CID, growth_ga4_property_id="123",
        raw={"ads": {"authorized_daily_usd": cap, "hard_max_daily_usd": 10,
                     "authorized_on": "2026-10-12", "authorized_quote": f"AUTHORIZE ADS: ${cap} per day"}})


@pytest.fixture
def db(tmp_path):
    value = DB(tmp_path / "state.db")
    yield value
    value.close()


@pytest.fixture(autouse=True)
def secrets(monkeypatch):
    for name in ("GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN",
                 "TELEGRAM_BOT_TOKEN", "TELEGRAM_ADMIN_CHAT_ID", "GOOGLE_SA_JSON"):
        monkeypatch.setenv(name, "FAKE_" + name)
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "NEVER_PUBLIC")
    monkeypatch.setattr(google, "access_token", lambda *a: "FAKE_GA_TOKEN")


class API:
    def __init__(self):
        self.status = {"1": "ENABLED"}
        self.budget = {"1": 5_000_000}
        self.spend = 0
        self.cost = 0
        self.outbound = 0
        self.signups = 0
        self.ad_clicks = 0
        self.currency = "USD"
        self.period = "DAILY"
        self.payment = "CLICKS"
        self.failure = ""
        self.telegram_fail = False
        self.ambiguous_pause = False
        self.requests, self.pauses, self.messages = [], [], []

    def response(self, request):
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == "oauth2.googleapis.com":
            if self.failure == "oauth":
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "FAKE_ADS_TOKEN"})
        body = json.loads(request.content)
        if host == "api.telegram.org":
            assert body["chat_id"] == "FAKE_TELEGRAM_ADMIN_CHAT_ID"
            self.messages.append(body["text"])
            return httpx.Response(500 if self.telegram_fail else 200, json={})
        if host == "analyticsdata.googleapis.com":
            assert self.failure != "unexpected_ga"
            if self.failure == "ga":
                return httpx.Response(500, text="FAKE_SECRET_RESPONSE")
            req = body["requests"][0]
            rows = []
            for cid in self.status:
                for event, count in (("session_start", 3), ("deal_click", self.outbound), ("sign_up", self.signups)):
                    if count:
                        rows.append({"dimensionValues": [{"value": v} for v in
                            (cid, CID, "(not set)", "(not set)", "google / cpc", event)],
                            "metricValues": [{"value": str(count)}]})
            return httpx.Response(200, json={"reports": [{"dimensionHeaders": req["dimensions"],
                "metricHeaders": [{"name": "eventCount"}], "rows": rows, "rowCount": len(rows),
                "metadata": {"timeZone": "America/New_York"}}]})
        assert host == "googleads.googleapis.com"
        assert request.headers["Authorization"] == "Bearer FAKE_ADS_TOKEN"
        if path.endswith("campaigns:mutate"):
            op = body["operations"][0]
            cid = op["update"]["resourceName"].split("/")[-1]
            # Every wire mutation is status-only. No enable, budget, create or delete.
            assert body == {"operations": [{"updateMask": "status", "update": {
                "resourceName": f"customers/{CID}/campaigns/{cid}", "status": "PAUSED"}}]}
            self.pauses.append(cid)
            if self.failure == "mutate" and cid == "1":
                return httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED",
                    "message": "FAKE_SECRET_RESPONSE", "details": [{"errors": [{"errorCode": {
                        "authorizationError": "USER_PERMISSION_DENIED"}}]}]}})
            self.status[cid] = "PAUSED"
            if self.ambiguous_pause:
                raise httpx.ReadTimeout("FAKE_SECRET_RESPONSE")
            return httpx.Response(200, json={"results": [{"resourceName": op["update"]["resourceName"]}]})
        query = body["query"]
        assert path.endswith("googleAds:search")
        if "campaign.status FROM campaign" in query:
            kind = "campaigns"
            rows = [{"campaign": {"id": k, "status": v}} for k, v in self.status.items()]
        elif "customer.time_zone" in query:
            kind = "account"
            rows = [{"customer": {"timeZone": "America/New_York", "currencyCode": self.currency}}]
        elif "campaign_budget" in query:
            kind = "budgets"
            rows = [{"campaign": {"id": k, "paymentMode": self.payment},
                     "campaignBudget": {"amountMicros": str(self.budget[k]), "period": self.period}}
                    for k, v in self.status.items() if v == "ENABLED"]
        else:
            kind = "costs"
            dates = re.findall(r"'(\d{4}-\d{2}-\d{2})'", query)
            daily = len(dates) == 2 and dates[0] == dates[1]
            if "FROM customer" in query:
                assert daily
                rows = [{"metrics": {"costMicros": str(self.spend), "clicks": "0"}}]
            else:
                rows = [{"campaign": {"id": k}, "metrics": {
                    "costMicros": str(self.spend if daily else self.cost), "clicks": str(self.ad_clicks)}}
                    for k in self.status]
        if self.failure == kind:
            return httpx.Response(503, text="FAKE_SECRET_RESPONSE")
        return httpx.Response(200, json={"results": rows})

    def check(self, db, cfg=None, *, key="run-1", now=NOW):
        with httpx.Client(transport=httpx.MockTransport(self.response)) as client:
            return guard.check(db, cfg or config(), now, client, run_key=key)


@pytest.mark.parametrize("authorized,hard,expected", [(0, 10, 0), (6, 10, 6), (15, 10, 10),
                                                      (10, 4, 4), (100, 100, 10)])
def test_cap_math(authorized, hard, expected):
    cfg = config(authorized)
    cfg.raw["ads"]["hard_max_daily_usd"] = hard
    opt = guard.settings(cfg, NOW)
    assert opt["cap_micros"] == expected * 1_000_000
    if authorized > hard or hard > 10:
        assert opt["warnings"]


@pytest.mark.parametrize("field,value", [("authorized_daily_usd", "nan"), ("authorized_daily_usd", -1),
    ("authorized_daily_usd", True), ("hard_max_daily_usd", "inf"), ("authorized_on", ""),
    ("authorized_on", "2027-01-01"), ("authorized_quote", "Sure, go ahead"),
    ("authorized_quote", "AUTHORIZE ADS: $9 per day")])
def test_invalid_authorization_fails_closed(field, value):
    cfg = config()
    cfg.raw["ads"][field] = value
    assert guard.settings(cfg, NOW)["cap_micros"] == 0


def test_shipped_authorization_is_off_as_the_operator_asked():
    # Operator, 2026-10-15: "go ahead and just turn off Google Ads." Cap 0 pauses.
    cfg = load_config()
    ads = cfg.raw["ads"]
    assert ads["authorized_daily_usd"] == 0 and ads["hard_max_daily_usd"] == 10
    assert guard.settings(cfg, NOW)["cap_micros"] == 0


@pytest.mark.parametrize("cap,budget,spend,paused,reason", [
    (0, 0, 0, True, "no spending authorization"),
    (10, 5_000_001, 0, True, "daily budget exceeds cap/2"),
    (10, 5_000_000, 7_999_999, False, ""),
    (10, 5_000_000, 8_000_000, True, "80%"),
    (4, 2_000_000, 3_200_000, True, "80%"),
])
def test_spend_rules(db, cap, budget, spend, paused, reason):
    api = API()
    api.budget["1"], api.spend = budget, spend
    assert api.check(db, config(cap)) == 0
    assert bool(api.pauses) == paused
    if reason:
        assert reason in api.messages[0]


def test_all_campaigns_pause_when_one_budget_or_performance_fails(db):
    api = API()
    api.status["2"], api.budget["2"] = "ENABLED", 1_000_000
    api.budget["1"] = 6_000_000
    assert api.check(db) == 0
    assert api.pauses == ["1", "2"]
    assert len(api.messages) == 2


def test_combined_budgets_cannot_bypass_account_cap(db):
    api = API()
    api.status["2"], api.budget["2"] = "ENABLED", 3_000_000
    api.budget["1"] = 3_000_000
    assert api.check(db) == 0
    assert api.pauses == ["1", "2"]
    assert "combined enabled" in api.messages[0]


@pytest.mark.parametrize("cost,signups,outbound,paused", [
    (14_999_999, 0, 0, False), (15_000_000, 0, 9, True), (15_000_000, 0, 10, False),
    (15_000_000, 1, 0, False), (20_000_000, 1, 13, True), (20_000_000, 1, 14, False),
    (21_000_000, 1, 14, False), (21_000_001, 1, 14, True), (20_000_000, 1, 0, True),
])
def test_performance_rules_and_boundaries(db, cost, signups, outbound, paused):
    api = API()
    api.cost, api.signups, api.outbound, api.ad_clicks = cost, signups, outbound, 100000
    assert api.check(db) == 0
    assert bool(api.pauses) == paused  # ad clicks must never count as Get deal clicks
    latest = json.loads(db.meta_get(guard.STATE_KEY))["latest"]
    assert latest["campaigns"][0]["outcomes"]["deal_click"] == outbound


@pytest.mark.parametrize("failure", ["oauth", "campaigns", "account", "budgets", "costs", "mutate"])
def test_api_failures_alert_each_run_without_secret_leaks(db, failure, caplog):
    api = API()
    api.failure = failure
    with caplog.at_level(logging.INFO):
        assert api.check(db, config(0)) == 1
        assert api.check(db, config(0), key="run-2") == 1
    actions = [m for m in api.messages if "ACTION REQUIRED" in m]
    assert len(actions) == 2
    assert all("Edit > Pause" in m and CID in m for m in actions)
    assert "FAKE_SECRET_RESPONSE" not in caplog.text + json.dumps(api.messages) + db.meta_get(guard.STATE_KEY)
    if failure not in ("oauth", "campaigns"):
        assert api.pauses
    if failure == "mutate":
        assert "USER_PERMISSION_DENIED" in actions[0]


def test_one_failed_mutation_does_not_block_other_campaigns(db):
    api = API()
    api.status["2"], api.budget["2"] = "ENABLED", 1_000_000
    api.failure = "mutate"
    assert api.check(db, config(0)) == 1
    assert api.status == {"1": "ENABLED", "2": "PAUSED"}
    assert any("campaign 2 PAUSED" in m for m in api.messages)


def test_idempotency_and_no_midnight_reenable(db):
    api = API()
    assert api.check(db, config(0)) == 0
    assert api.check(db, config(0)) == 0
    assert len(api.pauses) == len(api.messages) == 1
    assert len(json.loads(db.meta_get(guard.STATE_KEY))["samples"]) == 1
    # No mutation is made for a campaign already paused, even with authorization.
    assert api.check(db, config(10), key="new-day", now=NOW + timedelta(days=1)) == 0
    assert len(api.pauses) == 1


def test_pause_notification_retries_without_repeating_mutation(db):
    api = API()
    api.telegram_fail = True
    assert api.check(db, config(0)) == 1
    api.telegram_fail = False
    assert api.check(db, config(0), key="retry") == 0
    assert len(api.pauses) == 1
    assert json.loads(db.meta_get(guard.STATE_KEY))["pauses"][0]["notified"]


def test_ambiguous_mutate_is_confirmed_next_run(db):
    api = API()
    api.ambiguous_pause = True
    assert api.check(db, config(0)) == 1
    assert not json.loads(db.meta_get(guard.STATE_KEY))["pauses"][0]["confirmed"]
    assert api.check(db, config(0), key="retry") == 0
    assert len(api.pauses) == 1
    assert any("campaign 1 PAUSED" in m for m in api.messages)


@pytest.mark.parametrize("field,value", [("currency", "EUR"), ("period", "CUSTOM_PERIOD"),
                                        ("payment", "CONVERSIONS"), ("payment", None)])
def test_unsupported_spending_limits_pause(db, field, value):
    api = API()
    setattr(api, field, value)
    api.check(db)
    assert api.pauses == ["1"]


def test_missing_state_still_pauses_and_alerts():
    api = API()
    assert api.check(None) == 1
    assert api.pauses == ["1"]
    assert "state unavailable" in "\n".join(api.messages)


def test_missing_outcomes_never_become_zero_and_stop_unmeasured_spend(db):
    api = API()
    api.failure, api.cost = "ga", 15_000_000
    assert api.check(db) == 0
    latest = json.loads(db.meta_get(guard.STATE_KEY))["latest"]
    assert latest["campaigns"][0]["outcomes"] is None
    assert "outcomes unavailable" in api.messages[0]


def test_private_report_and_shadow_data(db, caplog):
    api = API()
    api.cost = 15_000_000
    api.check(db)
    data = guard.report_data(db, config(), NOW)
    _, text = report.build_report(config(), {"ads_guard": data}, {}, NOW.date())
    for expected in ("== Ads guard ==", "$10.00/day", "2026-10-12", "AUTHORIZE ADS: $10 per day",
                     "America/New_York", "$5.00", "$15.00", "Campaigns paused today", "zero sign-ups"):
        assert expected in text
        assert expected not in caplog.text
    assert guard.report_data(db, config(), NOW + timedelta(hours=2))["stale"]
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        collected, status = report.collect(config(), NOW, client, db_path=db.path)
    assert status["ads_guard"] == "ok"
    assert collected["ads_guard"]["pauses_today"]


def test_click_signal_is_read_but_not_used_as_campaign_credit(db):
    cfg = config()
    cfg.raw["first_party_clicks"] = {"enabled": True}
    hour = int((NOW - timedelta(days=1)).timestamp() // 3600)
    db.conn.execute("INSERT INTO deal_click_coverage VALUES (?,0)", (hour,))
    db.conn.execute("INSERT INTO deal_clicks VALUES (?,?,?)", ("a" * 16, hour, 1000))
    db.conn.commit()
    api = API()
    api.cost = 15_000_000
    assert api.check(db, cfg) == 0
    latest = json.loads(db.meta_get(guard.STATE_KEY))["latest"]
    assert latest["click_signal"]["observed_clicks"] == 1000
    assert latest["campaigns"][0]["outcomes"]["deal_click"] == 0
    assert api.pauses == ["1"]


def test_pacing_and_descriptive_user_agent():
    ticks, requests = [0.0], []
    def receive(request):
        requests.append((ticks[0], request))
        return httpx.Response(200)
    with httpx.Client(transport=httpx.MockTransport(receive)) as client:
        cfg = load_config()
        paced = guard.PacedClient(client, cfg, clock=lambda: ticks[0],
            sleep=lambda delay: ticks.__setitem__(0, ticks[0] + delay))
        for url in (google_ads.TOKEN_URL, google_ads.TOKEN_URL, google_ads.SEARCH.format("v25", CID),
                    google_ads.SEARCH.format("v25", CID)):
            paced.post(url)
    assert [t for t, _r in requests] == [0, 1, 1, 2]
    assert all(r.headers["User-Agent"] == cfg.resolved_user_agent() for _t, r in requests)


def test_workflow_wiring_and_cli(monkeypatch):
    workflows = {}
    for name in ("hourly", "ads-guard"):
        workflow = yaml.safe_load((ROOT / f".github/workflows/{name}.yml").read_text())
        workflows[name] = workflow
        assert workflow["concurrency"] == {"group": "dealradar", "cancel-in-progress": False}
        steps = next(iter(workflow["jobs"].values()))["steps"]
        step = next(s for s in steps if s.get("id") == "ads_guard")
        assert "--state-unavailable" in step["run"]
        assert "!cancelled()" in step["if"]
        assert set(step["env"]) == {"GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET",
            "GOOGLE_ADS_REFRESH_TOKEN", "GOOGLE_SA_JSON", "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_ADMIN_CHAT_ID", "STATE_RESTORED"}
        commit = next(s for s in steps if "commit_state.sh" in s.get("run", ""))
        assert "steps.restore.outcome == 'success'" in commit["if"]
        assert "steps.ads_guard.outcome != 'skipped'" in commit["if"]
    dedicated = workflows["ads-guard"]
    # Manual only: a schedule in the shared lock would cancel queued pipeline runs.
    assert "schedule" not in dedicated[True] and "workflow_dispatch" in dedicated[True]
    text = (ROOT / ".github/workflows/ads-guard.yml").read_text()
    for forbidden in ("KEEPA_KEY", "DEALS_CLAUDE_API_KEY", "SMTP_", "TELEGRAM_CHANNEL_ID",
                      "python -m dealradar run", "growth-report"):
        assert forbidden not in text
    called = []
    monkeypatch.setattr(guard, "run", lambda cfg, **kw: called.append(kw) or 0)
    assert cli.main(["ads-guard", "--db", "unused", "--state-unavailable"]) == 0
    assert called == [{"db_path": "unused", "state_unavailable": True}]


def test_authorization_clamping_alerts_every_run_and_never_edits_config(db):
    cfg = config(50)
    before = json.dumps(cfg.raw)
    api = API()
    assert api.check(db, cfg) == 0
    assert api.check(db, cfg, key="next") == 0
    assert not api.pauses
    assert len(api.messages) == 2
    assert all("AUTHORIZATION ABOVE HARD MAX" in m for m in api.messages)
    assert json.dumps(cfg.raw) == before


def test_performance_knobs_are_configurable(db):
    cfg = config()
    cfg.raw["ads"]["performance"] = {"zero_signup_cost_usd": 3, "min_deal_clicks": 5,
                                     "lookback_days": 7}
    api = API()
    api.cost, api.outbound = 3_000_000, 4
    assert api.check(db, cfg) == 0
    assert api.pauses == ["1"]
    snapshot = json.loads(db.meta_get(guard.STATE_KEY))["latest"]
    assert (snapshot["day"], snapshot["performance_start"], snapshot["performance_end"]) == (
        "2026-10-13", "2026-10-06", "2026-10-12")


def test_missing_admin_credentials_is_red_and_does_not_allow_spending(db, monkeypatch, caplog):
    monkeypatch.delenv("TELEGRAM_ADMIN_CHAT_ID")
    api = API()
    assert api.check(db) == 1
    assert api.pauses == ["1"]
    assert not api.messages
    assert "admin alert unavailable" in caplog.text


@pytest.mark.parametrize("stored", ["{", '{"pauses":null}', '{"pauses":[{}]}', '{"samples":[{}]}'])
def test_invalid_guard_state_still_pauses(db, stored):
    db.meta_set(guard.STATE_KEY, stored)
    api = API()
    assert api.check(db) == 1
    assert api.pauses == ["1"]


def test_failed_local_save_does_not_block_pause(db, monkeypatch):
    monkeypatch.setattr(db, "meta_set", lambda *a: (_ for _ in ()).throw(OSError("FAKE_SECRET")))
    api = API()
    assert api.check(db) == 1
    assert api.pauses == ["1"]
    assert "state write failed" in "\n".join(api.messages)


def test_spend_from_disabled_campaign_still_trips_account_cap(db):
    api = API()
    api.status["2"] = "PAUSED"
    api.spend = 8_000_000
    assert api.check(db) == 0
    assert api.pauses == ["1"]
    queries = [json.loads(r.content)["query"] for r in api.requests
               if r.url.path.endswith("googleAds:search")]
    account_cost = next(q for q in queries if "metrics.cost_micros" in q and "FROM customer" in q)
    assert "status" not in account_cost
    assert "'2026-10-13'" in account_cost


@pytest.mark.parametrize("repeated", [False, True])
def test_google_ads_pagination_includes_all_campaigns(repeated):
    tokens = []
    def respond(request):
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "fake"})
        body = json.loads(request.content)
        tokens.append(body.get("pageToken", ""))
        if len(tokens) == 1:
            return httpx.Response(200, json={"results": [{"campaign": {"id": "1", "status": "ENABLED"}}],
                                            "nextPageToken": "next"})
        return httpx.Response(200, json={"results": [{"campaign": {"id": "2", "status": "ENABLED"}}],
                                        **({"nextPageToken": "next"} if repeated else {})})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        api = google_ads.GuardAPI(client, config())
        if repeated:
            with pytest.raises(google_ads.GoogleAdsError, match="INVALID_PAGINATION"):
                api.campaigns()
        else:
            assert set(api.campaigns()) == {"1", "2"}
    assert tokens == ["", "next"]


@pytest.mark.parametrize("body", [{}, {"partialFailureError": {"code": 7}},
                                  {"results": [{"resourceName": "wrong"}]}])
def test_mutation_requires_a_confirmed_receipt(body):
    def respond(request):
        return httpx.Response(200, json={"access_token": "fake"} if
                              request.url.host == "oauth2.googleapis.com" else body)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(google_ads.GoogleAdsError, match="PAUSE_NOT_CONFIRMED"):
            google_ads.GuardAPI(client, config()).pause("1")


def test_redirect_is_a_guard_api_failure_even_with_success_shaped_json():
    def respond(request):
        return httpx.Response(200, json={"access_token": "fake"}) if request.url.host == "oauth2.googleapis.com" \
            else httpx.Response(302, json={"results": []}, headers={"Location": "https://example.test/"})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(google_ads.GoogleAdsError, match="HTTP 302"):
            google_ads.GuardAPI(client, config()).campaigns()


def test_ga4_attribution_is_exclusive_and_paginates():
    seen = []
    rows = [
        (["1", CID, "1", "1", "google / cpc", "session_start"], 3),
        (["1", CID, "1", "1", "google / cpc", "deal_click"], 7),
        (["(not set)", "(not set)", "2", "display name", "google / cpc", "deal_click"], 2),
        (["(not set)", "(not set)", "(not set)", "3", "google / cpc", "sign_up"], 1),
        (["(not set)", "(not set)", "1", "1", "google / organic", "deal_click"], 9999),
        (["(not set)", "(not set)", "(not set)", "named campaign", "google / cpc", "deal_click"], 9999),
        (["1", "999", "1", "1", "google / cpc", "deal_click"], 9999),
    ]
    def respond(request):
        req = json.loads(request.content)["requests"][0]
        offset = req["offset"]
        seen.append(offset)
        return httpx.Response(200, json={"reports": [{"dimensionHeaders": req["dimensions"],
            "metricHeaders": [{"name": "eventCount"}], "metadata": {"timeZone": "America/New_York"},
            "rowCount": len(rows), "rows": [{"dimensionValues": [{"value": d} for d in dims],
                "metricValues": [{"value": str(count)}]} for dims, count in rows[offset:offset + 2]]}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        data = google.ga4_ads_outcomes(client, "fake", "123", NOW.date(), NOW.date(), "America/New_York")
    assert data[(CID, "1")] == {"session_start": 3, "deal_click": 7, "sign_up": 0}
    assert data[("utm", "2")]["deal_click"] == 2
    assert data[("utm", "3")]["sign_up"] == 1
    assert data[("999", "1")]["deal_click"] == 9999
    assert ("utm", "1") not in data
    assert seen == [0, 2, 4, 6]


@pytest.mark.parametrize("metadata", [{"timeZone": "UTC"}, {"timeZone": "America/New_York", "subjectToThresholding": True},
    {"timeZone": "America/New_York", "samplingMetadatas": [{}]},
    {"timeZone": "America/New_York", "dataLossFromOtherRow": True}])
def test_incomplete_ga4_is_unavailable(metadata):
    def respond(request):
        req = json.loads(request.content)["requests"][0]
        return httpx.Response(200, json={"reports": [{"dimensionHeaders": req["dimensions"],
            "metricHeaders": [{"name": "eventCount"}], "metadata": metadata, "rows": []}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError):
            google.ga4_ads_outcomes(client, "fake", "123", NOW.date(), NOW.date(), "America/New_York")


def test_no_campaigns_is_safe_without_ga4(db):
    api = API()
    api.status = {}
    api.failure = "unexpected_ga"
    assert api.check(db, config(0)) == 0
    assert not api.pauses and not api.messages


def test_money_stop_precedes_outcome_requests(db):
    api = API()
    assert api.check(db, config(0)) == 0
    pause = next(i for i, r in enumerate(api.requests) if r.url.path.endswith("campaigns:mutate"))
    analytics = next(i for i, r in enumerate(api.requests) if r.url.host == "analyticsdata.googleapis.com")
    assert pause < analytics


def test_partial_pause_is_not_retried_in_the_same_run(db):
    api = API()
    api.failure = "mutate"
    assert api.check(db, config(0)) == 1
    assert api.pauses == ["1"]


def test_failed_pause_retries_produce_one_confirmed_receipt(db):
    api = API()
    api.failure = "mutate"
    assert api.check(db, config(0), key="failed-1") == 1
    assert api.check(db, config(0), key="failed-2") == 1
    api.failure = ""
    assert api.check(db, config(0), key="success") == 0
    events = json.loads(db.meta_get(guard.STATE_KEY))["pauses"]
    assert len(events) == 1 and events[0]["confirmed"] and events[0]["notified"]
    assert len([m for m in api.messages if "campaign 1 PAUSED" in m]) == 1


def test_pause_during_timezone_failure_appears_after_account_read_recovers(db):
    api = API()
    api.failure = "account"
    assert api.check(db, config(0)) == 1
    api.failure = ""
    assert api.check(db, config(0), key="recovered") == 0
    data = guard.report_data(db, config(0), NOW)
    assert len(data["pauses_today"]) == 1
