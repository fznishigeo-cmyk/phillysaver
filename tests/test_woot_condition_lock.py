"""Woot's explicit condition protects stored prices; unknown remains legacy."""

from datetime import timedelta
from contextlib import closing
import hashlib
import json

import pytest

from dealradar import accuracy, dedupe, savings, util
from dealradar.config import AppConfig
from dealradar.db import DB
from dealradar.fetch import woot
from dealradar.parse.schema import LLMDeal, to_extracted
from tests.test_woot import SRC, _adapter
from tests.test_woot_reprice import NOW, check, offer, stored


@pytest.fixture
def cfg():
    cfg = AppConfig()
    cfg.woot_condition_lock = True
    cfg.min_pct = 40
    cfg.used_min_pct = 50
    cfg.seo_product_pages_record_own_checks_enabled = True
    return cfg


@pytest.fixture
def db():
    with closing(DB(":memory:")) as db:
        yield db


def state(db):
    return {table: [tuple(row) for row in db.conn.execute(f"SELECT * FROM {table}")]
            for table in ("deals", "sightings", "price_history", "accuracy_samples")}


@pytest.mark.parametrize("conditions,raw,reading", [
    ([], "New", "Refurbished"),
    ([], "Brand New", "Open Box"),
    (["used"], "Refurbished", "New"),
    (["used", "open_box"], "Open Box", " nEw "),
])
@pytest.mark.parametrize("sale,sold_out", [(9.99, False), (15.99, False),
                                         ([9, 20], False), (9.99, True)])
def test_explicit_mismatch_skips_price_freshness_accuracy_and_history(
        db, cfg, conditions, raw, reading, sale, sold_out):
    row = stored(db, cfg, conditions=conditions, woot_condition=raw, woot_offer_id="jeans")
    accuracy.publish(db, [row], NOW - timedelta(minutes=1), "previous")
    before = state(db)
    stats = check(db, cfg, [offer(sale, condition=reading, IsSoldOut=sold_out)])
    assert stats == dict(confirmed=0, repriced=0, range_skipped=0, above_range=0)
    assert state(db) == before


@pytest.mark.parametrize("reading", ["New", "Refurbished", "", None])
@pytest.mark.parametrize("conditions", [[], ["used"]])
def test_legacy_without_provenance_still_updates_and_confirms(db, cfg, reading, conditions):
    row = stored(db, cfg, conditions=conditions)
    assert check(db, cfg, [offer(condition=reading)])["repriced"] == 1
    after = db.get_deal(row["dedupe_key"])
    assert after["final_price"] == 9.99
    assert after["woot_listed_at"] == after["price_rechecked_at"] == util.iso(NOW)
    assert "woot_condition" not in after and "woot_offer_id" not in after
    check(db, cfg, [dict(offer(condition=reading), EndDate=util.iso(NOW))])
    assert db.get_deal(row["dedupe_key"])["death"] == "api_ended"


@pytest.mark.parametrize("conditions,raw,reading", [
    ([], "", "Refurbished"), ([], "   ", "Refurbished"),
    (["used"], None, "New"), (["used"], "", "New"),
    ([], "New", ""), ([], "New", "   "), (["used"], "Refurbished", None),
    (["used", "open_box"], "Open Box", "Refurbished"),
    (["used"], "Refurbished", "Brand New"),
    ([], "New", "Brand New"),
    # Conflicting stored provenance is unknown, not proof of a condition.
    ([], "Refurbished", "New"), (["used"], "New", "Refurbished"),
    (["membership"], "New", "Refurbished"),
])
def test_unknown_or_same_class_keeps_legacy_update(db, cfg, conditions, raw, reading):
    row = stored(db, cfg, conditions=conditions, woot_condition=raw)
    assert check(db, cfg, [offer(condition=reading)])["repriced"] == 1
    assert db.get_deal(row["dedupe_key"])["final_price"] == 9.99


@pytest.mark.parametrize("stored_id,incoming_id,url,same", [
    ("jeans", "jeans", "https://electronics.woot.com/offers/jeans", True),
    ("jeans", "another", "https://electronics.woot.com/offers/jeans", False),
    (None, "jeans", "https://electronics.woot.com/offers/jeans", False),
    ("jeans", "jeans", "https://electronics.woot.com/offers/another", True),
    ("jeans", "another", "https://electronics.woot.com/offers/another", False),
])
def test_mismatched_condition_end_date_requires_same_offer_id(
        db, cfg, stored_id, incoming_id, url, same):
    fields = dict(conditions=[], woot_condition="New")
    if stored_id is not None:
        fields["woot_offer_id"] = stored_id
    row = stored(db, cfg, **fields)
    before = state(db)
    item = offer(condition="Refurbished", OfferId=incoming_id, Url=url, Title=row["item"])
    check(db, cfg, [dict(item, EndDate=util.iso(NOW))])
    after = db.get_deal(row["dedupe_key"])
    if same:
        assert after["death"] == "api_ended" and after["status"] == "dead"
        assert after["final_price"] == row["final_price"]
        assert after.get("price_rechecked_at") == row.get("price_rechecked_at")
        assert state(db)["price_history"] == before["price_history"]
    else:
        assert state(db) == before


@pytest.mark.parametrize("enabled", [False, True])
def test_fetch_does_not_reingest_a_mismatched_condition(db, cfg, monkeypatch, enabled):
    cfg.woot_condition_lock = enabled
    monkeypatch.setenv("WOOT_API_KEY", "offline-woot-key")
    stored(db, cfg, conditions=[], woot_condition="New", woot_offer_id="jeans")
    before = state(db)
    posts = _adapter({"Items": [offer(condition="Refurbished")]}).fetch_posts(
        SRC, None, db, cfg, NOW, None)
    if enabled:
        assert posts == []
        assert state(db) == before
    else:
        assert len(posts) == 1


@pytest.mark.parametrize("raw", ["New", "Refurbished", "", None])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("source", ["woot", "partner"])
def test_new_write_captures_only_woot_raw_provenance(db, cfg, monkeypatch, raw, enabled, source):
    cfg.woot_condition_lock = enabled
    monkeypatch.setenv("WOOT_API_KEY", "offline-woot-key")
    post = _adapter({"Items": [offer(condition=raw)]}).fetch_posts(
        SRC, None, None, cfg, NOW, None)[0]
    post.source_name = source
    ext = to_extracted(LLMDeal.model_validate(post.structured), post, "UTC", NOW)
    result = dedupe.upsert_extracted(db, ext, savings.compute_savings(ext), post, cfg, NOW)
    row = db.get_deal(result["dedupe_key"])
    if enabled and source == "woot":
        assert row["woot_condition"] == (raw or "")
        assert row["woot_offer_id"] == "jeans"
        # Repair/critic payload filters must retain optional private facts.
        assert "woot_condition" in dedupe._DEAL_FIELDS and "woot_offer_id" in dedupe._DEAL_FIELDS
    else:
        assert "woot_condition" not in row and "woot_offer_id" not in row


def test_ingestion_never_backfills_legacy_provenance(db, cfg, monkeypatch):
    monkeypatch.setenv("WOOT_API_KEY", "offline-woot-key")
    post = _adapter({"Items": [offer()]}).fetch_posts(SRC, None, None, cfg, NOW, None)[0]
    ext = to_extracted(LLMDeal.model_validate(post.structured), post, "UTC", NOW)
    cfg.woot_condition_lock = False
    first = dedupe.upsert_extracted(db, ext, savings.compute_savings(ext), post, cfg, NOW)
    cfg.woot_condition_lock = True
    dedupe.upsert_extracted(db, ext, savings.compute_savings(ext), post, cfg, NOW + timedelta(hours=1))
    row = db.get_deal(first["dedupe_key"])
    assert "woot_condition" not in row and "woot_offer_id" not in row


def legacy_outputs():
    """Frozen pre-fix outputs include SQL payloads, receipts and raw API posts."""
    outputs = []
    for conditions, raw in (([], "New"), (["used"], "Refurbished"), ([], None)):
        for reading in ("New", "Refurbished", ""):
            for sale, ended in ((9.99, False), (15.99, False), ([9, 20], False), (9.99, True)):
                cfg = AppConfig()
                cfg.woot_condition_lock = False
                cfg.seo_product_pages_record_own_checks_enabled = True
                with closing(DB(":memory:")) as db:
                    fields = dict(conditions=conditions)
                    if raw is not None:
                        fields.update(woot_condition=raw, woot_offer_id="jeans")
                    row = stored(db, cfg, **fields)
                    accuracy.publish(db, [row], NOW - timedelta(minutes=1), "previous")
                    item = offer(sale, condition=reading)
                    if ended:
                        item["EndDate"] = util.iso(NOW)
                    stats = check(db, cfg, [item])
                    post = woot.WootAdapter._map_item(item, SRC, NOW)
                    outputs.append(dict(stats=stats, state=state(db), structured=post.structured,
                                        content_hash=post.content_hash))
    return json.dumps(outputs, sort_keys=True, separators=(",", ":")).encode()


def test_switch_off_exact_legacy_outputs():
    assert hashlib.sha256(legacy_outputs()).hexdigest() == "e041e41c3ae24001ac90f63755d75230eecc6b0335992fcebfdbd863675ea9f9"
