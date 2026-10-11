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
from dealradar.growth import google, meta, report, stripe_stats

NOW = datetime(2026, 10, 11, 12, 23, tzinfo=timezone.utc)  # 08:23 in New York
TODAY_START = int(datetime(2026, 10, 11, 4, 0, tzinfo=timezone.utc).timestamp())
DAY = 86400

GOOGLE_TOKEN = "ya29.FAKE-GOOGLE-ACCESS-TOKEN"
META_TOKEN = "EAAFAKEMETASYSTEMTOKEN"
STRIPE_KEY = "rk_live_FAKESTRIPEKEY"
BOT_TOKEN = "123456:FAKE-BOT-TOKEN"
ADMIN_CHAT = "999888"

ENV_NAMES = ("GOOGLE_SA_JSON", "META_SYSTEM_TOKEN", "STRIPE_SECRET_KEY", "REPORT_EMAIL_TO",
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
        {"dimensionValues": [{"value": "deal_click"}, {"value": "yesterday"}], "metricValues": [{"value": "9001"}]}]},
    {"dimensionHeaders": [{"name": "sessionSourceMedium"}], "rows": [
        {"dimensionValues": [{"value": "google / organic"}], "metricValues": [{"value": "2500"}]},
        {"dimensionValues": [{"value": "facebook / paid"}], "metricValues": [{"value": "1200"}]}]},
    {"dimensionHeaders": [{"name": "landingPage"}], "rows": [
        {"dimensionValues": [{"value": "/secret-landing-page.html"}], "metricValues": [{"value": "3100"}]}]},
]}

SC_TOTAL = {"rows": [{"clicks": 834521.0, "impressions": 9123456.0, "ctr": 0.0914, "position": 17.26}]}
SC_QUERIES = {"rows": [{"keys": ["private <b>query</b> phrase"], "clicks": 300.0, "impressions": 4000.0,
                        "ctr": 0.075, "position": 4.2}]}
SC_PAGES = {"rows": [{"keys": ["https://wowpriced.com/hacks.html"], "clicks": 120.0,
                      "impressions": 2000.0, "ctr": 0.06, "position": 6.0}]}
SITEMAP = {"path": "https://wowpriced.com/sitemap.xml", "lastDownloaded": "2026-10-10T07:12:00Z",
           "isPending": False, "warnings": "2", "errors": "1",
           "contents": [{"type": "web", "submitted": "7", "indexed": "0"}]}


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
        auth = request.headers.get("authorization", "")
        if host == "oauth2.googleapis.com":
            form = parse_qs(request.content.decode())
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
        assert google.access_token(client, sa_json(rsa_key[0])) == GOOGLE_TOKEN
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
        "sign_up", "begin_checkout", "start_trial", "deal_click"]
    assert sources["dimensions"] == [{"name": "sessionSourceMedium"}] and sources["limit"] == 5
    assert pages["dimensions"] == [{"name": "landingPage"}] and pages["limit"] == 5

    assert out["yesterday"] == {"sessions": 812345, "totalUsers": 731942, "newUsers": 640217,
                                "sign_up": 61, "begin_checkout": 0, "start_trial": 0, "deal_click": 9001}
    assert out["window"]["totalUsers"] == 4410777
    assert out["window"]["sign_up"] == 402 and out["window"]["start_trial"] == 77
    assert out["sources"] == [("google / organic", 2500), ("facebook / paid", 1200)]
    assert out["pages"] == [("/secret-landing-page.html", 3100)]


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


# ------------------------------------------------------------------ sections


@pytest.mark.parametrize("google_, meta_, stripe_, ok", [
    (False, False, True, {"stripe"}),
    (True, False, False, {"ga4", "search_console", "sitemap"}),
    (False, True, False, {"meta_ads", "facebook", "instagram"}),
    (True, True, True, {"ga4", "search_console", "sitemap", "meta_ads", "facebook", "instagram", "stripe"}),
])
def test_each_missing_secret_skips_only_its_sections(cfg, rsa_key, monkeypatch, caplog,
                                                     google_, meta_, stripe_, ok):
    caplog.set_level(logging.INFO)
    set_sources(monkeypatch, rsa_key, google_=google_, meta_=meta_, stripe_=stripe_)
    fake = FakeApis()
    with client_for(fake) as client:
        data, status = report.collect(cfg, NOW, client)
    assert {k for k, v in status.items() if v == "ok"} == ok
    assert {k for k, v in status.items() if v == "skipped"} == set(status) - ok
    assert set(data) == ok
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
    assert status["ga4"] == status["instagram"] == "skipped"
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
    assert lines[2:8] == [
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
    assert text.rstrip().endswith("stripe ok")


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
    caplog.set_level(logging.INFO)
    set_telegram(monkeypatch)
    fake = FakeApis()
    with client_for(fake) as client:
        assert report.run(cfg, NOW, http_client=client) == 0
    assert fake.requests == []


def test_exit_code_only_fails_when_every_configured_section_failed(cfg, rsa_key, monkeypatch):
    set_telegram(monkeypatch)
    monkeypatch.setenv("STRIPE_SECRET_KEY", STRIPE_KEY)
    fake = FakeApis(fail={"api.stripe.com": 500})
    with client_for(fake) as client:
        assert report.run(cfg, NOW, http_client=client) == 1
    assert fake.telegram[0]["text"].rstrip().endswith("stripe error")  # still reported

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
                   "owner@example.com", "BEGIN PRIVATE KEY", rsa_key[0].splitlines()[1][:40]):
        assert secret not in public, secret
    assert not summary.exists()
    # ...and the operator still learns which section failed, by class and status only.
    assert "meta_ads: error HTTPStatusError 400" in caplog.messages
    assert "ga4: ok" in caplog.messages and "telegram: sent" in caplog.messages
    assert fake.verified == 1
