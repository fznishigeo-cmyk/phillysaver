"""Explicit Renewed labels cannot enter Keepa's NEW lane or evade used-off."""

import hashlib
import json
from dataclasses import asdict

import pytest

from dealradar import amazon_used, rank, util, verify
from dealradar.config import AppConfig, load_config
from dealradar.publish import site
from tests.test_keepa import FakeKeepa, NOW, SRC, _adapter, _amazon_row
from tests.test_price_accuracy_engine import db, seed


LABELED = [
    "Widget (Renewed)", "Widget (Refurbished)", "Widget [Renewed]",
    "Widget Renewed Premium", "Amazon Renewed Widget", "Widget (Renewed Premium)",
    "Widget - Renewed", "Widget – Refurbished", "Widget — Renewed",
    "Widget | Renewed", "Widget: Refurbished", "Widget [rEnEwEd PrEmIuM]",
]
ORDINARY = [
    "philosophy Renewed Hope in a Jar Moisturizer", "Renewed Skin Serum",
    "Refurbished-look Wood Sign", "Smart Plug, Can Be Used With Alexa",
    "Microfiber Cleaning Cloths, Can Be Used On Glass and Screens", "Used Book Sale Tote",
    "Renewed Hope Cream (Travel Size)", "Widget - Renewed Look", "Widget [Renewed Look]",
]


def config(lock=False, *, used=False):
    cfg = AppConfig()
    cfg.amazon_renewed_title_lock = lock
    cfg.amazon_used_enabled = used
    cfg.keepa_focus_categories = []
    cfg.keepa_sweep_enabled = False
    return cfg


def fetch(title, cfg, monkeypatch, *, warehouse=False):
    monkeypatch.setenv("KEEPA_KEY", "offline-key")
    row = dict(_amazon_row(1), title=title)
    if warehouse:
        row.update(current=[5000] + [-1] * 8 + [1000], avg=[10000])
    fake = FakeKeepa(amazon=[row], fresh=[row] if warehouse else [])
    return _adapter(fake, cfg).fetch_posts(SRC, None, None, cfg, NOW, None)


def titled_seed(db, cfg, title, **changes):
    deal = seed(db, cfg, **changes)
    db.conn.execute("UPDATE deals SET item=? WHERE dedupe_key=?", (title, deal["dedupe_key"]))
    db.conn.commit()
    return db.get_deal(deal["dedupe_key"])


@pytest.mark.parametrize("title", LABELED)
def test_explicit_renewed_label_never_maps_as_new(title, monkeypatch):
    # There is no Warehouse price in this response. Never invent a used price.
    assert fetch(title, config(True), monkeypatch) == []


@pytest.mark.parametrize("title", ORDINARY)
def test_product_name_words_stay_new(title, monkeypatch):
    baseline = fetch(title, config(False), monkeypatch)
    assert len(baseline) == 1 and baseline[0].structured["conditions"] == []
    assert fetch(title, config(True), monkeypatch) == baseline


def test_switch_off_and_missing_preserve_exact_pre_fix_posts(monkeypatch):
    baseline = [asdict(fetch(title, config(False), monkeypatch)[0]) for title in LABELED + ORDINARY]
    digest = hashlib.sha256(json.dumps(baseline, sort_keys=True, default=str).encode()).hexdigest()
    # Recorded against the unmodified mapper, including every RawPost field.
    assert digest == "199148053185a9196e2a92c38ef3ee02703126fe42b36309fdbe18ef6a87e772"
    implicit = AppConfig()
    implicit.amazon_used_enabled = False
    implicit.keepa_focus_categories = []
    implicit.keepa_sweep_enabled = False
    assert [asdict(fetch(title, implicit, monkeypatch)[0]) for title in LABELED + ORDINARY] == baseline


def test_used_enabled_keeps_actual_warehouse_offer_only(monkeypatch):
    title = "Widget (Renewed)"
    baseline = fetch(title, config(False, used=True), monkeypatch, warehouse=True)
    assert [p.structured["conditions"] for p in baseline] == [["used", "open_box"], []]
    assert fetch(title, config(True, used=True), monkeypatch, warehouse=True) == baseline[:1]
    assert fetch(title, config(True, used=True), monkeypatch) == []


@pytest.mark.parametrize("title", LABELED)
def test_stored_title_labels_follow_amazon_used_off_publication(db, title):
    cfg = config(True)
    row = titled_seed(db, cfg, title, price_rechecked_at=util.iso(NOW))
    assert amazon_used.excluded(row, cfg)
    assert rank.is_publishable(row, cfg) == (False, "amazon_used: disabled")
    cfg.amazon_used_enabled = True
    assert not amazon_used.excluded(row, cfg)
    assert rank.is_publishable(row, cfg)[0]
    cfg.amazon_used_enabled = False
    assert not amazon_used.excluded(dict(row, store="Woot", canonical_url="https://www.woot.com/offers/x"), cfg)
    assert not amazon_used.excluded(dict(row, store="Target", canonical_url="https://www.target.com/p/x"), cfg)
    assert db.get_deal(row["dedupe_key"]) == row


@pytest.mark.parametrize("title", ORDINARY)
def test_product_name_words_remain_publishable(db, title):
    cfg = config(True)
    row = titled_seed(db, cfg, title, price_rechecked_at=util.iso(NOW))
    assert not amazon_used.excluded(row, cfg)
    assert rank.is_publishable(row, cfg)[0]


def test_switch_off_preserves_legacy_title_publication(db):
    cfg = config(False)
    row = seed(db, cfg, price_rechecked_at=util.iso(NOW))
    for title in LABELED + ORDINARY:
        candidate = dict(row, item=title)
        # Legacy detection also joins the store and title into Amazon Renewed.
        expected = title == "Amazon Renewed Widget" or title.startswith("Renewed ")
        assert amazon_used.excluded(candidate, cfg) == expected
        assert rank.is_publishable(candidate, cfg)[0] is not expected


@pytest.mark.parametrize("price_type", [0, 18])
def test_focus_new_lanes_reject_renewed_labels(db, monkeypatch, price_type):
    from tests.test_accuracy_lite_counts import ZERO, recording
    from tests.test_keepa_focus_finder import Keepa, pull, row

    cfg = load_config()
    cfg.amazon_renewed_title_lock = True
    cfg.amazon_used_enabled = False
    cfg.keepa_focus_categories = []
    cfg.keepa_amazon_pages_per_pull = 1
    cfg.keepa_overflow_extra_pages_max = 0
    cfg.keepa_click_price_rule = "off"
    cfg.keepa_buybox_top_n = 0
    cfg.keepa_recheck_top_live = False
    cfg.keepa_focus_finder.update(enabled=True, max_pages_per_run=2, groups={"gadgets": ["Headphones"]})
    fake = Keepa(rows=lambda sel: [row(title="Headphones (Renewed)", price_type=price_type)]
                 if sel["priceTypes"] == [price_type] else [])
    with recording() as counts:
        posts, adapter, _, _ = pull(db, cfg, fake, monkeypatch)
    assert counts == ZERO | {"renewed_title_lane": 1}
    assert not posts and adapter.focus_summary["kept"] == 0
    assert adapter.focus_summary["dropped"]["drop"] == 1


def test_rendered_site_applies_existing_used_off_policy(db, tmp_path, monkeypatch):
    cfg = load_config()
    cfg.amazon_renewed_title_lock = True
    cfg.amazon_used_enabled = False
    cfg.keepa_click_price_rule = "off"
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    renewed = titled_seed(db, cfg, "Widget (Renewed)", price_rechecked_at=util.iso(NOW))
    ordinary = titled_seed(db, cfg, ORDINARY[0], asin="B000000002", price_rechecked_at=util.iso(NOW))
    site.render_site(db, cfg, NOW, out_dir=tmp_path)
    public = json.loads((tmp_path / "deals.json").read_text())
    assert [d["dedupe_key"] for d in public["deals"]] == [ordinary["dedupe_key"]]
    assert renewed["item"] not in (tmp_path / "index.html").read_text()
    assert db.get_deal(renewed["dedupe_key"]) == renewed


def test_title_only_renewed_skips_existing_used_off_rechecks(db, monkeypatch):
    cfg = config(True)
    cfg.feature_flags["keepa"] = True
    row = titled_seed(db, cfg, "Widget (Renewed)")
    monkeypatch.setenv("KEEPA_KEY", "offline-key")

    def forbidden(*args, **kwargs):
        pytest.fail("suppressed renewed title bought a Keepa recheck")

    monkeypatch.setattr(verify, "keepa_products", forbidden)
    monkeypatch.setattr(verify, "_keepa_product", forbidden)
    assert verify.keepa_check(row, cfg) is None
    assert not verify.recheck_amazon_prices([row], cfg)
    assert verify.recheck_amazon_price(row, cfg) is None
