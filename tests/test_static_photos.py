"""Task 274: static photos, condition browsing and SEO, entirely offline."""

import json
import re
import shutil
import socket
import subprocess
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path

import httpx
import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import dark, seo, seo_audit, site
from scripts.publication_budget import analyze
from tests.phone_budget_fixture import publish_fixture
from tests.test_dark_mode import contrast, rgb, tokens
from tests.test_offscreen_cards import offers
from tests.test_product_price_pages import seed
from tests.test_product_entities import insert
from tests.test_front_page import daily_deal
from tests.test_publish_site import NOW, make_deal
from tests.test_pwa import baseline_config, tree_hash
from tests.test_support import NOW as SNAPSHOT_NOW


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task274 network unavailable")
    monkeypatch.setattr(httpx.Client, "send", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(site, "util_now", lambda: NOW)


@pytest.fixture
def db():
    database = DB(":memory:")
    yield database
    database.close()


@pytest.fixture
def cfg():
    return AppConfig(custom_domain="wowpriced.test", paywall_open_access=True,
                     static_photos=True, dark_polish=True, front_page={"used_tab_only": True},
                     seo_hub_min_items=1, seo_store_page_min_deals=1,
                     seo_price_page_min_deals=1, traffic_product_families_enabled=True,
                     seo_event_pages_enabled=True, seo_event_pages_min_items=1,
                     seo_event_pages_events=[dict(slug="black-friday", name="Black Friday",
                         date="2026-10-09", keywords=["fixture"], window_days=[-10, 10])])


def rows(count=9):
    return [make_deal(dedupe_key=f"task274-{i}", store="Walmart", brand="Fixture",
                     item='Fixture stainless <smart> storage & organizer', category="kitchen",
                     canonical_url=f"https://www.walmart.com/ip/fixture/{123456789 + i}",
                     image_url="" if i == 0 else "http://images.example.invalid/unsafe.jpg" if i == 1 else
                         f"https://images.example.invalid/{i}.jpg",
                     conditions=["new"], codes=[], price_rechecked_at=NOW.isoformat())
            for i in range(count)]


def read(out, path):
    return (out / (path.strip("/") + "/index.html" if path != "/" else "index.html")).read_text()


class Images(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.images = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.images.append(dict(attrs))


@pytest.mark.parametrize("switch", ["static_photos", "used_tab_only"])
@pytest.mark.parametrize("value", [False, True])
def test_boolean_switches_and_missing_defaults(tmp_path, switch, value):
    raw = {"site": {switch: value}} if switch == "static_photos" else {
        "publish": {"front_page": {switch: value}}}
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(raw))
    configured = load_config(path)
    assert (configured.static_photos if switch == "static_photos" else configured.front_page[switch]) is value
    path.write_text("{}")
    assert load_config(path).static_photos is AppConfig().static_photos is False
    assert not load_config(path).front_page.get("used_tab_only", False)
    assert not AppConfig().front_page.get("used_tab_only", False)
    assert load_config().static_photos is True
    assert load_config().front_page["used_tab_only"] is True


@pytest.mark.parametrize("switch", ["static_photos", "used_tab_only"])
@pytest.mark.parametrize("value", [0, 1, "false", "true", None, [], {}])
def test_non_boolean_switches_rejected(tmp_path, switch, value):
    raw = {"site": {switch: value}} if switch == "static_photos" else {
        "publish": {"front_page": {switch: value}}}
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(raw))
    prefix = "site" if switch == "static_photos" else "publish.front_page"
    with pytest.raises(ValueError, match=re.escape(f"{prefix}.{switch} must be a boolean")):
        load_config(path)


@pytest.fixture
def photo_site(tmp_path, cfg, db):
    out = tmp_path / "site"
    result = site.render_site(db, cfg, NOW, out_dir=out, deals=rows())
    return out, result


@pytest.mark.parametrize("family", ["home", "category", "store", "price", "event", "best", "product_family"])
def test_every_static_grid_draws_all_https_photos(photo_site, family):
    out, result = photo_site
    path = {"home": "/", "category": "/deals/home-kitchen/", "store": "/stores/walmart/",
            "price": "/deals/under-25/", "event": "/black-friday/", "best": "/best-deals-today/",
            "product_family": "/" + next(out.glob("products/*/index.html")).parent.relative_to(out).as_posix() + "/"}[family]
    html = read(out, path)
    if family == "home":
        html = html.split('<div id="content"')[1].split('<div id="paywall"')[0]
    cards = re.findall(r'<article class="card\b.*?</article>', html, re.S)
    assert len(cards) == 9
    photos = Images("".join(cards)).images
    assert len(photos) == 7
    assert [image["loading"] for image in photos] == ["eager"] * 4 + ["lazy"] * 3
    for i, image in enumerate(photos):
        assert image["width"] == image["height"] == "400"
        assert image["referrerpolicy"] == "no-referrer"
        assert image["alt"] == rows()[0]["item"]
        assert image["src"].startswith("https://")
        assert "nextElementSibling.style.display='flex'" in image["onerror"]
        if i < 4:
            assert image["fetchpriority"] == "high"
        else:
            assert image["decoding"] == "async" and "fetchpriority" not in image
    assert sum(not Images(card).images for card in cards) == 2
    assert "http://images.example.invalid/unsafe.jpg" not in "".join(cards)
    for tag in re.findall(r'<meta\b[^>]*(?:og:image|twitter:image)[^>]*>', html):
        assert re.search(r'content="([^"]+)"', tag)[1] in {d["image_url"] for d in result["public_deals"]}


@pytest.mark.parametrize("image", ["https://images.example.invalid/hero.jpg", "", "http://images.example.invalid/unsafe.jpg"])
@pytest.mark.parametrize("dark_polish", [False, True])
def test_deal_hero_related_cap_and_loading(db, cfg, tmp_path, image, dark_polish):
    cfg.dark_polish = dark_polish
    source = rows(10)
    source[0]["image_url"] = image
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=source)
    deal = next(d for d in result["public_deals"] if d["dedupe_key"] == source[0]["dedupe_key"])
    html = read(tmp_path, deal["deal_path"])
    hero = re.search(r'<div class="detail-photo media">(.*?)</div>', html, re.S)
    assert bool(hero) is image.startswith("https://")
    if hero:
        photo = Images(hero[0]).images[0]
        assert photo["alt"] == source[0]["item"] and photo["src"] == image
        assert photo["loading"] == "eager" and photo["fetchpriority"] == "high"
        assert photo["referrerpolicy"] == "no-referrer"
        assert photo["width"] == photo["height"] == "400"
        assert html.index(hero[0]) < html.index('<p class="detail-price">')
    related = html.split('<h2>Related live deals</h2>')[1]
    assert 'class="grid related-grid"' in related
    assert len(re.findall(r'<article class="card\b', related)) == 4
    photos = Images(re.search(r'<div class="deal-detail".*?</main>', html, re.S)[0]).images
    assert sum(p["loading"] == "eager" for p in photos) == min(4, len(photos))
    assert all(p.get("decoding") == "async" for p in photos if p["loading"] == "lazy")
    assert seo.STATIC_PHOTO_STYLE in html
    assert "minmax(240px,1fr)" in html and "repeat(2,minmax(0,1fr))" in html
    assert "-webkit-line-clamp:2" in html and "object-fit:contain" in html
    assert 'background:var(--photo-tile,#d9dee4)' in html
    assert (dark.STYLE in html) is dark_polish


def test_static_photo_dark_tiles_reuse_254_contrast(photo_site):
    out, result = photo_site
    for path in ("/deals/home-kitchen/", result["public_deals"][-1]["deal_path"]):
        html = read(out, path)
        assert dark.STYLE in html
        assert html.index(seo.STATIC_PHOTO_STYLE) < html.index(dark.STYLE)
        assert '.media{background:var(--photo-tile)}' in dark.CSS
        assert '.media img{mix-blend-mode:multiply}' in dark.CSS
    color = tokens(dark.CSS)
    for foreground in ("fg", "muted", "link"):
        assert contrast(rgb(color[foreground]), rgb(color["card"])) >= 4.5
    assert color["photo-tile"] == "#d9dee4"


def test_spotlight_and_wall_share_four_eager_photos(cfg):
    public = [site._serialize_deal(d, NOW, cfg=cfg) for d in rows()]
    spotlight = [public[-1] | {"spotlight": {"source": "Meh", "ends_at": ""}}]
    html = seo.static_wall(public, True, now=NOW, front_page=cfg.front_page,
                           spotlight=spotlight, static_photos=True)
    photos = Images(html).images
    assert len(photos) == 7
    assert [p["loading"] for p in photos] == ["eager"] * 4 + ["lazy"] * 3


def test_pagination_resets_eager_loading_and_orders_new_across_pages(db, cfg, tmp_path):
    cfg.traffic_hub_pagination_enabled = True
    source = rows(65)
    for d in source[:5]:
        d["conditions"] = ["used"]
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=source)
    for root in ("/deals/home-kitchen/", "/stores/walmart/", "/deals/under-25/"):
        for page in (root, root + "page/2/"):
            html = read(tmp_path, page)
            photos = Images(html).images
            assert sum(p["loading"] == "eager" for p in photos) == min(4, len(photos))
            assert all(p.get("decoding") == "async" for p in photos if p["loading"] == "lazy")
    first = read(tmp_path, "/deals/home-kitchen/")
    second = read(tmp_path, "/deals/home-kitchen/page/2/")
    assert len(re.findall(r'<article class="card\b', first)) == 60
    assert 'class="condtag">USED' not in first
    assert len(re.findall(r'class="condtag">USED', second)) == 5


@pytest.mark.parametrize("enabled", [False, True])
def test_used_home_exclusion_and_stable_hub_partition(db, cfg, tmp_path, enabled):
    cfg.front_page["used_tab_only"] = enabled
    source = rows(10)
    for i, condition in enumerate(("used", "new", "open_box", "refurbished", "new", "used", "new", "new", "new", "new")):
        source[i].update(item=f"Fixture offer {i}", conditions=[condition])
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=source)
    public = result["public_deals"]
    home = read(tmp_path, "/").split('<div id="content"')[1].split('<div id="paywall"')[0]
    for d in public:
        assert (d["deal_path"] in home) is (not enabled or not seo.in_group(d, "USED"))
    for group, path in ((["home", "kitchen"], "/deals/home-kitchen/"), ("USED", "/deals/used-open-box/")):
        expected = [d for d in public if seo.in_group(d, group)]
        if enabled:
            expected.sort(key=lambda d: bool({"used", "open_box", "refurbished"} & set(d["conditions"])))
        actual = re.findall(r'<h3 class="title"><a href="([^"]+)"', read(tmp_path, path))
        assert actual == [d["deal_path"] for d in expected]


@pytest.mark.parametrize("enabled", [False, True])
def test_published_used_tab_and_search_logic(db, cfg, tmp_path, enabled):
    cfg.front_page["used_tab_only"] = enabled
    source = rows(10)
    for i, condition in enumerate(("used", "new", "open_box", "refurbished", "new", "used", "new", "new", "new", "new")):
        source[i].update(item=f"Fixture offer {i}", conditions=[condition])
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=source)
    result = subprocess.run(["node", str(Path(__file__).with_name("used_tab_only_logic.cjs")),
                             str(tmp_path / "index.html"), str(tmp_path / "deals.json"), str(int(enabled))],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("scenario", ["spotlight", "gone"])
@pytest.mark.parametrize("enabled", [False, True])
def test_search_orders_complete_inventory_before_placing_gone(db, cfg, tmp_path, scenario, enabled):
    cfg.front_page["used_tab_only"] = enabled
    source = rows(11)
    for i, d in enumerate(source):
        d.update(item=f"Fixture search match {i}", conditions=["new" if i < 2 or i == 10 else "used"])
    if scenario == "spotlight":
        cfg.front_page["spotlight"] = {"sources": ["meh"], "max_cards": 2}
        source[-1] = daily_deal(item="Fixture used spotlight", conditions=["used"],
                                image_url="https://images.example.invalid/meh.jpg")
    else:
        source[-1].update(status="dead", death="confirmed", died_at=NOW.isoformat())
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=source)
    result = subprocess.run(["node", str(Path(__file__).with_name("used_tab_only_logic.cjs")),
                             str(tmp_path / "index.html"), str(tmp_path / "deals.json"),
                             str(int(enabled)), scenario], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mode,expected", [
    ("membership", "489d1d02f241e1ebe54e84f2a4ae5c36daadbade57a7784b78263ea03f0477c1"),
    ("open", "b26de959ff433cb2687af92e517ca60100df9bc0113e4acbf0df09aea68e936a"),
    ("support", "91c77988b99c3ff7ba842caeb623e67d0a981f996fbec09cb0bc6b4ab972c230"),
    ("shipped", "145e417e40d46eda3289bcf930eb694149ace00c9c739d30eb838eba2a08686e"),
])
@pytest.mark.usefixtures("pre_extension_privacy")
def test_off_matches_recorded_tree_and_false_matches_missing(tmp_path, db, monkeypatch, mode, expected):
    monkeypatch.setattr(site, "util_now", lambda: SNAPSHOT_NOW)
    cfg = baseline_config(mode)
    cfg.static_photos = cfg.offscreen_cards = cfg.dark_polish = False  # recorded before task 274
    cfg.front_page.pop("used_tab_only", None)
    site.render_site(db, cfg, SNAPSHOT_NOW, out_dir=tmp_path / "site", deals=offers())
    assert tree_hash(tmp_path / "site") == expected
    before = {p.relative_to(tmp_path / "site"): p.read_bytes() for p in (tmp_path / "site").rglob("*") if p.is_file()}
    cfg.front_page["used_tab_only"] = False
    db.meta_set(site._TEASER_META, "[]")  # Compare the same closed-paywall drop.
    site.render_site(db, cfg, SNAPSHOT_NOW, out_dir=tmp_path / "site", deals=offers())
    assert all((tmp_path / "site" / name).read_bytes() == data for name, data in before.items())


def test_independent_switch_versions_and_idempotent_flip(db, cfg, tmp_path):
    versions = set()
    for photos, used in ((False, False), (True, False), (False, True), (True, True), (False, False)):
        cfg.static_photos, cfg.front_page["used_tab_only"] = photos, used
        result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows())
        version = result["full_payload"]["meta"]["site_version"]
        html = read(tmp_path, "/")
        assert ("function usedOrOpenBox" in html) is used
        assert "/*used-tab-only*/" not in html and "/*/used-tab-only*/" not in html
        if not photos and not used and versions:
            assert version == original
        else:
            assert version not in versions
            versions.add(version)
        if not photos and not used:
            original = version
        before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
        site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows())
        assert all(path.read_bytes() == data for path, data in before.items())


def test_google_noindex_unique_hubs_and_real_images(photo_site):
    out, result = photo_site
    hubs = [read(out, f"/deals/{slug}/") for _, _, slug in seo.HUB_GROUPS]
    for pattern in (r'<title>(.*?)</title>', r'<meta name="description" content="([^"]+)">'):
        assert len({re.search(pattern, html)[1] for html in hubs}) == len(hubs)
    assert 'content="noindex,follow"' in read(out, "/deals/pets/")
    for deal in result["public_deals"]:
        html = read(out, deal["deal_path"])
        products = seo_audit.Page(html, deal["deal_path"]).products()
        assert bool(products) is deal["image_url"].startswith("https://")
        assert all(p["image"] == deal["image_url"] for p in products)


def test_ended_deal_stays_noindex_with_photo(db, cfg, tmp_path):
    source = rows(1)[0] | dict(image_url="https://images.example.invalid/ended.jpg")
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[source])
    source.update(status="dead", death="confirmed", died_at=NOW.isoformat())
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[source])
    html = next(tmp_path.glob("deal/*/index.html")).read_text()
    assert 'content="noindex"' in html and 'class="detail-photo media"' in html
    assert "Related live deals" not in html
    assert not seo_audit.Page(html, "/deal/ended/").products()


@pytest.mark.parametrize("basis", ["", "90-day average price on Amazon"])
def test_shipped_amazon_has_no_history_statistics_or_links(db, tmp_path, basis):
    cfg = load_config()
    assert cfg.amazon_history == "hide"
    source = seed(db, 1)
    source[0]["regular_price_basis"] = basis
    source[0]["click_price_check"] = dict(asin=source[0]["asin"], final=source[0]["final_price"],
                                        at=NOW.isoformat(), buybox=source[0]["final_price"])
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=source)
    deal = result["public_deals"][0]
    assert not deal.get("click_price_unknown")
    assert "product_path" not in deal
    html = read(tmp_path, deal["deal_path"])
    assert 'class="price-history' not in html and 'href="/p/' not in html
    text = seo_audit.Page(html, deal["deal_path"]).scope.text()
    assert not re.search(r'lowest|highest|\b\d+ checks\b|90.day|usual price|our price checks', text, re.I)
    assert "Sale price:" in html and 'class="detail-photo media"' in html
    assert not list(tmp_path.glob("p/*/index.html"))


@pytest.mark.parametrize("basis,conditions,hidden", [
    ("90-day average price on Amazon", ["new"], True),
    ("30-day average on Amazon", ["new"], True),
    ("", ["new"], True),  # Legacy Amazon price-drop reference, before explicit basis fields.
    ("90-day average new price", ["used", "open_box"], True),
    ("lowest new price (90-day average)", ["used", "open_box"], True),
    ("List Price on Amazon", ["new"], False),
    ("current new price on Amazon", ["used", "open_box"], False),
    ("lowest new price (current offer)", ["used", "open_box"], False),
    ("Amazon's own new price", ["used", "open_box"], False),
    ("", ["used", "open_box"], False),
])
@pytest.mark.parametrize("history", ["hide", "show"])
def test_public_projection_hides_only_history_comparators(basis, conditions, hidden, history):
    cfg = load_config()
    cfg.amazon_reference = "history"  # recorded before task 276: isolate the history switch
    cfg.amazon_history = history
    source = make_deal(store="Amazon", canonical_url="https://www.amazon.com/dp/B000000001",
                       regular_price_basis=basis, conditions=conditions, verify_tier="history-checked",
                       sightings=[{"source_name": "keepa", "source_url": "https://keepa.com/fixture"}])
    before = json.dumps(source, sort_keys=True)
    public = site._serialize_deal(source, NOW, cfg=cfg)
    suppressed = hidden and history == "hide"
    for field in ("regular_price", "pct_at_checkout", "abs_savings"):
        assert public[field] == (None if suppressed else source[field])
    assert public.get("regular_price_basis", "") == ("" if suppressed else basis)
    assert ("usual $50.00" in public["headline"]) is (not suppressed)
    assert public["final_price"] == source["final_price"]
    assert json.dumps(source, sort_keys=True) == before


def test_history_comparators_absent_from_json_and_every_card_surface(db, cfg, tmp_path):
    cfg.amazon_history = "hide"
    source = rows(3)
    source[0].update(store="Amazon", canonical_url="https://www.amazon.com/dp/B000000001",
                     regular_price_basis="90-day average price on Amazon", verify_tier="history-checked",
                     image_url="https://images.example.invalid/amazon.jpg")
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=source)
    public = next(d for d in result["public_deals"] if d["dedupe_key"] == source[0]["dedupe_key"])
    for payload in (json.loads((tmp_path / "deals.json").read_text()), result["full_payload"]):
        deal = next(d for d in payload["deals"] if d["key"] == public["key"])
        assert all(deal[field] is None for field in ("regular_price", "pct_at_checkout", "abs_savings"))
        assert "regular_price_basis" not in deal and "usual $" not in deal["headline"]
    surfaces = set()
    for path in tmp_path.rglob("*.html"):
        html = path.read_text()
        for card in re.findall(r'<article class="card\b.*?</article>', html, re.S):
            if f'href="{public["deal_path"]}"' in card:
                surfaces.add(path.relative_to(tmp_path).parts[0])
                assert "<s>" not in card and "save $" not in card and 'class="flag"' not in card
                assert "90-day" not in card and "usual $" not in card
    assert surfaces >= {"index.html", "deals", "stores", "black-friday", "best-deals-today", "products", "deal"}
    detail = read(tmp_path, public["deal_path"])
    assert "Regular price:" not in detail and "90-day" not in detail
    assert "80% off" not in re.search(r"<title>(.*?)</title>", detail)[1]
    result = subprocess.run(["node", str(Path(__file__).with_name("used_tab_only_logic.cjs")),
                             str(tmp_path / "index.html"), str(tmp_path / "deals.json"), "1", "history"],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_retained_ended_amazon_page_keeps_shipped_history_policy(db, tmp_path):
    cfg = load_config()
    source = insert(db, "retained-amazon", store="Amazon", canonical_url="https://www.amazon.com/dp/B000000001",
                    asin="B000000001", item="Fixture retained Amazon", category="kitchen",
                    regular_price_basis="90-day average price on Amazon", verify_tier="history-checked",
                    image_url="https://images.example.invalid/ended.jpg", price_rechecked_at=NOW.isoformat())
    source["click_price_check"] = dict(asin=source["asin"], final=source["final_price"],
                                     at=NOW.isoformat(), buybox=source["final_price"])
    db.update_deal(source["dedupe_key"], payload=source)
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[source])
    path = result["public_deals"][0]["deal_path"]
    db.update_deal(source["dedupe_key"], status="dead", verify_tier="history-checked",
                   payload=dict(source, died_at=NOW.isoformat(), death="confirmed"))
    result = site.render_site(db, cfg, NOW + timedelta(days=2), out_dir=tmp_path, deals=[])
    assert not result["public_deals"]
    html = read(tmp_path, path)
    assert "This deal has ended" in html and re.search(r'content="noindex(?:,follow)?"', html)
    text = seo_audit.Page(html, path).scope.text()
    assert not re.search(r"price history|90.day|average|usual|80% off|save \$40", text, re.I)
    assert "✓ verified: The price was verified." in text


def test_shipped_phone_budgets_unchanged(tmp_path):
    cfg = load_config()
    publish_fixture(tmp_path, cfg)
    report = analyze(tmp_path / "site", cfg, expected_offers=1400)
    for key in ("home_html", "home_js", "home_css", "deals_json"):
        measured = report["metrics"][key]["gzip"]
        limit = cfg.phone_budget[f"{key}_gzip_max_bytes"]
        print(f"task274 {key}: {measured} <= {limit} gzip bytes")
        assert measured <= limit
    wall = read(tmp_path / "site", "/").split('<div id="content"')[1].split('<div id="paywall"')[0]
    cards = re.findall(r'<article class="card\b.*?</article>', wall, re.S)
    photos = Images("".join(cards)).images
    assert len(photos) > 4
    assert sum(p["loading"] != "lazy" for p in photos) == 4


def test_chromium_photos_and_cls_offline(photo_site):
    if not shutil.which("node"):
        pytest.skip("Node unavailable")
    out, _ = photo_site
    result = subprocess.run(["node", str(Path(__file__).with_name("static_photos_browser.cjs")), str(out)],
                            capture_output=True, text=True, timeout=120)
    print(result.stdout)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, (result.stdout + result.stderr)[-8000:]
