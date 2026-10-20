"""337: content-free diagnostics and bounded cleanup with permanent replay anchors."""

import json
import sqlite3

from dealradar.state_growers import posts_relation
from datetime import timedelta

import pytest
import yaml

from dealradar import source_audit, state_retention
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.util import iso
from scripts.state_growth import growers, inventory, main, snapshot
from tests.test_dedupe import make_post
from tests.test_lighter_state import add_call
from tests.test_product_entities import insert
from tests.test_state_growers import NOW, prune


@pytest.fixture
def db(tmp_path):
    state = DB(tmp_path / "state.db")
    yield state
    state.close()


def cfg(**changes):
    return AppConfig(state_retention_337=True, **changes)


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false", [], {}])
def test_flag_is_strict_bool(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"retention": {"state_retention_337": value}}))
    with pytest.raises(ValueError, match="state_retention_337"):
        load_config(path)


@pytest.mark.parametrize("name", ["api_cache_days", "post_receipt_details_days", "terminal_indexnow_versions_days"])
@pytest.mark.parametrize("value", [None, True, "1", 0, -1, 1.5, 3651])
def test_invalid_days(tmp_path, name, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"retention": {name: value}}))
    with pytest.raises(ValueError, match=name):
        load_config(path)


def test_defaults_shipped_and_off_is_previous_options(tmp_path):
    assert AppConfig().state_retention_337 is False
    path = tmp_path / "empty.yaml"
    path.write_text("{}")
    assert load_config(path).state_retention_337 is False
    assert load_config().state_retention_337 is True
    old = AppConfig(compact_deal_payloads=True)
    expected = state_retention.options(old)
    old.api_cache_days = old.post_receipt_details_days = old.terminal_indexnow_versions_days = 50
    assert state_retention.options(old) == expected
    assert state_retention.options(AppConfig()) is None


@pytest.mark.parametrize("days", [1, 3])
def test_api_cache_keeps_recent_boundary_future_null_and_normal_urls(db, days):
    cutoff = NOW - timedelta(days=days)
    for label, at in (("old", cutoff - timedelta(seconds=1)), ("boundary", cutoff),
                      ("recent", NOW), ("future", NOW + timedelta(days=1))):
        for prefix in ("api:", "https://feed.example/"):
            db.store_validators(prefix + label, '"etag"', "modified", at)
    db.conn.execute("INSERT INTO http_cache(url) VALUES ('api:null')")
    db.conn.commit()
    assert prune(db, cfg(api_cache_days=days))["http_cache_api"]["deleted"] == 1
    assert db.get_validators("api:old") == ("", "")
    for key in ("api:boundary", "api:recent", "api:future", "https://feed.example/old"):
        assert db.get_validators(key) == ('"etag"', "modified")
    assert db.conn.execute("SELECT 1 FROM http_cache WHERE url='api:null'").fetchone()


@pytest.mark.parametrize("days", [2, 4])
def test_post_details_trim_preserves_dedupe_discovery_pending_and_intake(db, days):
    from tests.test_source_audit import source

    cutoff = NOW - timedelta(days=days)
    posts = {}
    for label, at in (("old", cutoff - timedelta(seconds=1)), ("boundary", cutoff),
                      ("recent", NOW), ("future", NOW + timedelta(days=1))):
        p = make_post(source="partner", uid=label)
        posts[label] = p
        db.audit_seen([p], at)
        db.record_post(p, at)
        db.audit_parsed(p, dict(extracted=1, evidence=1), at)
    db.audit_seen([make_post(source="partner", uid="deferred")], NOW - timedelta(days=20))
    before = list(map(tuple, db.conn.execute(
        "SELECT id,source_name,content_hash,first_seen_at FROM posts_seen ORDER BY id")))
    full = {r["content_hash"]: dict(r) for r in db.conn.execute("SELECT * FROM posts_seen")}
    history = source_audit._history(db, source(), NOW, 7, NOW - timedelta(days=30))
    deferred = source_audit.intake_deferred(db, [source()], NOW)
    counts = prune(db, cfg(post_receipt_details_days=days))
    assert counts["posts_seen_details"]["updated"] == 1
    assert list(map(tuple, db.conn.execute(
        f"SELECT id,source_name,content_hash,first_seen_at FROM {posts_relation(db)} ORDER BY id"))) == before
    for label, p in posts.items():
        if label == "old":
            row = db.conn.execute("SELECT * FROM posts_seen_cold WHERE hash=?", (bytes.fromhex(p.content_hash),)).fetchone()
            assert json.loads(row["details"]) == [full[p.content_hash][k] for k in ("uid", "url", "published_at")]
        else:
            row = dict(db.conn.execute("SELECT * FROM posts_seen WHERE content_hash=?", (p.content_hash,)).fetchone())
            assert row == full[p.content_hash]
        assert db.post_is_known(p.content_hash) and not db.record_post(p, NOW)
        assert iso(db.post_first_seen(p.content_hash)) == full[p.content_hash]["first_seen_at"]
    assert source_audit._history(db, source(), NOW, 7, NOW - timedelta(days=30)) == history
    assert source_audit.intake_deferred(db, [source()], NOW) == deferred


@pytest.mark.parametrize("days", [1, 3])
def test_terminal_payloads_only_keep_sending_status_and_run_replay(db, days):
    cutoff = NOW - timedelta(days=days)
    versions = {"https://wowpriced.com/deal/widget/": "facts-hash"}
    for status in ("received", "failed", "sending"):
        for label, at in (("old", cutoff - timedelta(seconds=1)), ("boundary", cutoff),
                          ("recent", NOW), ("future", NOW + timedelta(days=1))):
            db.conn.execute("INSERT INTO indexnow_batches VALUES (?,?,?,?,?,?)",
                            ("endpoint", status + label, json.dumps(versions), iso(at), status, 200))
    db.conn.commit()
    counts = prune(db, cfg(terminal_indexnow_versions_days=days))
    assert counts["indexnow_batches"]["updated"] == 2
    for row in db.conn.execute("SELECT * FROM indexnow_batches"):
        expected = {} if row["status"] != "sending" and row["run_key"].endswith("old") else versions
        assert json.loads(row["versions"]) == expected
        assert not db.indexnow_claim("endpoint", row["run_key"], versions, NOW, {})
    db.indexnow_finish("endpoint", "sendingold", NOW, 200)
    prune(db, cfg(terminal_indexnow_versions_days=days), run="later")
    assert json.loads(db.indexnow_batch("endpoint", "sendingold")["versions"]) == {}


def seed_extra(db):
    old = NOW - timedelta(days=8)
    db.store_validators("api:old", '"etag"', "", old)
    db.record_post(make_post(uid="old"), old)
    db.conn.execute("INSERT INTO indexnow_batches VALUES ('endpoint','old',?,?,'received',200)",
                    (json.dumps({"/deal/": "facts"}), iso(old)))
    db.conn.commit()


def test_off_and_missing_have_identical_prior_bytes_and_no_new_bookkeeping(db):
    seed_extra(db)
    prune(db, AppConfig())
    before = list(db.conn.iterdump())
    prune(db, AppConfig())
    assert list(db.conn.iterdump()) == before
    assert db.get_validators("api:old") == ('"etag"', "")
    assert db.conn.execute("SELECT uid FROM posts_seen").fetchone()[0] == "old"
    assert json.loads(db.indexnow_batch("endpoint", "old")["versions"])
    assert "retention_337" not in state_retention.options(AppConfig(compact_deal_payloads=True))


def test_protected_facts_money_alerts_and_all_run_keys_survive(db):
    seed_extra(db)
    old = NOW - timedelta(days=20)
    deal = insert(db, "live", now=old)
    payload = dict(deal, gone_suspect_at=iso(old), photo_lookup_asin="B012345678", photo_lookup_found_none=True)
    db.update_deal("live", payload=payload)
    db.add_sighting(deal["id"], source_name="feed", source_url="https://feed.example/post", content_hash="fact",
                    seen_at=old, final_price=10)
    db.record_price("live", "Store", 10, old, "fact")
    db.conn.execute("INSERT INTO keepa_recheck_history VALUES ('live',?)", (json.dumps([{"run_key": "old"}]),))
    db.record_alert(deal_key="live", channel="test", dedup_token="sent", now=old)
    for status in ("sent", "pending"):
        db.claim_outgoing("test", status, "old", old)
        if status == "sent":
            db.finish_outgoing("test", status, old)
    db.delivery_batch("test", "old", ["live"], 1)
    db.start_run("old", "hourly", old)
    db.conn.execute("INSERT INTO accuracy_extra_runs VALUES ('old',?)", (iso(old),))
    db.conn.execute("INSERT INTO deal_click_runs(run_key,hour) VALUES ('old',?)", (int(old.timestamp() // 3600),))
    db.meta_set("appeal_score_run:old", "1")
    db.meta_set("ai_take_run:old", iso(old))
    add_call(db, "today", NOW, "settled", billed=NOW)
    add_call(db, "reserved", old, "reserved", days=(NOW, NOW + timedelta(days=1)))
    prune(db, AppConfig())  # Isolate new cleanup from established legacy expiry.
    protected = ("deals", "sightings", "price_history", "keepa_recheck_history", "alerts_sent", "outgoing",
                 "delivery_batches", "accuracy_extra_runs", "deal_click_runs", "llm_calls", "llm_reservations")
    before = {t: list(map(tuple, db.conn.execute(f"SELECT * FROM {t}"))) for t in protected}
    money = (db.spend_usd_for_day(NOW), db.spend_usd_for_day(NOW + timedelta(days=1)), db.spend_usd_for_run("today"))
    prune(db, cfg())
    assert {t: list(map(tuple, db.conn.execute(f"SELECT * FROM {t}"))) for t in protected} == before
    assert money == (db.spend_usd_for_day(NOW), db.spend_usd_for_day(NOW + timedelta(days=1)),
                     db.spend_usd_for_run("today"))
    assert db.conn.execute("SELECT 1 FROM run_stats WHERE run_key='old'").fetchone()
    assert db.meta_get("appeal_score_run:old") == "1" and db.meta_get("ai_take_run:old") == iso(old)
    assert db.alert_exists("test", "sent")
    assert not db.claim_outgoing("test", "sent", "new", NOW)
    assert not db.claim_outgoing("test", "pending", "new", NOW)
    assert db.delivery_batch("test", "old", ["different"], 1) == ["live"]
    assert not db.conn.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize("task", ["http_cache_api", "posts_seen_details", "indexnow_batches"])
def test_new_sweeps_bounded_replay_and_cross_run_progress(db, task):
    for i in range(7):
        old = NOW - timedelta(days=8)
        db.store_validators("api:" + str(i), "etag", "", old)
        db.record_post(make_post(uid=str(i)), old)
        db.conn.execute("INSERT INTO indexnow_batches VALUES ('endpoint',?,?,?,'received',200)",
                        (str(i), '{"path":"facts"}', iso(old)))
    db.conn.commit()
    policy = cfg(state_maintenance_max_rows=3, state_maintenance_budget_s=5)
    first = prune(db, policy, fast_lane=True)
    assert first[task]["examined"] == 3
    before = list(db.conn.iterdump())
    assert prune(db, policy, fast_lane=True) == first
    assert list(db.conn.iterdump()) == before
    for run in ("next", "last"):
        last = prune(db, policy, run=run, fast_lane=True)
    assert last[task]["completed"]
    assert db.conn.execute("SELECT COUNT(*) FROM http_cache").fetchone()[0] == 0
    assert not db.conn.execute("SELECT 1 FROM posts_seen").fetchone()
    assert db.conn.execute("SELECT COUNT(*) FROM posts_seen_cold").fetchone()[0] == 7
    assert not db.conn.execute("SELECT 1 FROM indexnow_batches WHERE versions!='{}'").fetchone()


@pytest.mark.parametrize("task,sql", [
    ("http_cache_api", "DELETE FROM http_cache WHERE rowid"),
    ("posts_seen_details", "INSERT OR IGNORE INTO posts_seen_cold"),
    ("indexnow_batches", "UPDATE indexnow_batches SET versions"),
])
def test_new_chunk_updates_and_cursor_roll_back_on_deadline(db, monkeypatch, task, sql):
    from tests.test_incremental_cache import ConnectionHook

    seed_extra(db)
    db.meta_set("state_maintenance_next_table", task)
    clock = [0.0]
    monkeypatch.setattr(state_retention.time, "monotonic", lambda: clock[0])
    real = db.conn

    def hook(when, statement):
        if when == "after" and statement.startswith(sql):
            clock[0] = 10.0

    rows = {t: list(map(tuple, db.conn.execute(f"SELECT * FROM {t}")))
            for t in ("http_cache", "posts_seen", "indexnow_batches")}
    db.conn = ConnectionHook(real, hook)
    try:
        assert prune(db, cfg(), deadline=1.0) == {}
    finally:
        db.conn = real
    assert {t: list(map(tuple, db.conn.execute(f"SELECT * FROM {t}"))) for t in rows} == rows
    assert db.meta_get("state_sweep:" + task) is None
    clock[0] = 0
    assert prune(db, cfg(), deadline=1.0)[task]["examined"] == 1


def test_cleanup_preserves_every_published_byte(db, tmp_path, monkeypatch):
    from dealradar.publish import site
    from tests.test_checker_index import files
    from tests.test_checker_page import config, render
    from tests.test_publish_site import NOW as published_at

    monkeypatch.setattr(site, "util_now", lambda: published_at)
    policy = config()
    policy.state_retention_337 = True
    out = tmp_path / "site"
    render(db, out, policy)
    # Receipts are intentionally older than the new windows but still within
    # the established 30-day guard; source links are in normalized sightings.
    for i, d in enumerate(db.all_deals()):
        post = make_post(source="partner", uid=str(i))
        db.record_post(post, published_at - timedelta(days=4))
        db.add_sighting(d["id"], source_name=post.source_name, source_url=post.url,
                        content_hash=post.content_hash, seen_at=published_at - timedelta(days=4),
                        final_price=d["final_price"])
    db.store_validators("api:old", "etag", "", published_at - timedelta(days=4))
    db.conn.execute("INSERT INTO indexnow_batches VALUES ('endpoint','old','{\"page\":\"hash\"}',?,'received',200)",
                    (iso(published_at - timedelta(days=4)),))
    db.conn.commit()
    site.render_site(db, policy, published_at, out_dir=out, deals=db.all_deals())
    before = files(out)
    counts = prune(db, policy, now=published_at)
    assert counts["posts_seen_details"]["updated"] == 0  # receipts with live deals remain hot
    site.render_site(db, policy, published_at, out_dir=out, deals=db.all_deals())
    assert files(out) == before


def test_snapshot_counts_all_tables_indexes_columns_and_growth_without_writes(tmp_path, monkeypatch, capsys):
    before, after = tmp_path / "before.db", tmp_path / "after.db"
    with sqlite3.connect(before) as conn:
        conn.execute('CREATE TABLE "odd""name" (id INTEGER PRIMARY KEY, evidence TEXT, unused TEXT)')
        conn.execute('CREATE INDEX receipts ON "odd""name" (evidence)')
        conn.execute('INSERT INTO "odd""name" VALUES (1,?,NULL)', ("private é",))
        conn.execute("CREATE TABLE empty (data BLOB)")
    original = before.read_bytes()
    after.write_bytes(original)
    with sqlite3.connect(after) as conn:
        conn.executemany('INSERT INTO "odd""name"(evidence) VALUES (?)', [("private é" * 1000,)] * 30)
        conn.execute("CREATE TABLE added (data BLOB)")
        conn.execute("DROP TABLE empty")
        expected = conn.execute("SELECT SUM(pgsize) FROM dbstat WHERE name IN ('odd\"name','receipts')").fetchone()[0]
    a, b = snapshot(before), snapshot(after)
    table = next(t for t in b["tables"] if t["name"] == 'odd"name')
    assert table["rows"] == 31 and table["bytes"] == expected
    assert table["column_bytes"]["evidence"] == len("private é".encode()) * 30001
    assert table["column_bytes"]["unused"] == 0
    changes = growers(a, b, 2)
    assert changes[0]["rows"] == 30 and changes[0]["mb_per_hour"] == changes[0]["bytes"] / 2e6
    assert next(t for t in changes if t["name"] == "empty")["bytes"] < 0
    assert next(t for t in changes if t["name"] == "added")["bytes"] > 0
    monkeypatch.setattr("sys.argv", ["state_growth", str(after), "--before", str(before), "--hours", "2"])
    main()
    output = capsys.readouterr().out
    assert "private" not in output and json.loads(output)["growers"] == changes
    assert before.read_bytes() == original


def test_length_fallback_is_labelled_and_counts_blobs_nulls_utf8_and_without_rowid(db):
    db.conn.execute("CREATE TABLE fallback (key TEXT PRIMARY KEY, data BLOB, value TEXT) WITHOUT ROWID")
    db.conn.execute("INSERT INTO fallback VALUES ('one',?,'é')", (bytes(100),))
    db.conn.execute("INSERT INTO fallback VALUES ('two',NULL,NULL)")

    class NoDbstat:
        def execute(self, sql):
            if "dbstat" in sql:
                raise sqlite3.OperationalError("no dbstat")
            return db.conn.execute(sql)

    result = inventory(NoDbstat())
    table = next(t for t in result["tables"] if t["name"] == "fallback")
    assert result["method"] == "length_sums"
    assert table == dict(name="fallback", rows=2, bytes=108, column_bytes=dict(key=6, data=100, value=2))
    with pytest.raises(ValueError, match="cannot compare"):
        growers(result, inventory(db.conn), 1)
    for value in (0, -1, float("inf"), float("nan"), True):
        with pytest.raises(ValueError, match="hours"):
            growers(result, result, value)


def test_prune_deadline_after_exhausted_run(tmp_path):
    from dealradar import pipeline
    cfg = AppConfig()
    assert cfg.state_maintenance_after_deadline is False
    reserve = pipeline._RESERVE_FOR_TAIL_S
    # OFF: exactly the previous tail deadline, even when it already passed.
    assert pipeline._prune_deadline(cfg, 1000.0, 2000.0) == 1000.0 - reserve
    cfg.state_maintenance_after_deadline = True
    cfg.state_maintenance_budget_s = 1.0
    # A busy hourly run that used its whole budget still gets the bounded trim.
    assert pipeline._prune_deadline(cfg, 1000.0, 2000.0) == 2001.0
    # A run with time left keeps the tail reserve.
    assert pipeline._prune_deadline(cfg, 1000.0, 100.0) == 1000.0 - reserve
    assert load_config().state_maintenance_after_deadline is True
    path = tmp_path / "config.yaml"
    for value in (None, 0, 1, "true", []):
        path.write_text(yaml.safe_dump({"retention": {"state_maintenance_after_deadline": value}}))
        with pytest.raises(ValueError, match="state_maintenance_after_deadline must be a boolean"):
            load_config(path)
