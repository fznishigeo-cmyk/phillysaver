"""296b: bounded expiry, retained reader windows, compact facts and run replay."""

import argparse
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import yaml

from dealradar import accuracy, cli, clicks, dedupe, source_audit, state_retention, util
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.keepa_rechecks import History, Schedule
from dealradar.util import iso
from dealradar.volatility import Plan
from tests.test_dedupe import make_cfg, make_extracted, make_post, make_savings
from tests.test_product_entities import insert

NOW = datetime(2026, 10, 18, 23, 30, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=100)


@pytest.fixture
def db(tmp_path):
    state = DB(tmp_path / "state.db")
    yield state
    state.close()


def configured(**changes):
    return AppConfig(accuracy_samples_days=9, deal_click_shown_days=8,
                     source_audit_posts_days=9, keepa_recheck_history_samples=12, compact_deal_payloads=True,
                     **changes)


def prune(db, cfg=None, *, run="maintenance", now=NOW, **kwargs):
    cfg = cfg or configured()
    db.start_run(run, "fast", now)
    db.prune(now, run_key=run, state_retention=state_retention.options(cfg),
             posts_seen_days=cfg.posts_seen_days, deals_days=cfg.deals_days, **kwargs)
    return json.loads(db.conn.execute("SELECT state_maintenance FROM run_stats WHERE run_key=?",
                                     (run,)).fetchone()[0])


def sample(db, key, at, *, bucket="rest", outcome="match"):
    db.conn.execute("INSERT INTO accuracy_samples VALUES (?,?,?,?,?,?,?,?)",
                    (key, key, iso(at), iso(at), bucket, "Amazon", "keepa", outcome))


def test_missing_and_null_knobs_retain_legacy_behavior(db, tmp_path):
    path = tmp_path / "config.yaml"
    for config in ({}, {"retention": {name: None for name in (
            "accuracy_samples_days", "deal_click_shown_days", "source_audit_posts_days",
            "keepa_recheck_history_samples")}}):
        path.write_text(yaml.safe_dump(config))
        assert state_retention.options(load_config(path)) is None
    assert state_retention.options(AppConfig()) is None
    insert(db, "live", now=OLD)
    insert(db, "expired", now=OLD)
    db.update_deal("expired", status="dead")
    sample(db, "ten-days", NOW - timedelta(days=10))
    sample(db, "expired", NOW - timedelta(days=36))
    db.conn.execute("INSERT INTO deal_click_shown VALUES ('shown',1,60,1)")
    db.conn.execute("INSERT INTO keepa_recheck_history VALUES ('live',?)", (json.dumps([{}] * 30),))
    before = db.conn.execute("SELECT payload FROM deals WHERE dedupe_key='live'").fetchone()[0]
    db.conn.commit()
    db.prune(NOW)
    assert [r[0] for r in db.conn.execute("SELECT deal_key FROM accuracy_samples")] == ["ten-days"]
    assert db.conn.execute("SELECT COUNT(*) FROM deal_click_shown").fetchone()[0] == 1
    assert len(json.loads(db.conn.execute("SELECT samples FROM keepa_recheck_history").fetchone()[0])) == 30
    assert db.conn.execute("SELECT payload FROM deals WHERE dedupe_key='live'").fetchone()[0] == before
    assert db.get_deal("expired") is None


@pytest.mark.parametrize("name,value", [
    *[(name, value) for name in ("accuracy_samples_days", "deal_click_shown_days", "source_audit_posts_days",
                               "keepa_recheck_history_samples") for value in (0, -1, True, "9", 1.5)],
    ("compact_deal_payloads", "true"), ("state_maintenance_max_rows", 0),
    ("state_maintenance_max_rows", True), ("state_maintenance_budget_s", float("inf")),
    ("state_maintenance_budget_s", float("nan")), ("state_maintenance_budget_s", True),
])
def test_invalid_knobs_fail_closed(tmp_path, name, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"retention": {name: value}}))
    with pytest.raises(ValueError, match="retention"):
        load_config(path)


def test_shipped_values_and_reader_floors():
    cfg = load_config()
    options = state_retention.options(cfg)
    assert (options["accuracy_days"], options["click_hours"], options["history_samples"]) == (14, 192, 12)
    assert cfg.deals_days == 14 and cfg.posts_seen_days == 30
    assert options["audit_days"] == 9
    cfg.accuracy_samples_days = cfg.deal_click_shown_days = cfg.keepa_recheck_history_samples = 1
    cfg.raw["first_party_clicks"]["lookback_hours"] = 720
    cfg.keepa_recheck_adaptive["history_samples"] = 24
    options = state_retention.options(cfg)
    assert (options["accuracy_days"], options["click_hours"], options["history_samples"]) == (14, 720, 24)


@pytest.mark.parametrize("zone,now", [
    ("America/New_York", datetime(2026, 11, 2, 4, 30, tzinfo=timezone.utc)),
    ("Pacific/Kiritimati", NOW), ("Pacific/Pago_Pago", NOW),
])
def test_accuracy_completed_local_week_boundaries_and_recent_sampling(db, zone, now):
    cfg = configured(timezone=zone)
    cutoff = now - timedelta(days=9)
    sample(db, "expired", cutoff - timedelta(seconds=1))
    sample(db, "boundary", cutoff)
    sample(db, "future", now + timedelta(days=1))
    local = now.astimezone(ZoneInfo(zone))
    for day in range(1, 8):
        at = (local - timedelta(days=day)).replace(hour=0, minute=0)
        sample(db, "day" + str(day), at, bucket="top30", outcome="higher" if day == 3 else "match")
    sample(db, "six-hours", now - timedelta(hours=6))
    sample(db, "recent", now - timedelta(hours=1))
    db.conn.commit()
    before = accuracy.report_data(db, cfg, now)
    recent_sql = "SELECT deal_key FROM accuracy_samples WHERE checked_at>=? ORDER BY deal_key"
    recent = list(map(tuple, db.conn.execute(recent_sql, (iso(now - timedelta(hours=6)),))))
    prune(db, cfg, now=now)
    assert accuracy.report_data(db, cfg, now) == before
    assert list(map(tuple, db.conn.execute(recent_sql, (iso(now - timedelta(hours=6)),)))) == recent
    assert db.conn.execute("SELECT 1 FROM accuracy_samples WHERE deal_key='expired'").fetchone() is None
    assert db.conn.execute("SELECT 1 FROM accuracy_samples WHERE deal_key='boundary'").fetchone()


@pytest.mark.parametrize("lookback", [168, 360, 720])
def test_click_features_shadow_report_and_coverage_keep_reader_window(db, lookback):
    from tests.test_click_signal import published

    cfg = configured(raw={"first_party_clicks": {"enabled": True, "lookback_hours": lookback}})
    rows = published()
    end = int(NOW.timestamp() // 3600)
    window = max(192, lookback)
    for hour in (end - window - 1, end - window, end - lookback, end - 1, end, end + 1):
        db.conn.execute("INSERT OR IGNORE INTO deal_click_coverage VALUES (?,0)", (hour,))
        for d in rows:
            db.conn.execute("INSERT OR IGNORE INTO deal_click_shown VALUES (?,?,3600,1)", (d["key"], hour))
            db.conn.execute("INSERT OR IGNORE INTO deal_clicks VALUES (?,?,3)", (d["key"], hour))
    db.conn.commit()
    before = clicks.features(db, cfg, NOW, rows)
    clicks.record_shadow(db, cfg, NOW, rows, run_key="published")
    report = clicks.report_data(db, cfg)
    prune(db, cfg)
    assert clicks.features(db, cfg, NOW, rows) == before
    assert clicks.report_data(db, cfg) == report
    assert clicks.shadow(before, cfg) == clicks.shadow(clicks.features(db, cfg, NOW, rows), cfg)
    assert min(r[0] for r in db.conn.execute("SELECT hour FROM deal_click_shown")) == end - window
    snapshot = list(db.conn.iterdump())
    clicks.observe_publication(db, cfg, NOW + timedelta(minutes=10), rows, run_key="published")
    assert list(db.conn.iterdump()) == snapshot


def test_keepa_retains_all_scheduler_cost_stability_and_replay_facts(db):
    from tests.test_price_accuracy_engine import product, seed
    from tests.test_value_per_token_rechecks import train

    cfg = load_config()
    deal = seed(db, cfg)
    train(db, cfg, deal)
    history = History(db, cfg, NOW)
    retained = history.samples[deal["dedupe_key"]]
    db.conn.execute("INSERT INTO keepa_recheck_history VALUES ('orphan','[]')")
    db.conn.commit()
    schedule = Schedule(history, {deal["dedupe_key"]: 0}, {})
    plan = Plan(history, [deal], {deal["dedupe_key"]: 0}, {}, 15)
    before = (history.request_coverage([deal]), history.latest_request(deal["dedupe_key"]),
              schedule.probability(deal, 2), plan.facts, plan.costs, history.observations)
    prune(db, cfg)
    after = History(db, cfg, NOW)
    after_plan = Plan(after, [deal], {deal["dedupe_key"]: 0}, {}, 15)
    assert (after.request_coverage([deal]), after.latest_request(deal["dedupe_key"]),
            Schedule(after, {deal["dedupe_key"]: 0}, {}).probability(deal, 2),
            after_plan.facts, after_plan.costs, after.observations) == before
    assert after.samples[deal["dedupe_key"]] == retained
    assert "orphan" not in after.samples
    after.record(deal, product(2500), True, NOW, "top_new", 1, 0)
    snapshot = list(db.conn.iterdump())
    History(db, cfg, NOW).record(deal, product(2500), True, NOW, "top_new", 1, 0)
    assert list(db.conn.iterdump()) == snapshot


def test_keepa_old_excess_samples_trim_to_configured_reader_floor(db):
    insert(db, "live", now=OLD)
    rows = [{"requested_at": iso(OLD + timedelta(hours=i)), "stable_since": iso(OLD)} for i in range(30)]
    db.conn.execute("INSERT INTO keepa_recheck_history VALUES ('live',?)", (json.dumps(rows),))
    db.conn.commit()
    cfg = configured()
    cfg.keepa_recheck_adaptive = {"history_samples": 20}
    prune(db, cfg)
    assert json.loads(db.conn.execute("SELECT samples FROM keepa_recheck_history").fetchone()[0]) == rows[-20:]


def test_deal_expiry_live_rows_recent_deaths_and_revival(db):
    cfg = make_cfg()
    post, extracted = make_post(), make_extracted(product_url="https://www.target.com/p/widget")
    post.published_at = OLD
    res = dedupe.upsert_extracted(db, extracted, make_savings(), post, cfg, OLD)
    key = res["dedupe_key"]
    for name, at in (("expired", NOW - timedelta(days=14, seconds=1)),
                     ("boundary", NOW - timedelta(days=14)), ("gone", NOW - timedelta(hours=1))):
        insert(db, name, now=OLD)
        d = db.get_deal(name)
        db.update_deal(name, status="dead", payload=dict(d, died_at=iso(at), death="api_confirmed"))
    d = db.get_deal(key)
    db.update_deal(key, status="dead", payload=dict(d, died_at=iso(NOW - timedelta(days=1)), death="price_moved"))
    insert(db, "ancient-live", now=OLD)
    before = db.get_deal(key)
    prune(db)
    assert db.get_deal("expired") is None
    assert all(db.get_deal(k) for k in ("boundary", "gone", "ancient-live"))
    assert db.get_deal(key) == before
    post = make_post(uid="revival", text="A newly verified offer")
    post.published_at = NOW
    result = dedupe.upsert_extracted(db, extracted, make_savings(), post, cfg, NOW)
    assert result["revived"] and not result["is_new_deal"]
    assert db.get_deal(key)["status"] == "alive"
    count = db.conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
    dedupe.upsert_extracted(db, extracted, make_savings(), post, cfg, NOW)
    assert db.conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0] == count


def test_compact_payload_rehydrates_columns_and_only_matching_sightings(db):
    cfg = make_cfg()
    ext = make_extracted(product_url="https://www.target.com/p/widget")
    for i in range(4):
        res = dedupe.upsert_extracted(db, ext, make_savings(), make_post(uid=str(i)), cfg, NOW)
    key = res["dedupe_key"]
    before = db.get_deal(key)
    raw = db.conn.execute("SELECT payload FROM deals WHERE dedupe_key=?", (key,)).fetchone()[0]
    prune(db)
    saved = db.conn.execute("SELECT payload FROM deals WHERE dedupe_key=?", (key,)).fetchone()[0]
    assert len(saved) < len(raw) * .7
    assert json.loads(saved)["_sightings_in_table"]
    assert "sightings" not in json.loads(saved) and "item" not in json.loads(saved)
    assert db.get_deal(key) == before
    assert db.all_deals() == [before] and db.deals_by_store(before["store"]) == [before]
    assert db.deal_by_canonical_url(before["canonical_url"]) == before
    sighting = before["sightings"][0]
    assert db.deals_for_post(sighting["source_name"], sighting["source_url"]) == [before]
    db.update_deal(key, payload=before)
    assert db.conn.execute("SELECT payload FROM deals WHERE dedupe_key=?", (key,)).fetchone()[0] == saved
    # Do not discard legacy facts which were never copied into sightings.
    before["sightings"].append(dict(sighting, source_name="legacy-only"))
    db.update_deal(key, payload=before)
    assert db.get_deal(key) == before


def test_posts_audit_reports_recent_parses_pending_discoveries_and_dedupe(db):
    from tests.test_source_audit import source

    cfg = configured()
    cutoff = NOW - timedelta(days=30)
    for name, at in (("expired", cutoff - timedelta(seconds=1)), ("boundary", cutoff),
                     ("recent", NOW - timedelta(days=1))):
        post = make_post(source="partner", uid=name)
        db.audit_seen([post], at)
        db.record_post(post, at)
        db.audit_parsed(post, dict(extracted=1, evidence=1), at)
    pending = make_post(source="partner", uid="pending")
    late = make_post(source="partner", uid="late")
    db.audit_seen([pending, late], OLD)
    db.record_post(late, NOW)
    db.audit_parsed(late, dict(extracted=3, evidence=2), NOW)
    before = source_audit._history(db, source(), NOW, 7, OLD)
    deferred = source_audit.intake_deferred(db, [source()], NOW)
    prune(db, cfg)
    assert source_audit._history(db, source(), NOW, 7, OLD) == before
    assert source_audit.intake_deferred(db, [source()], NOW) == deferred
    for name in ("boundary", "recent", "late"):
        post = make_post(source="partner", uid=name)
        assert db.post_is_known(post.content_hash) and not db.record_post(post, NOW)
        assert db.post_first_seen(post.content_hash) is not None
    assert not db.post_is_known(make_post(source="partner", uid="expired").content_hash)
    # A known 10-day-old post outlives its audit row; refetching it must not
    # invent a new discovery or make it eligible for another paid parse.
    post = make_post(source="partner", uid="ten-days")
    db.audit_seen([post], NOW - timedelta(days=10))
    db.record_post(post, NOW - timedelta(days=10))
    db.audit_parsed(post, dict(extracted=1, evidence=1), NOW - timedelta(days=10))
    prune(db, cfg, run="later")
    assert db.post_is_known(post.content_hash)
    db.audit_seen([post], NOW)
    assert db.conn.execute("SELECT 1 FROM source_audit_posts WHERE content_hash=?",
                           (post.content_hash,)).fetchone() is None


def test_every_table_is_bounded_replay_safe_and_finishes_across_runs(db):
    for i in range(150):
        key = f"old-{i:03}"
        sample(db, key, OLD)
        db.conn.execute("INSERT INTO deal_click_shown VALUES (?,1,60,1)", (key,))
        db.conn.execute("INSERT INTO keepa_recheck_history VALUES (?,'[]')", (key,))
        db.conn.execute("INSERT INTO posts_seen(source_name,uid,url,content_hash,first_seen_at)"
                        " VALUES ('feed',?,'',?,?)", (key, key, iso(OLD)))
        db.conn.execute("INSERT INTO source_audit_posts(source_name,content_hash,first_seen_at,parsed_at)"
                        " VALUES ('feed',?,?,?)", (key, iso(OLD), iso(OLD)))
        insert(db, key, now=OLD)
        db.update_deal(key, status="dead")
    cfg = configured(state_maintenance_max_rows=65, state_maintenance_budget_s=5)
    counts = prune(db, cfg, fast_lane=True)
    assert all(c["examined"] == 65 and c["deleted"] == 65 for c in counts.values())
    snapshot = list(db.conn.iterdump())
    assert prune(db, cfg, fast_lane=True) == counts
    assert list(db.conn.iterdump()) == snapshot
    for run in ("next", "last"):
        prune(db, cfg, run=run, fast_lane=True)
    for table in counts:
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_deadline_rollback_and_resume_are_atomic(db, monkeypatch):
    from tests.test_incremental_cache import ConnectionHook

    for i in range(70):
        sample(db, str(i), OLD)
    db.conn.commit()
    clock = [0.0]
    monkeypatch.setattr(state_retention.time, "monotonic", lambda: clock[0])
    real = db.conn
    def hook(when, sql):
        if when == "after" and sql.startswith("DELETE FROM accuracy_samples WHERE rowid"):
            clock[0] = 2.0
    db.conn = ConnectionHook(real, hook)
    try:
        assert prune(db, deadline=1.0) == {}
    finally:
        db.conn = real
    assert db.conn.execute("SELECT COUNT(*) FROM accuracy_samples").fetchone()[0] == 70
    assert prune(db, deadline=1.0) == {}
    clock[0] = 0
    assert prune(db, deadline=1.0)["accuracy_samples"]["deleted"] == 70


def test_cli_wiring_uses_reader_floors_and_bounded_progress(db, tmp_path, monkeypatch):
    sample(db, "old", OLD)
    sample(db, "needed", NOW - timedelta(days=7, hours=12))
    db.conn.commit()
    path = tmp_path / "config.yaml"
    path.write_text("retention:\n  accuracy_samples_days: 1\n")
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    assert cli.cmd_prune(argparse.Namespace(db=db.path, config=str(path))) == 0
    assert [r[0] for r in db.conn.execute("SELECT deal_key FROM accuracy_samples")] == ["needed"]
    assert json.loads(db.conn.execute("SELECT state_maintenance FROM run_stats").fetchone()[0])


def test_fresh_prefix_and_deleted_high_water_do_not_starve_dead_rows(db):
    for i in range(8):
        insert(db, str(i), now=OLD)
        if i >= 5:
            db.update_deal(str(i), status="dead")
    cfg = configured(state_maintenance_max_rows=2)
    for i in range(4):
        counts = prune(db, cfg, run=str(i))
        assert counts["deals"]["examined"] == 2
    assert [d["dedupe_key"] for d in db.all_deals()] == [str(i) for i in range(5)]
    assert json.loads(db.meta_get("state_sweep:deals")) is None
    # Even removal of the captured final row completes the next sweep.
    prune(db, cfg, run="again")
    db.conn.execute("DELETE FROM deals WHERE dedupe_key='4'")
    db.conn.commit()
    for run in ("resume", "finish"):
        prune(db, cfg, run=run)
    assert json.loads(db.meta_get("state_sweep:deals")) is None


def test_interrupted_run_rotates_tables_and_keeps_prior_commits(db, monkeypatch):
    from tests.test_incremental_cache import ConnectionHook

    for i in range(70):
        sample(db, str(i), OLD)
    db.conn.execute("INSERT INTO deal_click_shown VALUES ('old',1,60,1)")
    db.conn.commit()
    clock = [0.0]
    monkeypatch.setattr(state_retention.time, "monotonic", lambda: clock[0])
    def hook(when, sql):
        if when == "committed":
            clock[0] = 2.0
    real = db.conn
    db.conn = ConnectionHook(real, hook)
    try:
        first = prune(db, deadline=1.0)
        assert first["accuracy_samples"]["deleted"] == 64
        clock[0] = 0
        second = prune(db, deadline=1.0)
        assert second["accuracy_samples"]["deleted"] == 64
        assert second["deal_click_shown"]["deleted"] == 1
    finally:
        db.conn = real


def test_expired_rows_use_indexes_and_limit_before_loading(db):
    queries = [
        ("SELECT rowid FROM accuracy_samples WHERE checked_at<? ORDER BY checked_at LIMIT 64", iso(NOW)),
        ("SELECT rowid FROM deal_click_shown WHERE hour<? ORDER BY hour LIMIT 64", 1),
        ("SELECT rowid FROM posts_seen WHERE first_seen_at<? ORDER BY first_seen_at LIMIT 64", iso(NOW)),
    ]
    for query, cutoff in queries:
        plan = " ".join(r[3] for r in db.conn.execute("EXPLAIN QUERY PLAN " + query, (cutoff,)))
        assert "SEARCH" in plan and "INDEX" in plan and "TEMP B-TREE" not in plan


def test_compacted_publication_preserves_every_file_and_read_only_consumers(db, tmp_path, monkeypatch):
    from dealradar.publish import site
    from tests.test_checker_index import files
    from tests.test_checker_page import config, render
    from tests.test_publish_site import NOW as published_at

    monkeypatch.setattr(site, "util_now", lambda: published_at)
    cfg = config()
    out = tmp_path / "site"
    render(db, out, cfg)
    for d in db.all_deals():
        # Exercise real normalized sightings, historical facts and exact credits.
        for i, s in enumerate(d.get("sightings") or []):
            db.add_sighting(d["id"], source_name=s["source_name"], source_url=s["source_url"],
                            content_hash="original" + str(i), seen_at=util.parse_iso(s["seen_at"]),
                            final_price=s["final_price"])
        d["sightings"] = db.sightings_for(d["id"])
        db.update_deal(d["dedupe_key"], payload=d)
        db.record_price(d["dedupe_key"], d["store"], 50,
                        published_at - timedelta(days=400), "old-post")
    rows = db.all_deals()
    site.render_site(db, cfg, published_at, out_dir=out, deals=rows)
    before = files(out)
    prune(db, now=published_at)
    assert db.all_deals() == rows
    with_db = DB(db.path, read_only=True)
    try:
        assert with_db.all_deals() == rows
    finally:
        with_db.close()
    site.render_site(db, cfg, published_at, out_dir=out, deals=db.all_deals())
    assert files(out) == before


def test_fast_lane_defers_vacuum_hourly_keeps_existing_policy(db):
    db.conn.execute("CREATE TABLE fragmentation(payload BLOB)")
    db.conn.executemany("INSERT INTO fragmentation VALUES (?)", [(bytes(3000),)] * 200)
    db.conn.execute("DELETE FROM fragmentation WHERE rowid<=150")
    db.conn.commit()
    statements = []
    db.conn.set_trace_callback(statements.append)
    prune(db, fast_lane=True)
    assert "VACUUM" not in statements
    assert db.conn.execute("PRAGMA freelist_count").fetchone()[0] > 0
    prune(db, run="hourly")
    assert "VACUUM" in statements
    assert db.conn.execute("PRAGMA freelist_count").fetchone()[0] == 0


@pytest.mark.parametrize("new_sweep", [False, True])
@pytest.mark.parametrize("table", ["deals", "keepa_recheck_history", "source_audit_posts"])
def test_resumed_run_does_not_repeat_sweep_finished_by_another_connection(db, monkeypatch, new_sweep, table):
    from tests.test_incremental_cache import ConnectionHook

    for i in range(4):
        insert(db, str(i), now=NOW)
        db.conn.execute("INSERT INTO keepa_recheck_history VALUES (?,'[]')", (str(i),))
        db.audit_seen([make_post(uid=str(i))], NOW)
    cfg = configured(state_maintenance_budget_s=5)
    monkeypatch.setattr(state_retention, "CHUNK_ROWS", 2)
    clock = [0.0]
    monkeypatch.setattr(state_retention.time, "monotonic", lambda: clock[0])
    db.meta_set("state_maintenance_next_table", table)
    real = db.conn

    def interrupt_after_chunk(when, sql):
        if when == "committed":
            clock[0] = 10.0

    db.conn = ConnectionHook(real, interrupt_after_chunk)
    try:
        assert prune(db, cfg, run="first", fast_lane=True)[table]["examined"] == 2
    finally:
        db.conn = real
    other = DB(db.path)
    try:
        clock[0] = 0
        assert prune(other, cfg, run="second", fast_lane=True)[table]["examined"] == 2
        assert json.loads(other.meta_get("state_sweep:" + table)) is None
        if new_sweep:
            other.meta_set("state_maintenance_next_table", table)
            real_other = other.conn
            other.conn = ConnectionHook(real_other, interrupt_after_chunk)
            try:
                prune(other, cfg, run="third", fast_lane=True)
            finally:
                other.conn = real_other
    finally:
        other.close()
    cursor = db.meta_get("state_sweep:" + table)
    clock[0] = 0
    resumed = prune(db, cfg, run="first", fast_lane=True)[table]
    assert resumed["examined"] == 2  # its original sweep already finished
    assert resumed["completed"]
    assert db.meta_get("state_sweep:" + table) == cursor


@pytest.mark.parametrize("repair", ["recategorize_live_deals", "recategorize_other"])
@pytest.mark.parametrize("compact_writes", [False, True])
def test_raw_category_repairs_preserve_compacted_facts_and_social_stock(db, repair, compact_writes):
    from dealradar.keepa_categories import NAMES_META, ROOTS_META
    from dealradar.parse import extractor
    from dealradar.publish.social import plenty_of_stock

    cfg = make_cfg()
    cfg.social_require_stock_evidence = True
    ext = make_extracted(product_url="https://www.amazon.com/dp/B012345678", category="other")
    for source in ("slickdeals", "dealnews"):
        res = dedupe.upsert_extracted(db, ext, make_savings(), make_post(source=source), cfg, NOW)
    key = res["dedupe_key"]
    before = db.get_deal(key)
    assert plenty_of_stock(db, before, cfg)
    prune(db)
    db.compact_deal_payloads = compact_writes  # previously compacted rows survive switching off
    db.meta_set(NAMES_META, json.dumps({"101": "Home & Kitchen"}))
    db.meta_set(ROOTS_META, json.dumps({"B012345678": "101"}))
    getattr(extractor, repair)(db, None)
    assert db.get_deal(key) == dict(before, category="home")
    assert plenty_of_stock(db, db.get_deal(key), cfg)
    raw = json.loads(db.conn.execute("SELECT payload FROM deals WHERE dedupe_key=?", (key,)).fetchone()[0])
    assert raw["_sightings_in_table"] and "sightings" not in raw
