"""Read emitted pages offline; deliberately corrupt facts, links and sitemap."""

import json
import re
from datetime import timedelta
from xml.etree import ElementTree as ET

import pytest

from dealradar.config import load_config
from dealradar.publish import seo_audit
from tests.test_publish_site import NOW, make_deal
from tests.test_seo import ROOT, cfg, db, _render, _seed_week
from tests.test_seo_deal_pages import _path, _page


def _dates(out):
    xml = ET.parse(out / "sitemap.xml")
    return {row.find("{*}loc").text: row.find("{*}lastmod").text for row in xml.findall("{*}url")}


def test_audit_fixture_counts_sparse_schema_broken_link_and_ended(db, cfg, tmp_path, caplog):
    cfg.support_enabled = True
    cfg.seo_hub_min_items = 10
    deals = [make_deal(dedupe_key=f"audit{i}", item=f"Offer {i}", store="Walmart" if i < 15 else "Target",
                       category="electronics" if i < 15 else "kitchen", codes=[],
                       image_url=f"https://images.example/{i}.jpg") for i in range(20)]
    ended = make_deal(dedupe_key="ended", status="dead", death="confirmed", died_at=NOW.isoformat())
    res = _render(db, cfg, tmp_path, deals + [ended])
    clean = res["seo_audit"]
    assert clean["indexable_by_family"] == {"home": 1, "category": 1, "store": 1,
                                             "price": 3, "best_today": 1, "deal": 20, "product_family": 0, "product": 0}
    assert clean["sitemap_urls"] == clean["emitted_indexable"] == 36  # + disclosure.html (task 074)
    assert "seo_audit" not in json.loads((tmp_path / "deals.json").read_text())
    assert all(clean[k] == 0 for k in ("empty_indexable", "schema_mismatches", "broken_internal_links",
                                       "sitemap_missing", "sitemap_extra", "sitemap_duplicates"))
    ended_html = _page(tmp_path, _path(ended)).read_text()
    assert 'content="noindex"' in ended_html and '"@type": "Product"' not in ended_html

    # Make the five-offer hub indexable, disagree with a visible sale price,
    # break an internal href and replace a live sitemap URL with an ended URL.
    sparse = _page(tmp_path, "/deals/home-kitchen/")
    sparse.write_text(sparse.read_text().replace('<meta name="robots" content="noindex,follow">', ""))
    live = _page(tmp_path, _path(deals[0]))
    live.write_text(live.read_text().replace('"price": "10.00"', '"price": "99.00"').replace(
        "</main>", '<a href="/missing.html?private=DO-NOT-LOG#secret">broken</a></main>'))
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text(sitemap.read_text().replace("https://wowpriced.com" + _path(deals[0]),
                                                 "https://wowpriced.com" + _path(ended)))
    with caplog.at_level("INFO", logger="dealradar.publish.seo"):
        bad = seo_audit.audit(tmp_path, cfg, "https://wowpriced.com")
    assert bad["indexable_by_family"] == {**clean["indexable_by_family"], "category": 2}
    assert bad["empty_indexable"] == bad["schema_mismatches"] == bad["broken_internal_links"] == 1
    assert bad["sitemap_urls"] == 36 and bad["emitted_indexable"] == 37
    assert bad["sitemap_missing"] == 2 and bad["sitemap_extra"] == 1
    assert bad["offending_paths"]["broken_internal_links"] == ["/missing.html"]
    assert "DO-NOT-LOG" not in caplog.text and "?private=" not in caplog.text
    assert "https://" not in caplog.text
    print("Task 070 fault fixture:", {k: v for k, v in bad.items() if k != "offending_paths"})


def test_current_public_floor_noindex_removal_and_return(db, cfg, tmp_path):
    _seed_week(db, 12)
    cfg.paywall_open_access = True
    deals = [make_deal(dedupe_key=f"live{i}", category="electronics", codes=[]) for i in range(3)]
    hub = _page(tmp_path, "/deals/electronics/")
    full = _render(db, cfg, tmp_path, deals)
    assert full["seo_audit"]["indexable_by_family"]["category"] == 1
    _render(db, cfg, tmp_path, deals[:2], now=NOW + timedelta(hours=1))
    assert 'content="noindex,follow"' in hub.read_text()
    assert "12 new deals" in hub.read_text()  # history cannot qualify the page
    assert "https://wowpriced.com/deals/electronics/" not in _dates(tmp_path)
    _render(db, cfg, tmp_path, deals, now=NOW + timedelta(hours=2))
    assert 'name="robots"' not in hub.read_text()
    assert _dates(tmp_path)["https://wowpriced.com/deals/electronics/"] == (NOW + timedelta(hours=2)).isoformat()
    cfg.paywall_open_access = False
    locked = _render(db, cfg, tmp_path, deals)
    assert 'content="noindex,follow"' in hub.read_text()  # only two visible offers; locked does not count
    assert locked["seo_audit"]["empty_indexable"] == 0


def test_empty_home_and_best_return_and_family_minimum_config(db, cfg, tmp_path):
    cfg.paywall_open_access = True
    empty = _render(db, cfg, tmp_path, [])
    for family in ("home", "best_today", "deal"):
        assert empty["seo_audit"]["indexable_by_family"][family] == 0
    for page in (tmp_path / "index.html", _page(tmp_path, "/best-deals-today/")):
        assert 'content="noindex,follow"' in page.read_text()
    assert "https://wowpriced.com/" not in _dates(tmp_path)
    full = _render(db, cfg, tmp_path, [make_deal(codes=[])])
    assert full["seo_audit"]["indexable_by_family"]["home"] == 1
    assert full["seo_audit"]["indexable_by_family"]["best_today"] == 1
    configured = load_config(ROOT / "config.yaml")
    assert seo_audit.minimums(configured) == {"home": 1, "category": 10, "store": 15,
                                             "price": 10, "best_today": 1, "deal": 1, "product_family": 3, "product": 0,
                                             "event": 10}
    path = tmp_path / "custom.yaml"
    path.write_text("seo:\n  home_min_items: 2\n  best_today_min_items: 3\n  price_page_min_deals: 4\n  lastmod_retention_days: 2\n")
    custom = load_config(path)
    assert custom.seo_home_min_items == 2 and custom.seo_best_today_min_items == 3
    assert custom.seo_price_page_min_deals == 4 and custom.seo_lastmod_retention_days == 2


def test_lastmod_and_html_stable_across_midnight_then_price_and_title_change(db, cfg, tmp_path):
    cfg.support_enabled = True
    d = make_deal(codes=[], conditions=["used"], category="electronics", price_since="2026-10-09T12:00:00+00:00")
    deals = [dict(d, dedupe_key=f"p{i}", item=f"Product {i}") for i in range(16)]
    _render(db, cfg, tmp_path / "before", deals)
    before = {str(p.relative_to(tmp_path / "before")): p.read_bytes()
              for p in (tmp_path / "before").rglob("*.html")}
    dates = _dates(tmp_path / "before")
    _render(db, cfg, tmp_path / "same", deals, now=NOW + timedelta(days=1))
    assert before == {str(p.relative_to(tmp_path / "same")): p.read_bytes()
                      for p in (tmp_path / "same").rglob("*.html")}
    assert (tmp_path / "before/sitemap.xml").read_bytes() == (tmp_path / "same/sitemap.xml").read_bytes()
    change_time = NOW + timedelta(days=1, minutes=10)
    deals[0] = dict(deals[0], final_price=12.0, pct_at_checkout=76.0, abs_savings=38.0)
    changed = _render(db, cfg, tmp_path / "changed", deals, now=change_time)
    assert changed["seo_audit"]["schema_mismatches"] == 0
    price_dates = _dates(tmp_path / "changed")
    for path in (_path(deals[0]), "/", "/deals/electronics/", "/stores/walmart/", "/deals/under-25/"):
        assert price_dates["https://wowpriced.com" + path] == change_time.isoformat()
    assert any(price_dates["https://wowpriced.com" + _path(d)] == dates["https://wowpriced.com" + _path(d)]
               for d in deals[1:])
    assert price_dates["https://wowpriced.com/about.html"] == dates["https://wowpriced.com/about.html"]
    old_path = _path(deals[0])
    rename_time = change_time + timedelta(minutes=10)
    deals[0] = dict(deals[0], item="Renamed offer")
    _render(db, cfg, tmp_path / "renamed", deals, now=rename_time)
    assert '<h1>Renamed offer</h1>' in _page(tmp_path / "renamed", old_path).read_text()
    assert _dates(tmp_path / "renamed")["https://wowpriced.com" + old_path] == rename_time.isoformat()


@pytest.mark.parametrize("field,bad", [("price", "99.00"), ("priceCurrency", "EUR"),
    ("availability", "https://schema.org/OutOfStock"), ("itemCondition", "https://schema.org/UsedCondition"),
    ("name", "Other product")])
def test_every_required_schema_field_is_compared_to_visible_text(db, cfg, tmp_path, field, bad):
    cfg.paywall_open_access = True
    d = make_deal(codes=[], conditions=[], image_url="https://images.example/item.jpg")
    _render(db, cfg, tmp_path, [d])
    page = _page(tmp_path, _path(d))
    html = page.read_text()
    product = re.search(r'<script type="application/ld\+json">(.*?)</script>', html).group(1)
    data = json.loads(product)
    (data if field == "name" else data["offers"])[field] = bad
    page.write_text(html.replace(product, json.dumps(data)))
    assert seo_audit.audit(tmp_path, cfg, "https://wowpriced.com")["schema_mismatches"] == 1


def test_long_name_shortened_for_google_still_matches_the_visible_name(db, cfg, tmp_path):
    """Live (2026-10-14): names over Google's 150 characters are shortened
    in the markup; the audit flagged 224 such pages as mismatched."""
    cfg.paywall_open_access = True
    item = "Lifetime Vinyl Standard Cathedral Top Center Mount Shutter " * 4
    d = make_deal(item=item.strip(), codes=[], conditions=[],
                  image_url="https://images.example/item.jpg")
    _render(db, cfg, tmp_path, [d])
    html = _page(tmp_path, _path(d)).read_text()
    data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html).group(1))
    assert len(data["name"]) <= 150 and data["name"].endswith("…")
    assert seo_audit.audit(tmp_path, cfg, "https://wowpriced.com")["schema_mismatches"] == 0


def test_fingerprint_retention_and_nonfacts_do_not_move_dates(db, cfg):
    html = '<html><head><title>Offer</title></head><body><main><h1>Product</h1><p>$10.00</p></main><footer>2026</footer><script>build=1</script></body></html>'
    page = seo_audit.Page(html, "/deal/a-12345678/")
    dates = seo_audit.fact_lastmods(db, cfg, NOW, "https://wowpriced.com", {page.path: page})
    later = seo_audit.Page(html.replace("2026", "2027").replace("build=1", "build=2"), page.path)
    assert seo_audit.fact_lastmods(db, cfg, NOW + timedelta(days=1), "https://wowpriced.com", {page.path: later}) == dates
    cfg.seo_lastmod_retention_days = 2
    seo_audit.fact_lastmods(db, cfg, NOW + timedelta(days=2), "https://wowpriced.com", {})
    assert len(json.loads(db.meta_get("seo_page_facts"))) == 1
    seo_audit.fact_lastmods(db, cfg, NOW + timedelta(days=4), "https://wowpriced.com", {})
    assert db.meta_get("seo_page_facts") == "{}"
    db.meta_set("seo_page_facts", "corrupt")
    assert seo_audit.fact_lastmods(db, cfg, NOW, "https://wowpriced.com", {page.path: page}) == dates


def test_audit_does_not_accept_a_stale_page_as_an_emitted_internal_target(db, cfg, tmp_path):
    cfg.paywall_open_access = True
    _render(db, cfg, tmp_path, [make_deal(codes=[])])
    paths = list(seo_audit.read_pages(tmp_path))
    stale = tmp_path / "stale.html"
    stale.write_text("<title>Old file</title>")
    home = tmp_path / "index.html"
    home.write_text(home.read_text().replace("</body>", '<a href="stale.html?secret=PRIVATE">old</a></body>'))
    check = seo_audit.audit(tmp_path, cfg, "https://wowpriced.com", paths)
    assert check["broken_internal_links"] == 1
    assert check["offending_paths"]["broken_internal_links"] == ["/stale.html"]


def test_minimums_use_visible_cards_even_when_total_inventory_exceeds_the_cap(db, cfg, tmp_path):
    cfg.support_enabled = True
    cfg.seo_home_min_items = 49
    cfg.seo_hub_min_items = cfg.seo_store_page_min_deals = cfg.seo_price_page_min_deals = 61
    cfg.seo_best_today_min_items = 31
    cfg.seo_deal_page_min_items = 2
    floors = cfg.min_pct, cfg.site_show_from_pct
    rows = [make_deal(dedupe_key=f"cap{i}", category="electronics", codes=[]) for i in range(70)]
    result = _render(db, cfg, tmp_path, rows)
    assert result["seo_audit"]["indexable_by_family"] == dict.fromkeys(seo_audit.FAMILIES, 0)
    assert result["seo_audit"]["empty_indexable"] == result["seo_audit"]["broken_internal_links"] == 0
    assert (cfg.min_pct, cfg.site_show_from_pct) == floors


def test_fingerprint_state_is_bounded_and_always_keeps_current_urls(db, cfg, monkeypatch):
    monkeypatch.setattr(seo_audit, "_MAX_FACT_URLS", 3)
    page = lambda path: seo_audit.Page('<html><title>Facts</title><main>Fact</main></html>', path)
    pages = {f"/deal/{i}/": page(f"/deal/{i}/") for i in range(3)}
    seo_audit.fact_lastmods(db, cfg, NOW, "https://wowpriced.com", pages)
    seo_audit.fact_lastmods(db, cfg, NOW + timedelta(hours=1), "https://wowpriced.com", {"/deal/new/": page("/deal/new/")})
    state = json.loads(db.meta_get("seo_page_facts"))
    assert len(state) == 3 and "https://wowpriced.com/deal/new/" in state
    assert all(len(row) == 3 and re.fullmatch(r"[0-9a-f]{24}", row[0]) for row in state.values())
