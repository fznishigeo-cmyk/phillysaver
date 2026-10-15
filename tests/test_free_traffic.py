"""Free discovery: public facts, stable identities, real browser actions."""

import copy
import json
import re
import shutil
import subprocess
from datetime import timedelta
from html import unescape
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree as ET

import feedparser
import httpx
import pytest

from dealradar.config import AppConfig, load_config
from dealradar.growth import google, report
from dealradar.publish import traffic
from dealradar.publish.tracking import head_snippet
from tests.test_publish_site import NOW, make_deal
from tests.test_seo import cfg, db, _render
from tests.test_seo_deal_pages import _path, _page


def enable(cfg):
    cfg.traffic_feed_enabled = cfg.traffic_share_enabled = cfg.traffic_related_hubs_enabled = True


def test_config_default_switches_and_feed_bound(tmp_path):
    assert not traffic.enabled(AppConfig())
    shipped = load_config()
    assert traffic.enabled(shipped) and shipped.traffic_feed_max_items == 20  # operator 2026-10-15
    assert shipped.traffic_feed_details is False and AppConfig().traffic_feed_details is True
    path = tmp_path / "knobs.yaml"
    for number, expected in ((0, 1), (5000, 200)):
        path.write_text(f"traffic:\n  feed_enabled: true\n  share_enabled: false\n  feed_max_items: {number}\n")
        cfg = load_config(path)
        assert cfg.traffic_feed_enabled and not cfg.traffic_share_enabled
        assert not cfg.traffic_related_hubs_enabled and cfg.traffic_feed_max_items == expected


def test_feed_only_current_public_pages_and_stable_rerun(db, cfg, tmp_path):
    enable(cfg)
    cfg.seo_deal_pages_max = 1
    out = tmp_path / "site"
    _render(db, cfg, out)
    xml = ET.parse(out / "feed.xml")
    parsed = feedparser.parse((out / "feed.xml").read_bytes())
    assert not parsed.bozo and parsed.version == "rss20"
    items = xml.findall("./channel/item")
    assert len(items) == 1
    doc = json.loads((out / "deals.json").read_text())
    page_urls = {"https://wowpriced.com" + d["deal_path"] for d in doc["deals"] if d.get("deal_path")}
    assert {item.findtext("guid") for item in items} == page_urls
    for row in doc["deals"]:
        if row.get("locked"):
            assert row.get("item") is None
    link = urlsplit(items[0].findtext("link"))
    assert parse_qs(link.query) == {"utm_source": ["feed"], "utm_medium": ["rss"], "utm_campaign": ["deal"]}
    assert items[0].find("guid").attrib == {"isPermaLink": "true"}
    assert _page(out, link.path).exists()
    assert xml.find("./channel/{http://www.w3.org/2005/Atom}link").attrib["href"] == "https://wowpriced.com/feed.xml"
    assert "img.example" not in (out / "feed.xml").read_text()
    # Repeat the post-pass itself without rotating the teaser.
    before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    rows = [d for d in doc["deals"] if d.get("deal_path")]
    paths = {d["key"][:8]: d["deal_path"] for d in rows}
    traffic.publish(out, cfg, rows, paths, "https://wowpriced.com")
    assert before == {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    _render(db, cfg, out, [])
    assert not ET.parse(out / "feed.xml").findall("./channel/item")


def test_feed_facts_escaping_sort_cap_guids_and_ended_rows():
    base = {"key": "11111111abcd", "status": "alive", "item": 'Tool <script>& "x"\x01',
            "store": "Store", "final_price": 10, "conditions": ["open_box", "membership"],
            "first_seen_at": NOW.isoformat(), "codes": ["A&B"], "how": ["Clip <coupon>"],
            "checkout": {"summary": "Membership required", "shipping": "Shipping unknown"},
            "credits": [{"name": "Source <one>", "url": "https://source.test/post?x=1&y=2"}]}
    paths = {"11111111": "/deal/tool-11111111/", "22222222": "/deal/other-22222222/"}
    recent = dict(base, key="22222222abcd", first_seen_at=(NOW + timedelta(hours=1)).isoformat())
    rows = [base, dict(base, key="dead0000", status="dead"), dict(base, key="locked00", locked=True),
            dict(base, key="no-page!"), recent]
    first = traffic.feed_bytes(rows, paths, "https://wowpriced.com", 1)
    xml = ET.fromstring(first)
    assert len(xml.findall("./channel/item")) == 1
    item = xml.find("./channel/item")
    assert item.findtext("guid") == "https://wowpriced.com/deal/other-22222222/"
    description = item.findtext("description")
    assert "Sale price: $10.00 USD" in description and "Condition: open box, membership" in description
    assert "Membership required" in description and "Shipping unknown" in description
    assert "Clip &lt;coupon&gt;" in description and "A&amp;B" in description
    assert 'href="https://source.test/post?x=1&amp;y=2"' in description
    assert not any(x in first.decode() for x in ("<script>", "<img", "tag=", "\x01"))
    assert first == traffic.feed_bytes(list(reversed(rows)), paths, "https://wowpriced.com", 1)
    changed = ET.fromstring(traffic.feed_bytes([dict(recent, item="Renamed", final_price=9)], paths,
                                              "https://wowpriced.com", 1)).find("./channel/item")
    assert changed.findtext("guid") == item.findtext("guid")
    assert changed.findtext("pubDate") == item.findtext("pubDate")
    assert "$9.00" in changed.findtext("description")


def test_trimmed_feed_keeps_codes_steps_and_checkout_on_our_pages():
    row = {"key": "11111111abcd", "status": "alive", "item": "Tool", "store": "Store", "final_price": 10,
           "conditions": ["used"], "first_seen_at": NOW.isoformat(), "codes": ["SECRET10"],
           "how": ["Clip the coupon"], "checkout": {"summary": "Membership required"},
           "credits": [{"name": "Source", "url": "https://source.test/post"}]}
    paths = {"11111111": "/deal/tool-11111111/"}
    full = traffic.feed_bytes([row], paths, "https://wowpriced.com", 20)
    assert full == traffic.feed_bytes([row], paths, "https://wowpriced.com", 20, True)
    item = ET.fromstring(traffic.feed_bytes([row], paths, "https://wowpriced.com", 20, False)).find("./channel/item")
    description = item.findtext("description")
    assert "Sale price: $10.00 USD" in description and "Condition: used" in description
    assert 'href="https://source.test/post"' in description and "on the deal page" in description
    assert not any(x in description for x in ("SECRET10", "Clip the coupon", "Membership required"))
    assert item.findtext("link").startswith("https://wowpriced.com/deal/tool-11111111/?")


def test_detail_share_context_and_footer_do_not_change_wall_cards(db, cfg, tmp_path):
    cfg.paywall_open_access = True
    cfg.seo_store_page_min_deals = cfg.seo_price_page_min_deals = 1
    deals = [make_deal(dedupe_key=f"same{i}", item=f"Item {i}", codes=[], store="Macy's") for i in range(2)]
    out = tmp_path / "site"
    _render(db, cfg, out, deals)
    before = (out / "index.html").read_text()
    before_json = json.loads((out / "deals.json").read_text())["deals"]
    enable(cfg)
    _render(db, cfg, out, deals)
    after = (out / "index.html").read_text()
    cards = lambda html: re.findall(r'<article class="card.*?</article>', html, re.S)
    assert cards(after) == cards(before)
    assert before_json == json.loads((out / "deals.json").read_text())["deals"]
    assert '<details class="traffic-share">' not in after
    detail = _page(out, _path(deals[0])).read_text()
    assert '<summary>Share this deal</summary>' in detail
    assert 'data-share-native hidden' in detail and 'data-share-copy hidden' in detail
    shared = unescape(re.search(r'<input type="text" readonly value="([^"]+)"', detail).group(1))
    assert shared == traffic.tagged_url("https://wowpriced.com", _path(deals[0]), "share")
    assert 'property="og:url" content="https://wowpriced.com' + _path(deals[0]) + '"' in detail
    assert 'property="og:title"' in detail and 'property="og:image"' not in detail
    assert 'href="/stores/macys/" data-related-hub="store"' in detail
    assert 'href="/deals/under-10/" data-related-hub="price"' in detail
    assert 'href="/deals/under-25/" data-related-hub=' not in detail
    for page in out.rglob("*.html"):
        html = page.read_text()
        if page.name == "welcome.html":
            assert "traffic.js" not in html and "data-feed-link" not in html
            continue
        assert 'rel="alternate" type="application/rss+xml"' in html
        assert html.index("data-feed-link") > html.index("<footer>")
    assert "feed.xml" not in (out / "sitemap.xml").read_text()
    assert "/feed.xml\n  Content-Type: application/rss+xml; charset=utf-8" in (out / "_headers").read_text()
    feed = (out / "feed.xml").read_bytes()
    _render(db, cfg, out, deals, now=NOW + timedelta(hours=4))
    assert (out / "feed.xml").read_bytes() == feed
    cfg.traffic_feed_enabled = cfg.traffic_share_enabled = cfg.traffic_related_hubs_enabled = False
    _render(db, cfg, out, deals)
    assert not (out / "feed.xml").exists() and not (out / "traffic.js").exists()
    assert "wp-traffic" not in (out / "index.html").read_text()
    assert "traffic-share" not in _page(out, _path(deals[0])).read_text()
    assert cards((out / "index.html").read_text()) == cards(before)


def test_sparse_hubs_and_ended_offers_do_not_make_false_links():
    deal = {"key": "one", "store": "Walmart", "status": "alive"}
    assert traffic.related_hubs(deal, {}, {}) == ""
    assert traffic.related_hubs(deal, {"/stores/walmart/": ("Walmart", [deal])},
                                {"/deals/under-10/": ("Deals under $10", [deal])}) == ""
    other = {"key": "two"}
    assert traffic.related_hubs(deal, {}, {"/deals/under-10/": ("Deals under $10", [other])}) == ""
    assert traffic.share_html(dict(deal, status="dead"), "/deal/x-one/", "https://wowpriced.com") == ("", "")
    assert traffic.share_html(deal, "/deal/x-one/", "") == ("", "")


def test_browser_actions_and_failure_fallbacks():
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    result = subprocess.run(["node", "--test", "tests/free_traffic_logic.cjs"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("ga4", [False, True])
def test_traffic_events_are_bounded_ga4_only_even_under_privacy_controls(tmp_path, ga4):
    from tests.test_tracking import SAMPLE, HARNESS, _script

    if not shutil.which("node"):
        pytest.skip("node unavailable")
    ids = dict(SAMPLE)
    if not ga4:
        ids.pop("ga4_measurement_id")
    cfg = AppConfig(tracking=ids, traffic_feed_enabled=True)
    calls = [[event, {"method": method, "url": "PRIVATE", "store": "PRIVATE", "price": 11}]
             for event, method in (("deal_share", "native"), ("deal_share", "copy"),
                 ("share_arrival", "deal"), ("feed_arrival", "deal"), ("feed_open", "footer"),
                 ("related_hub_click", "store"), ("related_hub_click", "price"))]
    calls += [[event, {"method": "PRIVATE"}] for event in google.TRAFFIC_EVENTS]
    scenarios = {"baseline": {}, "open": {"calls": calls}, "gpc": {"gpc": True, "calls": calls},
                 "out": {"storage": {"wp.adsOptOut": "1"}, "calls": calls}}
    (tmp_path / "snippet.js").write_text(_script(head_snippet(cfg)))
    (tmp_path / "harness.cjs").write_text(HARNESS)
    (tmp_path / "scenarios.json").write_text(json.dumps(scenarios))
    result = subprocess.run(["node", "harness.cjs", "snippet.js", "scenarios.json"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    results = json.loads(result.stdout)
    for name in ("open", "gpc", "out"):
        sent = [e for e in results[name]["dataLayer"] if e[0] == "event"]
        assert sent == ([["event", event, {"method": params["method"], "send_to": SAMPLE["ga4_measurement_id"]}]
                         for event, params in calls[:7]] if ga4 else [])
        assert "PRIVATE" not in json.dumps(results[name])
        for tag in ("fbq", "ttq", "pintrk", "uetq"):
            assert results[name][tag] == (results["baseline"][tag] if name == "open" else None)


def test_growth_report_observed_arrivals_and_missing_samples():
    from tests.test_growth_report import GA4_RESPONSE

    response = copy.deepcopy(GA4_RESPONSE)
    response["reports"][1]["rows"].extend(
        {"dimensionValues": [{"value": event}, {"value": "yesterday"}],
         "metricValues": [{"value": str(i)}]} for i, event in enumerate(google.TRAFFIC_EVENTS, 1))
    def handler(request):
        body = json.loads(request.content)
        assert set(google.TRAFFIC_EVENTS) <= set(body["requests"][1]["dimensionFilter"]["filter"]["inListFilter"]["values"])
        return httpx.Response(200, json=response)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ga = google.ga4(client, "test-token", "12345", 7)
    product = {period: {name: ga[period][event] for name, event in google.PRODUCT_EVENTS.items()}
               for period in ("yesterday", "window")}
    _, text = report.build_report(AppConfig(), {"product": product}, {"product": "ok"}, NOW.date())
    for i, name in enumerate(google.TRAFFIC_EVENTS, 1):
        assert ga["window"][name] is None
        assert f"product.{name}: {i} / unavailable" in text
    assert "not unique people" in text and "not subscriptions" in text and "no traffic lift is inferred" in text


def test_phone_controls_wrap_without_wall_styles_and_no_browser_dependency():
    css = (traffic.TEMPLATES / "traffic.css").read_text()
    # At 360–390px, available width is the page width minus its existing
    # 16px side gutters. Long store/item names must not set a fixed width.
    assert "flex-wrap:wrap" in css and "overflow-wrap:anywhere" in css
    assert "min-height:44px" in css and "font-size:16px" in css
    assert "width:100%;max-width:100%;min-width:0" in css
    assert ".card" not in css and ".grid" not in css and "animation" not in css
    html, _ = traffic.share_html({"status": "alive", "item": 'Long name "<&' * 20},
                                 "/deal/long-12345678/", "https://wowpriced.com")
    assert 'role="status" aria-live="polite"' in html and '<label data-share-manual>' in html
    assert 'type="text" readonly' in html and '<summary>Share this deal</summary>' in html
