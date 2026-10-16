"""Hub inventory and exact product families, entirely offline."""

import copy
import hashlib
import json
import re
import shutil
import subprocess
from datetime import timedelta

import httpx
import pytest

from dealradar.config import AppConfig, load_config
from dealradar.growth import google, report
from dealradar.publish import inventory, seo, seo_audit, site
from dealradar.publish.tracking import head_snippet
from tests.test_publish_site import NOW, make_deal
from tests.test_seo import cfg, db, _render
from tests.test_seo_audit import _dates
from tests.test_seo_deal_pages import _page, _path


def offers(n, **kwargs):
    return [make_deal(dedupe_key=f"inventory{i}", item=f"LEGO Model {i}", codes=[],
                     category="electronics", canonical_url=f"https://www.walmart.com/ip/{10000+i}",
                     **kwargs) for i in range(n)]


def family_offers():
    return [make_deal(dedupe_key=f"airpods{i}", item="Apple AirPods Pro 2", brand="Apple",
                     store=store, codes=[], category="electronics", final_price=10 + i,
                     canonical_url=f"https://{store.lower()}.example/products/airpods")
            for i, store in enumerate(("Walmart", "Target", "Best Buy"))]


def clean_audit(result):
    audit = result["seo_audit"]
    assert all(audit[key] == 0 for key in ("empty_indexable", "schema_mismatches", "broken_internal_links",
               "sitemap_missing", "sitemap_extra", "sitemap_duplicates")), audit


def test_inventory_config_switches_floor_and_shipped_values(tmp_path):
    default, shipped = AppConfig(), load_config()
    assert not inventory.enabled(default) and default.traffic_product_family_min_offers == 3
    assert shipped.traffic_hub_pagination_enabled and shipped.traffic_product_families_enabled
    path = tmp_path / "config.yaml"
    path.write_text("traffic:\n  hub_pagination_enabled: true\n  product_family_min_offers: 7\n")
    cfg = load_config(path)
    assert cfg.traffic_hub_pagination_enabled and not cfg.traffic_product_families_enabled
    assert cfg.traffic_product_family_min_offers == 7
    path.write_text("traffic:\n  product_families_enabled: true\n  product_family_min_offers: 0\n")
    assert load_config(path).traffic_product_family_min_offers == 2


def test_pagination_crawl_counts_self_canonicals_floors_and_clean_removal(db, cfg, tmp_path):
    cfg.support_enabled = cfg.traffic_hub_pagination_enabled = True
    cfg.seo_hub_min_items = 4
    cfg.seo_store_page_min_deals = 3
    cfg.seo_price_page_min_deals = 2
    cfg.seo_price_pages = [25]
    deals = offers(123)
    clean_audit(_render(db, cfg, tmp_path, deals))
    dates = _dates(tmp_path)
    for root, family, floor in (("/deals/electronics/", "category", 4),
                                ("/stores/walmart/", "store", 3),
                                ("/deals/under-25/", "price", 2)):
        seen = []
        for number, count in ((1, 60), (2, 60), (3, 3)):
            path = inventory.page_path(root, number)
            html = _page(tmp_path, path).read_text()
            page = seo_audit.Page(html, path)
            assert page.live_offers() == count and seo_audit.page_family(path) == family
            assert page.indexable == (count >= floor)
            assert ("https://wowpriced.com" + path in dates) == page.indexable
            assert f'<link rel="canonical" href="https://wowpriced.com{path}">' in html
            assert f'href="{path}" aria-current="page">Page {number} of 3</a>' in html
            if number < 3:
                assert f'href="{root}page/{number+1}/" data-hub-page="{family}" rel="next"' in html
            if number > 1:
                assert f'href="{inventory.page_path(root, number-1)}" data-hub-page="{family}" rel="prev"' in html
                assert f"Page {number}" in page.first(tag="h1").text()
            assert "/page/1/" not in html and "/page/4/" not in html and "<img" not in html
            seen += [page.first(tag="a", scope=page.first(cls="title", scope=card)).attrs["href"]
                     for card in page.cards]
        assert len(seen) == len(set(seen)) == 123
    # Exactly 60 needs no page/2 or pagination links, even in a reused folder.
    clean_audit(_render(db, cfg, tmp_path, deals[:60]))
    assert not list(tmp_path.glob("*/**/page/*/index.html"))
    assert "/page/" not in (tmp_path / "sitemap.xml").read_text()
    assert 'class="inventory-nav"' not in _page(tmp_path, "/deals/electronics/").read_text()
    # Entire store and price hubs also disappear below their existing floor.
    clean_audit(_render(db, cfg, tmp_path, []))
    assert not (tmp_path / "stores/walmart").exists()
    assert not (tmp_path / "deals/under-25").exists()


def test_pagination_facts_lastmod_and_idempotency(db, cfg, tmp_path, monkeypatch):
    cfg.support_enabled = cfg.traffic_hub_pagination_enabled = True
    cfg.seo_price_pages = [25]
    cfg.seo_hub_min_items = cfg.seo_store_page_min_deals = cfg.seo_price_page_min_deals = 1
    deals = offers(61)
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    _render(db, cfg, tmp_path, deals)
    dates = _dates(tmp_path)
    page = _page(tmp_path, "/stores/walmart/page/2/")
    before = page.read_bytes()
    _render(db, cfg, tmp_path, deals, now=NOW + timedelta(days=1))
    assert page.read_bytes() == before and _dates(tmp_path) == dates
    # Update a visible fact on page 2 without changing the ranking or hub median.
    parsed = seo_audit.Page(before.decode(), "/stores/walmart/page/2/")
    link = parsed.first(tag="a", scope=parsed.first(cls="title", scope=parsed.cards[0])).attrs["href"]
    changed = copy.deepcopy(deals)
    target = next(d for d in changed if _path(d) == link)
    target["final_price"] = 9
    clean_audit(_render(db, cfg, tmp_path, changed, now=NOW + timedelta(days=2)))
    newer = _dates(tmp_path)
    assert newer["https://wowpriced.com/stores/walmart/page/2/"] == (NOW + timedelta(days=2)).isoformat()
    assert newer["https://wowpriced.com/stores/walmart/"] == dates["https://wowpriced.com/stores/walmart/"]
    _render(db, cfg, tmp_path, changed, now=NOW + timedelta(days=2))
    assert _dates(tmp_path) == newer


def test_family_pages_facts_cards_links_floor_removal_and_duplicate_offers(db, cfg, tmp_path):
    cfg.support_enabled = cfg.traffic_product_families_enabled = True
    deals = family_offers()
    # Another source/row for the same store/product does not add a fourth offer.
    duplicate = dict(deals[0], dedupe_key="another-source", final_price=9)
    result = _render(db, cfg, tmp_path, deals + [duplicate])
    clean_audit(result)
    pages = list((tmp_path / "products").glob("*/index.html"))
    assert len(pages) == 1 and result["seo_audit"]["indexable_by_family"]["product_family"] == 1
    path = "/" + pages[0].parent.relative_to(tmp_path).as_posix() + "/"
    html = pages[0].read_text()
    page = seo_audit.Page(html, path)
    assert page.live_offers() == 3 and "3 live offers. Lowest current price: $9.00 USD." in html
    assert "Stores: Best Buy, Target, Walmart." in html and "<img" not in html
    assert f'<link rel="canonical" href="https://wowpriced.com{path}">' in html
    assert "https://wowpriced.com" + path in _dates(tmp_path)
    for d in deals + [duplicate]:
        detail = _page(tmp_path, _path(d)).read_text()
        assert f'href="{path}" data-product-family' in detail
    # Every rendered offer is the unchanged shared hub card.
    payload = json.loads((tmp_path / "deals.json").read_text())["deals"]
    paths = {d["key"][:8]: d["deal_path"] for d in payload}
    expected = {seo.card_html(d, paths, NOW) for d in payload}
    for card_html in re.findall(r'<article class="card.*?</article>', html, re.S):
        assert card_html in expected
    dates, original = _dates(tmp_path), pages[0].read_bytes()
    _render(db, cfg, tmp_path, list(reversed(deals + [duplicate])), now=NOW + timedelta(days=1))
    assert pages[0].read_bytes() == original and _dates(tmp_path)["https://wowpriced.com" + path] == dates["https://wowpriced.com" + path]
    # Two distinct listings plus duplicate evidence still fall below three.
    clean_audit(_render(db, cfg, tmp_path, deals[:2] + [duplicate]))
    assert not (tmp_path / "products").exists() and "/products/" not in (tmp_path / "sitemap.xml").read_text()
    assert "data-product-family" not in _page(tmp_path, _path(deals[0])).read_text()


def test_exact_family_grouping_preserves_model_punctuation_size_pack_and_brand(cfg):
    cfg.traffic_product_families_enabled = True
    source = family_offers()
    # Only case and whitespace are insignificant. Similar names must never join.
    source[1]["item"] = "  apple  airpods pro 2  "
    for i, changes in enumerate((
            {"item": "Apple AirPods Pro 3"}, {"item": "Case for Apple AirPods Pro 2"},
            {"item": "Apple AirPods Pro 2 2-pack"}, {"quantity_or_unit": "2-pack"},
            {"brand": "Other"}, {"model": "Different"}, {"item": "Apple AirPods Pro 2.0"},
            {"item": "Apple AirPods Pro 20"}, {"item": "Apple AirPods Pro 2 Black"},
            {"item": "Apple AirPods Pro 2 USB-C"})):
        source.append(dict(source[0], dedupe_key=f"near{i}", **changes))
    live = [dict(d, key=hashlib.sha256(d["dedupe_key"].encode()).hexdigest()) for d in source]
    pages, members = inventory.product_families(cfg, live, source)
    assert len(pages) == 1 and len(members) == 3
    assert {d["dedupe_key"] for _, rows in pages.values() for d in rows} == {"airpods0", "airpods1", "airpods2"}
    # Punctuation-stripped slugs may collide; identity hashes must not.
    source = family_offers() + [dict(d, dedupe_key=d["dedupe_key"]+"x", item="Apple AirPods-Pro 2") for d in family_offers()]
    live = [dict(d, key=d["dedupe_key"]) for d in source]
    pages, _ = inventory.product_families(cfg, live, source)
    assert len(pages) == 2


@pytest.mark.parametrize("changes", [{"brand": ""}, {"status": "dead"}, {"locked": True},
    {"link_shared": True}, {"currency": "EUR"}, {"final_price": None}, {"flags": ["range"]}, {"flags": ["per_unit"]}])
def test_family_ambiguous_nonpublic_or_unpriced_offers_cannot_meet_floor(cfg, changes):
    cfg.traffic_product_families_enabled = True
    source = family_offers()
    source[0].update(changes)
    live = [dict(d, key=d["dedupe_key"]) for d in source]
    assert inventory.product_families(cfg, live, source) == ({}, {})


def test_asin_fallback_requires_exact_title_and_distinct_store_condition(cfg):
    cfg.traffic_product_families_enabled = True
    source = [dict(d, brand="", asin="B012345678") for d in family_offers()]
    live = [dict(d, key=d["dedupe_key"]) for d in source]
    assert len(inventory.product_families(cfg, live, source)[0]) == 1
    source[0]["asin"] = "B012345679"
    assert inventory.product_families(cfg, live, source) == ({}, {})
    source[0]["asin"] = "B012345678"
    source[0]["item"] = live[0]["item"] = "Apple AirPods Pro 3"
    assert inventory.product_families(cfg, live, source) == ({}, {})
    # One product can have three genuinely distinct physical-condition offers.
    source = [dict(d, store="Walmart", canonical_url="https://www.walmart.com/ip/12345",
                   conditions=condition) for d, condition in zip(family_offers(), ([], ["used"], ["open_box"]))]
    live = [dict(d, key=d["dedupe_key"]) for d in source]
    assert len(inventory.product_families(cfg, live, source)[0]) == 1
    source[2]["canonical_url"] += "?utm_source=another&ref=test"
    live[2]["conditions"] = source[2]["conditions"] = ["used", "coupon"]
    assert inventory.product_families(cfg, live, source) == ({}, {})


def test_family_overflow_and_configured_floor_remove_every_page_and_link(db, cfg, tmp_path):
    cfg.support_enabled = cfg.traffic_product_families_enabled = True
    # Family inventory stays reachable even when ordinary hub pagination is off.
    deals = [dict(d, item="LEGO Model 123") for d in offers(61)]
    clean_audit(_render(db, cfg, tmp_path, deals))
    root = next((tmp_path / "products").glob("*/index.html"))
    path = "/" + root.parent.relative_to(tmp_path).as_posix() + "/"
    second = _page(tmp_path, path + "page/2/")
    assert seo_audit.Page(root.read_text(), path).live_offers() == 60
    assert seo_audit.Page(second.read_text(), path + "page/2/").live_offers() == 1
    assert 'content="noindex,follow"' in second.read_text()
    assert f'href="{path}page/2/"' in root.read_text()
    assert "https://wowpriced.com" + path in _dates(tmp_path)
    assert "https://wowpriced.com" + path + "page/2/" not in _dates(tmp_path)
    cfg.traffic_product_family_min_offers = 62
    clean_audit(_render(db, cfg, tmp_path, deals))
    assert not (tmp_path / "products").exists()
    assert "data-product-family" not in _page(tmp_path, _path(deals[0])).read_text()
    assert db.indexnow_pages()["https://wowpriced.com" + path]["content_hash"] == "removed"


def test_locked_inventory_and_ended_pages_never_link_to_families(db, cfg, tmp_path):
    cfg.traffic_product_families_enabled = cfg.traffic_hub_pagination_enabled = True
    cfg.paywall_teaser_count = 2
    deals = family_offers() + offers(61)
    _render(db, cfg, tmp_path, deals)
    assert not (tmp_path / "products").exists()
    assert "/page/" not in (tmp_path / "sitemap.xml").read_text()
    cfg.support_enabled = True
    _render(db, cfg, tmp_path, deals)
    deals[0].update(status="dead", death="confirmed", died_at=NOW.isoformat())
    clean_audit(_render(db, cfg, tmp_path, deals))
    assert not (tmp_path / "products").exists()
    assert 'data-product-family' not in _page(tmp_path, _path(deals[0])).read_text()


def test_off_restores_every_byte_including_existing_traffic_and_tracking(db, cfg, tmp_path, monkeypatch):
    cfg.support_enabled = cfg.traffic_feed_enabled = cfg.traffic_share_enabled = cfg.traffic_related_hubs_enabled = True
    cfg.tracking = {"ga4_measurement_id": "G-ABC123XYZ9"}
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    deals = offers(61) + family_offers()
    out = tmp_path / "site"
    _render(db, cfg, out, deals)
    snapshot = lambda: {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    before = snapshot()
    cfg.traffic_product_families_enabled = cfg.traffic_hub_pagination_enabled = True
    clean_audit(_render(db, cfg, out, deals))
    assert before != snapshot() and (out / "products").exists()
    # The post-pass is itself idempotent.
    after = snapshot()
    cards = lambda data: re.findall(rb'<article class="card.*?</article>', data, re.S)
    assert cards(before["index.html"]) == cards(after["index.html"])
    assert json.loads(before["deals.json"])["deals"] == json.loads(after["deals.json"])["deals"]
    inventory.publish(out, cfg)
    assert snapshot() == after
    cfg.traffic_product_families_enabled = cfg.traffic_hub_pagination_enabled = False
    _render(db, cfg, out, deals)
    assert before == snapshot()


def test_new_switches_off_keep_pre_task_shipped_bytes(tmp_path, monkeypatch):
    from tests.test_pwa import baseline_config, sample_deals, tree_hash
    from tests.test_support import NOW as FIXED, render

    cfg = baseline_config("shipped")
    cfg.unconfirmed_label_enabled = False  # this pin predates the task 094 display option
    cfg.seo_product_pages_enabled = False  # predates product pages
    cfg.traffic_feed_details, cfg.traffic_feed_max_items = True, 50  # predates the RSS trim
    cfg.non_amazon_freshness_enabled = False  # and task 102's independent display guard
    cfg.raw["first_party_clicks"]["enabled"] = False  # and the click signal (on 2026-10-14)
    cfg.front_page["top_picks"] = {k: v for k, v in cfg.front_page["top_picks"].items()
                                   if k != "non_amazon_max_age_h"}  # predates the pick age
    cfg.traffic_product_families_enabled = cfg.traffic_hub_pagination_enabled = False
    monkeypatch.setattr(site, "util_now", lambda: FIXED)
    out, _, _, _ = render(tmp_path, cfg, sample_deals())
    assert tree_hash(out) == "4c206a91faa99b602ea490d54e4c756f5da229c1c16c839868fb8964fea8ccf9"


def test_inventory_node_links_and_phone_styles(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    script = tmp_path / "deal-actions.js"
    script.write_text(inventory.deal_actions((inventory.TEMPLATES / "deal-actions.js").read_text()))
    result = subprocess.run(["node", "tests/hub_inventory_logic.cjs", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    css = (inventory.TEMPLATES / "inventory.css").read_text()
    assert all(rule in css for rule in ("flex-wrap:wrap", "min-height:44px", "min-width:44px",
        "max-width:100%", "overflow-wrap:anywhere", ":focus-visible"))
    assert ".card" not in css and ".grid" not in css
    malicious = '<script>&"' * 100
    html = inventory.family_link({"key": "one", "status": "alive"}, {"one": ("/products/safe/", malicious)})
    assert malicious not in html and 'href="/products/safe/"' in html


@pytest.mark.parametrize("ga4", [False, True])
def test_inventory_dispatch_is_ga4_only_with_bounded_methods(tmp_path, ga4):
    from tests.test_tracking import SAMPLE, HARNESS, _script

    ids = dict(SAMPLE)
    if not ga4:
        ids.pop("ga4_measurement_id")
    cfg = AppConfig(tracking=ids, traffic_product_families_enabled=True, traffic_hub_pagination_enabled=True)
    valid = [("hub_page_click", method) for method in ("category", "store", "price", "product_family")]
    valid.append(("product_family_click", "deal"))
    valid.extend((name, "product_family") for name in ("deal_code_copy", "requirements_open", "requirements_deal_click"))
    calls = [[name, {"method": method, "url": "PRIVATE", "item": "PRIVATE"}] for name, method in valid]
    calls += [["hub_page_click", {"method": "PRIVATE"}], ["product_family_click", {"method": "store"}]]
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
        assert sent == ([["event", event, {"method": method, "send_to": SAMPLE["ga4_measurement_id"]}]
                         for event, method in valid] if ga4 else [])
        assert "PRIVATE" not in json.dumps(results[name])
        for tag in ("fbq", "ttq", "pintrk", "uetq"):
            assert results[name][tag] == (results["baseline"][tag] if name == "open" else None)


def test_search_console_families_include_hub_pages_and_products():
    rows = [{"keys": ["https://wowpriced.com" + path, "MOBILE"], "clicks": 2, "impressions": 10}
            for path in ("/deals/electronics/page/2/", "/stores/walmart/page/10/",
                         "/deals/under-25/page/3/", "/products/apple-airpods-abc/",
                         "/products/apple-airpods-abc/page/2/")]
    def handler(request):
        body = json.loads(request.content)
        return httpx.Response(200, json={"rows": rows if body.get("dimensions") == ["page", "device"] else []})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        data = google.search_console(client, "token", "sc-domain:wowpriced.com", (NOW - timedelta(days=7)).date(), NOW.date())
    counts = {row["family"]: row for row in data["by_family"]}
    assert counts["product_family"]["clicks"] == 4 and counts["product_family"]["impressions"] == 20
    for family in ("category", "store", "price"):
        assert counts[family]["clicks"] == 2
    assert counts["deal"]["clicks"] is None
    _, text = report.build_report(AppConfig(), {"search_console": data}, {}, NOW.date())
    assert "product_family: clicks 4" in text
