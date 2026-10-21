"""365: opt-in expiry, exact render protection, bounded resume and replay."""

import hashlib
import json
import logging
import time
from datetime import timedelta

import pytest
import yaml

from dealradar import accuracy, clicks, dedupe, offsite_expiry, state_retention
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import members, product_pages, site
from dealradar.util import iso
from tests.test_dedupe import make_extracted, make_post, make_savings
from tests.test_members import Factory
from tests.test_product_entities import insert
from tests.test_publish_site import NOW
from tests.test_e2e_offline import env as pipeline_env, test_rerun_is_idempotent as check_pipeline_rerun


@pytest.fixture
def db(tmp_path):
    state = DB(tmp_path / "expiry.db")
    yield state
    state.close()


def policy(**kw):
    return AppConfig(offsite_unseen_days=7, state_maintenance_budget_s=5, **kw)


def prune(db, cfg, *, keys=frozenset(), run="expiry", **kw):
    db.start_run(run, "fast", NOW)
    db.prune(NOW, run_key=run, state_retention=state_retention.options(cfg),
             offsite_protected_keys=keys, fast_lane=True, **kw)
    return json.loads(db.conn.execute("SELECT state_maintenance FROM run_stats WHERE run_key=?",
                                     (run,)).fetchone()[0])


@pytest.mark.parametrize("value", [True, False, 0, -1, 3651, "7", 7.0, [], {}])
def test_strict_days(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"retention": {"offsite_unseen_days": value}}))
    with pytest.raises(ValueError, match="offsite_unseen_days"):
        load_config(path)
    with pytest.raises(ValueError, match="offsite_unseen_days"):
        offsite_expiry.validate_days(value)


@pytest.mark.parametrize("value", [None, 1, 3650])
def test_valid_days_default_off_and_operator_setting(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"retention": {"offsite_unseen_days": value}}))
    assert load_config(path).offsite_unseen_days == value
    # Operator 2026-10-21 "CONFIRMED YES GO AHEAD": 7 days; the code default stays OFF.
    assert load_config().offsite_unseen_days == 7
    assert AppConfig().offsite_unseen_days is None


@pytest.mark.parametrize("growers", [False, True])
def test_off_writer_database_and_every_publication_byte_match(db, tmp_path, monkeypatch, growers):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = AppConfig(state_retention_337=growers, paywall_open_access=True)
    insert(db, "old-public", now=NOW - timedelta(days=100),
           image_url="https://images.example/public.jpg")
    insert(db, "old-hidden", now=NOW - timedelta(days=100), pct_at_checkout=1)
    out = tmp_path / "site"
    before = site.render_site(db, cfg, NOW, out_dir=out)
    assert "offsite_protected_keys" not in before
    prune(db, cfg)
    db_before = db.conn.serialize()
    tree_before = {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    options_before = state_retention.options(cfg)
    cfg.offsite_unseen_days = None
    after = site.render_site(db, cfg, NOW, out_dir=out)
    prune(db, cfg)
    assert db.conn.serialize() == db_before
    assert {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()} == tree_before
    assert after == before
    assert state_retention.options(cfg) == options_before
    assert db.get_deal("old-hidden")
    assert not db.conn.execute("SELECT 1 FROM sqlite_master WHERE name='offsite_deal_anchors'").fetchone()


@pytest.mark.parametrize("growers", [False, True])
def test_old_offsite_deleted_cascade_and_dead_policy_unchanged(db, growers, caplog):
    for key, days, status in (("expire-secret", 8, "alive"), ("unknown-secret", 8, "suspect"),
                              ("boundary", 7, "alive"), ("recent", 6, "alive"),
                              ("published", 100, "alive"), ("dead-recent", 8, "dead"),
                              ("dead-old", 15, "dead"), ("bad-clock", 8, "alive")):
        d = insert(db, key, now=NOW - timedelta(days=days))
        db.update_deal(key, status=status)
        db.add_sighting(d["id"], source_name="feed", source_url="https://secret.example/row",
                        content_hash=key, seen_at=NOW, final_price=10)
        db.conn.execute("INSERT INTO keepa_recheck_history VALUES (?, '[{}]')", (key,))
    db.conn.execute("UPDATE deals SET last_seen_at='unknown' WHERE dedupe_key='bad-clock'")
    db.conn.commit()
    with caplog.at_level(logging.INFO):
        counts = prune(db, policy(state_retention_337=growers), keys={"published"})
    assert counts["deals"]["offsite_expired"] == {"alive": 1, "other_non_dead": 1}
    assert {d["dedupe_key"] for d in db.all_deals()} == {
        "boundary", "recent", "published", "dead-recent", "bad-clock"}
    assert db.conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0] == 5
    for key in ("expire-secret", "unknown-secret"):
        assert not db.conn.execute("SELECT 1 FROM keepa_recheck_history WHERE deal_key=?", (key,)).fetchone()
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("offsite expiry:")]
    assert len(lines) == 1 and "'alive': 1" in lines[0] and "'other_non_dead': 1" in lines[0]
    assert "protected={'alive': 1" in lines[0] and "left_by_bound={'alive': 0" in lines[0]
    assert all(secret not in lines[0] for secret in ("secret", "published", "http", "Acme"))


def test_no_complete_render_proof_keeps_every_non_dead_row(db):
    insert(db, "old", now=NOW - timedelta(days=100))
    assert prune(db, policy(), keys=None)["deals"]["deleted"] == 0
    assert db.get_deal("old")


@pytest.mark.parametrize("growers", [False, True])
def test_actual_render_keeps_old_rows_on_all_selected_surfaces(db, tmp_path, monkeypatch, growers):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = policy(state_retention_337=growers, paywall_open_access=True,
                 support_enabled=True, engagement={"saved_deals": True}, seo_hub_min_items=1,
                 seo_product_pages_enabled=True)
    cfg.checker["enabled"] = True
    cfg.amazon_history = "show"
    cfg.front_page["spotlight"] = {"enabled": True, "sources": ["meh"], "max_cards": 3}
    rows = []
    for key in ("wall", "remembered", "spotlight"):
        row = insert(db, key, now=NOW - timedelta(days=8), asin="B012345678",
                     canonical_url="https://www.amazon.com/dp/B012345678", store="Amazon",
                     price_rechecked_at=iso(NOW), image_url="https://images.example/item.jpg",
                     **({"pct_at_checkout": None, "regular_price": None, "abs_savings": None,
                         "sightings": [{"source_name": "meh",
                         "source_url": "https://meh.com/", "seen_at": iso(NOW), "final_price": 10}]}
                        if key == "spotlight" else {}))
        rows.append(row)
    insert(db, "hidden", now=NOW - timedelta(days=8), pct_at_checkout=1)
    out = tmp_path / "site"
    result = site.render_site(db, cfg, NOW, out_dir=out)
    assert {d["dedupe_key"] for d in result["public_deals"]} >= {"wall", "remembered", "spotlight"}
    assert "spotlight" in result["full_payload"]
    assert list((out / "deal").glob("*/index.html"))
    assert list((out / "p").glob("*/index.html"))
    assert result["checker"]["live"] > 0
    tree = {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert "hidden" not in result["offsite_protected_keys"]
    prune(db, cfg, keys=result["offsite_protected_keys"])
    assert db.get_deal("hidden") is None
    assert all(db.get_deal(key) for key in ("wall", "remembered", "spotlight"))
    site.render_site(db, cfg, NOW, out_dir=out)
    assert {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()} == tree


def test_retained_product_and_indexnow_receipts_protect_unlisted_rows(db):
    for key in ("retained-product", "receipt-deal", "receipt-product", "removed-receipt", "offsite"):
        insert(db, key, now=NOW - timedelta(days=100))
    products = {"1": {"path": "/p/retained/", "offers": {"retained-product": {}}}}
    db.meta_set(product_pages.META, json.dumps({"pages": {
        "2": {"path": "/p/receipt/", "offers": {"receipt-product": {}}}}}))
    for key, removed in (("receipt-deal", 0), ("removed-receipt", 1)):
        hash8 = hashlib.sha256(key.encode()).hexdigest()[:8]
        db.conn.execute("INSERT INTO indexnow_pages(url,content_hash,removed) VALUES (?, 'hash', ?)",
                        (f"https://wowpriced.com/deal/old-{hash8}/", removed))
    db.conn.execute("INSERT INTO indexnow_pages(url,content_hash) VALUES ('https://wowpriced.com/p/receipt/','hash')")
    db.conn.commit()
    keys = offsite_expiry.protected_keys(db, [], products)
    assert keys == {"retained-product", "receipt-deal", "receipt-product"}
    prune(db, policy(), keys=keys)
    assert {d["dedupe_key"] for d in db.all_deals()} == keys
    assert db.conn.execute("SELECT COUNT(*) FROM indexnow_pages").fetchone()[0] == 3


def test_unmapped_live_product_receipt_cannot_prove_absence(db):
    insert(db, "unknown-surface", now=NOW - timedelta(days=8))
    db.conn.execute("INSERT INTO indexnow_pages(url,content_hash) VALUES ('https://wowpriced.com/p/unknown/','hash')")
    db.conn.commit()
    keys = offsite_expiry.protected_keys(db, [], {})
    assert keys is None
    prune(db, policy(), keys=keys)
    assert db.get_deal("unknown-surface")


def test_hidden_row_contributing_public_week_stats_is_kept(db, tmp_path, monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = AppConfig(offsite_unseen_days=3, max_photoless_h=1, paywall_open_access=True)
    insert(db, "hidden-week-stat", now=NOW - timedelta(days=4), category="electronics")
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path / "site")
    assert result["published"] == 0
    assert "hidden-week-stat" in result["offsite_protected_keys"]
    prune(db, cfg, keys=result["offsite_protected_keys"])
    assert db.get_deal("hidden-week-stat")


def test_hidden_row_contributing_optional_public_lowest_seen_is_kept(db, tmp_path, monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = policy(paywall_open_access=True, product_entities_lowest_seen_enabled=True)
    for key, pct in (("visible-price", 80), ("hidden-price", 1)):
        insert(db, key, now=NOW - timedelta(days=8), pct_at_checkout=pct,
               store="Amazon", asin="B012345678", canonical_url="https://www.amazon.com/dp/B012345678",
               price_rechecked_at=iso(NOW), image_url="https://images.example/price.jpg")
    for days in (8, 9, 10):
        db.record_price("hidden-price", "Amazon", 5, NOW - timedelta(days=days), f"history-{days}")
    out = tmp_path / "site"
    result = site.render_site(db, cfg, NOW, out_dir=out)
    assert {d["dedupe_key"] for d in result["public_deals"]} == {"visible-price"}
    assert any("Lowest we've seen: $5.00" in p.read_text() for p in (out / "deal").glob("*/index.html"))
    assert "hidden-price" in result["offsite_protected_keys"]
    prune(db, cfg, keys=result["offsite_protected_keys"])
    assert db.get_deal("hidden-price")


def test_actual_remembered_deal_page_and_retained_product_page_are_protected(db, tmp_path, monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = policy(paywall_open_access=True, seo_product_pages_enabled=True)
    cfg.amazon_history = "show"
    insert(db, "previously-public", now=NOW - timedelta(days=8), asin="B012345678",
           canonical_url="https://www.amazon.com/dp/B012345678", store="Amazon",
           conditions=["used_like_new"], price_rechecked_at=iso(NOW),
           image_url="https://images.example/used.jpg")
    out = tmp_path / "site"
    first = site.render_site(db, cfg, NOW, out_dir=out)
    assert first["published"] == 1
    cfg.amazon_used_enabled = False
    second = site.render_site(db, cfg, NOW, out_dir=out)
    assert second["published"] == 0 and "previously-public" in second["offsite_protected_keys"]
    assert list((out / "deal").glob("*/index.html"))
    prune(db, cfg, keys=second["offsite_protected_keys"])
    assert db.get_deal("previously-public")["status"] == "alive"

    # A retained product page can carry an offer that no longer qualifies for
    # the wall, independently of the remembered deal-page path above.
    cfg.amazon_used_enabled = True
    db.update_deal("previously-public", pct_at_checkout=1)
    third = site.render_site(db, cfg, NOW, out_dir=out)
    assert third["published"] == 0 and not list((out / "deal").glob("*/index.html"))
    assert list((out / "p").glob("*/index.html"))
    assert "previously-public" in third["offsite_protected_keys"]
    prune(db, cfg, keys=third["offsite_protected_keys"], run="retained")
    assert db.get_deal("previously-public")


@pytest.mark.parametrize("growers", [False, True])
def test_bound_resumes_across_reopen_and_replay_does_no_more_work(db, growers, caplog):
    for i in range(137):
        insert(db, str(i), now=NOW - timedelta(days=8))
    cfg = policy(state_retention_337=growers, state_maintenance_max_rows=65)
    transactions, deletes = [], []
    def trace(sql):
        if sql == "BEGIN IMMEDIATE":
            transactions.append(set())
        if sql.startswith("DELETE FROM deals WHERE rowid="):
            transactions[-1].add(sql)  # SQLite traces FK cascades with the parent's SQL again.
            if sql not in deletes:
                deletes.append(sql)
    db.conn.set_trace_callback(trace)
    with caplog.at_level(logging.INFO):
        first = prune(db, cfg)
    assert first["deals"]["examined"] == first["deals"]["deleted"] == 65
    assert max(map(len, transactions)) == 64
    assert "left_by_bound={'alive': 72" in caplog.text
    cursor = db.meta_get("state_sweep:deals")
    assert json.loads(cursor)["after"] == 65
    before = db.conn.serialize()
    assert prune(db, cfg) == first
    assert db.conn.serialize() == before and len(deletes) == 65
    reopened = DB(db.path)
    try:
        assert reopened.meta_get("state_sweep:deals") == cursor
        assert prune(reopened, cfg, run="second")["deals"]["deleted"] == 65
        assert prune(reopened, cfg, run="third")["deals"]["deleted"] == 7
        assert reopened.all_deals() == []
    finally:
        reopened.close()


def test_expired_deadline_and_interrupted_chunk_roll_back_inventory_and_anchors(db, monkeypatch):
    insert(db, "rollback", now=NOW - timedelta(days=8))
    cfg = policy()
    counts = prune(db, cfg, state_deadline=time.monotonic() - 1)
    assert counts == {} and db.get_deal("rollback")
    original = offsite_expiry.retire
    def interrupt(state, row):
        original(state, row)
        raise state_retention._Deferred
    monkeypatch.setattr(offsite_expiry, "retire", interrupt)
    prune(db, cfg)
    assert db.get_deal("rollback") and db.meta_get("state_sweep:deals") is None
    assert not db.conn.execute("SELECT 1 FROM sqlite_master WHERE name='offsite_deal_anchors'").fetchone()


@pytest.mark.parametrize("growers", [False, True])
@pytest.mark.parametrize("expiry_off", [False, True])
def test_resighting_recreates_cleanly_preserving_history_and_member_send_guards(db, monkeypatch, growers, expiry_off):
    cfg = policy(state_retention_337=growers, members_email_enabled=True,
                 members_email_every_minutes=60)
    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.setattr(members, "member_list", lambda *a, **kw: ["reader@example.com"])
    post = make_post(uid="original")
    extracted = make_extracted(store="Walmart", product_url="https://www.walmart.com/ip/12345")
    original = dedupe.upsert_extracted(db, extracted, make_savings(), post, cfg, NOW - timedelta(days=8))
    key = original["dedupe_key"]
    factory = Factory()
    assert members.send_member_alerts(db, cfg, NOW - timedelta(days=8), deals=[original["payload"]],
                                      smtp_factory=factory, run_key="sent-run")["sent"] == 1
    db.conn.execute("UPDATE deals SET ai_public_at=?,ai_public_price=25 WHERE dedupe_key=?", (iso(NOW), key))
    db.conn.execute("INSERT INTO keepa_recheck_history VALUES (?, '[{}]')", (key,))
    accuracy.store_compact(db, key, "sample-run", NOW, NOW, "rest", "keepa", "match")
    db.conn.execute("INSERT INTO accuracy_samples VALUES (?,?,?,?,?,?,?,?)",
                    (key, "sample-run", iso(NOW), iso(NOW), "rest", "Walmart", "woot", "match"))
    hour = int(NOW.timestamp() // 3600)
    db.conn.execute("INSERT INTO deal_clicks VALUES (?,?,1)", (key, hour))
    db.conn.execute("INSERT INTO deal_click_shown VALUES (?,?,60,1)", (key, hour))
    db.conn.execute("INSERT INTO deal_click_runs VALUES ('click-run',?,1,1)", (hour,))
    clicks.compact_schema(db)
    clicks.add_exposure(db, key, hour, 60)
    db.conn.commit()
    tables = ("alerts_sent", "outgoing", "delivery_batches", "product_offers", "products", "product_keys", "price_history",
              "accuracy_samples", "accuracy_samples_compact", "accuracy_sample_keys", "accuracy_sample_runs",
              "deal_clicks", "deal_click_shown", "deal_click_runs", "deal_click_days")
    saved = {t: list(map(tuple, db.conn.execute(f"SELECT * FROM {t}"))) for t in tables}
    prune(db, cfg)
    assert db.get_deal(key) is None
    assert {t: list(map(tuple, db.conn.execute(f"SELECT * FROM {t}"))) for t in tables} == saved
    if expiry_off:
        cfg.offsite_unseen_days = None
    later_post = make_post(uid="fresh-sighting")
    later = dedupe.upsert_extracted(db, extracted, make_savings(), later_post, cfg, NOW)
    assert later["is_new_deal"] and db.get_deal(key)["status"] == "alive"
    assert len(db.sightings_for(later["deal_id"])) == 1
    assert tuple(db.conn.execute("SELECT ai_public_at,ai_public_price FROM deals").fetchone()) == (iso(NOW), 25)
    assert members.send_member_alerts(db, cfg, NOW, deals=[later["payload"]],
                                      smtp_factory=factory, run_key="fresh-run")["sent"] == 0
    assert len(factory.servers) == 1
    before = {t: db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in tables + ("deals", "sightings", "offsite_deal_anchors", "meta", "run_stats")}
    dedupe.upsert_extracted(db, extracted, make_savings(), later_post, cfg, NOW)
    members.send_member_alerts(db, cfg, NOW, deals=[later["payload"]], smtp_factory=factory, run_key="fresh-run")
    prune(db, cfg)
    assert {t: db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before} == before
    assert len(factory.servers) == 1
    assert not db.conn.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize("growers", [False, True])
def test_pipeline_same_run_replay_after_actual_expiry(pipeline_env, capsys, growers):
    from pathlib import Path
    path = Path(pipeline_env["cfg"])
    raw = yaml.safe_load(path.read_text())
    raw["retention"]["offsite_unseen_days"] = 7
    raw["retention"]["state_retention_337"] = growers
    path.write_text(yaml.safe_dump(raw))
    state = DB(pipeline_env["db"])
    insert(state, "unpublished-before-run", now=NOW - timedelta(days=8),
           pct_at_checkout=1, category="kitchen", image_url="https://images.example/old.jpg")
    state.meta_set("recategorized_v2", json.dumps({"keys": ["unpublished-before-run"]}))
    state.close()
    check_pipeline_rerun(pipeline_env, capsys, "shadow")
    state = DB(pipeline_env["db"])
    try:
        assert state.get_deal("unpublished-before-run") is None
        assert state.conn.execute("SELECT 1 FROM offsite_deal_anchors WHERE dedupe_key='unpublished-before-run'").fetchone()
    finally:
        state.close()
