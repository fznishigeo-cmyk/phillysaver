"""Task 211: offline campaign attribution, optional profiles and honest totals."""

import json
import logging
import re
import subprocess
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from dealradar.config import AppConfig, load_config, social_profiles
from dealradar.growth import google, report
from dealradar.publish import meta, pinterest, profiles, telegram_pub, traffic
from dealradar.publish.tracking import head_snippet
from tests.test_pinterest import candidate
from tests.test_seo import cfg, db, _render
from tests.test_tracking import HARNESS, SAMPLE, _script

MONDAY = date(2026, 10, 26)
PROFILES = {"pinterest": "https://www.pinterest.com/wowpriced/",
            "facebook": "https://www.facebook.com/wowpriced/",
            "instagram": "https://www.instagram.com/wowpriced/",
            "telegram": "https://t.me/wowpriced"}


def test_social_payloads_tagged_price_free_and_share_feed_unchanged():
    cfg = load_config()
    deal = candidate("one", 1, item="Kitchen tool $19.99", store="Amazon $4.99")
    path = "/deal/tool-12345678/"
    payloads = {
        "pinterest": pinterest.pin_payload(deal, path, "board", "#ad")["link"],
        **{network: meta.caption(deal, path, cfg, network) for network in ("facebook", "instagram")},
        "telegram": telegram_pub.format_message(deal, path),
    }
    for source, text in payloads.items():
        url = re.search(r"https://\S+", text).group()
        assert urlsplit(url).path == path
        assert parse_qs(urlsplit(url).query) == {
            "utm_source": [source], "utm_medium": ["social"], "utm_campaign": ["deal"]}
        assert "$" not in text and "19.99" not in text and "4.99" not in text
    pin = pinterest.pin_payload(deal, path, "board", "#ad")
    assert "$" not in pin["title"] + pin["description"] + pin["alt_text"]
    for source, medium in (("share", "referral"), ("feed", "rss")):
        assert traffic.tagged_url("https://wowpriced.com", path, source) == (
            f"https://wowpriced.com{path}?utm_source={source}&utm_medium={medium}&utm_campaign=deal")


@pytest.mark.parametrize("network", PROFILES)
@pytest.mark.parametrize("bad", ["http://{host}/wowpriced", "https://{host}.evil.test/wowpriced",
    "https://evil.test@{host}/wowpriced", "https://{host}:443/wowpriced", "https://{host}/bad\npath",
    "javascript:alert(1)", "https://{host}/", "https://{host}/<script>"])
def test_profile_urls_strict_host_and_https(network, bad, caplog):
    host = urlsplit(PROFILES[network]).netloc
    value = bad.format(host=host)
    assert social_profiles({network: value}) == {}
    assert value not in caplog.text


def test_profile_config_loads_and_empty_defaults(tmp_path):
    assert AppConfig().social_profiles == load_config().social_profiles == {}
    path = tmp_path / "config.yaml"
    path.write_text("social:\n  profiles:\n" + "".join(f"    {key}: {url}\n" for key, url in PROFILES.items()))
    assert load_config(path).social_profiles == PROFILES
    assert profiles.site_version("prior", AppConfig()) == "prior"
    assert profiles.site_version("prior", AppConfig(social_profiles=PROFILES)) != "prior"


@pytest.mark.parametrize("support,paywall,opened", [(False, False, False), (False, True, False),
                                                    (False, True, True), (True, True, False)])
def test_profiles_empty_preserve_every_byte_set_follow_rows_and_schema(db, cfg, tmp_path, monkeypatch,
                                                                    support, paywall, opened):
    cfg.support_enabled, cfg.paywall_enabled, cfg.paywall_open_access = support, paywall, opened
    cfg.traffic_share_enabled = True
    out = tmp_path / "site"
    with monkeypatch.context() as patch:
        patch.setattr(profiles, "publish", lambda *args: None)
        _render(db, cfg, out)
    tree = lambda: {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    before = tree()
    cfg.social_profiles = dict.fromkeys(PROFILES, "")
    profiles.publish(out, cfg, "https://wowpriced.com")
    assert tree() == before
    cfg.social_profiles = PROFILES
    profiles.publish(out, cfg, "https://wowpriced.com")
    after = tree()
    profiles.publish(out, cfg, "https://wowpriced.com")
    assert tree() == after
    for path in out.rglob("*.html"):
        html = path.read_text()
        if "</footer>" in html:
            footer = re.search(r"<footer>.*?</footer>", html, re.S).group()
            assert "Follow wowpriced" in footer
            for url in PROFILES.values():
                assert f'href="{url}"' in footer
        for tag in re.findall(r'<(?:link[^>]+rel="canonical"|meta[^>]+property="og:url")[^>]*>', html):
            assert "utm_" not in tag
        schema = [json.loads(s) for s in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
        organizations = [s for s in schema if s.get("@type") == "Organization"]
        if path == out / "index.html":
            assert organizations[0]["sameAs"] == list(PROFILES.values())
        else:
            assert not organizations
    if support:
        join = (out / "join.html").read_text()
        assert "Follow wowpriced" in re.search(r"<main.*?</main>", join, re.S).group()
    cfg.social_profiles = {}
    profiles.publish(out, cfg, "https://wowpriced.com")
    assert tree() == before


def api_report(dimensions, metrics, rows):
    return {"dimensionHeaders": [{"name": d} for d in dimensions],
            "metricHeaders": [{"name": m} for m in metrics],
            "rows": [{"dimensionValues": [{"value": d} for d in ds],
                      "metricValues": [{"value": str(m)} for m in ms]} for ds, ms in rows]}


def test_render_site_wires_profiles_and_credit_tracking_on_real_pages(db, cfg, tmp_path):
    cfg.support_enabled = True
    cfg.social_profiles = PROFILES
    cfg.tracking = {"ga4_measurement_id": SAMPLE["ga4_measurement_id"]}
    out = tmp_path / "site"
    _render(db, cfg, out)
    home = (out / "index.html").read_text()
    assert '"sameAs":' in home and 'class="social-follow"' in home
    assert 'class="social-follow"' in (out / "join.html").read_text()
    paths = json.loads((out / "deals.json").read_text())["deals"]
    detail = (out / paths[0]["deal_path"].strip("/") / "index.html").read_text()
    assert re.search(r'<span class="credit">.*?>Slickdeals</a>', home)
    assert re.search(r'<p class="detail-sources">.*?>Slickdeals</a>', detail)
    for page in (home, detail):
        assert 'closest(".credit a, .detail-sources a")' in page
        assert 'window.wpTrack("credit_click", {partner: link.textContent})' in page


def weekly_response():
    return {"reports": [
        api_report(["dateRange"], ["totalUsers", "sessions"], [
            (["this_week"], [150, 220]), (["last_week"], [100, 140])]),
        api_report(["sessionDefaultChannelGroup", "sessionSource", "sessionManualSource", "sessionMedium"],
                   ["sessions"], [
            (["Organic Search", "google", "(not set)", "organic"], [50]),
            (["Organic Search", "bing", "(not set)", "organic"], [10]),
            (["Organic Social", "l.facebook.com", "(not set)", "referral"], [20]),
            (["Organic Social", "pinterest.com", "pinterest", "social"], [30]),
            (["Organic Social", "l.instagram.com", "(not set)", "referral"], [15]),
            (["Unassigned", "t.me", "telegram", "social"], [5]),
            (["Direct", "(direct)", "(not set)", "(none)"], [40]),
            (["Referral", "partner.test", "(not set)", "referral"], [25]),
            (["Paid Social", "facebook", "facebook", "cpc"], [25])])
    ]}


def test_ga4_weekly_fixture_dates_unique_people_and_channel_grouping(monkeypatch):
    monkeypatch.setattr(google.time, "sleep", lambda _: None)
    def handler(request):
        totals, sources = json.loads(request.content)["requests"]
        assert totals["dateRanges"] == [
            {"startDate": "2026-10-19", "endDate": "2026-10-25", "name": "this_week"},
            {"startDate": "2026-10-12", "endDate": "2026-10-18", "name": "last_week"}]
        assert sources["metrics"] == [{"name": "sessions"}]
        return httpx.Response(200, json=weekly_response())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = google.ga4_weekly(client, "fake", "123", MONDAY)
    assert result["this_week"] == {"people": 150, "sessions": 220}
    assert result["channels"] == {"Organic Search": 60, "Organic Social · Facebook": 20,
        "Organic Social · Pinterest": 30, "Organic Social · Instagram": 15, "Organic Social · Telegram": 5,
        "Direct": 40, "Referral": 25, "Other channels": 25}
    data = {"visitors_week": result, "ga4": {"yesterday": {"totalUsers": 12, "sessions": 18}},
            "search_week": {"clicks": 9, "impressions": 180}, "credits_week": {"Hip2Save": 3},
            "telegram_subscribers": {"subscribers": 21},
            "pinterest": {"weekly": {"outbound_clicks": 7, "pin_days": 4, "expected_pin_days": 10}}}
    lines = "\n".join(report._visitor_scoreboard(data, date(2026, 10, 25)))
    assert lines.startswith("Real visitors yesterday: 12 (people), 18 sessions")
    assert "150 (people) vs 100 last week; +50.0%" in lines
    assert "Organic Social · Telegram: 5" in lines
    assert "9 clicks, 180 appearances" in lines and "recent days may be missing" in lines
    assert "Telegram subscribers now: 21" in lines
    assert "Pinterest clicks to our site this week: 7 (4/10" in lines and "Hip2Save: 3" in lines
    assert "Pinterest's full weekly total is not confirmed" in lines


@pytest.mark.parametrize("current,previous,expected", [(80, 100, "-20.0%"), (100, 100, "+0.0%"),
    (0, 0, "0.0%"), (2, 0, "percentage unavailable (last week was 0)"), (None, 2, "not confirmed")])
def test_weekly_math_zero_and_missing_values(current, previous, expected):
    data = {"visitors_week": {"this_week": {"people": current}, "last_week": {"people": previous}}}
    lines = report._visitor_scoreboard(data, date(2026, 10, 25))
    assert lines[3].endswith(expected)
    assert "not confirmed" in lines[0]
    assert "Scoreboard" not in "\n".join(report._visitor_scoreboard(data, date(2026, 10, 26)))


@pytest.mark.parametrize("problem", ["http", "partial", "sampled", "malformed"])
def test_weekly_api_failures_never_return_fake_zero(monkeypatch, problem):
    monkeypatch.setattr(google.time, "sleep", lambda _: None)
    body = weekly_response()
    if problem == "partial":
        body["reports"][1]["rowCount"] = 10001
    elif problem == "sampled":
        body["reports"][0]["metadata"] = {"subjectToThresholding": True}
    elif problem == "malformed":
        body["reports"][0]["metricHeaders"] = []
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
            403 if problem == "http" else 200, json=body))) as client:
        with pytest.raises((ValueError, httpx.HTTPError)):
            google.ga4_weekly(client, "fake", "123", MONDAY)


def test_weekly_credit_query_and_search_week(monkeypatch):
    monkeypatch.setattr(google.time, "sleep", lambda _: None)
    def handler(request):
        body = json.loads(request.content)
        if request.url.host == "analyticsdata.googleapis.com":
            query, = body["requests"]
            assert query["dimensionFilter"]["filter"]["stringFilter"]["value"] == "credit_click"
            return httpx.Response(200, json={"reports": [api_report(["customEvent:partner"], ["eventCount"],
                [(["Slickdeals"], [8]), (["Hip2Save"], [3])])]})
        assert body["startDate"] == "2026-10-19" and body["endDate"] == "2026-10-25"
        assert body["dataState"] == "all"
        return httpx.Response(200, json={"rows": [{"clicks": 6, "impressions": 70}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert google.ga4_credits(client, "fake", "123", MONDAY) == {"Slickdeals": 8, "Hip2Save": 3}
        assert google.search_week(client, "fake", "sc-domain:wowpriced.com", MONDAY)["clicks"] == 6


@pytest.mark.parametrize("connected,fails", [(False, False), (True, False), (True, True)])
def test_monday_telegram_count_quiet_when_absent_private_when_present(monkeypatch, caplog, connected, fails):
    from tests.test_growth_report import ENV_NAMES
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(report.clock, "sleep", lambda _: None)
    if connected:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-secret-sentinel")
        monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@private-channel-sentinel")
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.path.endswith("/getChatMemberCount")
        assert json.loads(request.content) == {"chat_id": "@private-channel-sentinel"}
        return httpx.Response(403 if fails else 200, json={"ok": not fails, "result": 12345})
    with caplog.at_level(logging.INFO), httpx.Client(transport=httpx.MockTransport(handler)) as client:
        data, status = report.collect(AppConfig(), datetime(2026, 10, 26, 12, tzinfo=timezone.utc), client)
    assert len(calls) == int(connected)
    assert "fake-secret-sentinel" not in caplog.text and "private-channel-sentinel" not in caplog.text
    assert "12345" not in caplog.text
    lines = "\n".join(report._visitor_scoreboard(data, date(2026, 10, 25)))
    if connected:
        assert status["telegram_subscribers"] == ("error" if fails else "ok")
        assert "Telegram subscribers now: " + ("not confirmed" if fails else "12,345") in lines
    else:
        assert "telegram" not in caplog.text.lower() and "Telegram subscribers" not in lines


@pytest.mark.parametrize("hour,weekly", [(1, False), (12, True)])
def test_weekly_collection_uses_local_monday_and_partner_failure_is_isolated(monkeypatch, caplog, hour, weekly):
    from tests.test_growth_report import ENV_NAMES
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOOGLE_SA_JSON", "fake-private-key-sentinel")
    monkeypatch.setattr(google, "access_token", lambda *args: "fake-access-sentinel")
    monkeypatch.setattr(google.time, "sleep", lambda _: None)
    ga = {period: {**dict.fromkeys(google.EVENTS, 0), "totalUsers": 12, "sessions": 18, "newUsers": 10}
          for period in ("yesterday", "window")}
    ga.update(sources=[], pages=[])
    monkeypatch.setattr(google, "ga4", lambda *args: ga)
    for name in ("ga4_stores", "ga4_saved", "ga4_deal_pages", "ga4_deal_sessions"):
        monkeypatch.setattr(google, name, lambda *args, **kwargs: {})
    monkeypatch.setattr(google, "search_console", lambda *args: {})
    cfg = AppConfig(growth_ga4_property_id="123", growth_search_console_site="sc-domain:wowpriced.com")
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.host == "searchconsole.googleapis.com":
            return httpx.Response(200, json={"rows": [{"clicks": 9, "impressions": 180}]})
        query = json.loads(request.content)["requests"]
        if len(query) == 1:
            return httpx.Response(400, json={"error": {"message": "fake-private-key-sentinel fake-access-sentinel"}})
        return httpx.Response(200, json=weekly_response())
    with caplog.at_level(logging.INFO), httpx.Client(transport=httpx.MockTransport(handler)) as client:
        data, status = report.collect(cfg, datetime(2026, 10, 26, hour, tzinfo=timezone.utc), client)
    assert len(calls) == (3 if weekly else 0)
    if weekly:
        assert status["visitors_week"] == status["search_week"] == "ok" and status["credits_week"] == "error"
        _, text = report.build_report(cfg, data, status, date(2026, 10, 25))
        assert "Real visitors this week: 150 (people) vs 100 last week; +50.0%" in text
        assert "Source-credit clicks by partner: not confirmed" in text
    else:
        assert "visitors_week" not in status
    for private in ("fake-private-key-sentinel", "fake-access-sentinel", "150", "180"):
        assert private not in caplog.text


@pytest.mark.parametrize("ga4", [True, False])
def test_credit_click_uses_existing_tracking_gates_and_never_sends_url(tmp_path, ga4):
    ids = dict(SAMPLE)
    if not ga4:
        ids.pop("ga4_measurement_id")
    calls = [["credit_click", {"partner": "Slickdeals", "url": "PRIVATE", "price": 12, "store": "PRIVATE"}],
             ["credit_click", {"partner": "https://PRIVATE/path"}], ["credit_click", {"partner": ""}]]
    scenarios = {"open": {"calls": calls}, "gpc": {"gpc": True, "calls": calls},
                 "out": {"storage": {"wp.adsOptOut": "1"}, "calls": calls}}
    code = _script(head_snippet(AppConfig(tracking=ids)))
    # Exercise the delegated listener on both server and browser card markup.
    harness = HARNESS.replace('document: { createElement:',
        'document: { addEventListener(name, fn) { this.listener = fn; }, createElement:')
    harness = harness.replace('if (sc.breakMeta)', '''
    if (ctx.document.listener) {
      for (const kind of ["card", "deal"]) ctx.document.listener({target: {closest(selector) {
        if (!selector.includes(".credit a") || !selector.includes(".detail-sources a")) throw Error("selector");
        return {textContent: "Hip2Save", href: "PRIVATE"};
      }}});
    }
    if (sc.breakMeta)''')
    (tmp_path / "snippet.js").write_text(code)
    (tmp_path / "harness.cjs").write_text(harness)
    (tmp_path / "scenarios.json").write_text(json.dumps(scenarios))
    result = subprocess.run(["node", "harness.cjs", "snippet.js", "scenarios.json"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    for name, values in json.loads(result.stdout).items():
        events = [e for e in values["dataLayer"] or [] if e[0] == "event"]
        assert len(events) == (3 if ga4 else 0)
        assert [e[2]["partner"] for e in events] == (["Hip2Save", "Hip2Save", "Slickdeals"] if ga4 else [])
        assert "PRIVATE" not in json.dumps(values)
        for tag in ("fbq", "ttq", "pintrk", "uetq"):
            assert not values[tag] or not any("credit_click" in json.dumps(e) for e in values[tag])
