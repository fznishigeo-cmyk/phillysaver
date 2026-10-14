"""Confirmed deaths, conservative API rechecks, and inert inline wall cards."""
import json
import re
import shutil
import subprocess
from datetime import timedelta

import pytest

from dealradar import pipeline, rank, verify
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.fetch.woot import _recheck_offers
from dealradar.publish import seo
from dealradar.publish.site import _serialize_deal, render_site
from dealradar.util import iso
from tests.test_publish_site import NOW, make_deal


@pytest.fixture
def db(tmp_path):
    database = DB(tmp_path / "state.db")
    yield database
    database.close()


def seed(db, **over):
    d = make_deal(**over)
    db.insert_deal(**{k: d[k] for k in (
        "dedupe_key", "store", "item", "canonical_url", "final_price",
        "pct_at_checkout", "abs_savings", "online_only", "expires_at")},
        normalized_item=d["item"].lower(), payload=d, now=NOW)
    if d["status"] == "dead":
        db.update_deal(d["dedupe_key"], status="dead")
    return d["dedupe_key"]


@pytest.mark.parametrize("reason", ["post_says_over", "stated_expiry"])
@pytest.mark.parametrize("store", ["Amazon", "Woot", "Target"])
@pytest.mark.parametrize("live", [True, False])
def test_unconfirmed_signal_needs_two_api_readings(db, monkeypatch, reason, store, live):
    url = {"Amazon": "https://www.amazon.com/dp/B012345678",
           "Woot": "https://home.woot.com/offers/lamp",
           "Target": "https://www.target.com/p/lamp"}[store]
    key = seed(db, store=store, canonical_url=url, codes=[],
               asin="B012345678" if store == "Amazon" else "",
               expires_at=iso(NOW - timedelta(minutes=1)),
               price_rechecked_at=iso(NOW - timedelta(minutes=5)),
               recheck_tried_at=iso(NOW - timedelta(minutes=2)))
    cfg = AppConfig(feature_flags={"keepa": True})
    if reason == "stated_expiry":
        verify.mark_expired_dead(db, cfg, NOW)
    else:
        verify.mark_unconfirmed_dead(db, db.get_deal(key), reason, NOW)
    row = db.get_deal(key)
    assert row["death"] == reason
    if store == "Target":
        assert row["status"] == "dead"
        assert rank.site_deals(db, cfg, NOW) == []
        return
    assert row["status"] == "alive" and row["gone_suspect_at"] == iso(NOW)
    assert not row["price_rechecked_at"] and not row["recheck_tried_at"]
    readings = []

    def amazon(deals, *args, **kwargs):
        readings.append(True)
        return {d["dedupe_key"]: live for d in deals}

    monkeypatch.setattr(verify, "recheck_amazon_prices", amazon)

    def check(at):
        if store == "Amazon":
            pipeline._recheck_prices(db, cfg, at)
        else:
            _recheck_offers(db, [{"Url": url, "SalePrice": 10,
                                 "IsSoldOut": not live}], at)

    if store == "Amazon":
        check(NOW)
        assert not readings  # queued for the next run, despite a recent check
    first = NOW + timedelta(hours=2)  # text alone must never count as reading one
    check(first)
    row = db.get_deal(key)
    assert row["status"] == "alive"
    assert row["gone_suspect_at"] == ("" if live else iso(first))
    check(first + timedelta(minutes=20))
    assert db.get_deal(key)["status"] == "alive"
    check(first + timedelta(hours=pipeline._CONFIRM_GONE_H, minutes=1))
    row = db.get_deal(key)
    assert row["status"] == ("alive" if live else "dead")
    assert row["death"] == ("" if live else "confirmed")
    if live:
        # The same old expiry does not repeatedly undo an authoritative reading.
        verify.mark_expired_dead(db, cfg, first + timedelta(hours=2))
        assert not db.get_deal(key)["gone_suspect_at"]


def test_woot_absent_ambiguous_or_inconclusive_offer_is_not_gone(db):
    url = "https://www.woot.com/offers/lamp"
    key = seed(db, canonical_url=url)
    verify.mark_unconfirmed_dead(db, db.get_deal(key), "post_says_over", NOW)
    before = db.get_deal(key)
    for offers in ([], [{"Url": url}], [{"Url": url, "IsSoldOut": True}] * 2):
        _recheck_offers(db, offers, NOW + timedelta(hours=2))
        assert db.get_deal(key) == before
    _recheck_offers(db, [{"Url": url, "EndDate": iso(NOW)}], NOW + timedelta(hours=2))
    assert db.get_deal(key)["death"] == "api_ended"
    assert db.get_deal(key)["status"] == "dead"


@pytest.mark.parametrize("reason", ["confirmed", "api_ended", "stated_expiry", "post_says_over", ""])
def test_json_and_rank_publish_only_confirmed_deaths(db, tmp_path, reason):
    seed(db, status="dead", death=reason, died_at=iso(NOW))
    cfg = AppConfig(paywall_open_access=True)
    rows = rank.site_deals(db, cfg, NOW)
    assert bool(rows) == (reason in rank.VERIFIED_DEATHS)
    # Also protect explicit deals callers, not just rank.site_deals.
    render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=db.all_deals())
    published = json.loads((tmp_path / "site/deals.json").read_text())["deals"]
    assert bool(published) == bool(rows)
    if published:
        assert published[0]["gone"] is True
        assert published[0]["died_at"] == iso(NOW)


def test_gone_retention_is_24_hours(db):
    cfg = load_config("config.yaml")
    assert cfg.show_dead_hours == AppConfig().show_dead_hours == 24
    seed(db, status="dead", death="confirmed", died_at=iso(NOW))
    assert rank.site_deals(db, cfg, NOW + timedelta(hours=24))
    assert not rank.site_deals(db, cfg, NOW + timedelta(hours=24, seconds=1))


def assert_placement(rows, original):
    assert all(d["status"] != "dead" for d in rows[:8])
    for i in range(len(rows)):
        assert sum(d["status"] == "dead" for d in rows[i:i + 8]) <= 1
    assert [d["key"] for d in rows if d["status"] == "alive"] == [
        d["key"] for d in original if d["status"] == "alive"]


def js_function(name):
    # Optional engagement and saved-list hooks are stripped, as the
    # publisher does when config.yaml engagement is empty.
    source = re.sub(r"<!--(engagement|saved)-->.*?<!--/\1-->", "",
                    (seo.TEMPLATES_DIR / "index.html").read_text(), flags=re.S)
    start = source.index(f"  function {name}(")
    end = source.index("\n  }", start) + 4
    return source[start:end]


def run_js(script, data):
    assert shutil.which("node"), "Node is required to verify the wall behavior"
    result = subprocess.run(["node", "-e", script], input=json.dumps(data),
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize("pattern", ["D" * 20 + "L" * 75, "LD" * 40, "L" * 12 + "DD" + "L" * 15,
                                    "D" * 10, "LLD", "L" * 8 + "D", "L" * 30])
def test_python_and_js_placement(pattern):
    rows = [{"key": str(i), "status": "dead" if c == "D" else "alive"} for i, c in enumerate(pattern)]
    placed = seo.place_gone(rows)
    js = run_js(js_function("placeGone") + '\nconsole.log(JSON.stringify(placeGone(JSON.parse(require("fs").readFileSync(0,"utf8")))));', rows)
    assert js == placed
    assert_placement(placed, rows)
    if pattern.startswith("D" * 20):
        assert [i for i, d in enumerate(placed) if d["status"] == "dead"] == list(range(8, len(placed), 8))
        assert len(placed[:60]) == 60  # gone cards consume the normal page budget
    if pattern.startswith("L" * 12):
        assert placed[12]["key"] == "12"  # keep rank when no deferral is needed


@pytest.mark.parametrize("hours,label", [(0.5, "gone just now"), (2.5, "gone 3h ago"), (24, "gone 24h ago")])
def test_static_and_js_gone_card_keeps_only_credit_links_active(hours, label):
    d = _serialize_deal(make_deal(status="dead", death="confirmed", died_at=iso(NOW - timedelta(hours=hours))), NOW)
    script = '''
    var d = JSON.parse(require("fs").readFileSync(0,"utf8"));
    var state = {newKeys:{}}, TIER_LABEL = {}, cardImageIndex = 0;
    var isLocked = () => false, isNew = () => false, lit = () => false, isFreePick = () => false;
    var dealKey = d => d.key, storeHue = () => 0, num = x => x == null ? null : Number(x);
    var esc = x => String(x == null ? "" : x).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/"/g,"&quot;");
    var ageText = () => "";
    ''' + f'Date.now = () => {int(NOW.timestamp() * 1000)};\n'
    script += '\n'.join(js_function(n) for n in ("usedTag", "goneText", "mediaHtml", "cardHtml"))
    script += '\nconsole.log(JSON.stringify(cardHtml(d)));'
    for html in (seo.card_html(d, {d["key"][:8]: "/deal/lamp/"}, NOW), run_js(script, d)):
        assert 'class="card gone' in html
        assert 'aria-disabled="true"' not in html
        assert "Just missed · " + label in html
        assert not re.search(r"<(?:button|input)\b|tabindex=|onclick=", html)
        assert re.findall(r'<a href="([^"]+)"', html) == [
            "https://slickdeals.net/f/1", "https://hip2save.com/p/2"]
        assert "Get deal" not in html
        assert "via Slickdeals" in re.sub(r"<[^>]+>", "", html)


def test_ticker_and_search_use_liveness_correctly():
    rows = [_serialize_deal(make_deal(dedupe_key=str(i), item=f"Lamp {i}", status="dead" if i == 0 else "alive"), NOW) for i in range(3)]
    script = '''
    var state = {deals: JSON.parse(require("fs").readFileSync(0,"utf8")), catTab:0};
    var TICKER_MAX = 16, CATGROUPS = [["Trending", null, ""]], searchIndex = new WeakMap();
    var num = Number, isLocked = () => false, offer = () => ({}), esc = String, dealKey = d => d.key;
    var matchesEligibility = () => true; // Eligibility intersections have their own published-page harness.
    var values = {q:"Lamp 0", storesel:"", minpct:""};
    var document = {getElementById: id => ({value: values[id]})};
    ''' + '\n'.join(js_function(n) for n in (
        "tickerItems", "searchText", "normalizeSearch", "searchWords", "indexedWords", "oneSearchEdit", "matchesSearch", "filtered"))
    script += '\nconsole.log(JSON.stringify({ticker:tickerItems(), filtered:filtered()}));'
    result = run_js(script, rows)
    assert [d["key"] for d in result["ticker"]] == [d["key"] for d in rows[1:]]
    assert [d["key"] for d in result["filtered"]] == [rows[0]["key"]]


@pytest.mark.parametrize("support", [False, True])
@pytest.mark.parametrize("sort_key,direction", [("pct", -1), ("price", 1)])
def test_render_sorts_and_paginates_inline_cards(support, sort_key, direction):
    from dealradar.publish.site import copy_variant

    rows = [_serialize_deal(make_deal(dedupe_key=str(i), item=f"Lamp {i}",
              status="dead" if i % 3 == 0 else "alive", death="confirmed",
              pct_at_checkout=99 - i / 3, final_price=i + 1), NOW) for i in range(100)]
    script = '''
    var state = {deals:JSON.parse(require("fs").readFileSync(0,"utf8")), shown:60,
                 meta:{floors:{min_pct:70}}, sortKey:SORT_KEY, sortDir:DIRECTION};
    var rendered = [], content = {innerHTML:"", querySelector: () => null, appendChild: () => {}};
    var document = {getElementById: id => id === "content" ? content : null};
    var window = {wpSupport:{refresh: () => {}}}, panel = {}, moreObserver = null, loadedOnce = true;
    var renderSearchCount = () => {}; // Search count has its own published-page harness.
    var paintTicker = () => {}, updatePaywall = () => {}, openNudge = () => false;
    var filtered = () => state.deals, isLocked = () => false, canJoin = () => true, openAccess = () => true;
    var openBand = () => "<aside>Join</aside>", num = x => x == null ? null : Number(x);
    var gridHtml = rows => {rendered.push(...rows); return "<div class=grid></div>";}, dealKey = d => d.key;
    '''.replace("SORT_KEY", json.dumps(sort_key)).replace("DIRECTION", str(direction))
    script += '\n'.join(js_function(n) for n in (
        "sortVal", "cmp", "ordered", "frontPageView", "frontPageMix", "placeGone", "activeSpotlight"))
    script += '\n' + copy_variant(js_function("render"), True, support)
    script += '\nrender(); console.log(JSON.stringify({rows:rendered, html:content.innerHTML}));'
    result = run_js(script, rows)
    assert len(result["rows"]) == 60
    assert 'Just missed' not in result["html"] and 'moremore' in result["html"]
    placed = result["rows"]
    assert all(d["status"] == "alive" for d in placed[:8])
    assert any(d["status"] == "dead" for d in placed)
    assert all(sum(d["status"] == "dead" for d in placed[i:i + 8]) <= 1 for i in range(60))


def test_static_wall_inline_spacing_and_other_pages_live_only(db, tmp_path):
    deals = [make_deal(dedupe_key=str(i), item=f"LAMP-UNIQUE-{i}-END", category="electronics",
                      status="dead" if i % 3 == 0 else "alive", death="confirmed",
                      died_at=iso(NOW - timedelta(hours=3))) for i in range(90)]
    cfg = AppConfig(paywall_open_access=True)
    render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=deals)
    html = (tmp_path / "site/index.html").read_text()
    wall = html.split('<div id="content"', 1)[1].split('<div id="paywall"', 1)[0]
    cards = re.findall(r'<article\b.*?</article>', wall, re.S)
    assert len(cards) == 48
    assert all('class="card gone' not in c for c in cards[:8])
    assert all(sum('class="card gone' in c for c in cards[i:i + 8]) <= 1 for i in range(len(cards)))
    assert '<h2 class="secth missed"' not in html
    for page in (tmp_path / "site").rglob("index.html"):
        if page == tmp_path / "site/index.html" or page.parent.parent.name == "deal":
            continue
        content = page.read_text()
        assert all(d["item"] not in content for d in deals if d["status"] == "dead"), page


def test_hidden_unconfirmed_deal_keeps_its_ended_noindex_page(db, tmp_path):
    key = seed(db, category="electronics")
    cfg = AppConfig(paywall_open_access=True)
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out)
    path = next((out / "deal").glob("*/index.html"))
    verify.mark_unconfirmed_dead(db, db.get_deal(key), "post_says_over", NOW)
    render_site(db, cfg, NOW, out_dir=out)
    assert 'This deal has ended' in path.read_text()
    assert '<meta name="robots" content="noindex">' in path.read_text()
    assert not json.loads((out / "deals.json").read_text())["deals"]


@pytest.mark.parametrize("price,codes", [([10, 11], []), (20, ["SAVE10"])])
def test_woot_variant_or_coupon_price_is_inconclusive(db, price, codes):
    url = "https://www.woot.com/offers/lamp"
    key = seed(db, canonical_url=url, codes=codes)
    verify.mark_unconfirmed_dead(db, db.get_deal(key), "stated_expiry", NOW)
    before = db.get_deal(key)
    for hours in (2, 4):
        _recheck_offers(db, [{"Url": url, "SalePrice": price, "IsSoldOut": False}],
                        NOW + timedelta(hours=hours))
        assert db.get_deal(key) == before
