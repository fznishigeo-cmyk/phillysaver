"""Structured data and image connection hints from published facts only."""

import json
import re
from datetime import timedelta, timezone
from html import unescape
from urllib.parse import urlsplit

import pytest

from dealradar.config import load_config
from dealradar.publish import seo
from tests.test_publish_site import NOW, make_deal
from tests.test_seo import ROOT, cfg, db, _render
from tests.test_seo_deal_pages import _page, _path


def _blocks(html):
    return [json.loads(s) for s in re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]


@pytest.mark.parametrize("conditions,condition", [
    ([], "NewCondition"), (["used"], "UsedCondition"),
    (["open_box"], "UsedCondition"), (["refurbished"], "RefurbishedCondition"),
])
def test_live_product_uses_published_facts_and_utc_publish_date(db, cfg, tmp_path, conditions, condition):
    cfg.paywall_open_access = True
    d = make_deal(final_price=12.345, conditions=conditions, category="kitchen",
                  image_url="https://images.example/item.jpg", brand="Brand & Co")
    # Local October 9 is already October 10 UTC; first_seen_at is older.
    publish = NOW.replace(hour=23, minute=30, tzinfo=timezone(timedelta(hours=-2)))
    _render(db, cfg, tmp_path, [d], now=publish)
    published = json.loads((tmp_path / "deals.json").read_text())["deals"][0]
    html = _page(tmp_path, _path(d)).read_text()
    product, crumbs = _blocks(html)
    assert product == {
        "@context": "https://schema.org", "@type": "Product", "name": published["item"],
        "image": published["image_url"], "brand": {"@type": "Brand", "name": published["brand"]},
        "offers": {"@type": "Offer", "price": f'{published["final_price"]:.2f}',
                   "priceCurrency": "USD", "availability": "https://schema.org/InStock",
                   "url": "https://wowpriced.com" + _path(d),
                   "seller": {"@type": "Organization", "name": published["store"]},
                   "itemCondition": "https://schema.org/" + condition},
    }
    assert f'<strong>${product["offers"]["price"]}</strong>' in html
    assert f'<strong>${product["offers"]["price"]}</strong> USD' in html
    assert "Availability: Live offer" in html
    assert not seo.seo_audit.Page(html, _path(d)).schema_mismatch()
    assert not {"review", "aggregateRating", "ratingValue", "priceValidUntil", "shippingDetails",
                "hasMerchantReturnPolicy"} & set(product)
    assert not {"review", "aggregateRating", "ratingValue", "priceValidUntil", "shippingDetails",
                "hasMerchantReturnPolicy"} & set(product["offers"])
    assert [c["item"] for c in crumbs["itemListElement"]] == [
        "https://wowpriced.com/", "https://wowpriced.com/deals/home-kitchen/",
        "https://wowpriced.com" + _path(d)]


@pytest.mark.parametrize("image", [None, "", "http://images.example/item.jpg", "javascript:alert(1)"])
def test_optional_product_fields_omitted(db, cfg, tmp_path, image):
    cfg.paywall_open_access = True
    d = make_deal(image_url=image, brand="")
    _render(db, cfg, tmp_path, [d])
    product = _blocks(_page(tmp_path, _path(d)).read_text())[0]
    assert product["@type"] == "Product"
    assert "image" not in product and "brand" not in product


@pytest.mark.parametrize("mixed,cap", [(False, 3000), (True, 3000), (True, 5)])
def test_all_emitted_json_ld_breadcrumb_paths_and_card_order(db, cfg, tmp_path, mixed, cap):
    cfg.support_enabled = True
    cfg.seo_deal_pages_max = cap
    cfg.front_page = load_config(ROOT / "config.yaml").front_page if mixed else {}
    deals = [make_deal(dedupe_key=f"k{i}", item=f"Item {i}", codes=[],
                       store="Walmart" if i < 36 else "Target",
                       category="electronics" if i < 36 else "kitchen",
                       conditions=["open_box"] if i % 3 == 0 else []) for i in range(72)]
    _render(db, cfg, tmp_path, deals)
    lists = 0
    for page in tmp_path.rglob("*.html"):
        html = page.read_text()
        for block in _blocks(html):
            if block["@type"] == "BreadcrumbList":
                entries = block["itemListElement"]
                assert [c["position"] for c in entries] == list(range(1, len(entries) + 1))
                assert entries[0]["name"] == "Home" and entries[0]["item"] == "https://wowpriced.com/"
                assert _page(tmp_path, urlsplit(entries[-1]["item"]).path) == page
                assert len(entries) == (3 if "/deal/" in entries[-1]["item"] else 2)
                for crumb in entries:
                    assert _page(tmp_path, urlsplit(crumb["item"]).path).is_file()
            elif block["@type"] == "ItemList":
                lists += 1
                cards = [unescape(p) for p in re.findall(r'<h3 class="title"><a href="([^"]+)"', html)
                         if p.startswith("/deal/")][:30]
                assert block["itemListElement"] == [
                    {"@type": "ListItem", "position": i, "url": "https://wowpriced.com" + path}
                    for i, path in enumerate(cards, 1)]
                assert all(_page(tmp_path, p).is_file() for p in cards)
        if page.parent.parent.name == "deal" or page.parent.name == "best-deals-today" \
                or page.parent.parent.name in {"deals", "stores"}:
            assert any(b["@type"] == "BreadcrumbList" for b in _blocks(html))
    # One ItemList per listing page that shows deal cards (hubs, best-today,
    # stores, prices); a page with no linked cards emits none.
    listing = [p for p in tmp_path.rglob("*.html") if p != tmp_path / "index.html"
               and p.parent.parent.name != "deal"
               and re.search(r'<h3 class="title"><a href="/deal/', p.read_text())]
    assert lists == len(listing) >= 1 + 2  # best-today and the store pages at least


def test_json_ld_keeps_script_closing_text_escaped(db, cfg, tmp_path):
    cfg.paywall_open_access = True
    text = '</script><script>alert("x")</script>'
    d = make_deal(item=text, brand=text, store=text, image_url="https://images.example/" + text)
    _render(db, cfg, tmp_path, [d])
    html = _page(tmp_path, _path(d)).read_text()
    product, crumbs = _blocks(html)
    assert product["name"] == product["brand"]["name"] == product["offers"]["seller"]["name"] == text
    assert product["image"] == d["image_url"] and crumbs["itemListElement"][-1]["name"] == text
    assert '\\u003c/script>' in html and text not in html


def _preconnects(out):
    head = (out / "index.html").read_text().split("</head>")[0]
    return re.findall(r'<link rel="preconnect" href="([^"]+)">', head)


def test_preconnect_uses_only_first_24_published_wall_cards(db, cfg, tmp_path):
    cfg.paywall_open_access = True
    hosts = ["m.media-amazon.com"] * 12 + ["first.example"] * 7 + ["second.example"] * 5 + ["later.example"] * 40
    deals = [make_deal(dedupe_key=f"i{i}", image_url=f"https://{host}/{i}.jpg")
             for i, host in enumerate(hosts)]
    _render(db, cfg, tmp_path, deals)
    assert _preconnects(tmp_path) == ["https://m.media-amazon.com", "https://first.example"]
    # Render again with new leading images; never retain the old host hint.
    _render(db, cfg, tmp_path, deals[24:] + deals[:24])
    assert _preconnects(tmp_path) == ["https://m.media-amazon.com", "https://later.example"]


@pytest.mark.parametrize("host", ["", "wowpriced.com", "www.wowpriced.com", "m.media-amazon.com"])
def test_preconnect_omits_missing_own_and_duplicate_hosts(db, cfg, tmp_path, host):
    cfg.paywall_open_access = True
    d = make_deal(image_url=f"https://{host}/image.jpg" if host else "")
    _render(db, cfg, tmp_path, [d])
    assert _preconnects(tmp_path) == ["https://m.media-amazon.com"]


def test_preconnect_tracks_top_picks_and_excludes_locked_images(db, cfg, tmp_path):
    cfg.front_page = load_config(ROOT / "config.yaml").front_page
    deals = [make_deal(dedupe_key=f"i{i}", item=f"Product {i} title", brand="",
                       store="A" if i < 24 else "B",
                       category="auto" if i < 24 else ("kitchen" if i % 2 else "home"),
                       image_url="https://niche.example/p.jpg" if i < 24 else "https://broad.example/p.jpg")
             for i in range(48)]
    cfg.paywall_open_access = True
    _render(db, cfg, tmp_path, deals)
    assert _preconnects(tmp_path) == ["https://m.media-amazon.com", "https://broad.example"]
    cfg.paywall_open_access = False
    cfg.front_page = {}
    _render(db, cfg, tmp_path / "locked", deals)
    assert _preconnects(tmp_path / "locked") == ["https://m.media-amazon.com", "https://niche.example"]


def test_preconnect_counts_http_image_hosts_but_ignores_non_web_urls(db, cfg, tmp_path):
    cfg.paywall_open_access = True
    deals = [make_deal(dedupe_key=f"i{i}", image_url=url) for i, url in enumerate([
        "http://images.example/item.jpg", "data:image/png;base64,AA", "https://[invalid"])]
    _render(db, cfg, tmp_path, deals)
    assert _preconnects(tmp_path) == ["https://m.media-amazon.com", "https://images.example"]


def test_empty_item_list_is_omitted():
    from dealradar.publish import seo
    assert seo._item_list([], {}, "https://wowpriced.com") == ""
    assert seo._item_list([{"key": "abcdefgh00"}], {}, "https://wowpriced.com") == ""
