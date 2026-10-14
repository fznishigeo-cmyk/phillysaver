"""Optional same-product comparisons respect the public inventory boundary."""

import json
import re

import pytest

from dealradar.publish import product_offers, seo, site
from dealradar.publish.site import _serialize_deal, render_site
from tests.test_product_entities import insert
from tests.test_publish_site import NOW
from tests.test_seo import db, cfg
from tests.test_seo_deal_pages import _page, _path


@pytest.fixture(autouse=True)
def publish_clock(monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)


def offers(db):
    return [insert(db, "a", asin="B012345678", store="Walmart"),
            insert(db, "b", asin="B012345678", store="Target", final_price=9),
            insert(db, "c", asin="B012345678", store="Woot", final_price=8)]


def tree(out):
    return {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()}


def test_only_enabled_detail_block_changes_bytes_and_ids_never_publish(db, cfg, tmp_path, monkeypatch):
    cfg.paywall_open_access = True
    rows = offers(db)
    off, on, restored = (tmp_path / name for name in ("off", "on", "restored"))
    render_site(db, cfg, NOW, out_dir=off, deals=rows)
    cfg.product_entities_also_at_enabled = True
    result = render_site(db, cfg, NOW, out_dir=on, deals=rows)
    assert (on / "deals.json").read_bytes() == (off / "deals.json").read_bytes()
    assert "product_id" not in json.dumps(result["full_payload"])
    before, after = tree(off), tree(on)
    assert before.keys() == after.keys()
    for path in before:
        if path.startswith("deal/"):
            assert b"Also at " in after[path] and b"Also at " not in before[path]
            assert re.sub(rb'<aside aria-label="Other stores">.*?</aside>', b"", after[path]) == before[path]
        else:
            assert after[path] == before[path], path
    html = _page(on, _path(rows[0])).read_text()
    assert f'Also at <a href="{_path(rows[1])}">Target</a> for $9.00 USD' in html
    assert f'Also at <a href="{_path(rows[2])}">Woot</a> for $8.00 USD' in html
    assert seo.seo_audit.Page(html, _path(rows[0])).schema_mismatch() is False
    cfg.product_entities_also_at_enabled = False
    monkeypatch.setattr(product_offers, "blocks", lambda *args: pytest.fail("off must not render comparison HTML"))
    render_site(db, cfg, NOW, out_dir=restored, deals=rows)
    assert tree(restored) == before


@pytest.mark.parametrize("excluded", ["private", "ended", "used", "variant", "same_store", "capped"])
def test_no_private_dead_wrong_condition_variant_or_same_store_comparison(db, cfg, tmp_path, excluded):
    cfg.paywall_open_access = True
    cfg.product_entities_also_at_enabled = True
    rows = offers(db)[:2]
    a, b = rows
    if excluded == "private":
        rows = [a]  # b is in state but absent from the public inventory.
    elif excluded == "ended":
        rows[1] = dict(b, status="dead", death="confirmed", died_at=NOW.isoformat())
    elif excluded == "used":
        rows[1] = insert(db, "used", store="Target", asin="B012345678", conditions=["used"])
    elif excluded == "variant":
        rows[1] = insert(db, "variant", store="Target", asin="B012345678", size="XL")
    elif excluded == "same_store":
        rows[1] = dict(b, store="Walmart")
    elif excluded == "capped":
        cfg.seo_deal_pages_max = 1
    render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=rows)
    assert "Also at " not in _page(tmp_path / "site", _path(a)).read_text()
    assert all("Also at " not in p.read_text() for p in (tmp_path / "site/deal").rglob("*.html"))


def test_locked_offers_stay_private(db, cfg, tmp_path):
    cfg.product_entities_also_at_enabled = True
    cfg.paywall_open_access = False
    cfg.paywall_teaser_count = 1
    rows = offers(db)
    render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=rows)
    public = json.loads((tmp_path / "site/deals.json").read_text())["deals"]
    assert sum(bool(d.get("locked")) for d in public) == 2
    assert all("Also at " not in p.read_text() for p in (tmp_path / "site/deal").rglob("*.html"))


def test_block_uses_public_price_escapes_store_and_keeps_checkout_terms(db):
    rows = offers(db)
    rows[1]["store"] = 'Target <"store">'
    rows[1]["codes"] = ["SAVE10"]
    live = [_serialize_deal(d, NOW) for d in rows]
    paths = {d["key"][:8]: _path(r) for d, r in zip(live, rows)}
    # Raw state never supplies the displayed price; use the already-published one.
    rows[1]["final_price"] = 9999
    block = product_offers.blocks(live, rows, paths)["a"]
    assert 'Target &lt;&quot;store&quot;&gt;</a> for $9.00 USD' in block
    assert "9999" not in block and "Promo code required" in block
    assert "<script" not in block
    assert product_offers.blocks(live, rows, paths) == product_offers.blocks(list(reversed(live)), rows, paths)


@pytest.mark.parametrize("override", [
    {"final_price": None}, {"final_price": -1}, {"final_price": float("inf")},
    {"flags": ["range"]}, {"flags": ["per_unit"]}, {"locked": True}, {"status": "dead"},
])
def test_invalid_or_qualified_prices_are_not_comparisons(db, override):
    rows = offers(db)[:2]
    live = [_serialize_deal(d, NOW) for d in rows]
    paths = {d["key"][:8]: _path(r) for d, r in zip(live, rows)}
    live[1].update(override)
    assert product_offers.blocks(live, rows, paths) == {}


def test_new_price_and_removed_offer_refresh_block_without_new_identity(db, cfg, tmp_path):
    cfg.paywall_open_access = True
    cfg.product_entities_also_at_enabled = True
    rows = offers(db)
    render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=rows)
    db.update_deal("b", payload=dict(rows[1], final_price=7), final_price=7)
    rows[1] = db.get_deal("b")
    assert rows[1]["product_id"] == rows[0]["product_id"]
    render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=rows[:2])
    html = _page(tmp_path / "site", _path(rows[0])).read_text()
    assert 'Target</a> for $7.00 USD' in html and 'Woot</a> for $8.00 USD' not in html
