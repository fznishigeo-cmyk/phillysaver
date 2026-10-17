"""Social posts only for items with plenty of stock (operator, 2026-10-13/14:
"one person buys it, it's no longer available, everyone thinks it's a stupid
website" — the KOBE range hood and the one-left jerseys). One-offs stay on
the website; they never go to Pinterest, Facebook, Instagram or Telegram."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from dealradar.config import load_config
from dealradar.db import DB
from dealradar.publish.social import plenty_of_stock

NOW = datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc)
HOOD = "KOBE Range Hoods RAX2130SQB-WM-2 Wall Mount Range Hood, Stainless Steel"
JERSEY = "Vantage Apparel Heather Jersey Men's Team 1/4 Zip, Tcu Horned Frogs, XL"


@pytest.fixture
def db(tmp_path):
    database = DB(tmp_path / "social-stock.db")
    yield database
    database.close()


@pytest.fixture
def cfg():
    cfg = load_config()
    cfg.keepa_click_price_rule = "off"  # recorded before task 275; stock evidence only
    return cfg


def seed(db, key, *, item="Ninja Air Fryer 4 Qt", pct=60.0, sources=("keepa_amazon_drops",),
         category="home", conditions=()):
    payload = {"dedupe_key": key, "item": item, "store": "Amazon", "category": category,
               "pct_at_checkout": pct, "conditions": list(conditions),
               "sightings": [{"source_name": s, "source_url": f"https://{s}.example/{key}",
                              "seen_at": NOW.isoformat(), "final_price": 20.0} for s in sources]}
    db.insert_deal(dedupe_key=key, store="Amazon", item=item, normalized_item=item,
                   canonical_url="", payload=payload, final_price=20.0, pct_at_checkout=pct,
                   abs_savings=30.0, online_only="true", expires_at="", now=NOW)
    return {k: payload[k] for k in ("dedupe_key", "item", "category", "pct_at_checkout", "conditions")}


def test_shipped_config_requires_stock_evidence(cfg):
    assert cfg.social_require_stock_evidence is True
    assert cfg.social_extreme_pct == 80
    assert cfg.social_exclude_categories == ["clothing"]
    assert {"limited_qty", "clearance_aisle", "pricing_error", "in_store_only"} <= set(
        cfg.social_exclude_conditions)


def test_amazon_sold_item_below_the_extreme_floor_can_go_social(db, cfg):
    assert plenty_of_stock(db, seed(db, "fryer"), cfg)


def test_the_range_hood_one_off_never_goes_social(db, cfg):
    # 82% off, seen only in Amazon's own price lane: a clearance one-off risk.
    assert not plenty_of_stock(db, seed(db, "hood", item=HOOD, pct=82.0), cfg)
    # Two independent deal sites posting it is evidence of broad stock.
    assert plenty_of_stock(db, seed(db, "hood2", item=HOOD, pct=82.0,
                                    sources=("slickdeals", "dealnews")), cfg)


def test_sized_apparel_never_goes_social_whatever_the_evidence(db, cfg):
    for key, item, category in (("jersey", JERSEY, "sports"),
                                ("wedge", "Kenneth Cole REACTION Women's Card Wedge, Soft Gold PU, 8",
                                 "home"),
                                ("tee", "Hanes Men's Tee", "clothing")):
        deal = seed(db, key, item=item, pct=50.0, category=category,
                    sources=("keepa_amazon_drops", "slickdeals", "dealnews"))
        assert not plenty_of_stock(db, deal, cfg), item


@pytest.mark.parametrize("item", [
    "Instant Pot Duo - only 2 left", "Dyson V8 (limited stock)", "Lodge Skillet while supplies last",
    "Ninja Blender - select colors", "Clearance Patio Set", "Yeti Tumbler, final sale"])
def test_scarce_wording_never_goes_social(db, cfg, item):
    deal = seed(db, item[:10], item=item, sources=("keepa_amazon_drops", "slickdeals", "dealnews"))
    assert not plenty_of_stock(db, deal, cfg)


def test_single_deal_site_or_unknown_row_is_not_enough(db, cfg):
    assert not plenty_of_stock(db, seed(db, "woot", sources=("woot",)), cfg)
    assert plenty_of_stock(db, seed(db, "two", sources=("woot", "slickdeals")), cfg)
    assert not plenty_of_stock(db, {"dedupe_key": "missing", "item": "Air Fryer",
                                    "pct_at_checkout": 50.0}, cfg)


def test_one_offs_are_skipped_by_every_social_network(db, cfg, tmp_path):
    # The shared candidate list feeds Pinterest, Facebook, Instagram and Telegram.
    import hashlib
    import json

    from dealradar.publish.social import _candidates

    cfg.site_dir = str(tmp_path / "site")
    rows, paths = [], {}
    for pick, (key, kwargs) in enumerate([("fryer", {}), ("hood", {"item": HOOD, "pct": 82.0}),
                                          ("jersey", {"item": JERSEY, "category": "sports"}),
                                          ("limited", {"conditions": ("limited_qty",)})], 1):
        deal = seed(db, key, **kwargs)
        short = hashlib.sha256(key.encode()).hexdigest()[:16]
        paths[short[:8]] = f"/deal/product-{short[:8]}/"
        page = tmp_path / "site" / "deal" / f"product-{short[:8]}"
        page.mkdir(parents=True)
        (page / "index.html").write_text("published page")
        rows.append({**deal, "key": short, "pick": pick, "status": "alive", "earns": True,
                     "store": "Amazon", "image_url": "https://images.example/p.jpg"})
    (tmp_path / "site" / "deals.json").write_text(json.dumps({"deals": rows}))
    db.meta_set("seo_deal_paths", json.dumps(paths))
    assert [d["dedupe_key"] for d, _ in _candidates(db, cfg, {})] == ["fryer"]
