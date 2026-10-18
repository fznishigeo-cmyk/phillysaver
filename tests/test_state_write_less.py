"""296c: lossless compact writers, mixed-state readers and bounded migration."""

import json
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from dealradar import accuracy, clicks, dedupe, state_retention
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.util import iso
from tests.test_click_signal import published
from tests.test_dedupe import make_cfg, make_extracted, make_post, make_savings
from tests.test_state_growers import NOW, configured, db, prune, sample


def config(compact=True):
    cfg = configured(compact_accuracy_samples=compact, compact_click_exposure=compact)
    cfg.raw["first_party_clicks"] = {"enabled": True}
    return cfg


def shown(db, at):
    db._accuracy_published = {
        key: {"created_run": "original", "at": iso(at - timedelta(days=50)),
              "final": 25, "bucket": bucket, "store": "Amazon", "checkout": key == "checkout"}
        for key, bucket in (("top", "top30"), ("middle", "top120"), ("rest", "rest"), ("checkout", "rest"))}


@pytest.mark.parametrize("name", ["compact_accuracy_samples", "compact_click_exposure"])
@pytest.mark.parametrize("value", [None, "true", 1, [], {}])
def test_strict_knobs(tmp_path, name, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"retention": {name: value}}))
    with pytest.raises(ValueError, match="retention"):
        load_config(path)


def test_missing_false_and_legacy_writer_bytes(tmp_path):
    path = tmp_path / "config.yaml"
    snapshots = []
    for name, raw in (("missing", {}), ("false", {"retention": {
            "compact_accuracy_samples": False, "compact_click_exposure": False}})):
        path.write_text(yaml.safe_dump(raw))
        cfg = load_config(path)
        assert not cfg.compact_accuracy_samples and not cfg.compact_click_exposure
        state = DB(tmp_path / (name + ".db"))
        try:
            shown(state, NOW)
            accuracy.record(state, cfg, "top", NOW, source="keepa", price=25, run_key="sample")
            assert tuple(state.conn.execute("SELECT * FROM accuracy_samples").fetchone()) == (
                "top", "sample", iso(NOW), iso(NOW), "top30", "Amazon", "keepa", "match")
            cfg.raw["first_party_clicks"] = {"enabled": True}
            clicks.observe_publication(state, cfg, NOW, published(2), run_key="one")
            clicks.observe_publication(state, cfg, NOW + timedelta(minutes=15), published(2), run_key="two")
            assert [tuple(r)[1:] for r in state.conn.execute("SELECT * FROM deal_click_shown")] == [
                (int(NOW.timestamp() // 3600), 900., 1), (int(NOW.timestamp() // 3600), 900., 2)]
            assert not accuracy.compact_exists(state) and not clicks.compact_exists(state)
            snapshots.append(state.conn.serialize())
        finally:
            state.close()
    assert snapshots[0] == snapshots[1]
    assert load_config().compact_accuracy_samples and load_config().compact_click_exposure


@pytest.mark.parametrize("zone,now", [
    ("America/New_York", datetime(2026, 11, 2, 4, 30, tzinfo=timezone.utc)),
    ("America/New_York", datetime(2026, 3, 9, 4, 30, tzinfo=timezone.utc)),
    ("Pacific/Kiritimati", NOW), ("Pacific/Pago_Pago", NOW),
])
def test_accuracy_equivalence_receipts_recent_reads_and_rollback(tmp_path, zone, now):
    states = [DB(tmp_path / f"accuracy-{i}.db") for i in range(2)]
    configs = [config(False), config()]
    try:
        for state in states:
            shown(state, now)
        for cfg in configs:
            cfg.timezone = zone
        for hour in range(0, 10 * 24, 6):
            at = now - timedelta(hours=240 - hour, microseconds=100)
            # Exercise old -> compact -> old -> compact within one retained window.
            configs[1].compact_accuracy_samples = hour % 24 != 0
            for state, cfg in zip(states, configs):
                for key in state._accuracy_published:
                    for source in ("keepa", "woot"):
                        args = dict(source=source, price=(25, 30, 20)[hour // 6 % 3],
                                    unavailable=hour % 30 == 0, run_key=f"run-{hour}")
                        accuracy.record(state, cfg, key, at, **args)
                        accuracy.record(state, cfg, key, at + timedelta(seconds=1), **args)
                        accuracy.record(state, cfg, key, at + timedelta(minutes=1),
                                        **dict(args, observed=at, run_key=f"cached-{hour}"))
        legacy = accuracy.report_data(states[0], configs[0], now)
        assert accuracy.report_data(states[1], configs[1], now) == legacy
        assert accuracy.report_lines(accuracy.report_data(states[1], configs[1], now)) == accuracy.report_lines(legacy)
        for hours in (6, 12, 24):
            assert accuracy.recent_keys(states[0], now - timedelta(hours=hours)) == accuracy.recent_keys(
                states[1], now - timedelta(hours=hours))
        count = sum(states[1].conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("accuracy_samples", "accuracy_samples_compact"))
        assert count == states[0].conn.execute("SELECT COUNT(*) FROM accuracy_samples").fetchone()[0]
        configs[1].compact_accuracy_samples = True
        prune(states[1], configs[1], now=now)
        assert accuracy.report_data(states[1], configs[1], now) == legacy
        assert states[1].conn.execute("SELECT COUNT(*) FROM accuracy_samples").fetchone()[0] == 0
        read_only = DB(states[1].path, read_only=True)
        try:
            assert accuracy.report_data(read_only, AppConfig(timezone=zone), now) == legacy
        finally:
            read_only.close()
    finally:
        for state in states:
            state.close()


@pytest.mark.parametrize("lookback", [1, 23, 168, 720])
def test_exposure_features_exact_through_partial_coverage_gaps_and_toggle(tmp_path, lookback):
    states = [DB(tmp_path / f"click-{i}.db") for i in range(2)]
    configs = [config(False), config()]
    rows = published(4)
    start = NOW - timedelta(days=2, minutes=5, microseconds=123456)
    try:
        for cfg in configs:
            cfg.raw["first_party_clicks"]["lookback_hours"] = lookback
        for i in range(0, 192):
            at = start + timedelta(minutes=15 * i)
            configs[1].compact_click_exposure = i % 5 != 0
            for state, cfg in zip(states, configs):
                clicks.observe_publication(state, cfg, at, rows[:1 + i % 4] if i % 13 else [], run_key=f"r{i}")
                clicks.observe_publication(state, cfg, at + timedelta(minutes=1), rows, run_key=f"r{i}")
                if i % 4 == 0:
                    hour = int(at.timestamp() // 3600)
                    # Missing coverage and late-arriving/capped buckets are material.
                    if i % 12:
                        state.conn.execute("INSERT OR REPLACE INTO deal_click_coverage VALUES (?,?)", (hour, i % 8 == 0))
                    state.conn.execute("INSERT OR REPLACE INTO deal_clicks VALUES (?,?,?)", (rows[0]["key"], hour, i % 7))
                    state.conn.commit()
            if i % 19 == 0:
                assert clicks.features(states[0], configs[0], at, rows) == clicks.features(states[1], configs[1], at, rows)
        for state, cfg in zip(states, configs):
            clicks.observe_publication(state, cfg, NOW + timedelta(hours=2), rows, run_key="outage")
        expected = clicks.features(states[0], configs[0], NOW, rows)
        assert clicks.features(states[1], configs[1], NOW, rows) == expected
        configs[1].compact_click_exposure = True
        prune(states[1], configs[1])
        assert clicks.features(states[1], configs[1], NOW, rows) == expected
        configs[1].compact_click_exposure = False
        assert clicks.features(states[1], configs[1], NOW, rows) == expected
        assert clicks.shadow(expected, configs[0]) == clicks.shadow(clicks.features(states[1], configs[1], NOW, rows), configs[1])
    finally:
        for state in states:
            state.close()


def test_migration_limits_replay_expiry_and_orphan_dictionaries(db):
    cfg = config()
    cfg.state_maintenance_max_rows = 64
    for i in range(150):
        sample(db, f"key-{i}", NOW - timedelta(days=i % 11))
        db.conn.execute("INSERT INTO deal_click_shown VALUES (?,?,?,1)",
                        (f"key-{i}", int(NOW.timestamp() // 3600) - i * 24, i % 3 * 1800))
    db.conn.commit()
    before = accuracy.report_data(db, cfg, NOW)
    first = prune(db, cfg)
    assert first["accuracy_samples"]["examined"] == first["deal_click_shown"]["examined"] == 64
    saved = list(db.conn.iterdump())
    prune(db, cfg)
    assert list(db.conn.iterdump()) == saved
    for i in range(4):
        prune(db, cfg, run=f"continue-{i}")
    assert accuracy.report_data(db, cfg, NOW) == before
    assert db.conn.execute("SELECT COUNT(*) FROM accuracy_samples").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM deal_click_shown").fetchone()[0] == 0
    for i in range(5):
        prune(db, cfg, run=f"expire-{i}", now=NOW + timedelta(days=40))
    for table in ("accuracy_samples_compact", "accuracy_sample_keys", "accuracy_sample_runs", "deal_click_days"):
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_compact_receipts_survive_retention_boundary_and_legacy_cleanup(db):
    cfg = config()
    shown(db, NOW)
    cutoff = NOW - timedelta(days=9)
    for i, at in enumerate((cutoff - timedelta(seconds=1), cutoff, NOW, NOW + timedelta(days=1))):
        accuracy.record(db, cfg, "top", at, source="keepa", price=25, run_key=str(i))
    prune(db, cfg)
    assert [r[0] for r in db.conn.execute("SELECT checked_at FROM accuracy_samples_compact ORDER BY checked_at")] == [
        int(at.timestamp()) for at in (cutoff, NOW, NOW + timedelta(days=1))]
    db.prune(NOW + timedelta(days=50))  # all new knobs omitted on rollback
    assert db.conn.execute("SELECT COUNT(*) FROM accuracy_sample_keys").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM accuracy_sample_runs").fetchone()[0] == 0


@pytest.mark.parametrize("lookback", [168, 720])
def test_daily_exposure_retains_boundary_hours_zero_rows_and_future(db, lookback):
    cfg = config()
    cfg.deal_click_shown_days = 1
    cfg.raw["first_party_clicks"]["lookback_hours"] = lookback
    rows = published(1)
    end = int(NOW.timestamp() // 3600)
    cutoff = end - lookback
    for hour, seconds in ((cutoff - 25, 3600), (cutoff - 1, 3600), (cutoff, 100.125),
                          (end - 1, 0), (end, 1000), (end + 24, 2000)):
        db.conn.execute("INSERT INTO deal_click_shown VALUES (?,?,?,1)", (rows[0]["key"], hour, seconds))
        db.conn.execute("INSERT INTO deal_click_coverage VALUES (?,0)", (hour,))
        db.conn.execute("INSERT INTO deal_clicks VALUES (?,?,3)", (rows[0]["key"], hour))
    db.conn.commit()
    expected = clicks.features(db, cfg, NOW, rows)
    prune(db, cfg)
    assert clicks.features(db, cfg, NOW, rows) == expected
    assert min(r[0] for r in db.conn.execute("SELECT day FROM deal_click_days")) == cutoff // 24
    assert dict(((key, hour), seconds) for key, hour, seconds in clicks.exposure(db, end, end + 25))[
        rows[0]["key"], end + 24] == 2000


@pytest.mark.parametrize("table", ["accuracy_samples", "deal_click_shown"])
def test_interrupted_migration_rolls_back_data_schema_and_cursor(db, monkeypatch, table):
    from tests.test_incremental_cache import ConnectionHook

    cfg = config()
    cfg.compact_accuracy_samples = table == "accuracy_samples"
    cfg.compact_click_exposure = table == "deal_click_shown"
    sample(db, "recent", NOW - timedelta(days=1))
    db.conn.execute("INSERT INTO deal_click_shown VALUES ('recent',?,123.5,1)", (int(NOW.timestamp() // 3600) - 1,))
    db.conn.commit()
    db.meta_set("state_maintenance_next_table", table)
    clock = [0.]
    monkeypatch.setattr(state_retention.time, "monotonic", lambda: clock[0])
    real = db.conn
    def hook(when, sql):
        if when == "after" and sql.startswith(f"DELETE FROM {table} WHERE rowid"):
            clock[0] = 2.
    db.conn = ConnectionHook(real, hook)
    try:
        assert prune(db, cfg, deadline=1.) == {}
    finally:
        db.conn = real
    assert not accuracy.compact_exists(db) and not clicks.compact_exists(db)
    assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
    assert db.meta_get("state_maintenance_next_table") == table
    clock[0] = 0.
    assert prune(db, cfg, deadline=1.)[table]["updated"] == 1


def test_upsert_payload_bytes_fall_and_facts_match(tmp_path):
    states = [DB(tmp_path / f"upsert-{i}.db") for i in range(2)]
    try:
        for state, compact in zip(states, (False, True)):
            state.compact_deal_payloads = compact
            for i in range(20):
                result = dedupe.upsert_extracted(state, make_extracted(), make_savings(), make_post(uid=str(i)), make_cfg(), NOW)
            key = result["dedupe_key"]
        assert states[0].get_deal(key) == states[1].get_deal(key)
        payloads = [state.conn.execute("SELECT payload FROM deals").fetchone()[0].encode() for state in states]
        assert len(payloads[1]) < len(payloads[0]) * .3
        assert json.loads(payloads[1])["_sightings_in_table"]
    finally:
        for state in states:
            state.close()
