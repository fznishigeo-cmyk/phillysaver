"""Broad landing appeal: deterministic compatibility rules, no publication changes."""

import copy
import json
import re

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import site
from dealradar.rank import appeal_summary, is_publishable, niche_reason, pick_order, pick_score, score
from tests.test_front_page import pick_deal
from tests.test_publish_site import NOW
from tests.test_seo import db


def legacy_config():
    cfg = load_config()
    cfg.open_box_scarcity = False  # recorded before task 273
    cfg.front_page["appeal"]["want"]["enabled"] = False
    cfg.front_page["appeal"]["season"] = []
    return cfg


@pytest.mark.parametrize("title,reason", [
    ("OtterBox Case for Samsung Galaxy S25 Edge", "named-device accessory"),
    ("Spigen Cover for iPhone 17 Pro Max", "named-device accessory"),
    ("Screen Protector for Google Pixel 10", "named-device accessory"),
    ("Screen Protector for Google Pixel 10a", "named-device accessory"),
    ("Samsung Z Fold7 Case", "named-device accessory"),
    ("Case for Galaxy Z Flip7", "named-device accessory"),
    ("iPad Air 11 Screen Protector", "named-device accessory"),
    ("Cover for iPad Pro 13", "named-device accessory"),
    ("Band for Apple Watch Series 11", "named-device accessory"),
    ("Apple Watch Ultra 2 Strap", "named-device accessory"),
    ("Charger Dock for iPhone 17 Pro Max", "named-device accessory"),
    ("Charging Stand for Pixel 10", "named-device accessory"),
    ("Charging Dock for Apple Watch Series 11", "named-device accessory"),
    ("Sigma 30mm f/1.4 Lens Sony E-Mount", "camera-system accessory"),
    ("Canon RF mount 50mm Lens", "camera-system accessory"),
    ("Nikon Z 24-70mm Camera Lens", "camera-system accessory"),
    ("Lens Adapter for Sony E", "camera-system accessory"),
    ("Camera Mount for Nikon Z", "camera-system accessory"),
    ("Replacement Parts Compatible With Dyson V15", "model-specific replacement part"),
    ("HEPA Filter Replacement for Shark NV360", "model-specific replacement part"),
    ("Ink Cartridges Compatible with Epson 212", "model-specific replacement part"),
    ("Toner Cartridge for Brother HL-L2350DW", "model-specific replacement part"),
    ("Ink for HP OfficeJet Pro 9015", "model-specific replacement part"),
    ("Replacement Bulb for Epson EX3280", "model-specific replacement part"),
    ("Blades Compatible with Cricut Maker 3", "model-specific replacement part"),
    ("Replacement Battery for Dyson V8", "model-specific replacement part"),
    ("Vacuum Belts Replacement for Bissell 1606", "model-specific replacement part"),
    ("Brake Pads for 2015-2022 Ford F-150", "vehicle-specific part"),
    ("Floor Mats Fits 2018–2024 Toyota Camry", "vehicle-specific part"),
])
def test_clear_model_specific_items(title, reason):
    assert niche_reason({"item": title}) == reason


@pytest.mark.parametrize("title", [
    "USB-C Phone Charger", "Charger for iPhone 17 Pro Max", "USB Cable for Pixel 10",
    "USB-C Power Bank for iPhone & Android", "Universal Phone Charging Dock",
    "Charging Dock for iPhone & Android", "Case for iPhone & Android",
    "Universal Case for iPad Air 11", "Samsung Galaxy S25 Edge 256GB Phone",
    "Apple iPhone 17 Pro Max", "Google Pixel 10", "Samsung Z Fold7 Smartphone",
    "iPad Air 11 Tablet", "Apple Watch Series 11", "Sony Wireless Headphones",
    "Headphones for iPhone 17 Pro Max", "Sony Alpha E-Mount Mirrorless Camera",
    "Headphones with Case for iPhone 17 Pro Max", "Galaxy S25 Edge Smartphone with Case",
    "Camera Lens Cleaning Kit", "Universal Camera Mount", "Replacement Filter",
    "Replacement Blades", "LED Light Bulbs", "Ink Cartridge", "Filter Compatible with Dyson",
    "Kitchen Knife Carving Set", "Women's Jeans", "Running Shoes", "Lululemon Handbag",
    "Ionic Hair Dryer", "Chocolate Candy", "LEGO Building Toys", "Cotton Sheets",
    "Coffee Filters 200 Count", "20x25x1 Furnace Filter", "10-Inch Saw Blades",
    "Coffee Filters for Brewing 12 Cups", "Filters for Coffee Brewer 12-Cup",
    "Universal Windshield Wipers", "Bag for Sony E Camera", "Phone Case", "Watch Band",
    "Case for my phone", "Hair Rollers for Long Hair 24 pcs", "Women's Belt for Jeans Dresses Size 32",
    "LED Light Bulbs for Ceiling Fan 60 Watt", "A19 LED Light Bulbs for Home 100W Equivalent", "", None,
])
def test_broad_or_uncertain_items_keep_normal_appeal(title):
    assert niche_reason({"item": title}) == ""


def sample_deals():
    return [pick_deal(dedupe_key="case", item="OtterBox Case for Samsung Galaxy S25 Edge",
                      category="electronics", brand="OtterBox", pct_at_checkout=90),
            pick_deal(dedupe_key="knives", item="Kitchen Knife Carving Set",
                      category="kitchen", brand="Henckels", pct_at_checkout=80),
            pick_deal(dedupe_key="bag", item="Lululemon Handbag",
                      category="fashion", brand="Lululemon", pct_at_checkout=78)]


def test_config_defaults_and_shipped_knobs(tmp_path):
    assert not AppConfig().front_page.get("appeal", {}).get("enabled", False)
    path = tmp_path / "empty.yaml"
    path.write_text("{}")
    assert not load_config(path).front_page.get("appeal", {}).get("enabled", False)
    appeal = load_config().front_page["appeal"]
    assert appeal["enabled"] and appeal["niche_factor"] == .25
    assert appeal["category_factors"] == {
        "home": 1.15, "kitchen": 1.15, "beauty": 1.15, "fashion": 1.15,
        "toys_games": 1.05, "kids_baby": 1.05, "health": 1.05, "grocery": 1.05,
        "electronics": 1.05, "auto": .85}


def test_only_pick_score_changes_and_all_factors_are_configurable():
    cfg = legacy_config()
    deal = sample_deals()[0]
    original = copy.deepcopy(deal)
    raw_rank = score(deal, cfg, NOW)
    publishable = is_publishable(deal, cfg)
    cfg.front_page["appeal"]["enabled"] = False
    raw_pick = pick_score(deal, cfg, NOW)
    cfg.front_page["appeal"] = {"enabled": True, "niche_factor": .4,
                                "category_factors": {"electronics": 1.1}}
    assert pick_score(deal, cfg, NOW) == pytest.approx(raw_pick * .4 * 1.1)
    assert score(deal, cfg, NOW) == raw_rank
    assert is_publishable(deal, cfg) == publishable
    assert deal == original
    # Other categories default to 1.0, including devices themselves.
    phone = dict(deal, item="Samsung Galaxy S25 Edge Phone", category="computers")
    cfg.front_page["appeal"]["enabled"] = False
    phone_score = pick_score(phone, cfg, NOW)
    cfg.front_page["appeal"]["enabled"] = True
    assert pick_score(phone, cfg, NOW) == phone_score


def test_category_tilt_moves_near_ties_but_preserves_clear_winners():
    cfg = legacy_config()
    electronics = pick_deal(category="electronics", pct_at_checkout=80)
    fashion = pick_deal(category="fashion", pct_at_checkout=78)
    winner = pick_deal(category="electronics", pct_at_checkout=99, abs_savings=200)
    cfg.front_page["appeal"]["enabled"] = False
    assert pick_score(fashion, cfg, NOW) < pick_score(electronics, cfg, NOW)
    cfg.front_page["appeal"]["enabled"] = True
    assert pick_score(electronics, cfg, NOW) < pick_score(fashion, cfg, NOW) < pick_score(winner, cfg, NOW)
    # A niche accessory can still be scored when supply is sparse.
    assert pick_score(sample_deals()[0], cfg, NOW) > 0


def titles(path):
    return re.findall(r'<h3 class="title"><a href="[^"]+">([^<]+)</a>', path.read_text())


def tree(out):
    return {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()}


def test_niche_sinks_in_landing_order_but_remains_on_wall_and_other_pages(db, tmp_path, monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = legacy_config()
    cfg.seo_store_page_min_deals = cfg.seo_price_page_min_deals = 1
    source = sample_deals()
    before = copy.deepcopy(source)
    out = tmp_path / "site"
    cfg.front_page["appeal"]["enabled"] = False
    site.render_site(db, cfg, NOW, out_dir=out, deals=source)
    raw = json.loads((out / "deals.json").read_text())["deals"]
    assert [d["dedupe_key"] for d in pick_order(raw, cfg.front_page)] == ["case", "knives", "bag"]
    listing_bytes = {name: data for name, data in tree(out).items()
                     if name.startswith(("stores/", "deals/"))}

    cfg.front_page["appeal"]["enabled"] = True
    result = site.render_site(db, cfg, NOW, out_dir=out, deals=source)
    public = json.loads((out / "deals.json").read_text())["deals"]
    assert [d["dedupe_key"] for d in pick_order(public, cfg.front_page)] == ["knives", "bag", "case"]
    assert {d["dedupe_key"] for d in public} == {d["dedupe_key"] for d in source}
    assert source == before
    assert titles(out / "index.html")[:3] == [source[i]["item"] for i in (1, 2, 0)]
    assert titles(out / "best-deals-today/index.html") == [source[i]["item"] for i in (1, 2, 0)]
    assert listing_bytes == {name: data for name, data in tree(out).items() if name in listing_bytes}
    assert result["appeal"] == {"niche_in_wall": 1, "niche_in_top30": 1,
                                "niche_examples": [source[0]["item"]]}
    assert "appeal" not in json.loads((out / "deals.json").read_text())["meta"]["front_page"]


def test_summary_counts_full_wall_and_first_30_with_three_examples():
    normal = [dict(sample_deals()[1], dedupe_key=f"normal-{i}") for i in range(30)]
    niche = [dict(sample_deals()[0], item=f"Case {i} for Galaxy S25 Edge") for i in range(4)]
    excluded = [dict(niche[0], locked=True), dict(niche[0], status="dead")]
    assert appeal_summary(excluded + niche[:1] + normal + niche[1:]) == {
        "niche_in_wall": 4, "niche_in_top30": 1,
        "niche_examples": [d["item"] for d in niche[:3]]}
    assert appeal_summary([]) == {"niche_in_wall": 0, "niche_in_top30": 0, "niche_examples": []}


def test_niche_leaves_top30_when_broad_choices_are_available(db, tmp_path):
    cfg = legacy_config()
    case = sample_deals()[0]
    categories = ["home", "kitchen", "fashion", "beauty", "health", "toys_games"]
    source = [case] + [pick_deal(dedupe_key=f"broad-{i}", item=f"Everyday Item {i}",
                                brand=f"Brand {i}", category=categories[i % len(categories)])
                       for i in range(36)]
    out = tmp_path / "site"
    cfg.front_page["appeal"]["enabled"] = False
    before = site.render_site(db, cfg, NOW, out_dir=out, deals=source)
    assert before["appeal"]["niche_in_top30"] == 1
    cfg.front_page["appeal"]["enabled"] = True
    after = site.render_site(db, cfg, NOW, out_dir=out, deals=source)
    assert after["published"] == before["published"] == 37
    assert after["appeal"] == {"niche_in_wall": 1, "niche_in_top30": 0,
                               "niche_examples": [case["item"]]}


@pytest.mark.parametrize("mode", ["membership", "open", "support", "shipped"])
def test_disabled_and_omitted_switch_match_every_published_byte(db, tmp_path, monkeypatch, mode):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = legacy_config()
    cfg.support_enabled = mode in {"support", "shipped"}
    cfg.paywall_open_access = mode == "open"
    cfg.paywall_teaser_count = 2
    out = tmp_path / "site"
    appeal = cfg.front_page.pop("appeal")
    site.render_site(db, cfg, NOW, out_dir=out, deals=sample_deals())
    before = tree(out)
    db.meta_set("teaser_recent", "[]")
    cfg.front_page["appeal"] = dict(appeal, enabled=False)
    site.render_site(db, cfg, NOW, out_dir=out, deals=sample_deals())
    assert tree(out) == before
