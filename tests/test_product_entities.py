"""Durable product identity: no fuzzy links, new requests, or public IDs."""

import json
import sqlite3
from datetime import timedelta

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.dedupe import upsert_extracted
from dealradar.models import ExtractedDeal, RawPost
from dealradar.products import identity
from dealradar.savings import compute_savings
from dealradar.util import iso
from tests.test_publish_site import NOW, make_deal


@pytest.fixture
def db(tmp_path):
    database = DB(tmp_path / "products.db")
    yield database
    database.close()


def insert(db, key, *, now=NOW, **facts):
    d = make_deal(**dict(dict(dedupe_key=key, item="Acme AB123 headphones", brand="Acme",
                             canonical_url="", conditions=[], codes=[]), **facts))
    db.insert_deal(dedupe_key=key, store=d["store"], item=d["item"],
                   normalized_item=d["item"].lower(), canonical_url=d["canonical_url"],
                   payload=d, final_price=d["final_price"], pct_at_checkout=d["pct_at_checkout"],
                   abs_savings=d["abs_savings"], online_only=d["online_only"],
                   expires_at="", now=now)
    return db.get_deal(key)


def snapshot(db):
    return {table: [tuple(r) for r in db.conn.execute(f"SELECT * FROM {table} ORDER BY 1,2")]
            for table in ("products", "product_keys", "product_offers")}


def legacy(path, count=5):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE deals (
        id INTEGER PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE, store TEXT NOT NULL,
        item TEXT NOT NULL, normalized_item TEXT NOT NULL, canonical_url TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL, final_price REAL, pct_at_checkout REAL, abs_savings REAL,
        online_only TEXT NOT NULL DEFAULT 'unknown', verify_tier TEXT NOT NULL DEFAULT 'unverified',
        status TEXT NOT NULL DEFAULT 'alive', expires_at TEXT NOT NULL DEFAULT '',
        first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL)""")
    for i in range(count):
        payload = {"asin": "B012345678", "conditions": [], "price_published_at": iso(NOW)}
        conn.execute("INSERT INTO deals(dedupe_key,store,item,normalized_item,payload,final_price,"
                     "first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?)",
                     (f"legacy-{i}", "Amazon", "Headphones", "headphones", json.dumps(payload),
                      10 + i, iso(NOW - timedelta(days=i)), iso(NOW)))
    conn.commit()
    conn.close()


def test_migration_is_additive_idempotent_and_read_only_legacy_still_works(tmp_path):
    path = tmp_path / "legacy.db"
    legacy(path)
    ro = DB(path, read_only=True)
    original = ro.conn.execute("SELECT * FROM deals ORDER BY id").fetchall()
    assert all(d["product_id"] is None for d in ro.all_deals())
    ro.close()
    db = DB(path)
    assert not db.conn.execute("SELECT * FROM products").fetchall()  # bounded backfill, not migration
    assert [tuple(r)[:16] for r in db.conn.execute("SELECT * FROM deals ORDER BY id")] == list(map(tuple, original))
    assert db.backfill_products(20) == 5
    before, deals = snapshot(db), db.all_deals()
    db.close()
    db = DB(path)
    assert db.backfill_products(20) == 0
    assert snapshot(db) == before and db.all_deals() == deals
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    db.close()


def test_backfill_is_bounded_resumable_and_once_per_run(tmp_path):
    path = tmp_path / "legacy.db"
    legacy(path)
    db = DB(path)
    assert db.backfill_products(0) == 0
    db.start_run("run-1", "fast", NOW)
    assert db.backfill_products(2, run_key="run-1") == 2
    before = snapshot(db)
    db.close()
    db = DB(path)
    db.start_run("run-1", "fast", NOW)
    assert db.backfill_products(2, run_key="run-1") == 0
    assert snapshot(db) == before
    db.start_run("run-2", "hourly", NOW)
    assert db.backfill_products(2, run_key="run-2") == 2
    assert db.backfill_products(2) == 1
    assert db.backfill_products(2) == 0
    assert len({d["product_id"] for d in db.all_deals()}) == 1
    product = db.conn.execute("SELECT * FROM products").fetchone()
    assert product["first_seen_at"] == iso(NOW - timedelta(days=4))
    assert product["last_seen_at"] == iso(NOW)
    db.close()


def test_failed_backfill_rolls_back_links_and_run_checkpoint(tmp_path, monkeypatch):
    from dealradar import products

    path = tmp_path / "legacy.db"
    legacy(path)
    db = DB(path)
    db.start_run("retry", "fast", NOW)
    attach = products.attach

    def interrupted(conn, deal):
        if deal["dedupe_key"] == "legacy-1":
            raise RuntimeError("interrupted batch")
        attach(conn, deal)

    monkeypatch.setattr(products, "attach", interrupted)
    with pytest.raises(RuntimeError, match="interrupted batch"):
        db.backfill_products(2, run_key="retry")
    assert not db.conn.execute("SELECT * FROM product_offers").fetchall()
    assert not db.conn.execute("SELECT * FROM products").fetchall()
    monkeypatch.setattr(products, "attach", attach)
    assert db.backfill_products(2, run_key="retry") == 2
    db.close()


def test_maintenance_does_not_bypass_the_backfill_limit(tmp_path):
    path = tmp_path / "legacy.db"
    legacy(path)
    db = DB(path)
    assert db.backfill_products(2) == 2
    for deal in db.all_deals():
        db.update_deal(deal["dedupe_key"], payload=dict(deal, image_url="https://images.test/item.jpg"),
                       verify_tier="history-checked")
    assert db.conn.execute("SELECT COUNT(*) FROM product_offers").fetchone()[0] == 2
    db.update_deal("legacy-4", last_seen_at=NOW + timedelta(hours=1))
    assert db.get_deal("legacy-4")["product_id"] is not None  # An incoming sighting attaches now.
    assert db.backfill_products(20) == 2
    db.close()


@pytest.mark.parametrize("first,second,reason", [
    ({"asin": "b012345678"}, {"asin": "B012345678"}, "asin"),
    ({"upc": "036000291452"}, {"ean": "0036000291452"}, "gtin"),
    ({"gtin": "00036000291452"}, {"upc": "036000291452"}, "gtin"),
    ({"sku": "ab123"}, {"sku": "AB123"}, "sku"),
    ({"model": "AB123"}, {"model": "ab123"}, "brand_model"),
    ({"model": "AB123"}, {"model_number": "ab123"}, "brand_model"),
    ({"canonical_url": "https://amazon.com/dp/B012345678?tag=source-20"},
     {"canonical_url": "https://www.amazon.com/gp/product/B012345678?tag=another-20"}, "asin"),
    ({"store": "Best Buy", "canonical_url": "https://bestbuy.com/site/headphones/1234567.p"},
     {"store": "Best Buy", "sku": "1234567"}, "sku"),
    ({"store": "eBay", "canonical_url": "https://ebay.com/itm/headphones/123456789012"},
     {"store": "eBay", "ebay_legacy_id": "123456789012"}, "sku"),
    ({"store": "Woot", "woot_id": "offer-123"}, {"store": "Woot", "woot_id": "offer-123"}, "sku"),
    ({"canonical_url": "https://electronics.woot.com/offers/acme-headphones?ref=a"},
     {"canonical_url": "https://electronics.woot.com/offers/acme-headphones?ref=b"}, "sku"),
])
def test_exact_identity_resolution(db, first, second, reason):
    a, b = insert(db, "a", **first), insert(db, "b", **second)
    assert a["product_id"] == b["product_id"]
    assert db.conn.execute("SELECT match FROM product_offers WHERE dedupe_key='b'").fetchone()[0] == reason
    assert len(db.all_deals()) == 2  # Linking never combines offers or changes prices.


@pytest.mark.parametrize("difference", [
    {"conditions": ["used"]}, {"conditions": ["open_box"]}, {"conditions": ["refurbished"]},
    {"quantity_or_unit": "2-pack"}, {"quantity": 2}, {"pack_size": 2},
    {"capacity": "512 GB"}, {"size": "XL"}, {"color": "cerulean"},
    {"variant": {"size": "large", "color": "green"}}, {"condition_grade": "like new"},
    {"model": "AB124"}, {"currency": "CAD"}, {"region": "CA"},
    {"item": "Acme AB123 headphones 2-pack"}, {"item": "Acme AB123 headphones cerulean"},
    {"item": "Acme AB123 headphones 256GB"}, {"item": "Acme AB123 headphones XL"},
    {"item": "Acme AB123 headphones used - like new"},
])
def test_variants_and_conditions_never_share_products_even_with_same_asin(db, difference):
    a = insert(db, "a", asin="B012345678")
    b = insert(db, "b", asin="B012345678", **difference)
    assert a["product_id"] != b["product_id"]


@pytest.mark.parametrize("first,second", [
    ({}, {}),  # Identical item and brand alone are not an identity.
    ({"model": "ABC"}, {"model": "ABC"}),
    ({"model": "AB12", "brand": ""}, {"model": "AB12", "brand": ""}),
    ({"model": "Wireless"}, {"model": "Wireless"}),
    ({"model": "2026"}, {"model": "2026"}),
    ({"model": "256GB"}, {"model": "256GB"}),
    ({"item": "Acme AB123 headphones"}, {"item": "Acme AB123 wireless headphones"}),
    ({"upc": "036000291453"}, {"upc": "036000291453"}),  # Bad checksum.
    ({"upc": "000000000000"}, {"upc": "000000000000"}),
    ({"asin": "short"}, {"asin": "short"}),
    ({"asin": "B012345678", "link_shared": True}, {"asin": "B012345678", "link_shared": True}),
    ({"store": "eBay", "sku": "1234567"}, {"store": "Best Buy", "sku": "1234567"}),
    ({"model": "AB123", "asin": "B012345678"}, {"model": "AB123", "asin": "B012345679"}),
    ({"model": "AB123", "gtin": "036000291452"}, {"model": "AB123", "gtin": "4006381333931"}),
])
def test_no_fuzzy_inferred_invalid_or_conflicting_identities(db, first, second):
    a, b = insert(db, "a", **first), insert(db, "b", **second)
    assert a["product_id"] != b["product_id"]


def test_sku_match_records_actual_reason_when_incoming_also_has_a_new_asin(db):
    a = insert(db, "a", sku="AB123")
    b = insert(db, "b", sku="AB123", asin="B012345678")
    assert a["product_id"] == b["product_id"]
    assert db.conn.execute("SELECT match FROM product_offers WHERE dedupe_key='b'").fetchone()[0] == "sku"
    assert dict(db.conn.execute("SELECT kind,value FROM product_keys")) == {
        "sku:walmart": "ab123", "asin": "B012345678"}


@pytest.mark.parametrize("field,store", [("ebay_legacy_id", "eBay"), ("woot_id", "Woot")])
def test_relisting_gets_a_separate_offer_under_the_same_exact_product(db, field, store):
    a = insert(db, "original", store=store, asin="B012345678", **{field: "123456789012"})
    b = insert(db, "relisted", store=store, asin="B012345678", **{field: "123456789013"})
    assert a["product_id"] == b["product_id"]
    assert db.conn.execute("SELECT COUNT(*) FROM product_offers").fetchone()[0] == 2
    before = snapshot(db)
    db.update_deal("original", payload=a)
    assert snapshot(db) == before
    # A new listing ID alone cannot borrow the previous listing's product.
    c = insert(db, "unknown", store=store, **{field: "123456789014"})
    assert c["product_id"] != a["product_id"]


def test_conflicting_aliases_do_not_union_established_products(db):
    a = insert(db, "a", asin="B012345678")
    b = insert(db, "b", upc="036000291452")
    bridge = insert(db, "bridge", asin="B012345678", upc="036000291452")
    assert a["product_id"] != b["product_id"]
    assert bridge["product_id"] is None
    assert db.conn.execute("SELECT match FROM product_offers WHERE dedupe_key='bridge'").fetchone()[0] == "conflict"
    before = snapshot(db)
    db.update_deal("bridge", payload=bridge)
    assert snapshot(db) == before


def test_a_changed_variant_is_quarantined_instead_of_relabelling_history(db):
    a = insert(db, "a", asin="B012345678")
    db.record_price("a", "Walmart", 10, NOW, "original")
    db.update_deal("a", payload=dict(a, color="red"))
    assert db.get_deal("a")["product_id"] is None
    assert db.product_price_history(a["product_id"], 90, now=NOW) == []
    assert db.price_observations("a", NOW, NOW) == [{"price": 10, "observed_at": iso(NOW)}]
    db.update_deal("a", payload=a)
    assert db.get_deal("a")["product_id"] is None  # No automatic unquarantine.


def test_upsert_attaches_and_rerun_adds_no_product_or_price_rows(db):
    post = RawPost("source", "post", "https://source.test/post", "Headphones", "Price $10, was $50",
                   NOW, NOW)
    extracted = ExtractedDeal(item="Acme AB123 headphones", store="Amazon", brand="Acme",
                              product_url="https://amazon.com/dp/B012345678", source_url=post.url,
                              source_name=post.source_name, regular_price=50, final_price=10)
    savings = compute_savings(extracted)
    first = upsert_extracted(db, extracted, savings, post, AppConfig(), NOW)
    product_id = first["payload"]["product_id"]
    assert product_id and db.get_deal(first["dedupe_key"])["product_id"] == product_id
    before = snapshot(db)
    second = upsert_extracted(db, extracted, savings, post, AppConfig(), NOW)
    assert not second["is_new_deal"] and second["payload"]["product_id"] == product_id
    assert snapshot(db) == before
    assert len(db.product_price_history(product_id, 90, now=NOW)) == 1
    assert "product_id" not in json.loads(db.conn.execute("SELECT payload FROM deals").fetchone()[0])


def test_history_window_duplicates_stores_pruned_offers_and_relisting(db):
    a = insert(db, "a", asin="B012345678", store="Amazon")
    b = insert(db, "b", asin="B012345678", store="Woot")
    used = insert(db, "used", asin="B012345678", conditions=["used"])
    for key, price, age, content in [("a", 20, 90, "start"), ("a", 15, 1, "both"),
                                    ("b", 12, 0, "today"), ("a", 99, 91, "old"),
                                    ("a", 99, -1, "future"), ("used", 5, 0, "used")]:
        db.record_price(key, db.get_deal(key)["store"], price, NOW - timedelta(days=age), content)
    db.add_sighting(a["id"], source_name="partner", source_url="https://partner.test/a",
                    content_hash="both", seen_at=NOW - timedelta(days=1), final_price=15)
    db.add_sighting(b["id"], source_name="woot", source_url="https://woot.com/a",
                    content_hash="only-sighting", seen_at=NOW - timedelta(hours=1), final_price=13)
    history = db.product_price_history(a["product_id"], 90, now=NOW)
    assert [(h["price"], h["store"], h["condition"]) for h in history] == [
        (20, "Amazon", "new"), (15, "Amazon", "new"), (13, "Woot", "new"), (12, "Woot", "new")]
    db.conn.execute("DELETE FROM deals WHERE dedupe_key='a'")
    db.conn.commit()
    assert db.product_price_history(a["product_id"], 90, now=NOW) == history
    relisted = insert(db, "next-month", asin="B012345678", store="Amazon")
    assert relisted["product_id"] == a["product_id"] != used["product_id"]
    assert db.product_price_history(99999, 90, now=NOW) == []
    assert db.product_price_history(a["product_id"], -1, now=NOW) == []


def test_config_defaults_and_loaded_switch(tmp_path):
    assert not AppConfig().product_entities_also_at_enabled
    assert not load_config().product_entities_also_at_enabled
    assert load_config().product_entities_backfill_per_run == 200
    path = tmp_path / "config.yaml"
    path.write_text("product_entities:\n  also_at_enabled: true\n  backfill_per_run: 3\n")
    cfg = load_config(path)
    assert cfg.product_entities_also_at_enabled and cfg.product_entities_backfill_per_run == 3


def test_shared_url_selection_and_contradictory_identifiers_are_conservative(db):
    a = insert(db, "a", asin="B012345678", canonical_url="https://amazon.com/dp/B012345679")
    b = insert(db, "b", asin="B012345678")
    assert a["product_id"] != b["product_id"]
    assert identity(a)[0] == []
    keys, _ = identity(dict(b, upc="036000291452", ean="4006381333931"))
    assert keys == []
    db.update_deal("b", canonical_url="https://amazon.com/dp/B012345679")
    assert db.get_deal("b")["product_id"] is None  # Quarantine contradictions introduced later, too.
