"""Tests for dealradar.growth — the daily private growth report. Offline:
every API is an httpx.MockTransport, SMTP a fake factory, and the RS256 key
a throwaway one made by openssl."""

from __future__ import annotations

import base64
import json
import logging
import os
import smtplib
import stat
import subprocess
import tempfile
import time
from datetime import date, datetime, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from dealradar import cli
from dealradar.config import AppConfig, load_config
from dealradar.growth import bing, google, google_ads, meta, report, site_health, stripe_stats

NOW = datetime(2026, 10, 11, 12, 23, tzinfo=timezone.utc)  # 08:23 in New York
TODAY_START = int(datetime(2026, 10, 11, 4, 0, tzinfo=timezone.utc).timestamp())
DAY = 86400

GOOGLE_TOKEN = "ya29.FAKE-GOOGLE-ACCESS-TOKEN"
META_TOKEN = "EAAFAKEMETASYSTEMTOKEN"
STRIPE_KEY = "rk_live_FAKESTRIPEKEY"
BOT_TOKEN = "123456:FAKE-BOT-TOKEN"
ADMIN_CHAT = "999888"
GADS_ENV = {"GOOGLE_ADS_CLIENT_ID": "123-fake.apps.googleusercontent.com",
            "GOOGLE_ADS_CLIENT_SECRET": "GOCSPX-FAKESECRET",
            "GOOGLE_ADS_REFRESH_TOKEN": "1//FAKE-REFRESH-TOKEN"}
GADS_TOKEN = "ya29.FAKE-ADS-ACCESS-TOKEN"
CF_OPS_TOKEN = "FAKE-CLOUDFLARE-OPS-TOKEN"
BING_KEY = "FAKE-BING-WEBMASTER-KEY"

ENV_NAMES = (*GADS_ENV, "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
             "CLOUDFLARE_OPS_TOKEN", "BING_WEBMASTER_API_KEY",
             "GOOGLE_SA_JSON", "META_SYSTEM_TOKEN", "STRIPE_SECRET_KEY", "REPORT_EMAIL_TO",
             "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM",
             "TELEGRAM_BOT_TOKEN", "TELEGRAM_ADMIN_CHAT_ID", "TELEGRAM_CHANNEL_ID",
             "GITHUB_STEP_SUMMARY")

# Distinctive values, so a leak into the logs cannot be a coincidence.
GA4_RESPONSE = {"reports": [
    {"dimensionHeaders": [{"name": "dateRange"}], "rows": [
        {"dimensionValues": [{"value": "yesterday"}],
         "metricValues": [{"value": "812345"}, {"value": "731942"}, {"value": "640217"}]},
        {"dimensionValues": [{"value": "window"}],
         "metricValues": [{"value": "5120333"}, {"value": "4410777"}, {"value": "3900111"}]}]},
    {"dimensionHeaders": [{"name": "eventName"}, {"name": "dateRange"}], "rows": [
        {"dimensionValues": [{"value": "sign_up"}, {"value": "yesterday"}], "metricValues": [{"value": "61"}]},
        {"dimensionValues": [{"value": "sign_up"}, {"value": "window"}], "metricValues": [{"value": "402"}]},
        {"dimensionValues": [{"value": "start_trial"}, {"value": "window"}], "metricValues": [{"value": "77"}]},
        {"dimensionValues": [{"value": "deal_click"}, {"value": "yesterday"}], "metricValues": [{"value": "9001"}]},
        {"dimensionValues": [{"value": "support_click"}, {"value": "yesterday"}], "metricValues": [{"value": "12"}]},
        {"dimensionValues": [{"value": "support_click"}, {"value": "window"}], "metricValues": [{"value": "45"}]},
        *[{"dimensionValues": [{"value": event}, {"value": period}], "metricValues": [{"value": str(count)}]}
          for event, counts in (("home_load_attempt", (400, 2500)), ("first_load_failed", (12, 100)),
                                ("static_fallback_shown", (11, 94)), ("first_load_recovered", (5, 45)),
                                ("install_offer_shown", (8, 51)), ("install_accepted", (2, 9)),
                                ("standalone_session", (3, 17)))
          for period, count in zip(("yesterday", "window"), counts)]]},
    {"dimensionHeaders": [{"name": "sessionSourceMedium"}], "rows": [
        {"dimensionValues": [{"value": "google / organic"}], "metricValues": [{"value": "2500"}]},
        {"dimensionValues": [{"value": "facebook / paid"}], "metricValues": [{"value": "1200"}]}]},
    {"dimensionHeaders": [{"name": "landingPage"}], "rows": [
        {"dimensionValues": [{"value": "/secret-landing-page.html"}], "metricValues": [{"value": "3100"}]}]},
]}

STORES_RESPONSE = {"reports": [
    {"dimensionHeaders": [{"name": "customEvent:store"}], "rows": [
        {"dimensionValues": [{"value": "Amazon"}], "metricValues": [{"value": str(count)}]},
        {"dimensionValues": [{"value": "Target"}], "metricValues": [{"value": "1"}]}],
     "totals": [{"dimensionValues": [{"value": "RESERVED_TOTAL"}],
                 "metricValues": [{"value": str(total)}]}]}
    for count, total in ((8000, 9001), (18000, 20001))
]}

PRODUCT_RESPONSE = {"reports": [{
    "dimensionHeaders": [{"name": "eventName"}, {"name": "dateRange"}],
    "rows": [{"dimensionValues": [{"value": event}, {"value": key}],
              "metricValues": [{"value": str(count)}]}
             for event, key, count in [("deal_save", "yesterday", 23), ("deal_save", "window", 41),
                                       ("saved_view", "window", 28), ("saved_deal_click", "window", 17)]]}]
}
RETURNING_RESPONSE = {"reports": [{
    "dimensionHeaders": [{"name": "newVsReturning"}],
    "rows": [{"dimensionValues": [{"value": kind}], "metricValues": [{"value": str(count)}]}
             for kind, count in [("new", 80), ("returning", 20), ("(not set)", 7)]]}]
}

SC_TOTAL = {"rows": [{"clicks": 834521.0, "impressions": 9123456.0, "ctr": 0.0914, "position": 17.26}]}
SC_QUERIES = {"rows": [{"keys": ["private <b>query</b> phrase"], "clicks": 300.0, "impressions": 4000.0,
                        "ctr": 0.075, "position": 4.2}]}
SC_PAGES = {"rows": [{"keys": ["https://wowpriced.com/hacks.html"], "clicks": 120.0,
                      "impressions": 2000.0, "ctr": 0.06, "position": 6.0}]}
SITEMAP = {"path": "https://wowpriced.com/sitemap.xml", "lastDownloaded": "2026-10-10T07:12:00Z",
           "isPending": False, "warnings": "2", "errors": "1",
           "contents": [{"type": "web", "submitted": "7", "indexed": "0"}]}

CF_ROWS = [{"dimensions": {"date": f"2026-10-{day:02}"}, "uniq": {"uniques": visitors},
            "sum": {"pageViews": views, "requests": requests}}
           for day, visitors, views, requests in ((4, 700111, 1100444, 2100777),
                                                 (9, 800222, 1200555, 2200888),
                                                 (10, 900333, 1300666, 2300999))]
BING_ROWS = [{"Date": f"/Date({int(datetime(2026, 10, day, 7, tzinfo=timezone.utc).timestamp() * 1000)}-0700)/",
              "Clicks": clicks, "Impressions": impressions}
             for day, clicks, impressions in ((4, 512345, 823456), (9, 612345, 923456), (10, 712345, 1023456))]


GADS_ROWS = [
    {"segments": {"date": "2026-10-04"}, "metrics": {"costMicros": "2500000", "impressions": "400",
                                                     "clicks": "10", "conversions": 1.0}},
    {"segments": {"date": "2026-10-09"}, "metrics": {"costMicros": "1000000", "impressions": "100",
                                                     "clicks": "3"}},
    {"segments": {"date": "2026-10-10"}, "metrics": {"costMicros": "4560000", "impressions": "321",
                                                     "clicks": "12", "conversions": 2.0}},
]


def insights(spend: str, start_trial_key: str) -> dict:
    return {"data": [{"spend": spend, "impressions": "665544", "clicks": "33221", "ctr": "4.99",
                      "cpc": "0.37", "actions": [
                          {"action_type": "complete_registration", "value": "41"},
                          {"action_type": "offsite_conversion.fb_pixel_complete_registration", "value": "999"},
                          {"action_type": "offsite_conversion.fb_pixel_initiate_checkout", "value": "23"},
                          {"action_type": start_trial_key, "value": "11"}]}]}


def subscriptions() -> list[dict]:
    """Newest first, like Stripe."""
    y = TODAY_START - DAY
    return [
        {"id": "sub_1", "status": "trialing", "created": y + 100, "trial_start": y + 100, "trial_end": y + 7 * DAY},
        {"id": "sub_2", "status": "trialing", "created": y - 2 * DAY, "trial_start": y - 2 * DAY,
         "trial_end": y + 5 * DAY, "canceled_at": y + 50},  # cancel at period end, asked yesterday
        {"id": "sub_3", "status": "active", "created": y - 9 * DAY, "trial_start": y - 9 * DAY,
         "trial_end": y - 2 * DAY},  # converted inside the window
        {"id": "sub_4", "status": "canceled", "created": y - 20 * DAY, "trial_start": y - 20 * DAY,
         "trial_end": y - 13 * DAY, "canceled_at": y - 3 * DAY},
        {"id": "sub_5", "status": "active", "created": y - 60 * DAY, "trial_start": None, "trial_end": None},
        {"id": "sub_6", "status": "active", "created": y - 90 * DAY, "trial_start": y - 90 * DAY,
         "trial_end": y - 83 * DAY},
    ]


class FakeApis:
    """One handler for every host the report talks to."""

    def __init__(self, pub_path=None, *, fail: dict[str, int] | None = None):
        self.pub_path = pub_path
        self.fail = fail or {}       # host -> status code
        self.requests: list[httpx.Request] = []
        self.verified = 0
        self.telegram: list[dict] = []
        self.subs = subscriptions()

    def _verify(self, jwt: str) -> bool:
        signing_input, _, sig = jwt.rpartition(".")
        with tempfile.TemporaryDirectory() as d:
            data, sig_path = os.path.join(d, "in"), os.path.join(d, "sig")
            with open(data, "wb") as fh:
                fh.write(signing_input.encode())
            with open(sig_path, "wb") as fh:
                fh.write(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)))
            res = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(self.pub_path),
                                  "-signature", sig_path, data], capture_output=True)
        return res.returncode == 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host in self.fail:
            # A body that echoes secrets and numbers, to prove it is never logged.
            return httpx.Response(self.fail[host], text=f"denied {GOOGLE_TOKEN} {META_TOKEN} 7316642")
        if host == "wowpriced.com":
            if path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /\n")
            if path == "/deals.json":
                return httpx.Response(200, json={"deals": [], "meta": {
                    "published_at": NOW.isoformat(), "cadence_minutes": 15, "locked_count": 0}})
            if path == "/":
                return httpx.Response(200, text='<main id="content"></main>')
        auth = request.headers.get("authorization", "")
        if host == "oauth2.googleapis.com":
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["refresh_token"]:
                assert {k: v[0] for k, v in form.items() if k != "grant_type"} == {
                    "client_id": GADS_ENV["GOOGLE_ADS_CLIENT_ID"],
                    "client_secret": GADS_ENV["GOOGLE_ADS_CLIENT_SECRET"],
                    "refresh_token": GADS_ENV["GOOGLE_ADS_REFRESH_TOKEN"]}
                return httpx.Response(200, json={"access_token": GADS_TOKEN, "expires_in": 3599})
            assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
            jwt = form["assertion"][0]
            if self.pub_path is not None:
                if not self._verify(jwt):
                    return httpx.Response(400, json={"error": "invalid_grant"})
                self.verified += 1
            return httpx.Response(200, json={"access_token": GOOGLE_TOKEN, "expires_in": 3599,
                                             "token_type": "Bearer"})
        if host == "analyticsdata.googleapis.com":
            assert auth == f"Bearer {GOOGLE_TOKEN}"
            queries = json.loads(request.content)["requests"]
            dims = queries[0].get("dimensions")
            if dims == [{"name": "customEvent:store"}]:
                return httpx.Response(200, json=STORES_RESPONSE)
            if len(queries) == 1:
                if dims == [{"name": "eventName"}]:
                    return httpx.Response(200, json=PRODUCT_RESPONSE)
                if dims == [{"name": "newVsReturning"}]:
                    return httpx.Response(200, json=RETURNING_RESPONSE)
            return httpx.Response(200, json=GA4_RESPONSE)
        if host == "searchconsole.googleapis.com":
            assert auth == f"Bearer {GOOGLE_TOKEN}"
            if path.endswith("/searchAnalytics/query"):
                dims = json.loads(request.content)["dimensions"]
                return httpx.Response(200, json={(): SC_TOTAL, ("query",): SC_QUERIES,
                                                 ("page",): SC_PAGES}[tuple(dims)])
            if request.method == "PUT":
                return httpx.Response(204)
            return httpx.Response(200, json=SITEMAP)
        if host == "graph.facebook.com":
            assert auth == f"Bearer {META_TOKEN}"
            if path.endswith("/insights"):
                rng = json.loads(request.url.params["time_range"])
                if rng["since"] == rng["until"]:
                    return httpx.Response(200, json=insights("12.34", "offsite_conversion.fb_pixel_start_trial"))
                return httpx.Response(200, json=insights("86.10", "start_trial"))
            followers = {"5550001": 4321, "7770002": 8765}[path.rsplit("/", 1)[1]]
            return httpx.Response(200, json={"followers_count": followers, "id": "x"})
        if host == "api.stripe.com":
            assert auth == "Basic " + base64.b64encode(f"{STRIPE_KEY}:".encode()).decode()
            assert request.url.params["status"] == "all"
            after = request.url.params.get("starting_after")
            i = 0 if after is None else [s["id"] for s in self.subs].index(after) + 1
            page = self.subs[i:i + 4]
            return httpx.Response(200, json={"data": page, "has_more": i + 4 < len(self.subs)})
        if host == "googleads.googleapis.com":
            assert auth == f"Bearer {GADS_TOKEN}"
            if path == "/v26/customers:listAccessibleCustomers":
                return httpx.Response(200, json={"resourceNames": ["customers/5861019939",
                                                                   "customers/4976667972"]})
            assert path == "/v26/customers/4976667972/googleAds:search"
            assert request.headers["login-customer-id"] == "5861019939"
            assert "developer-token" not in request.headers
            body = json.loads(request.content)
            assert "BETWEEN '2026-10-04' AND '2026-10-10'" in body["query"]
            if "pageToken" not in body:  # two pages, to prove paging
                return httpx.Response(200, json={"results": GADS_ROWS[:2], "nextPageToken": "p2"})
            assert body["pageToken"] == "p2"
            return httpx.Response(200, json={"results": GADS_ROWS[2:]})
        if host == "api.cloudflare.com":
            assert auth == f"Bearer {CF_OPS_TOKEN}"
            if path == "/client/v4/zones":
                assert request.url.params["name"] == "wowpriced.com"
                return httpx.Response(200, json={"success": True, "result": [
                    {"id": "other-zone", "name": "other.example"},
                    {"id": "wowpriced-zone", "name": "wowpriced.com"}]})
            assert request.method == "POST" and path == "/client/v4/graphql"
            body = json.loads(request.content)
            assert body["variables"] == {"zone": "wowpriced-zone", "start": "2026-10-04",
                                         "end": "2026-10-10", "days": 7}
            assert all(field in body["query"] for field in
                       ("httpRequests1dGroups", "date_geq", "date_leq", "uniques", "pageViews", "requests"))
            return httpx.Response(200, json={"data": {"viewer": {"zones": [
                {"httpRequests1dGroups": CF_ROWS}]}}, "errors": None})
        if host == "ssl.bing.com":
            assert request.method == "GET" and path == "/webmaster/api.svc/json/GetRankAndTrafficStats"
            assert dict(request.url.params) == {"siteUrl": "https://wowpriced.com/", "apikey": BING_KEY}
            return httpx.Response(200, json={"d": BING_ROWS})
        if host == "api.telegram.org":
            self.telegram.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    def hosts(self) -> set[str]:
        return {r.url.host for r in self.requests}


class FakeSMTP:
    def __init__(self, host, port, timeout=None):
        self.sent: list = []

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        self.sent.append(msg)

    def quit(self):
        pass


class FakeSMTPFactory:
    def __init__(self):
        self.servers: list[FakeSMTP] = []

    def __call__(self, host, port, timeout=None):
        self.servers.append(FakeSMTP(host, port))
        return self.servers[-1]

    @property
    def sent(self) -> list:
        return [m for s in self.servers for m in s.sent]


@pytest.fixture(scope="module")
def rsa_key(tmp_path_factory):
    d = tmp_path_factory.mktemp("rsa")
    subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                    "-out", str(d / "key.pem")], check=True, capture_output=True)
    subprocess.run(["openssl", "pkey", "-in", str(d / "key.pem"), "-pubout", "-out", str(d / "pub.pem")],
                   check=True, capture_output=True)
    return (d / "key.pem").read_text(), d / "pub.pem"


def sa_json(private_key: str) -> str:
    return json.dumps({"type": "service_account", "client_email": "report@proj.iam.gserviceaccount.com",
                       "private_key_id": "kid123", "private_key": private_key,
                       "token_uri": "https://oauth2.googleapis.com/token"})


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.setattr(site_health.time, "sleep", lambda _: None)
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def cfg():
    return AppConfig(contact_email="hello@wowpriced.com", custom_domain="wowpriced.com",
                     timezone="America/New_York", growth_report_days=7,
                     growth_ga4_property_id="987654", growth_search_console_site="sc-domain:wowpriced.com",
                     growth_sitemap_url="https://wowpriced.com/sitemap.xml",
                     growth_meta_ad_account_id="1234", growth_meta_page_id="5550001",
                     growth_instagram_user_id="7770002")


def set_sources(monkeypatch, rsa_key, *, google_=True, meta_=True, stripe_=True):
    if google_:
        monkeypatch.setenv("GOOGLE_SA_JSON", sa_json(rsa_key[0]))
    if meta_:
        monkeypatch.setenv("META_SYSTEM_TOKEN", META_TOKEN)
    if stripe_:
        monkeypatch.setenv("STRIPE_SECRET_KEY", STRIPE_KEY)


def set_telegram(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", ADMIN_CHAT)
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@publicchannel")


def client_for(fake: FakeApis) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(fake))


# ------------------------------------------------------------------ Google auth


def test_service_account_key_with_bom_and_whitespace_parses():
    sa = {"client_email": "fake@example.com", "private_key": "fake-key"}
    assert google.load_service_account(" \n\ufeff " + json.dumps(sa) + "\n ") == sa


@pytest.mark.parametrize("text, starts, filename", [
    ("private-service-account.json", False, True),
    ('{"client_email": "private@example.com", "private_key": "truncated', True, False),
    ('{"client_email": "private@example.com"}', True, False),
    ('{"private_key": "private-key-marker"}', True, False),
    ('["private-key-marker"]', False, False),
])
def test_invalid_service_account_reports_only_shape(text, starts, filename):
    with pytest.raises(ValueError) as exc:
        google.load_service_account(text)
    message = str(exc.value)
    assert f"character count: {len(text)}" in message
    assert f"starts with {{: {starts}" in message
    assert f"ends with .json: {filename}" in message
    assert text not in message
    assert "private@example.com" not in message
    assert "private-key-marker" not in message
    assert exc.value.__suppress_context__ or exc.value.__context__ is None


def test_jwt_is_signed_and_accepted_by_the_token_endpoint(rsa_key, tmp_path, monkeypatch):
    tmp = tmp_path / "t"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    modes = []
    real_run = subprocess.run

    def spy(cmd, **kw):
        if "-sign" in cmd:  # the key file, while openssl reads it
            modes.append(stat.S_IMODE(os.stat(cmd[-1]).st_mode))
        return real_run(cmd, **kw)

    monkeypatch.setattr(google.subprocess, "run", spy)
    fake = FakeApis(rsa_key[1])
    with client_for(fake) as client:
        assert google.access_token(client, " \ufeff" + sa_json(rsa_key[0]) + "\n") == GOOGLE_TOKEN
    assert fake.verified == 1
    assert modes == [0o600]
    assert list(tmp.iterdir()) == []  # the key file is gone

    jwt = parse_qs(fake.requests[0].content.decode())["assertion"][0]
    header, claims, _ = (part + "=" * (-len(part) % 4) for part in jwt.split("."))
    header, claims = json.loads(base64.urlsafe_b64decode(header)), json.loads(base64.urlsafe_b64decode(claims))
    assert header == {"alg": "RS256", "typ": "JWT", "kid": "kid123"}
    assert claims["iss"] == "report@proj.iam.gserviceaccount.com"
    assert claims["aud"] == "https://oauth2.googleapis.com/token"
    assert claims["scope"].split() == ["https://www.googleapis.com/auth/analytics.readonly",
                                       "https://www.googleapis.com/auth/webmasters"]
    assert claims["exp"] - claims["iat"] == 3600 and abs(claims["iat"] - time.time()) < 60


def test_a_jwt_signed_with_another_key_is_refused(rsa_key, tmp_path):
    other = tmp_path / "other.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                    "-out", str(other)], check=True, capture_output=True)
    fake = FakeApis(rsa_key[1])
    with client_for(fake) as client, pytest.raises(httpx.HTTPStatusError):
        google.access_token(client, sa_json(other.read_text()))
    assert fake.verified == 0


# ------------------------------------------------------------------ parsing


def test_ga4_request_and_parsing():
    fake = FakeApis()
    with client_for(fake) as client:
        out = google.ga4(client, GOOGLE_TOKEN, "987654", 7)
    req = fake.requests[0]
    assert req.url.path == "/v1beta/properties/987654:batchRunReports"
    totals, events, sources, pages = json.loads(req.content)["requests"]
    assert totals["dateRanges"] == [
        {"startDate": "yesterday", "endDate": "yesterday", "name": "yesterday"},
        {"startDate": "7daysAgo", "endDate": "yesterday", "name": "window"}]
    assert [m["name"] for m in totals["metrics"]] == ["sessions", "totalUsers", "newUsers"]
    assert events["dimensionFilter"]["filter"]["inListFilter"]["values"] == [
        "sign_up", "begin_checkout", "start_trial", "deal_click", "support_click",
        "home_load_attempt", "first_load_failed", "static_fallback_shown", "first_load_recovered",
        "install_offer_shown", "install_accepted", "standalone_session"]
    assert sources["dimensions"] == [{"name": "sessionSourceMedium"}] and sources["limit"] == 5
    assert pages["dimensions"] == [{"name": "landingPage"}] and pages["limit"] == 5

    assert out["yesterday"] == {"sessions": 812345, "totalUsers": 731942, "newUsers": 640217,
                                "sign_up": 61, "begin_checkout": 0, "start_trial": 0, "deal_click": 9001,
                                "support_click": 12, "home_load_attempt": 400, "first_load_failed": 12,
                                "static_fallback_shown": 11, "first_load_recovered": 5,
                                "install_offer_shown": 8, "install_accepted": 2, "standalone_session": 3}
    assert out["window"]["totalUsers"] == 4410777
    assert out["window"]["sign_up"] == 402 and out["window"]["start_trial"] == 77
    assert out["window"]["support_click"] == 45
    assert out["window"]["home_load_attempt"] == 2500 and out["window"]["first_load_failed"] == 100
    assert out["window"]["static_fallback_shown"] == 94 and out["window"]["first_load_recovered"] == 45
    assert out["sources"] == [("google / organic", 2500), ("facebook / paid", 1200)]
    assert out["pages"] == [("/secret-landing-page.html", 3100)]


def test_saved_counts_and_seven_day_visitor_classification(cfg, caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakeApis()
    with client_for(fake) as client:
        data = google.ga4_saved(client, GOOGLE_TOKEN, "987654", 3)
    events, returning = (json.loads(req.content)["requests"][0] for req in fake.requests)
    assert events["dateRanges"][1] == {"startDate": "3daysAgo", "endDate": "yesterday", "name": "window"}
    assert events["dimensionFilter"]["filter"]["inListFilter"]["values"] == list(google.SAVED_EVENTS)
    assert returning["dimensions"] == [{"name": "newVsReturning"}]
    assert returning["metrics"] == [{"name": "sessions"}]
    assert returning["dateRanges"] == [{"startDate": "7daysAgo", "endDate": "yesterday"}]
    assert data["window"] == {"deal_save": 41, "saved_view": 28, "saved_deal_click": 17}
    assert data["yesterday"] == {"deal_save": 23, "saved_view": None, "saved_deal_click": None}
    assert data["returning"] == {"sessions": 20, "classified_sessions": 100, "unclassified_sessions": 7}
    cfg.growth_report_days = 3
    _, text = report.build_report(cfg, {"saved": data}, {}, date(2026, 10, 10))
    assert "== Product (GA4 event sample" in text
    assert "Feature actions (yesterday / last 3 days; event counts):" in text
    assert "deal_save: 23 / 41" in text
    assert "saved_view: unavailable (no observed event) / 28" in text
    assert "Observed returning sessions (last 7 completed days): 20 / 100 classified sessions (20.0%)" in text
    assert "7 unclassified sessions excluded" in text
    assert "save-to-purchase cohort or cross-device sync statistic" in text
    assert "41" not in caplog.text and GOOGLE_TOKEN not in text + caplog.text


@pytest.mark.parametrize("classification", [
    {}, {"dimensionHeaders": [{"name": "newVsReturning"}], "rows": []},
    {"dimensionHeaders": [{"name": "totalUsers"}], "rows": [
        {"dimensionValues": [{"value": "new"}], "metricValues": [{"value": "80"}]}]},
    {"dimensionHeaders": [{"name": "newVsReturning"}], "rows": [
        {"dimensionValues": [{"value": "(not set)"}], "metricValues": [{"value": "80"}]}]},
])
def test_missing_visitor_classification_is_unavailable_not_inferred(cfg, classification):
    fake = FakeApis()

    def handler(request):
        query = json.loads(request.content)["requests"][0]
        if query.get("dimensions") == [{"name": "newVsReturning"}]:
            return httpx.Response(200, json={"reports": [classification]})
        return fake(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        data = google.ga4_saved(client, GOOGLE_TOKEN, "987654", 7)
    assert data["window"]["deal_save"] == 41
    assert data["returning"] == {"unavailable": "visitor classification not returned"}
    _, text = report.build_report(cfg, {"saved": data}, {}, date(2026, 10, 10))
    assert "Observed returning sessions (last 7 completed days): unavailable" in text
    assert "0 classified sessions" not in text and "0.0%" not in text


@pytest.mark.parametrize("failure", ["dimension", "permission", "transport", "json"])
def test_visitor_classification_failure_preserves_saved_counts(cfg, monkeypatch, caplog, failure):
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("GOOGLE_SA_JSON", "configured")
    monkeypatch.setattr(google, "access_token", lambda *_: GOOGLE_TOKEN)
    fake = FakeApis()

    def handler(request):
        if request.url.host == "analyticsdata.googleapis.com":
            query = json.loads(request.content)["requests"][0]
            if query.get("dimensions") == [{"name": "newVsReturning"}]:
                if failure == "transport":
                    raise httpx.ConnectError(GOOGLE_TOKEN, request=request)
                if failure == "json":
                    return httpx.Response(200, text=GOOGLE_TOKEN)
                return httpx.Response(400 if failure == "dimension" else 403,
                                      json={"error": {"message": GOOGLE_TOKEN}})
        return fake(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        data, status = report.collect(cfg, NOW, client)
    assert status["saved"] == status["ga4"] == "ok"
    assert data["saved"]["window"]["saved_deal_click"] == 17
    _, text = report.build_report(cfg, data, status, date(2026, 10, 10))
    assert "visitor classification unavailable" in text
    assert GOOGLE_TOKEN not in text + caplog.text


def test_no_saved_rows_and_old_report_snapshots_are_unavailable(cfg):
    def handler(request):
        return httpx.Response(200, json={"reports": [{}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        data = google.ga4_saved(client, GOOGLE_TOKEN, "987654", 7)
    assert all(value is None for value in data["window"].values())
    _, text = report.build_report(cfg, {"saved": data}, {}, date(2026, 10, 10))
    assert "deal_save: unavailable (no observed event) / unavailable (no observed event)" in text
    with client_for(FakeApis()) as client:
        old_ga = google.ga4(client, GOOGLE_TOKEN, "987654", 7)
    for status in ({}, {"saved": "skipped"}, {"saved": "error"}):
        _, text = report.build_report(cfg, {"ga4": old_ga}, status, date(2026, 10, 10))
        assert "Feature actions and returning sessions: unavailable" in text


def test_new_only_classification_is_an_observed_zero_with_a_denominator(cfg):
    fake = FakeApis()

    def handler(request):
        query = json.loads(request.content)["requests"][0]
        if query.get("dimensions") == [{"name": "newVsReturning"}]:
            return httpx.Response(200, json={"reports": [{"dimensionHeaders": [{"name": "newVsReturning"}],
                "rows": [{"dimensionValues": [{"value": "new"}], "metricValues": [{"value": "10"}]}]}]})
        return fake(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        data = google.ga4_saved(client, GOOGLE_TOKEN, "987654", 7)
    _, text = report.build_report(cfg, {"saved": data}, {}, date(2026, 10, 10))
    assert "0 / 10 classified sessions (0.0%)" in text


def test_search_console_query_and_sitemap_submit():
    fake = FakeApis()
    with client_for(fake) as client:
        sc = google.search_console(client, GOOGLE_TOKEN, "sc-domain:wowpriced.com",
                                   date(2026, 10, 2), date(2026, 10, 8))
        sm = google.submit_sitemap(client, GOOGLE_TOKEN, "sc-domain:wowpriced.com",
                                   "https://wowpriced.com/sitemap.xml")
    queries = [r for r in fake.requests if r.url.path.endswith("/searchAnalytics/query")]
    assert queries[0].url.raw_path == b"/webmasters/v3/sites/sc-domain%3Awowpriced.com/searchAnalytics/query"
    body = json.loads(queries[0].content)
    assert (body["startDate"], body["endDate"]) == ("2026-10-02", "2026-10-08")
    assert sc["clicks"] == 834521 and sc["impressions"] == 9123456
    assert sc["ctr"] == pytest.approx(0.0914) and sc["position"] == pytest.approx(17.26)
    assert sc["queries"] == [("private <b>query</b> phrase", 300, 4000)]
    assert sc["pages"] == [("https://wowpriced.com/hacks.html", 120, 2000)]

    put, get = fake.requests[-2:]
    assert (put.method, get.method) == ("PUT", "GET")
    assert put.url.raw_path == (b"/webmasters/v3/sites/sc-domain%3Awowpriced.com/sitemaps/"
                                b"https%3A%2F%2Fwowpriced.com%2Fsitemap.xml")
    assert sm == {"url": "https://wowpriced.com/sitemap.xml", "errors": 1, "warnings": 2,
                  "last_downloaded": "2026-10-10T07:12:00Z", "pending": False, "submitted": 7}


def test_meta_insights_and_followers():
    fake = FakeApis()
    with client_for(fake) as client:
        ads = meta.ad_insights(client, META_TOKEN, "v24.0", "1234", date(2026, 10, 10), date(2026, 10, 4))
        page = meta.followers(client, META_TOKEN, "v24.0", "5550001")
        ig = meta.followers(client, META_TOKEN, "v24.0", "7770002")
    req = fake.requests[0]
    assert req.url.path == "/v24.0/act_1234/insights"
    assert json.loads(req.url.params["time_range"]) == {"since": "2026-10-10", "until": "2026-10-10"}
    assert json.loads(fake.requests[1].url.params["time_range"]) == {"since": "2026-10-04", "until": "2026-10-10"}
    assert all(META_TOKEN not in str(r.url) for r in fake.requests)  # header only
    # The aggregated type wins over the pixel's own; the pixel's is the fallback.
    assert ads["yesterday"] == {"spend": 12.34, "impressions": 665544, "clicks": 33221, "ctr": 4.99,
                                "cpc": 0.37, "complete_registration": 41, "initiate_checkout": 23,
                                "start_trial": 11}
    assert ads["window"]["spend"] == 86.10 and ads["window"]["start_trial"] == 11
    assert (page, ig) == (4321, 8765)


def test_meta_no_delivery_is_zero():
    def handler(request):
        return httpx.Response(200, json={"data": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ads = meta.ad_insights(client, META_TOKEN, "v24.0", "1234", date(2026, 10, 10), date(2026, 10, 4))
    assert ads["yesterday"]["spend"] == 0.0 and ads["window"]["start_trial"] == 0


def test_stripe_counts_across_pages():
    fake = FakeApis()
    with client_for(fake) as client:
        st = stripe_stats.stats(client, STRIPE_KEY, yesterday_start=TODAY_START - DAY,
                                window_start=TODAY_START - 7 * DAY, today_start=TODAY_START,
                                monthly_price=9.99)
    assert len(fake.requests) == 2  # paged with starting_after
    assert fake.requests[1].url.params["starting_after"] == "sub_4"
    assert st == {"test_mode": False, "trials_yesterday": 1, "trials_window": 2, "trialing": 2,
                  "active": 3, "converted": 1, "canceled": 2, "mrr": 29.97}


def test_stripe_mrr_mixed_plans_and_missing_prices():
    def subscription(status, price=None):
        return {"status": status, "items": {"data": [{"price": price}]}}

    subs = [
        subscription("active", {"recurring": {"interval": "year"}, "unit_amount": 4900}),
        subscription("active", {"recurring": {"interval": "month"}, "unit_amount": 999}),
        {"status": "active"},
        subscription("active", {"recurring": {"interval": "year"}, "unit_amount": None}),
        subscription("trialing", {"recurring": {"interval": "year"}, "unit_amount": 4900}),
        subscription("canceled", {"recurring": {"interval": "year"}, "unit_amount": 4900}),
    ]
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": subs}))) as client:
        st = stripe_stats.stats(client, STRIPE_KEY, yesterday_start=TODAY_START - DAY,
                                window_start=TODAY_START - 7 * DAY, today_start=TODAY_START,
                                monthly_price=9.99)
    assert st["active"] == 4
    assert st["mrr"] == round(49 / 12 + 3 * 9.99, 2)


# ------------------------------------------------------------------ sections


@pytest.mark.parametrize("google_, meta_, stripe_, ok", [
    (False, False, True, {"stripe"}),
    (True, False, False, {"ga4", "ga4_stores", "search_console", "sitemap"}),
    (False, True, False, {"meta_ads", "facebook", "instagram"}),
    (True, True, True, {"ga4", "ga4_stores", "search_console", "sitemap", "meta_ads", "facebook", "instagram", "stripe"}),
])
def test_each_missing_secret_skips_only_its_sections(cfg, rsa_key, monkeypatch, caplog,
                                                     google_, meta_, stripe_, ok):
    caplog.set_level(logging.INFO)
    set_sources(monkeypatch, rsa_key, google_=google_, meta_=meta_, stripe_=stripe_)
    fake = FakeApis()
    with client_for(fake) as client:
        data, status = report.collect(cfg, NOW, client)
    ok = ok | {"site_health"} | ({"product", "saved"} if google_ else set())
    assert {k for k, v in status.items() if v == "ok"} == ok
    assert {k for k, v in status.items() if v == "skipped"} == set(status) - ok
    assert set(data) == ok | {"subscribers"}
    if not google_:
        assert "ga4: skipped (GOOGLE_SA_JSON not set)" in caplog.messages
        assert not {"oauth2.googleapis.com", "analyticsdata.googleapis.com"} & fake.hosts()
    if not meta_:
        assert "meta_ads: skipped (META_SYSTEM_TOKEN not set)" in caplog.messages
        assert "graph.facebook.com" not in fake.hosts()
    if not stripe_:
        assert "stripe: skipped (STRIPE_SECRET_KEY not set)" in caplog.messages
        assert "api.stripe.com" not in fake.hosts()
    if google_:  # one token for every Google section
        assert sum(r.url.host == "oauth2.googleapis.com" for r in fake.requests) == 1


def test_empty_config_values_skip_their_section(cfg, rsa_key, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    set_sources(monkeypatch, rsa_key)
    cfg.growth_ga4_property_id = ""
    cfg.growth_instagram_user_id = ""
    with client_for(FakeApis()) as client:
        _, status = report.collect(cfg, NOW, client)
    assert status["ga4"] == status["product"] == status["instagram"] == "skipped"
    assert status["search_console"] == status["facebook"] == "ok"
    assert "ga4: skipped (growth.ga4_property_id not set)" in caplog.messages


def test_a_failing_section_does_not_stop_the_others(cfg, rsa_key, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    set_sources(monkeypatch, rsa_key)
    fake = FakeApis(fail={"searchconsole.googleapis.com": 403})
    with client_for(fake) as client:
        _, status = report.collect(cfg, NOW, client)
    assert status["search_console"] == status["sitemap"] == "error"
    assert status["ga4"] == status["stripe"] == status["meta_ads"] == "ok"
    assert "search_console: error HTTPStatusError 403" in caplog.messages
    assert GOOGLE_TOKEN not in caplog.text and "denied" not in caplog.text


def test_invalid_service_account_logs_shape_without_content(cfg, monkeypatch, caplog):
    text = "private-service-account.json"
    monkeypatch.setenv("GOOGLE_SA_JSON", text)
    fake = FakeApis()
    with client_for(fake) as client:
        _, status = report.collect(cfg, NOW, client)
    for name in ("ga4", "search_console", "sitemap"):
        assert status[name] == "error"
        assert any(f"{name}: error GOOGLE_SA_JSON" in m for m in caplog.messages)
    assert "character count: 28" in caplog.text
    assert "ends with .json: True" in caplog.text
    assert text not in caplog.text
    assert fake.hosts() == {"wowpriced.com"}


def test_other_value_error_messages_remain_private(cfg, monkeypatch, caplog):
    monkeypatch.setenv("GOOGLE_SA_JSON", "configured")

    def fail(*args):
        raise ValueError("private-response-marker")

    monkeypatch.setattr(google, "access_token", fail)
    with client_for(FakeApis()) as client:
        _, status = report.collect(cfg, NOW, client)
    assert status["ga4"] == "error"
    assert "ga4: error ValueError" in caplog.messages
    assert "private-response-marker" not in caplog.text


def test_config_loader_reads_the_growth_block(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("publish:\n  custom_domain: wowpriced.com\n"
                    "growth:\n  ga4_property_id: properties/987654\n  meta_ad_account_id: act_1234\n"
                    "  report_days: 14\n")
    c = load_config(path)
    assert c.growth_ga4_property_id == "987654" and c.growth_meta_ad_account_id == "1234"
    assert c.growth_report_days == 14
    assert c.growth_sitemap_url == "https://wowpriced.com/sitemap.xml"
    assert c.growth_meta_graph_version == "v24.0" and c.growth_search_console_site == ""
    shipped = load_config()
    assert shipped.growth_search_console_site == "sc-domain:wowpriced.com"
    assert shipped.growth_sitemap_url == "https://wowpriced.com/sitemap.xml"


# ------------------------------------------------------------------ report + delivery


def full_run(cfg, rsa_key, monkeypatch, **fake_kw):
    set_sources(monkeypatch, rsa_key)
    fake = FakeApis(**fake_kw)
    smtp = FakeSMTPFactory()
    with client_for(fake) as client:
        code = report.run(cfg, NOW, http_client=client, smtp_factory=smtp)
    return code, fake, smtp


def test_report_headline_first_then_sections(cfg, rsa_key, monkeypatch):
    set_telegram(monkeypatch)
    code, fake, _ = full_run(cfg, rsa_key, monkeypatch)
    assert code == 0
    (msg,) = fake.telegram
    assert msg["chat_id"] == ADMIN_CHAT  # the admin chat, never the channel
    lines = msg["text"].splitlines()
    assert lines[0] == "Growth report for Sat Oct 10, 2026: yesterday / last 7 days"
    assert lines[2:10] == [
        "Sessions: 812,345 / 5,120,333",
        "Clicks out yesterday: 9,001",
        "Visitors: 731,942 / 4,410,777",
        "Sign-ups: 61 / 402",
        "Trials started: 1 / 2",            # Stripe, the source of truth
        "Paying members: 3 (MRR estimate $29.97)",
        "Ad spend: $12.34 / $86.10",
        "Cost per trial: $12.34 / $43.05",
    ]
    text = msg["text"]
    for header in ("== Google Analytics ==", "== Search Console, Oct 02 to Oct 08 (its data lags 2-3 days) ==",
                   "== Sitemap ==", "== Meta ==", "== Stripe =="):
        assert header in text
    assert "  google / organic: 2,500" in text
    assert "Clicks 834,521, impressions 9,123,456, CTR 9.1%, average position 17.3" in text
    assert "7 URLs, 1 errors, 2 warnings, last read 2026-10-10T07:12:00Z" in text
    assert "Facebook Page followers: 4,321" in text and "Instagram followers: 8,765" in text
    assert "Cancellations (7 days): 2" in text
    assert text.rstrip().endswith("stripe ok, product ok")


def test_product_counts_share_the_fixed_ga4_query_and_stay_private(cfg, rsa_key, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    set_sources(monkeypatch, rsa_key, meta_=False, stripe_=False)
    fake = FakeApis()
    with client_for(fake) as client:
        data, status = report.collect(cfg, NOW, client)
    assert status["product"] == "ok"
    assert data["product"] == {
        "yesterday": {"home_load_attempts": 400, "first_load_failures": 12,
                      "static_fallback_shown": 11, "first_load_recoveries": 5,
                      "install_offer_shown": 8, "install_accepted": 2, "standalone_session": 3},
        "window": {"home_load_attempts": 2500, "first_load_failures": 100,
                   "static_fallback_shown": 94, "first_load_recoveries": 45,
                   "install_offer_shown": 51, "install_accepted": 9, "standalone_session": 17}}
    # Main totals/events/sources/pages + store query; load counts add no request.
    # The two single queries are the saved-deal events and new/returning (task 066).
    queries = [json.loads(r.content) for r in fake.requests if r.url.host == "analyticsdata.googleapis.com"]
    assert [len(q["requests"]) for q in queries] == [4, 2, 1, 1]
    _, text = report.build_report(cfg, data, status, date(2026, 10, 10))
    for line in ("product.home_load_attempts: 400 / 2,500", "product.first_load_failures: 12 / 100",
                 "product.static_fallback_shown: 11 / 94", "product.first_load_recoveries: 5 / 45",
                 "product.install_offer_shown: 8 / 51", "product.install_accepted: 2 / 9",
                 "product.standalone_session: 3 / 17"):
        assert line in text and line not in caplog.text
    assert "Install offers and acceptances are actions" in text and "not retained users" in text
    assert "product: ok" in caplog.messages


@pytest.mark.parametrize("reason", ["credentials", "property", "api"])
def test_product_counts_are_unavailable_when_ga4_cannot_be_read(cfg, monkeypatch, caplog, reason):
    fake = FakeApis()
    if reason != "credentials":
        monkeypatch.setenv("GOOGLE_SA_JSON", "configured")
    if reason == "property":
        cfg.growth_ga4_property_id = ""
    if reason == "api":
        monkeypatch.setattr(google, "access_token", lambda *a: GOOGLE_TOKEN)

        def fail(*args):
            raise ValueError("private failure https://private.example/item")

        monkeypatch.setattr(google, "ga4", fail)
    with client_for(fake) as client:
        data, status = report.collect(cfg, NOW, client)
    expected = "error" if reason == "api" else "skipped"
    assert status["product"] == expected and "product" not in data
    _, text = report.build_report(cfg, data, status, date(2026, 10, 10))
    assert f"product: not available ({expected})" in text and "product.home_load_attempts:" not in text
    assert "product.install_offer_shown:" not in text and "product.install_accepted:" not in text
    assert "product.standalone_session:" not in text
    assert "private failure" not in text + caplog.text and "private.example" not in text + caplog.text


def test_absent_ga4_event_rows_are_successful_zero_counts(cfg, monkeypatch):
    monkeypatch.setenv("GOOGLE_SA_JSON", "configured")
    monkeypatch.setattr(google, "access_token", lambda *a: GOOGLE_TOKEN)
    empty = {"reports": [{}, {}, {}, {}]}
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=empty))) as client:
        data, status = report.collect(cfg, NOW, client)
    assert status["ga4"] == status["product"] == "ok"
    assert all(count == 0 for period in data["product"].values() for count in period.values())
    _, text = report.build_report(cfg, data, status, date(2026, 10, 10))
    assert "product.home_load_attempts: 0 / 0" in text
    for event in ("install_offer_shown", "install_accepted", "standalone_session"):
        assert f"product.{event}: 0 / 0" in text


def test_trials_fall_back_to_ga4_and_cost_per_trial_handles_zero(cfg, rsa_key, monkeypatch):
    set_sources(monkeypatch, rsa_key, stripe_=False)
    with client_for(FakeApis()) as client:
        data, status = report.collect(cfg, NOW, client)
    _, text = report.build_report(cfg, data, status, date(2026, 10, 10))
    assert "Trials started: 0 / 77" in text
    assert "Cost per trial: n/a / $1.12" in text
    assert "Paying members" not in text


def test_email_goes_to_report_email_to_with_escaped_html(cfg, rsa_key, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("REPORT_EMAIL_TO", "owner@example.com")
    code, fake, smtp = full_run(cfg, rsa_key, monkeypatch)
    assert code == 0 and fake.telegram == []
    (msg,) = smtp.sent
    assert msg["To"] == "owner@example.com"
    assert msg["Subject"] == "wowpriced.com growth report 2026-10-10"
    plain, html = (p.get_payload(decode=True).decode() for p in msg.get_payload())
    assert "private <b>query</b> phrase" in plain
    assert "private &lt;b&gt;query&lt;/b&gt; phrase" in html and "<b>query" not in html


def test_email_falls_back_to_the_contact_email(cfg, rsa_key, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    _, _, smtp = full_run(cfg, rsa_key, monkeypatch)
    assert [m["To"] for m in smtp.sent] == ["hello@wowpriced.com"]


def test_both_channels(cfg, rsa_key, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    set_telegram(monkeypatch)
    code, fake, smtp = full_run(cfg, rsa_key, monkeypatch)
    assert code == 0 and len(smtp.sent) == 1 and len(fake.telegram) == 1


def test_no_channel_sends_nothing(cfg, rsa_key, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    code, fake, smtp = full_run(cfg, rsa_key, monkeypatch)
    assert code == 0
    assert smtp.servers == [] and fake.telegram == []
    assert "api.telegram.org" not in fake.hosts()
    assert "no private channel configured" in caplog.messages


def test_nothing_configured_sends_nothing(cfg, monkeypatch, caplog):
    cfg.custom_domain = ""
    caplog.set_level(logging.INFO)
    set_telegram(monkeypatch)
    fake = FakeApis()
    with client_for(fake) as client:
        assert report.run(cfg, NOW, http_client=client) == 0
    assert fake.requests == []


def test_exit_code_only_fails_when_every_configured_section_failed(cfg, rsa_key, monkeypatch):
    set_telegram(monkeypatch)
    monkeypatch.setenv("STRIPE_SECRET_KEY", STRIPE_KEY)
    fake = FakeApis(fail={"api.stripe.com": 500, "wowpriced.com": 500})
    with client_for(fake) as client:
        assert report.run(cfg, NOW, http_client=client) == 1
    assert fake.telegram[0]["text"].rstrip().endswith("stripe error, product skipped")  # still reported

    code, _, _ = full_run(cfg, rsa_key, monkeypatch, fail={"api.stripe.com": 500})
    assert code == 0  # the other sections worked


def test_a_failed_delivery_is_logged_but_exits_0(cfg, rsa_key, monkeypatch, caplog):
    """Per the brief, only sections set the exit code; delivery logs its error."""
    caplog.set_level(logging.INFO)
    set_telegram(monkeypatch)
    code, _, _ = full_run(cfg, rsa_key, monkeypatch, fail={"api.telegram.org": 403})
    assert code == 0
    assert "telegram: error HTTP 403" in caplog.messages


def test_telegram_text_stays_under_the_limit(cfg, rsa_key, monkeypatch):
    set_telegram(monkeypatch)
    monkeypatch.setattr(report, "build_report", lambda *a: ("s", "x" * 9000))
    _, fake, _ = full_run(cfg, rsa_key, monkeypatch)
    assert len(fake.telegram[0]["text"]) == report.TELEGRAM_MAX


# ------------------------------------------------------------------ public logs


def test_cli_never_prints_metrics_report_or_tokens(rsa_key, tmp_path, monkeypatch, capsys, caplog):
    """The real entry point with every source and both channels on and one
    section failing: stdout, stderr, the log records and the step summary
    carry section status only."""
    caplog.set_level(logging.DEBUG)
    set_sources(monkeypatch, rsa_key)
    set_traffic(monkeypatch)
    set_telegram(monkeypatch)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "2525")
    monkeypatch.setenv("SMTP_PASSWORD", "smtp-FAKE-password")
    monkeypatch.setenv("REPORT_EMAIL_TO", "owner@example.com")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    config = tmp_path / "config.yaml"
    config.write_text("contact_email: hello@wowpriced.com\npublish:\n  custom_domain: wowpriced.com\n"
                      "growth:\n  ga4_property_id: '987654'\n  search_console_site: sc-domain:wowpriced.com\n"
                      "  meta_ad_account_id: '1234'\n  meta_page_id: '5550001'\n"
                      "  instagram_user_id: '7770002'\n")
    fake = FakeApis(rsa_key[1], fail={"graph.facebook.com": 400})
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(fake)))
    smtp = FakeSMTPFactory()
    monkeypatch.setattr(smtplib, "SMTP", smtp)

    assert cli.main(["growth-report", "--config", str(config)]) == 0
    out, err = capsys.readouterr()
    logged = caplog.text + "\n".join(r.getMessage() for r in caplog.records)
    public = out + err + logged

    (msg,) = smtp.sent
    text = fake.telegram[0]["text"]
    assert text == msg.get_payload()[0].get_payload(decode=True).decode()
    for line in text.splitlines():
        if line.strip():
            assert line.strip() not in public, line
    for value in ("731942", "731,942", "4,410,777", "834,521", "9,123,456", "4,321", "8,765",
                  "$29.97", "7316642", "private <b>query", "secret-landing-page", "google / organic"):
        assert value not in public, value
    for secret in (GOOGLE_TOKEN, META_TOKEN, STRIPE_KEY, BOT_TOKEN, "smtp-FAKE-password",
                   "owner@example.com", "BEGIN PRIVATE KEY", rsa_key[0].splitlines()[1][:40], CF_OPS_TOKEN, BING_KEY):
        assert secret not in public, secret
    assert not summary.exists()
    # ...and the operator still learns which section failed, by class and status only.
    assert "meta_ads: error HTTPStatusError 400" in caplog.messages
    assert "ga4: ok" in caplog.messages and "telegram: sent" in caplog.messages
    assert fake.verified == 1


# ------------------------------------------------------------------ Google Ads


def gads_cfg(cfg):
    cfg.growth_google_ads_customer_id = "4976667972"
    cfg.growth_google_ads_login_customer_id = "5861019939"
    return cfg


def test_google_ads_totals_yesterday_and_window(cfg, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    for k, v in GADS_ENV.items():
        monkeypatch.setenv(k, v)
    with client_for(FakeApis()) as client:
        data, status = report.collect(gads_cfg(cfg), NOW, client)
    assert status["google_ads"] == "ok"
    assert data["google_ads"] == {
        "yesterday": {"spend": 4.56, "impressions": 321, "clicks": 12, "conversions": 2.0},
        "window": {"spend": 8.06, "impressions": 821, "clicks": 25, "conversions": 3.0}}
    _, text = report.build_report(cfg, data, status, date(2026, 10, 10))
    assert "Ad spend: $4.56 / $8.06" in text  # no Meta: Google alone
    assert "== Google Ads ==\nSpend $4.56 / $8.06, impressions 321 / 821, clicks 12 / 25, " \
           "conversions 2 / 3" in text
    assert not any(v in caplog.text for v in (*GADS_ENV.values(), GADS_TOKEN))


def test_google_ads_skipped_without_its_secrets_or_account(cfg, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    with client_for(FakeApis()) as client:
        _, status = report.collect(gads_cfg(cfg), NOW, client)
    assert status["google_ads"] == "skipped"
    assert "google_ads: skipped (GOOGLE_ADS_CLIENT_ID not set)" in caplog.messages
    for k, v in GADS_ENV.items():
        monkeypatch.setenv(k, v)
    cfg.growth_google_ads_customer_id = ""
    with client_for(FakeApis()) as client:
        _, status = report.collect(cfg, NOW, client)
    assert status["google_ads"] == "skipped"


@pytest.mark.parametrize("host, code, body, logged", [
    ("oauth2.googleapis.com", 400,
     {"error": "invalid_grant", "error_description": "Bad 1//FAKE-REFRESH-TOKEN"},
     "google_ads: error HTTP 400 invalid_grant"),
    ("googleads.googleapis.com", 403,
     {"error": {"code": 403, "message": "secret 7316642", "status": "PERMISSION_DENIED",
                "details": [{"errors": [{"errorCode": {"authorizationError": "USER_PERMISSION_DENIED"},
                                         "message": "customer 4976667972 <b>x</b>"}]}]}},
     'google_ads: error HTTP 403 PERMISSION_DENIED USER_PERMISSION_DENIED "secret" '
     "(sign-in reaches 2 account(s); ad account listed: yes; manager listed: yes; "
     "reachable: ...7972; ...9939)"),
    ("googleads.googleapis.com", 502, None, "google_ads: error HTTP 502 (sign-in reaches 2 "
     "account(s); ad account listed: yes; manager listed: yes; reachable: ...7972; ...9939)"),
])
def test_google_ads_errors_log_only_googles_codes(cfg, monkeypatch, caplog, host, code, body, logged):
    for k, v in GADS_ENV.items():
        monkeypatch.setenv(k, v)

    def handler(request):
        if request.url.host == host and not request.url.path.endswith("listAccessibleCustomers"):
            return (httpx.Response(code, json=body) if body is not None
                    else httpx.Response(code, text=f"oops {GADS_TOKEN}"))
        return FakeApis()(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        _, status = report.collect(gads_cfg(cfg), NOW, client)
    assert status["google_ads"] == "error"
    assert logged in caplog.messages
    for leak in (*GADS_ENV.values(), GADS_TOKEN, "7316642", "4976667972", "<b>"):
        assert leak not in caplog.text


def test_config_loader_reads_google_ads_ids(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("growth:\n  google_ads_customer_id: 497-666-7972\n"
                    "  google_ads_login_customer_id: '586-101-9939'\n")
    c = load_config(path)
    assert (c.growth_google_ads_customer_id, c.growth_google_ads_login_customer_id,
            c.growth_google_ads_api_version) == ("4976667972", "5861019939", "v26")
    shipped = load_config()
    assert shipped.growth_google_ads_customer_id == "4412275803"
    assert shipped.growth_google_ads_login_customer_id == ""


def test_google_ads_steps_down_from_a_version_google_does_not_serve(cfg, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    for k, v in GADS_ENV.items():
        monkeypatch.setenv(k, v)
    seen = []

    def handler(request):
        if request.url.host == "googleads.googleapis.com":
            seen.append(request.url.path.split("/")[1])
            if request.url.path.startswith(("/v26/", "/v25/")):
                return httpx.Response(404, json={"error": {"code": 404, "status": "NOT_FOUND",
                                                           "message": "Method not found."}})
            request = httpx.Request(request.method, str(request.url).replace("/v24/", "/v26/"),
                                    headers=request.headers, content=request.content)
        return FakeApis()(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        _, status = report.collect(gads_cfg(cfg), NOW, client)
    assert status["google_ads"] == "ok"
    assert seen[:3] == ["v26", "v25", "v24"] and set(seen[3:]) == {"v24"}
    assert "google_ads: API v24" in caplog.messages


def test_google_ads_error_names_the_managers_accounts_by_status(cfg, monkeypatch, caplog):
    for k, v in GADS_ENV.items():
        monkeypatch.setenv(k, v)

    def handler(request):
        path = request.url.path
        if path == "/v26/customers/4976667972/googleAds:search":
            return httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED", "details": [
                {"errors": [{"errorCode": {"authorizationError": "CUSTOMER_NOT_ENABLED"}}]}]}})
        if path == "/v26/customers/5861019939/googleAds:search":
            assert "customer_client" in json.loads(request.content)["query"]
            return httpx.Response(200, json={"results": [
                {"customerClient": {"id": "5861019939", "status": "ENABLED", "manager": True}},
                {"customerClient": {"id": "4976667972", "status": "SETUP_IN_PROGRESS"}},
                {"customerClient": {"id": "1112223333", "status": "ENABLED"}}]})
        return FakeApis()(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        _, status = report.collect(gads_cfg(cfg), NOW, client)
    assert status["google_ads"] == "error"
    assert any(m.endswith("reachable: ...7972; ...9939 holds: ...7972 SETUP_IN_PROGRESS, "
                          "...3333 ENABLED)") for m in caplog.messages), caplog.messages


# ------------------------------------------------------------------ Cloudflare + Bing traffic


def set_traffic(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_OPS_TOKEN", CF_OPS_TOKEN)
    monkeypatch.setenv("BING_WEBMASTER_API_KEY", BING_KEY)


def test_traffic_totals_are_private_and_other_sections_still_run(cfg, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    set_traffic(monkeypatch)
    fake = FakeApis()
    with client_for(fake) as client:
        data, status = report.collect(cfg, NOW, client)
    assert status["cloudflare_traffic"] == status["bing"] == status["site_health"] == "ok"
    assert data["cloudflare_traffic"] == {
        "yesterday": {"visitors": 900333, "page_views": 1300666, "requests": 2300999},
        "window": {"visitors": 2400666, "page_views": 3601665, "requests": 6602664}}
    assert data["bing"] == {"yesterday": {"clicks": 712345, "impressions": 1023456},
                            "window": {"clicks": 1837035, "impressions": 2770368}, "has_data": True}
    _, text = report.build_report(cfg, data, status, date(2026, 10, 10))
    assert "Visitors (Cloudflare): yesterday 900,333, last 7 days 2,400,666; page views 3,601,665" in text
    assert "Cloudflare page views (yesterday / window): 1,300,666 / 3,601,665; requests 2,300,999 / 6,602,664" in text
    assert "Clicks (yesterday / window): 712,345 / 1,837,035; impressions 1,023,456 / 2,770,368" in text
    for secret in (CF_OPS_TOKEN, BING_KEY):
        assert secret not in caplog.text and secret not in text
    for section in ("cloudflare_traffic", "bing"):
        assert f"{section}: ok" in caplog.messages
        for counts in (data[section]["yesterday"], data[section]["window"]):
            for value in counts.values():
                assert str(value) not in caplog.text and f"{value:,}" not in caplog.text


@pytest.mark.parametrize("missing, section, host", [
    ("CLOUDFLARE_OPS_TOKEN", "cloudflare_traffic", "api.cloudflare.com"),
    ("BING_WEBMASTER_API_KEY", "bing", "ssl.bing.com"),
])
def test_traffic_missing_secret_skips_only_its_section(cfg, monkeypatch, caplog, missing, section, host):
    caplog.set_level(logging.INFO)
    set_traffic(monkeypatch)
    monkeypatch.delenv(missing)
    fake = FakeApis()
    with client_for(fake) as client:
        data, status = report.collect(cfg, NOW, client)
    assert status[section] == "skipped" and section not in data
    assert f"{section}: skipped" in caplog.messages and host not in fake.hosts()
    assert status["bing" if section == "cloudflare_traffic" else "cloudflare_traffic"] == "ok"


@pytest.mark.parametrize("host, section, line", [
    ("api.cloudflare.com", "cloudflare_traffic", "cloudflare_traffic: error"),
    ("ssl.bing.com", "bing", "bing: error HTTP 500"),
])
def test_traffic_http_errors_never_log_keys_urls_bodies_or_metrics(cfg, monkeypatch, caplog, host, section, line):
    caplog.set_level(logging.DEBUG)
    set_traffic(monkeypatch)
    fake = FakeApis(fail={host: 500})
    with client_for(fake) as client:
        data, status = report.collect(cfg, NOW, client)
    assert status[section] == "error" and section not in data
    assert status["site_health"] == "ok"
    assert line in caplog.messages
    for private in (BING_KEY, CF_OPS_TOKEN, "7316642", "apikey=", "denied"):
        assert private not in caplog.text


@pytest.mark.parametrize("body", [{"d": []}, {"d": None}, {"d": [
    {"Date": "2026-10-03T00:00:00Z", "Clicks": 1111, "Impressions": 9999}]}])
def test_empty_or_old_bing_data_is_normal(cfg, monkeypatch, body):
    monkeypatch.setenv("BING_WEBMASTER_API_KEY", BING_KEY)

    def handler(request):
        return httpx.Response(200, json=body) if request.url.host == "ssl.bing.com" else FakeApis()(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        data, status = report.collect(cfg, NOW, client)
    assert status["bing"] == "ok"
    _, text = report.build_report(cfg, data, status, date(2026, 10, 10))
    assert "no Bing data yet" in text


@pytest.mark.parametrize("failure", ["http", "transport", "json", "rows"])
def test_bing_exception_messages_never_carry_the_key(caplog, failure):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="httpx")

    def handler(request):
        if failure == "transport":
            raise httpx.ConnectError(f"failed {request.url}", request=request)
        if failure == "http":
            return httpx.Response(500, text=BING_KEY)
        if failure == "json":
            return httpx.Response(200, text=f"invalid {BING_KEY}")
        return httpx.Response(200, json={"d": [{"Date": BING_KEY}]})

    http_level = logging.getLogger("httpx").level
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(bing.BingError) as exc:
            bing.stats(client, BING_KEY, "wowpriced.com", date(2026, 10, 10), date(2026, 10, 4))
    assert str(exc.value) == ("HTTP 500" if failure == "http" else "")
    assert BING_KEY not in caplog.text and BING_KEY not in str(exc.value)
    assert exc.value.__suppress_context__ or exc.value.__context__ is None
    assert logging.getLogger("httpx").level == http_level


@pytest.mark.parametrize("stamp, expected", [
    ("/Date(1316156400000-0700)/", date(2011, 9, 16)),
    ("/Date(1316131200000)/", date(2011, 9, 16)),
    ("/Date(1316091600000+1100)/", date(2011, 9, 16)),
    ("2026-10-10T00:00:00-07:00", date(2026, 10, 10)),
])
def test_bing_dates_use_the_reported_daily_bucket(stamp, expected):
    assert bing._day(stamp) == expected


@pytest.mark.parametrize("body", [
    {"errors": [{"message": f"denied {CF_OPS_TOKEN} 7316642"}], "data": None},
    {"data": {"viewer": {"zones": []}}, "errors": None},
])
def test_cloudflare_graphql_errors_are_private(cfg, monkeypatch, caplog, body):
    set_traffic(monkeypatch)

    def handler(request):
        if request.url.path == "/client/v4/graphql":
            return httpx.Response(200, json=body)
        return FakeApis()(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        _, status = report.collect(cfg, NOW, client)
    assert status["cloudflare_traffic"] == "error" and status["bing"] == "ok"
    assert "cloudflare_traffic: error" in caplog.messages
    assert CF_OPS_TOKEN not in caplog.text and "7316642" not in caplog.text


def test_traffic_workflow_secrets_and_bing_domain_are_wired():
    from dealradar.config import ROOT

    workflow = (ROOT / ".github/workflows/growth-report.yml").read_text()
    for name in ("CLOUDFLARE_OPS_TOKEN", "BING_WEBMASTER_API_KEY"):
        assert f"{name}: ${{{{ secrets.{name} }}}}" in workflow
    assert "ssl.bing.com" in cli.CORE_DOMAINS


def test_traffic_uses_the_configured_window_and_excludes_today(cfg, monkeypatch):
    set_traffic(monkeypatch)
    cfg.growth_report_days = 3
    seen = []

    def handler(request):
        if request.url.path == "/client/v4/graphql":
            body = json.loads(request.content)
            seen.append(body["variables"])
            rows = CF_ROWS + [{"dimensions": {"date": "2026-10-11"},
                               "uniq": {"uniques": 999999}, "sum": {"pageViews": 999999, "requests": 999999}}]
            return httpx.Response(200, json={"data": {"viewer": {"zones": [{"httpRequests1dGroups": rows}]}}})
        if request.url.host == "ssl.bing.com":
            return httpx.Response(200, json={"d": BING_ROWS + [
                {"Date": "2026-10-11T00:00:00Z", "Clicks": 999999, "Impressions": 999999}]})
        return FakeApis()(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        data, status = report.collect(cfg, NOW, client)
    assert seen == [{"zone": "wowpriced-zone", "start": "2026-10-08", "end": "2026-10-10", "days": 3}]
    assert data["cloudflare_traffic"]["window"]["visitors"] == 1700555
    assert data["bing"]["window"]["clicks"] == 1324690
    _, text = report.build_report(cfg, data, status, date(2026, 10, 10))
    assert "last 3 days 1,700,555" in text
