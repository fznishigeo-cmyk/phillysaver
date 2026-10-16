"""Offline Keyword Planner reads, private output and request-boundary checks."""

import ast
import base64
import copy
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import yaml

from dealradar import cli
from dealradar.config import AppConfig, load_config
from dealradar.growth import google_ads, keyword_volumes as volumes, report

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
TOKEN = "FAKE_PRIVATE_ACCESS_TOKEN"
SECRETS = {name: "FAKE_" + name for name in (
    "GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN", "REPORTS_REPO_TOKEN")}
KEYWORDS = ["target price history", "price tracker", "best buy price history",
            "price trackers", "fake discount", "woot price history"]
METRICS = {"results": [
    {"text": "target price history", "keywordMetrics": {"avgMonthlySearches": "125"}},
    {"text": "price tracker", "closeVariants": ["price trackers"], "keywordMetrics": {
        "avgMonthlySearches": "98765", "competition": "HIGH",
        "lowTopOfPageBidMicros": "1", "highTopOfPageBidMicros": "9223372036854775807",
        "averageCpcMicros": "4321000", "monthlySearchVolumes": [
            {"year": "2025", "month": "DECEMBER", "monthlySearches": "90000"},
            {"year": "2026", "month": "JANUARY", "monthlySearches": "80000"},
            {"year": 2026, "month": "FEBRUARY", "monthlySearches": "100000"}]}},
    {"text": "woot price history", "keywordMetrics": {
        "avgMonthlySearches": "0", "averageCpcMicros": "0"}},
    {"text": "best buy price history", "keywordMetrics": {"avgMonthlySearches": "98765"}},
]}


class Clock:
    def __init__(self):
        self.time = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.time

    def sleep(self, delay):
        assert delay >= 0
        self.sleeps.append(delay)
        self.time += delay


@pytest.fixture(autouse=True)
def environment(monkeypatch, caplog):
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    # Match the CLI's suppression of transport chatter in public logs.
    caplog.set_level(logging.WARNING, logger="httpx")
    caplog.set_level(logging.INFO)


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(volumes, "clock", clock)
    monkeypatch.setattr(report, "clock", clock)
    return clock


def config():
    return AppConfig(growth_google_ads_api_version="v25", growth_google_ads_customer_id="4412275803",
                     growth_keyword_volumes={"keywords": KEYWORDS.copy(), "geo_target": 2840, "language": 1000},
                     raw={"growth": {"reports_repo": "owner/private-reports"}})


class API:
    def __init__(self, clock, *, metrics=None, tokens=None, get_status=404, put_status=201):
        self.clock = clock
        self.metrics = list(metrics) if metrics is not None else [(200, copy.deepcopy(METRICS))]
        self.tokens = list(tokens) if tokens is not None else [(200, {"access_token": TOKEN})]
        self.get_status, self.put_status = get_status, put_status
        self.calls, self.files = [], []

    def handler(self, request):
        self.calls.append((self.clock.monotonic(), request))
        if str(request.url) == google_ads.TOKEN_URL:
            assert request.method == "POST"
            code, body = self.tokens.pop(0)
        elif str(request.url) == volumes.METRICS_URL.format("v25", "4412275803"):
            assert request.method == "POST"
            code, body = self.metrics.pop(0)
        else:
            assert request.url.host == "api.github.com"
            assert request.url.path.startswith("/repos/owner/private-reports/contents/")
            assert request.headers["authorization"] == "Bearer " + SECRETS["REPORTS_REPO_TOKEN"]
            if request.method == "GET":
                code, body = self.get_status, {"sha": "EXISTING_SHA"}
            else:
                assert request.method == "PUT"
                self.files.append(json.loads(request.content))
                code, body = self.put_status, {}
        return httpx.Response(code, json=body)

    def run(self, cfg=None, now=NOW):
        with httpx.Client(transport=httpx.MockTransport(self.handler)) as client:
            return volumes.run(cfg or config(), http_client=client, now=now)

    def content(self):
        return base64.b64decode(self.files[-1]["content"]).decode()


def assert_private(caplog):
    for planted in (*KEYWORDS, "98765", "4321000", "$4.321000", "9223372036854775807", "HIGH",
                    TOKEN, *SECRETS.values(), "PRIVATE_MESSAGE", "999888777666"):
        assert planted not in caplog.text


@pytest.mark.parametrize("manager", ["", "1234567890"])
def test_happy_path_request_timing_sorted_private_table(clock, caplog, manager):
    api, cfg = API(clock), config()
    cfg.growth_google_ads_login_customer_id = manager
    assert api.run(cfg) == 0
    requests = [r for _, r in api.calls]
    assert [(r.method, str(r.url)) for r in requests] == [
        ("POST", google_ads.TOKEN_URL),
        ("POST", volumes.METRICS_URL.format("v25", "4412275803")),
        ("GET", "https://api.github.com/repos/owner/private-reports/contents/keywords/2026-10-16.md"),
        ("PUT", "https://api.github.com/repos/owner/private-reports/contents/keywords/2026-10-16.md")]
    times = [t for t, _ in api.calls]
    assert all(b - a >= 1 for a, b in zip(times, times[1:]))
    assert parse_qs(requests[0].content.decode()) == {
        "grant_type": ["refresh_token"], "client_id": [SECRETS["GOOGLE_ADS_CLIENT_ID"]],
        "client_secret": [SECRETS["GOOGLE_ADS_CLIENT_SECRET"]],
        "refresh_token": [SECRETS["GOOGLE_ADS_REFRESH_TOKEN"]]}
    assert requests[1].headers["authorization"] == "Bearer " + TOKEN
    assert requests[1].headers.get("login-customer-id", "") == manager
    if not manager:
        assert "login-customer-id" not in requests[1].headers
    assert all(r.headers["user-agent"] == cfg.resolved_user_agent() for r in requests)
    assert json.loads(requests[1].content) == {
        "keywords": KEYWORDS, "geoTargetConstants": ["geoTargetConstants/2840"],
        "language": "languageConstants/1000", "keywordPlanNetwork": "GOOGLE_SEARCH",
        "historicalMetricsOptions": {"includeAverageCpc": True}}
    text = api.content()
    assert text.startswith("# Keyword volumes — 2026-10-16 UTC; geo 2840; language 1000; API v25.")
    assert "Google rounds these; accounts with little spend may get coarse values" in text
    rows = [line.split(" | ") for line in text.splitlines() if line.startswith("| ")][1:]
    assert [row[0] for row in rows] == ["| best buy price history", "| price tracker",
        "| target price history", "| woot price history", "| fake discount"]
    assert rows[1][1:9] == ["price trackers", "98765", "2026-01: 80000", "2026-02: 100000",
        "HIGH", "$0.000001", "$9223372036854.775807", "$4.321000"]
    assert rows[2][3:9] == [""] * 6
    assert rows[3][2] == "0" and rows[3][8] == "$0.000000"
    assert rows[-1][-1] == "merged or no data |"
    assert api.files[0]["message"] == "Keyword volumes 2026-10-16"
    assert set(caplog.messages) == {"keyword volumes: keywords sent 6", "keyword volumes: results received 4",
                                    "reports repo: saved", "keyword volumes: saved"}
    assert_private(caplog)


def test_utc_date_and_idempotent_private_replacement(clock):
    api = API(clock, get_status=200)
    local = datetime(2026, 10, 16, 0, 30, tzinfo=timezone(timedelta(hours=5)))
    assert api.run(now=local) == 0
    assert api.calls[-1][1].url.path.endswith("/keywords/2026-10-15.md")
    assert api.files[0]["sha"] == "EXISTING_SHA"
    assert "2026-10-15 UTC" in api.content()


def error(code="RESOURCE_EXHAUSTED"):
    return {"error": {"status": code, "message": "PRIVATE_MESSAGE 999888777666 price tracker",
        "details": [{"errors": [{"errorCode": {"authorizationError": "USER_PERMISSION_DENIED"},
                                  "message": "PRIVATE_MESSAGE 98765"}]}]}}


@pytest.mark.parametrize("status,body", [(429, {}), (400, error()), (200, error())])
def test_one_retry_after_thirty_seconds(clock, caplog, status, body):
    api = API(clock, metrics=[(status, body), (200, METRICS)])
    assert api.run() == 0
    assert len(api.calls) == 5
    assert [t for t, _ in api.calls[:3]] == [0, 1, 31]
    assert clock.sleeps.count(30) == 1
    assert_private(caplog)


@pytest.mark.parametrize("status,body", [(429, error()), (400, error()), (200, error())])
def test_second_exhaustion_stops_without_a_file(clock, caplog, status, body):
    api = API(clock, metrics=[(status, body), (status, body)])
    assert api.run() == 1
    assert len(api.calls) == 3 and not api.files
    assert "RESOURCE_EXHAUSTED" in caplog.text
    assert "keyword volumes: not saved" in caplog.messages
    assert_private(caplog)


def test_token_retry_uses_the_only_retry_and_three_request_budget(clock, caplog):
    api = API(clock, tokens=[(429, error()), (200, {"access_token": TOKEN})], metrics=[(429, error())])
    assert api.run() == 1
    assert [t for t, _ in api.calls] == [0, 30, 31]
    assert len(api.calls) == 3 and not api.files
    assert_private(caplog)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 503])
def test_other_google_errors_codes_only_no_retry(clock, caplog, status):
    api = API(clock, metrics=[(status, error("PERMISSION_DENIED"))])
    assert api.run() == 1
    assert len(api.calls) == 2 and not api.files
    assert f"HTTP {status} PERMISSION_DENIED USER_PERMISSION_DENIED" in caplog.text
    assert_private(caplog)


def test_oauth_error_also_uses_codes_only(clock, caplog):
    api = API(clock, tokens=[(400, {"error": "invalid_grant", "error_description": "PRIVATE_MESSAGE 98765"})])
    assert api.run() == 1
    assert len(api.calls) == 1 and not api.files
    assert "invalid_grant" in caplog.text
    assert_private(caplog)


@pytest.mark.parametrize("secret", SECRETS.keys() - {"REPORTS_REPO_TOKEN"})
def test_missing_google_secret_sends_nothing(clock, monkeypatch, caplog, secret):
    monkeypatch.delenv(secret)
    api = API(clock)
    assert api.run() == 1
    assert api.calls == []
    assert "MISSING_CREDENTIALS" in caplog.text
    assert_private(caplog)


@pytest.mark.parametrize("missing", ["token", "repo"])
def test_missing_private_destination_drops_results(clock, monkeypatch, caplog, missing):
    api, cfg = API(clock), config()
    if missing == "token":
        monkeypatch.delenv("REPORTS_REPO_TOKEN")
    else:
        cfg.raw["growth"]["reports_repo"] = ""
    assert api.run(cfg) == 1
    assert len(api.calls) == 2 and not api.files
    assert "keyword volumes: not saved" in caplog.messages
    assert_private(caplog)


@pytest.mark.parametrize("get_status,put_status,requests", [(403, 201, 3), (404, 403, 4), (200, 500, 4)])
def test_private_save_http_errors_fail_without_public_results(clock, caplog, get_status, put_status, requests):
    api = API(clock, get_status=get_status, put_status=put_status)
    assert api.run() == 1
    assert len(api.calls) == requests
    assert "keyword volumes: not saved" in caplog.messages
    assert_private(caplog)


@pytest.mark.parametrize("doc", [None, [], "private price tracker 98765", {"results": None},
    {"results": {}}, {"results": [None]}, {"results": [{}]},
    {"results": [{"text": "price tracker", "keywordMetrics": []}]},
    {"results": [{"text": "price tracker", "closeVariants": "price trackers"}]},
    {"results": [{"text": "price tracker", "closeVariants": [123]}]},
    {"results": [{"text": "price tracker", "keywordMetrics": {"avgMonthlySearches": "98765.5"}}]},
    {"results": [{"text": "price tracker", "keywordMetrics": {"avgMonthlySearches": True}}]},
    {"results": [{"text": "price tracker", "keywordMetrics": {"averageCpcMicros": -1}}]},
    {"results": [{"text": "price tracker", "keywordMetrics": {"competition": {"private": "value"}}}]},
    {"results": [{"text": "price tracker", "keywordMetrics": {"monthlySearchVolumes": {}}}]},
    {"results": [{"text": "price tracker", "keywordMetrics": {"monthlySearchVolumes": [None]}}]},
    {"results": [{"text": "price tracker", "keywordMetrics": {"monthlySearchVolumes": [{"month": "BAD"}]}}]},
])
def test_invalid_shapes_are_not_saved(clock, caplog, doc):
    api = API(clock, metrics=[(200, doc)])
    assert api.run() == 1
    assert len(api.calls) == 2 and not api.files
    assert "INVALID_RESPONSE" in caplog.text
    assert_private(caplog)


@pytest.mark.parametrize("doc", [{}, {"results": []}, {"results": [{"text": "price tracker"}]}])
def test_empty_results_and_missing_values_stay_blank(clock, caplog, doc):
    api = API(clock, metrics=[(200, doc)])
    assert api.run() == 0
    text = api.content()
    for keyword in KEYWORDS:
        assert f"| {keyword} |" in text
    if doc.get("results"):
        assert "| price tracker |  |  |  |  |  |  |  |  |  |" in text
    else:
        assert text.count("merged or no data") == len(KEYWORDS)
    assert_private(caplog)


@pytest.mark.parametrize("url", [
    "https://googleads.googleapis.com/v25/customers/4412275803/campaigns:mutate",
    "https://googleads.googleapis.com/v25/customers/4412275803/googleAds:search",
    "https://googleads.googleapis.com/v25/customers/9999999999:generateKeywordHistoricalMetrics",
    "https://googleads.googleapis.com/v26/customers/4412275803:generateKeywordHistoricalMetrics",
    "http://oauth2.googleapis.com/token", "https://oauth2.googleapis.com/token?extra=1",
    "https://example.invalid/token",
])
def test_url_allowlist_refuses_before_transport(clock, url):
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("transport reached"))) as client:
        google = volumes._GoogleRequests(client, config())
        with pytest.raises(google_ads.GoogleAdsError, match="^URL_NOT_ALLOWED$"):
            google.post(url)
        assert google.sent == 0


def test_module_has_no_ads_write_dependencies():
    source = Path(volumes.__file__).read_text()
    tree = ast.parse(source)
    for forbidden in ("mutate", "ads_guard", "ads_setup", "GuardAPI"):
        assert forbidden not in source
        assert forbidden not in ast.dump(tree)
    transport_calls = [node.func.attr for node in ast.walk(tree)
                       if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                       and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "client"]
    assert transport_calls == ["post"]


def test_redirects_are_never_followed(clock, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(307, headers={"Location": "https://example.invalid/private"})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        assert volumes.run(config(), http_client=client, now=NOW) == 1
    assert len(calls) == 1
    assert "HTTP 307" in caplog.text
    assert_private(caplog)


def test_transport_errors_never_log_exception_messages(clock, caplog):
    def handler(request):
        raise httpx.ReadTimeout("PRIVATE_MESSAGE 98765 price tracker")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert volumes.run(config(), http_client=client, now=NOW) == 1
    assert "HTTP_ERROR" in caplog.text
    assert_private(caplog)


def write_config(tmp_path, settings):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"growth": {"keyword_volumes": settings}}))
    return path


@pytest.mark.parametrize("keywords", [[], ["duplicate", "duplicate"], ["Uppercase"],
    [f"keyword {n}" for n in range(101)], ["a" * 81], ["bad/characters"], ["bad_underscore"],
    [" leading"], ["trailing "], [""], [None], [123], "price tracker", ["line\nbreak"], ["café"]])
def test_invalid_keyword_config_rejected(tmp_path, keywords):
    with pytest.raises(ValueError, match="growth.keyword_volumes.keywords"):
        load_config(write_config(tmp_path, {"keywords": keywords}))


@pytest.mark.parametrize("field", ["geo_target", "language"])
@pytest.mark.parametrize("value", [0, -1, "1000", 1.5, True, None])
def test_geo_and_language_require_positive_ints(tmp_path, field, value):
    with pytest.raises(ValueError, match=f"growth.keyword_volumes.{field}"):
        load_config(write_config(tmp_path, {"keywords": ["price tracker"], field: value}))


@pytest.mark.parametrize("settings", [None, [], "price tracker", {}, {"keywords": ["valid"], "extra": True}])
def test_invalid_keyword_settings_rejected(tmp_path, settings):
    with pytest.raises(ValueError, match="growth.keyword_volumes"):
        load_config(write_config(tmp_path, settings))


def test_valid_config_boundaries_and_shipped_starter_list(tmp_path):
    keywords = ["a" * 80, "store's price-history", *[f"phrase {n}" for n in range(98)]]
    cfg = load_config(write_config(tmp_path, {"keywords": keywords, "geo_target": 123, "language": 456}))
    assert cfg.growth_keyword_volumes == {"keywords": keywords, "geo_target": 123, "language": 456}
    shipped = load_config()
    assert shipped.growth_google_ads_api_version == "v25"
    assert shipped.growth_google_ads_customer_id == "4412275803"
    settings = shipped.growth_keyword_volumes
    starter = ("best buy price history, best buy price tracker, target price history, target price tracker, "
        "woot price history, price drop alert, price drop alerts, price drop alert email, price tracker, "
        "price history chart, is this a good deal, is this a good price, price checker, was price checker, "
        "is this sale real, fake discount, fake discount checker, walmart was price, walmart rollback meaning, "
        "is walmart rollback a good deal, walmart price history, walmart price tracker, costco price adjustment, "
        "target price adjustment, target price match, best buy price match, best buy price match after purchase, "
        "home depot price adjustment, lowes price adjustment, kohls price adjustment, macys price adjustment, "
        "price adjustment after purchase, black friday price adjustment, amazon price checker, amazon price tracker, "
        "amazon price history, amazon typical price, ps5 price history, airpods price history, camelcamelcamel walmart"
    ).split(", ")
    assert settings == {"keywords": starter, "geo_target": 2840, "language": 1000}
    assert len(starter) == 40


@pytest.mark.parametrize("field,value", [("growth_google_ads_api_version", "v25/other"),
    ("growth_google_ads_customer_id", ""), ("growth_google_ads_customer_id", "123/other"),
    ("growth_google_ads_login_customer_id", "123/other"), ("growth_keyword_volumes", {})])
def test_invalid_runtime_config_sends_nothing(clock, caplog, field, value):
    cfg, api = config(), API(clock)
    setattr(cfg, field, value)
    assert api.run(cfg) == 1
    assert api.calls == []
    assert "INVALID_CONFIG" in caplog.text


def test_save_copy_default_path_message_and_bytes_unchanged(clock):
    api = API(clock)
    with httpx.Client(transport=httpx.MockTransport(api.handler)) as client:
        assert report.save_copy(config(), "Growth report for Oct 16", "private text\n", NOW.date(), client=client)
    assert [r.url.path for _, r in api.calls] == [
        "/repos/owner/private-reports/contents/reports/2026-10-16.md"] * 2
    assert api.files[0] == {"message": "Growth report 2026-10-16", "content": base64.b64encode(
        b"# Growth report for Oct 16\n\nprivate text\n").decode()}
    assert [t for t, _ in api.calls] == [0, 1]


def test_cli_uses_common_config_and_lazy_runner(monkeypatch, tmp_path):
    path = write_config(tmp_path, {"keywords": ["price tracker"]})
    calls = []
    monkeypatch.setattr(volumes, "run", lambda cfg: calls.append(cfg) or 1)
    assert cli.main(["keyword-volumes", "--config", str(path), "--db", str(tmp_path / "unused.db")]) == 1
    assert calls[0].growth_keyword_volumes["keywords"] == ["price tracker"]
    assert not (tmp_path / "unused.db").exists()


def test_manual_workflow_shape_and_secrets_only_in_command_env():
    source = (ROOT / ".github/workflows/keyword-volumes.yml").read_text()
    workflow = yaml.safe_load(source)
    assert workflow.get("on", workflow.get(True)) == {"workflow_dispatch": None}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {"group": "keyword-volumes", "cancel-in-progress": False}
    job, = workflow["jobs"].values()
    assert job["timeout-minutes"] == 5
    steps = job["steps"]
    assert len(steps) == 4
    assert steps[0]["uses"].startswith("actions/checkout@")
    assert steps[1]["uses"].startswith("actions/setup-python@")
    assert steps[1]["with"]["python-version"] == "3.12"
    assert steps[2]["run"] == "pip install -e ."
    assert steps[3]["run"] == "python -m dealradar keyword-volumes"
    assert steps[3]["env"] == {name: "${{ secrets." + name + " }}" for name in SECRETS}
    for line in source.splitlines():
        if "secrets." in line:
            assert re.fullmatch(r"          ([A-Z_]+): \$\{\{ secrets.\1 \}\}", line)
    assert all(forbidden not in source for forbidden in ("schedule:", "restore_state", "commit_state", "git push"))
