"""Money reporting and the hourly affiliate guard, entirely offline."""
import json
import logging
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import yaml

from dealradar.growth import google, report, subscribers
from tests.test_growth_report import (FakeApis, GOOGLE_TOKEN, NOW,
                                      cfg, clean_env, client_for)

ROOT = Path(__file__).resolve().parents[1]
ADDRESS = "private-subscriber@example.com"
CF_TOKEN = "private-cloudflare-token"


def test_store_query_body_and_parse():
    fake = FakeApis()
    with client_for(fake) as client:
        data = google.ga4_stores(client, GOOGLE_TOKEN, "987654", 7)
    body = json.loads(fake.requests[0].content)
    assert len(body["requests"]) == 2
    for query, start in zip(body["requests"], ("yesterday", "7daysAgo")):
        assert query["dateRanges"] == [{"startDate": start, "endDate": "yesterday"}]
        assert query["dimensions"] == [{"name": "customEvent:store"}]
        assert query["metrics"] == [{"name": "eventCount"}]
        assert query["dimensionFilter"] == {"filter": {"fieldName": "eventName",
            "stringFilter": {"matchType": "EXACT", "value": "deal_click"}}}
        assert query["limit"] == 10 and query["metricAggregations"] == ["TOTAL"]
        assert query["orderBys"] == [{"metric": {"metricName": "eventCount"}, "desc": True}]
    assert data == {"yesterday": {"total": 9001, "stores": [("Amazon", 8000), ("Target", 1)]},
                    "window": {"total": 20001, "stores": [("Amazon", 18000), ("Target", 1)]}}


@pytest.mark.parametrize("message", [
    "Field customEvent:store is not a valid dimension. For a list of valid dimensions...",
    "Unknown dimension customEvent:store", "customEvent:store is not registered",
])
def test_unknown_store_dimension_does_not_affect_report(cfg, monkeypatch, caplog, message):
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("GOOGLE_SA_JSON", "configured")
    monkeypatch.setattr(google, "access_token", lambda *_: GOOGLE_TOKEN)
    fake = FakeApis()

    def api(request):
        if request.url.host == "analyticsdata.googleapis.com" and len(json.loads(request.content)["requests"]) == 2:
            return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT", "message": message}})
        return fake(request)

    with httpx.Client(transport=httpx.MockTransport(api)) as client:
        data, status = report.collect(cfg, NOW, client)
    assert status["ga4_stores"] == "skipped"
    assert status["ga4"] == status["search_console"] == status["sitemap"] == "ok"
    _, text = report.build_report(cfg, data, status, date(2026, 10, 10))
    assert google.STORE_REGISTRATION in text
    assert "Clicks out yesterday: 9,001" in text and "support_click 12 / 45" in text
    assert message not in caplog.text


def test_other_store_errors_are_errors(cfg, monkeypatch):
    monkeypatch.setenv("GOOGLE_SA_JSON", "configured")
    monkeypatch.setattr(google, "access_token", lambda *_: GOOGLE_TOKEN)
    fake = FakeApis()

    def api(request):
        if request.url.host == "analyticsdata.googleapis.com" and len(json.loads(request.content)["requests"]) == 2:
            return httpx.Response(403, json={"error": {"message": "customEvent:store unknown private"}})
        return fake(request)

    with httpx.Client(transport=httpx.MockTransport(api)) as client:
        _, status = report.collect(cfg, NOW, client)
    assert status["ga4_stores"] == "error" and status["ga4"] == "ok"


class FakeCloudflare:
    def __init__(self, title="wowpriced_subs", denied="", missing=False):
        self.title, self.denied, self.missing = title, denied, missing
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        assert request.method == "GET"
        assert request.headers["Authorization"] == f"Bearer {CF_TOKEN}"
        base = "/client/v4/accounts/account/storage/kv/namespaces"
        assert request.url.path in (base, base + "/subs-id/keys")  # never read values
        if self.denied and (self.denied == "namespaces" or request.url.path.endswith("/keys")):
            return httpx.Response(403, text=f"denied {ADDRESS} {CF_TOKEN}")
        if request.url.path == base:
            page = request.url.params["page"]
            return httpx.Response(200, json={"success": True,
                "result": [] if page == "1" or self.missing else [{"id": "subs-id", "title": self.title}],
                "result_info": {"total_pages": 2}})
        assert request.url.params["prefix"] == "sub:" and request.url.params["limit"] == "1000"
        cursor = request.url.params.get("cursor", "")
        # The middle page is empty with a cursor: do not stop there.
        stamps, next_cursor = {
            "": (["2026-10-10T04:00:00Z", "2026-10-11T03:59:59Z", "2026-10-10T03:59:59Z"], "next"),
            "next": ([], "last"),
            "last": (["2026-10-11T04:00:00Z", "2026-10-10T15:00:00-04:00", None], ""),
        }[cursor]
        return httpx.Response(200, json={"success": True,
            "result": [{"name": f"sub:{ADDRESS}", "metadata": {"ts": ts,
                        "confirmed": False, "token": "private-confirmation-token"}} for ts in stamps],
            "result_info": {"cursor": next_cursor}})


@pytest.mark.parametrize("title", subscribers.NAMES)
def test_subscriber_pagination_counts_and_privacy(cfg, monkeypatch, caplog, title):
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", CF_TOKEN)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "account")
    fake = FakeCloudflare(title)
    other = FakeApis()
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: fake(req) if req.url.host == "api.cloudflare.com" else other(req))) as client:
        data, status = report.collect(cfg, NOW, client)
    assert data["subscribers"] == {"total": 6, "yesterday": 3}
    assert status["subscribers"] == "ok" and len(fake.requests) == 5
    _, text = report.build_report(cfg, data, status, date(2026, 10, 10))
    assert "Join free yesterday: 3" in text
    assert "Join free list: total 6, new yesterday 3" in text
    for private in (ADDRESS, CF_TOKEN, "private-confirmation-token"):
        assert private not in text + caplog.text + repr(data)


@pytest.mark.parametrize("denied", ["namespaces", "keys"])
def test_subscribers_403_is_safe_and_other_sections_continue(cfg, monkeypatch, caplog, denied):
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", CF_TOKEN)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "account")
    fake, other = FakeCloudflare(denied=denied), FakeApis()
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: fake(req) if req.url.host == "api.cloudflare.com" else other(req))) as client:
        data, status = report.collect(cfg, NOW, client)
    _, text = report.build_report(cfg, data, status, date(2026, 10, 10))
    assert status["subscribers"] == "error" and status["site_health"] == "ok"
    assert "subscribers: not available (HTTPStatusError 403)" in text
    assert "Join free yesterday:" not in text
    assert ADDRESS not in text + caplog.text and CF_TOKEN not in text + caplog.text


def test_missing_cloudflare_secret_is_one_line(cfg):
    fake = FakeApis()
    with client_for(fake) as client:
        data, status = report.collect(cfg, NOW, client)
    _, text = report.build_report(cfg, data, status, date(2026, 10, 10))
    assert status["subscribers"] == "skipped"
    assert text.count("subscribers: not available (CLOUDFLARE_API_TOKEN not set)") == 1
    assert "api.cloudflare.com" not in fake.hosts()


def test_missing_namespace_is_unavailable():
    fake = FakeCloudflare(missing=True)
    tz = ZoneInfo("America/New_York")
    with client_for(fake) as client:
        assert subscribers.counts(client, CF_TOKEN, "account", datetime(2026, 10, 10, tzinfo=tz),
                                  datetime(2026, 10, 11, tzinfo=tz)) == {"unavailable": "KV namespace not found"}
    assert len(fake.requests) == 2


def test_money_totals_and_headlines(cfg):
    fake = FakeApis()
    with client_for(fake) as client:
        ga = google.ga4(client, GOOGLE_TOKEN, "987654", 7)
        stores = google.ga4_stores(client, GOOGLE_TOKEN, "987654", 7)
    _, text = report.build_report(cfg, {"ga4": ga, "ga4_stores": stores,
        "subscribers": {"total": 456, "yesterday": 12}}, {}, date(2026, 10, 10))
    assert text.splitlines()[2:5] == ["Sessions: 812,345 / 5,120,333",
                                    "Clicks out yesterday: 9,001", "Join free yesterday: 12"]
    assert "== Money ==" in text
    assert "Clicks out by store (yesterday): total 9,001\n  Amazon: 8,000\n  Target: 1" in text
    assert "Clicks out by store (last 7 days): total 20,001\n  Amazon: 18,000" in text


def test_growth_workflow_cloudflare_env():
    workflow = yaml.safe_load((ROOT / ".github/workflows/growth-report.yml").read_text())
    env = next(step["env"] for step in workflow["jobs"]["report"]["steps"]
               if step.get("run") == "python -m dealradar growth-report")
    for name in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"):
        assert env[name] == "${{ secrets." + name + " }}"


def test_site_check_amazon_guard_offline():
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    subprocess.run(["node", str(ROOT / "tests/site_check_logic.cjs")],
                   check=True, capture_output=True, text=True)
    script = (ROOT / "scripts/site_check.js").read_text()
    step = script.split('if (device === "desktop") await step(10,', 1)[1].split('fs.mkdirSync("shots"', 1)[0]
    assert "checkAmazonTag(doc, amazonTag(config))" in step and "check(result.ok, result.reason)" in step
    assert "checkSovrnLinks(doc)" in step and "check(sovrn.ok, sovrn.reason)" in step
    assert "sovrn: ${sovrn.links} links" in step
    assert '"../config.yaml"' in step
    assert "fetch" not in step and "goto" not in step
    assert script.count('readSite("deals.json")') == 1
