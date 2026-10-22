"""State retention and compaction, using real SQLite databases."""

import argparse
import logging
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from dealradar import cli, util
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.util import iso

NOW = datetime(2026, 10, 13, 0, 30, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=90)
RETENTION = {"llm_calls_days": 35, "run_stats_days": 60,
             "http_cache_days": 30, "outgoing_days": 60}
SHIPPED_RETENTION = {**RETENTION, "llm_calls_days": 10, "run_stats_days": 10}


@pytest.fixture
def db(tmp_path):
    state = DB(tmp_path / "state.db")
    yield state
    state.close()


def add_call(db, key, started, status, *, billed=None, days=()):
    db.conn.execute(
        "INSERT INTO llm_calls(call_key,run_key,model,started_at,billed_at,"
        " reserved_usd,cost_usd,status) VALUES (?,?,?,?,?,?,?,?)",
        (key, key, "fixture", iso(started), iso(billed) if billed else None,
         .2, .07 if billed else None, status))
    db.conn.executemany("INSERT INTO llm_reservations(call_key,day,usd) VALUES (?,?,?)",
                        [(key, iso(day)[:10], .2) for day in days])
    db.conn.commit()


@pytest.mark.parametrize("days", [35, 0, -5])
def test_prune_preserves_pending_and_both_utc_spend_days(db, days):
    today = NOW.replace(hour=0, minute=0)
    tomorrow = today + timedelta(days=1)
    keep = {"old-reserved", "old-with-current-reservation", "newly-started"}
    for status in ("settled", "released", "uncertain", "reserved"):
        add_call(db, "old-" + status, OLD, status,
                 billed=OLD if status in ("settled", "uncertain") else None,
                 days=(OLD, OLD + timedelta(days=1)))
        for label, at in (("today", today), ("tomorrow", tomorrow)):
            key = label + "-" + status
            keep.add(key)
            add_call(db, key, at, status,
                     billed=at if status in ("settled", "uncertain") else None,
                     days=(at, at + timedelta(days=1)) if status in ("uncertain", "reserved") else ())
    add_call(db, "old-with-current-reservation", OLD, "uncertain", billed=OLD,
             days=(today, tomorrow))
    add_call(db, "newly-started", today, "settled", billed=OLD)
    for label, at in (("today", today), ("tomorrow", tomorrow)):
        key = "newly-billed-" + label
        keep.add(key)
        add_call(db, key, OLD, "settled", billed=at)
        db.conn.execute("INSERT INTO llm_legacy_spend(run_key,billed_at,usd) VALUES (?,?,?)",
                        (label, iso(at), .03))
    db.conn.commit()
    before = [db.spend_usd_for_day(at) for at in (today, tomorrow)]
    assert all(value > 0 for value in before)
    reservations = [tuple(row) for row in db.conn.execute(
        "SELECT * FROM llm_reservations ORDER BY call_key,day") if row[0] in keep]

    # In New York it is still yesterday. Only UTC dates may guard the ledger.
    db.prune(NOW.astimezone(timezone(timedelta(hours=-4))), llm_calls_days=days)

    assert {row[0] for row in db.conn.execute("SELECT call_key FROM llm_calls")} == keep
    assert [tuple(row) for row in db.conn.execute(
        "SELECT * FROM llm_reservations ORDER BY call_key,day")] == reservations
    assert [db.spend_usd_for_day(at) for at in (today, tomorrow)] == pytest.approx(before)
    assert not db.conn.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize("days", [35, 7])
def test_call_retention_respects_the_configured_boundary(db, days):
    cutoff = NOW - timedelta(days=days)
    for status in ("settled", "released", "uncertain"):
        for label, at in (("expired", cutoff - timedelta(seconds=1)), ("boundary", cutoff)):
            add_call(db, label + status, at, status,
                     billed=at if status != "released" else None)
    add_call(db, "recent-reservation", OLD, "uncertain", billed=OLD,
             days=(NOW - timedelta(days=1),))
    db.prune(NOW, llm_calls_days=days)
    assert {row[0] for row in db.conn.execute("SELECT call_key FROM llm_calls")} == {
        "boundarysettled", "boundaryreleased", "boundaryuncertain", "recent-reservation"}


@pytest.mark.parametrize("knobs", [{}, {"run_stats_days": 3, "http_cache_days": 2,
                                       "outgoing_days": 4}])
def test_prune_bounds_stats_cache_and_completed_outgoing(db, knobs, caplog):
    limits = {**RETENTION, **knobs}
    for label, extra in (("expired", timedelta(seconds=1)), ("boundary", timedelta())):
        at = NOW - timedelta(days=limits["run_stats_days"]) - extra
        db.start_run(label, "fast", at)
        at = NOW - timedelta(days=limits["http_cache_days"]) - extra
        db.store_validators("https://example.com/" + label, '"etag"', "", at)
        at = NOW - timedelta(days=limits["outgoing_days"]) - extra
        for status in ("sent", "rejected"):
            token = label + status
            assert db.claim_outgoing("test", token, label, at)
            db.finish_outgoing("test", token, at, rejected=status == "rejected")
    assert db.claim_outgoing("test", "pending", "old", OLD)
    assert db.claim_outgoing("test", "recent-finish", "old", OLD)
    db.finish_outgoing("test", "recent-finish", NOW)
    assert db.record_price("widget", "Store", 12.5, OLD, "old-price")
    assert db.record_alert(deal_key="widget", channel="test", dedup_token="old-alert", now=OLD)

    with caplog.at_level(logging.INFO, logger="dealradar.db"):
        db.prune(NOW, **knobs)

    assert {row[0] for row in db.conn.execute("SELECT run_key FROM run_stats")} == {"boundary"}
    assert {row[0] for row in db.conn.execute("SELECT url FROM http_cache")} == {
        "https://example.com/boundary"}
    # Sent records never expire: a long-lived deal must not be posted twice.
    assert {row[0] for row in db.conn.execute("SELECT dedup_token FROM outgoing")} == {
        "expiredsent", "boundarysent", "boundaryrejected", "pending", "recent-finish"}
    assert db.price_stats("widget") == (1, 12.5)
    assert db.alert_exists("test", "old-alert")
    assert "price_history=1" in caplog.text and "alerts_sent=1" in caplog.text


@pytest.mark.parametrize("fragmentation", ["below", "exact", "above"])
def test_vacuum_only_above_ten_percent_with_data_intact(db, fragmentation, caplog):
    # Each 3000-byte row occupies one SQLite page. Make the page count a
    # multiple of ten to exercise the exact 10% boundary without fake PRAGMAs.
    db.conn.execute("CREATE TABLE fragmentation(id INTEGER PRIMARY KEY, payload BLOB)")
    db.conn.executemany("INSERT INTO fragmentation(payload) VALUES (?)", [(bytes(3000),)] * 200)
    db.conn.commit()
    pages = db.conn.execute("PRAGMA page_count").fetchone()[0]
    db.conn.executemany("INSERT INTO fragmentation(payload) VALUES (?)",
                        [(bytes(3000),)] * ((-pages) % 10))
    db.conn.commit()
    pages = db.conn.execute("PRAGMA page_count").fetchone()[0]
    deleted = pages // 10 + {"below": -1, "exact": 0, "above": 1}[fragmentation]
    db.conn.execute("DELETE FROM fragmentation WHERE id <= ?", (deleted,))
    db.conn.commit()
    free = db.conn.execute("PRAGMA freelist_count").fetchone()[0]
    assert free == deleted
    retained = [tuple(row) for row in db.conn.execute("SELECT * FROM fragmentation ORDER BY id")]
    statements = []
    db.conn.set_trace_callback(lambda sql: statements.append((sql, db.conn.in_transaction)))

    with caplog.at_level(logging.INFO, logger="dealradar.db"):
        db.prune(NOW)

    compacted = fragmentation == "above"
    assert [in_tx for sql, in_tx in statements if sql == "VACUUM"] == ([False] if compacted else [])
    after = db.conn.execute("PRAGMA page_count").fetchone()[0]
    after_free = db.conn.execute("PRAGMA freelist_count").fetchone()[0]
    if compacted:
        sqls = [sql for sql, _ in statements]
        assert sqls.index("PRAGMA wal_checkpoint(FULL)") < sqls.index("VACUUM")
        assert after < pages and after_free == 0
        assert f"pages={pages}->{after}" in caplog.text
    else:
        assert (after, after_free) == (pages, free)
    assert [tuple(row) for row in db.conn.execute("SELECT * FROM fragmentation ORDER BY id")] == retained
    assert db.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_blocked_vacuum_is_logged_and_never_fails_the_run(db, caplog):
    import sqlite3

    db.conn.execute("CREATE TABLE fragmentation(id INTEGER PRIMARY KEY, payload BLOB)")
    db.conn.executemany("INSERT INTO fragmentation(payload) VALUES (?)", [(bytes(3000),)] * 50)
    db.conn.commit()
    db.conn.execute("DELETE FROM fragmentation WHERE id <= 25")
    db.conn.commit()
    real = db.conn

    class Locked:
        def execute(self, sql, *args):
            if sql == "VACUUM":
                raise sqlite3.OperationalError("database is locked")
            return real.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(real, name)

    db.conn = Locked()
    try:
        with caplog.at_level(logging.INFO, logger="dealradar.db"):
            db.prune(NOW)
    finally:
        db.conn = real
    assert "state compaction deferred (database is locked)" in caplog.text
    assert db.conn.execute("SELECT COUNT(*) FROM fragmentation").fetchone()[0] == 25


def test_retention_defaults_and_config_loading(tmp_path):
    for cfg, expected in ((AppConfig(), RETENTION), (load_config(), SHIPPED_RETENTION)):
        assert {name: getattr(cfg, name) for name in RETENTION} == expected
    custom = {name: index + 1 for index, name in enumerate(RETENTION)}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"retention": custom}))
    cfg = load_config(path)
    assert {name: getattr(cfg, name) for name in RETENTION} == custom


def test_cli_prune_uses_configured_retention(db, tmp_path, monkeypatch):
    at = NOW - timedelta(days=3)
    add_call(db, "settled", at, "settled", billed=at)
    db.start_run("old", "fast", at)
    db.store_validators("https://example.com/old", '"etag"', "", at)
    assert db.claim_outgoing("test", "failed", "old", at)
    db.finish_outgoing("test", "failed", at, rejected=True)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"retention": dict.fromkeys(RETENTION, 1)}))
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    assert cli.cmd_prune(argparse.Namespace(db=db.path, config=str(path))) == 0
    for table in ("llm_calls", "run_stats", "http_cache", "outgoing"):
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
