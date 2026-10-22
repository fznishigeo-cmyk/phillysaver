"""Offline state diagnostics, size gate, retention readers and replay anchors."""

import argparse
import gzip
import json
import sqlite3
from datetime import timedelta

import pytest
import yaml

from dealradar import appeal_score, cli, util, verify
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import indexnow
from dealradar.state_size import check_size, table_sizes, validate_limits
from dealradar.util import iso
from tests.test_lighter_state import add_call
from tests.test_product_entities import insert
from tests.test_publish_site import NOW
from tests.test_workflows import (KEY, _git, branch_blob, decrypt, origin_branches, repo,
                                 run_script, seed_encrypted_state)

RETENTION = dict(indexnow_batch_versions_days=7, appeal_scores_days=7)


@pytest.fixture
def db(tmp_path):
    state = DB(tmp_path / "state.db")
    yield state
    state.close()


def batch(db, key, at, status=200):
    versions = {"https://wowpriced.com/deal/" + key: key}
    db.indexnow_discover(versions, at, 7)
    assert db.indexnow_claim(indexnow.INDEXNOW_ENDPOINT, key, versions, at,
                            dict.fromkeys(versions, iso(at + timedelta(hours=6))))
    if status != "sending":
        db.indexnow_finish(indexnow.INDEXNOW_ENDPOINT, key, at, status)
    return versions


@pytest.mark.parametrize("status", [200, 202, 429, None, "sending"])
def test_batch_retention_preserves_receipts_pending_finishes_and_replays(db, status):
    old = NOW - timedelta(days=8)
    versions = batch(db, "old", old, status)
    batch(db, "boundary", NOW - timedelta(days=7), status)
    pages = db.indexnow_pages()
    cfg = AppConfig(custom_domain="wowpriced.com", seo_indexnow_key="0123456789abcdef")
    db.meta_set("indexnow_since", iso(old))
    metrics = indexnow.metrics(db, cfg, NOW)
    db.prune(NOW, **RETENTION)
    row = db.indexnow_batch(indexnow.INDEXNOW_ENDPOINT, "old")
    assert json.loads(row["versions"]) == (versions if status == "sending" else {})
    assert json.loads(db.indexnow_batch(indexnow.INDEXNOW_ENDPOINT, "boundary")["versions"])
    assert db.indexnow_pages() == pages
    assert indexnow.metrics(db, cfg, NOW) == metrics
    assert not db.indexnow_claim(indexnow.INDEXNOW_ENDPOINT, "old", versions, NOW, {})

    class NoNetwork:
        def post(self, *args, **kwargs):
            pytest.fail("a replay must not submit again")

    assert indexnow.submit_indexnow(db, cfg, NoNetwork(), deployed=True, run_key="old", now=NOW) == (
        0 if status in (200, 202) else 1)
    db.indexnow_finish(indexnow.INDEXNOW_ENDPOINT, "old", NOW, 200)
    if status == "sending":
        assert db.indexnow_pages()[next(iter(versions))]["received_at"] == iso(NOW)
    else:
        assert db.indexnow_pages() == pages
    db.prune(NOW, **RETENTION)
    snapshot = list(db.conn.iterdump())
    db.prune(NOW, **RETENTION)
    assert list(db.conn.iterdump()) == snapshot


@pytest.mark.parametrize("ttl_h", [72, 240])
def test_expired_scores_only_and_ttl_beats_retention(db, ttl_h):
    cfg = load_config()
    cfg.appeal_score.update(enabled=True, ttl_h=ttl_h)
    cutoff = NOW - max(timedelta(days=7), timedelta(hours=ttl_h))
    candidates = []
    for key, at in (("old", cutoff - timedelta(seconds=1)), ("boundary", cutoff),
                    ("needed", NOW - timedelta(hours=ttl_h) + timedelta(seconds=1)),
                    ("future", NOW + timedelta(days=1))):
        db.conn.execute("INSERT INTO appeal_scores VALUES (?,?,?,?)", (key, 10, 4, iso(at)))
        candidates.append((dict(dedupe_key=key, final_price=10), 1))
    db.meta_set("appeal_score_run:old", "1")
    before = appeal_score.ratings(db, cfg, NOW, candidates)
    db.prune(NOW, **RETENTION, appeal_scores_ttl_h=ttl_h)
    assert appeal_score.ratings(db, cfg, NOW, candidates) == before == {"needed": 4}
    assert {r[0] for r in db.conn.execute("SELECT dedupe_key FROM appeal_scores")} == {
        "boundary", "needed", "future"}
    # Even with the expired score gone, the paid-call claim still blocks replay.
    assert appeal_score.ratings(db, cfg, NOW, candidates, parser=object(), run_key="old") == before


def test_unbounded_readers_evidence_and_money_are_untouched(db):
    old = NOW - timedelta(days=400)
    deal = insert(db, "widget", now=old, asin="B012345678", store="Amazon")
    for i, at in enumerate((old, NOW - timedelta(days=90), NOW - timedelta(hours=1), NOW)):
        assert db.add_sighting(deal["id"], source_name=f"feed{i}", source_url=f"https://feed{i}.example/post",
                               content_hash=f"post{i}", seen_at=at, final_price=deal["final_price"])
        assert db.record_price("widget", "Amazon", 100, at, f"post{i}")
    assert db.record_price("widget", "Amazon", 100, NOW - timedelta(days=90), "check:boundary")
    db.cache_post_parse("post", [{"evidence": {"final_price_quote": "$10"}}], NOW)
    db.store_validators("https://feed.example/rss", '"etag"', "modified", NOW)
    db.meta_set("ai_take:widget", json.dumps({"key": "widget", "attempted_at": iso(old),
                                            "previous": [{"facts_hash": "old-facts"}]}))
    db.meta_set("pinterest_posted", json.dumps({"widget": {"at": iso(old)}}))
    assert db.record_alert(deal_key="widget", channel="telegram", dedup_token="sent", now=old)
    for status in ("sent", "pending"):
        assert db.claim_outgoing("telegram", status, "old-run", old)
        if status == "sent":
            db.finish_outgoing("telegram", status, old)
    add_call(db, "pending-call", old, "reserved", days=(NOW, NOW + timedelta(days=1)))
    add_call(db, "current-call", NOW, "settled", billed=NOW)
    db.prune(NOW)  # Apply legacy maintenance first; isolate the new policies.

    def readers():
        return (db.sightings_for(deal["id"]), db.deals_for_post("feed0", "https://feed0.example/post"),
                db.price_stats("widget"), db.product_price_history(deal["product_id"], None, now=NOW),
                db.price_observations("widget", NOW - timedelta(days=90), NOW),
                db.distinct_sources_within(deal["id"], NOW - timedelta(hours=24), now=NOW),
                verify.assign_tier(db, deal, AppConfig(), NOW),
                db.cached_post_parse("post", NOW, 2), db.get_validators("https://feed.example/rss"),
                db.last_alert("widget", "telegram"), db.outgoing_for("telegram"),
                db.spend_usd_for_day(NOW), db.spend_usd_for_day(NOW + timedelta(days=1)))

    before = readers()
    dump = list(db.conn.iterdump())
    db.prune(NOW, **RETENTION)
    assert readers() == before
    assert list(db.conn.iterdump()) == dump
    assert not db.add_sighting(deal["id"], source_name="feed0", source_url="https://feed0.example/post",
                               content_hash="post0", seen_at=NOW, final_price=deal["final_price"])
    assert not db.record_price("widget", "Amazon", 100, NOW, "post0")
    assert not db.claim_outgoing("telegram", "sent", "retry", NOW)
    assert not db.claim_outgoing("telegram", "pending", "retry", NOW)
    assert db.alert_exists("telegram", "sent")
    assert not db.conn.execute("PRAGMA foreign_key_check").fetchall()


def test_missing_knobs_keep_old_payloads_and_scores(db, tmp_path):
    batch(db, "old", NOW - timedelta(days=400))
    db.conn.execute("INSERT INTO appeal_scores VALUES ('old',10,4,?)", (iso(NOW - timedelta(days=400)),))
    cfg_path = tmp_path / "empty.yaml"
    cfg_path.write_text("{}")
    for cfg in (AppConfig(), load_config(cfg_path)):
        assert cfg.indexnow_batch_versions_days is cfg.appeal_scores_days is None
        assert cfg.state_warn_enc_mb is cfg.state_max_enc_mb is None
    db.prune(NOW)
    assert json.loads(db.indexnow_batch(indexnow.INDEXNOW_ENDPOINT, "old")["versions"])
    assert db.conn.execute("SELECT COUNT(*) FROM appeal_scores").fetchone()[0] == 1
    shipped = load_config()
    assert (shipped.indexnow_batch_versions_days, shipped.appeal_scores_days) == (7, 7)
    assert (shipped.state_warn_enc_mb, shipped.state_max_enc_mb) == (70, 95)


def test_cli_passes_retention_and_reader_ttl(db, tmp_path, monkeypatch):
    batch(db, "old", NOW - timedelta(days=8))
    db.conn.execute("INSERT INTO appeal_scores VALUES ('needed',10,4,?)", (iso(NOW - timedelta(days=8)),))
    db.conn.commit()
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"retention": RETENTION, "appeal_score": {"ttl_h": 240}}))
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    assert cli.cmd_prune(argparse.Namespace(db=db.path, config=str(path))) == 0
    assert db.indexnow_batch(indexnow.INDEXNOW_ENDPOINT, "old")["versions"] == "{}"
    assert db.conn.execute("SELECT COUNT(*) FROM appeal_scores").fetchone()[0] == 1


@pytest.mark.parametrize("value", [0, -1, True, "7", 1.5, 3651])
@pytest.mark.parametrize("name", RETENTION)
def test_invalid_retention_fails_closed(tmp_path, name, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"retention": {name: value}}))
    with pytest.raises(ValueError, match="retention"):
        load_config(path)


@pytest.mark.parametrize("settings", [[], None, {"max_enc_mb": True}, {"max_enc_mb": 0},
    {"max_enc_mb": -1}, {"max_enc_mb": float("nan")}, {"warn_enc_mb": float("inf")},
    {"max_enc_mb": "90"}, {"warn_enc_mb": 90, "max_enc_mb": 90}])
def test_invalid_size_limits_fail_closed(settings):
    with pytest.raises(ValueError):
        validate_limits(settings)


@pytest.mark.parametrize("size,code,level", [(70_000_000, 0, ""), (70_000_001, 0, "WARNING"),
    (89_999_999, 0, "WARNING"), (90_000_000, 1, "ERROR"), (90_000_001, 1, "ERROR")])
def test_exact_byte_guard_boundaries(size, code, level, capsys):
    assert check_size(size, 70, 90) == code
    output = capsys.readouterr().out
    assert level in output if level else not output
    assert check_size(size, None, None) == 0


def test_dbstat_includes_indexes_and_page_fallback(db):
    db.conn.execute("CREATE TABLE big(id TEXT PRIMARY KEY, body TEXT)")
    db.conn.executemany("INSERT INTO big VALUES (?,?)", [(str(i) * 100, "private row" * 1000) for i in range(20)])
    db.conn.commit()
    measured = table_sizes(db.conn)
    top = measured["tables"][0]
    assert top["name"] == "big"
    expected = db.conn.execute("SELECT SUM(pgsize) FROM dbstat WHERE name IN"
                               " (SELECT name FROM sqlite_schema WHERE tbl_name='big')").fetchone()[0]
    assert top["bytes"] == expected
    assert measured["tables"] == sorted(measured["tables"], key=lambda row: (-row["bytes"], row["name"]))

    class NoDbstat:
        def execute(self, sql):
            if "dbstat" in sql:
                raise sqlite3.OperationalError("no such table: dbstat")
            return db.conn.execute(sql)

    fallback = table_sizes(NoDbstat())
    assert fallback == measured | {"method": "page_counts", "tables": []}


def test_commit_size_line_noop_and_exact_guard_before_remote_access(repo, tmp_path):
    with sqlite3.connect(repo / "state.db") as conn:
        conn.execute("CREATE TABLE facts(id INTEGER PRIMARY KEY, evidence TEXT)")
        conn.execute("INSERT INTO facts(evidence) VALUES ('private evidence must never be logged')")
    first = run_script(repo, key=KEY)
    assert first.returncode == 0, first.stderr + first.stdout
    line, = [s for s in first.stdout.splitlines() if s.startswith("[commit_state] size ")]
    sizes = json.loads(line.removeprefix("[commit_state] size "))
    assert sizes["method"] == "dbstat" and sizes["tables"][0]["bytes"] > 0
    assert sizes["db_bytes"] == (repo / "state.db").stat().st_size
    assert sizes["gz_bytes"] < sizes["enc_bytes"] <= sizes["gz_bytes"] + 32
    assert "private evidence" not in first.stdout + first.stderr
    assert KEY not in first.stdout + first.stderr
    again = run_script(repo, key=KEY)
    assert again.returncode == 0 and "no-op" in again.stdout
    assert again.stdout.count("[commit_state] size ") == 1
    tip = _git(repo, "rev-parse", "origin/data")
    # A broken local origin proves the size failure occurs before even ls-remote.
    _git(repo, "remote", "set-url", "origin", (tmp_path / "missing.git").as_uri())
    (repo / "config.yaml").write_text(yaml.safe_dump({"state": {
        "max_enc_mb": sizes["enc_bytes"] / 1_000_000}}))
    failed = run_script(repo, key=KEY)
    assert failed.returncode == 1 and "ERROR" in failed.stdout and "refusing push" in failed.stdout
    assert "could not establish remote state" not in failed.stdout
    assert failed.stderr == ""
    assert _git(repo, "rev-parse", "origin/data") == tip
    assert not (repo / ".state-worktree").exists()


def test_invalid_guard_configuration_never_creates_data_branch(repo):
    (repo / "state.db").write_bytes(b"fixture")
    (repo / "config.yaml").write_text("state: {max_enc_mb: .nan}")
    result = run_script(repo, key=KEY)
    assert result.returncode == 1 and "refusing push" in result.stdout
    assert origin_branches(repo) == {"main"}


@pytest.mark.parametrize("config_text", ["", "# Use application defaults.\n", "null\n"])
def test_default_configuration_can_save_healthy_state(repo, tmp_path, config_text):
    config = repo / "config.yaml"
    config.write_text(config_text)
    assert load_config(config).state_max_enc_mb is None
    with sqlite3.connect(repo / "state.db") as conn:
        conn.execute("CREATE TABLE facts(value TEXT)")
        conn.execute("INSERT INTO facts VALUES ('retained')")
    plain = (repo / "state.db").read_bytes()
    result = run_script(repo, key=KEY)
    assert result.returncode == 0, result.stdout + result.stderr
    assert decrypt(branch_blob(repo, "state.db.enc"), tmp_path) == plain


def test_unchanged_plaintext_cannot_reuse_an_oversized_remote_encoding(repo, tmp_path):
    with sqlite3.connect(repo / "state.db") as conn:
        conn.execute("CREATE TABLE facts(value TEXT)")
        conn.execute("INSERT INTO facts VALUES (?)", ("synthetic fact " * 1000,))
    plain = (repo / "state.db").read_bytes()
    seed_encrypted_state(repo, gzip.compress(plain, compresslevel=0, mtime=0))
    (repo / "state.db").write_bytes(plain)
    (repo / "config.yaml").write_text("state: {max_enc_mb: 0.001}")
    assert len(branch_blob(repo, "state.db.enc")) > 1000
    result = run_script(repo, key=KEY)
    assert result.returncode == 0, result.stdout + result.stderr
    encrypted = branch_blob(repo, "state.db.enc")
    assert len(encrypted) < 1000
    assert decrypt(encrypted, tmp_path) == plain
