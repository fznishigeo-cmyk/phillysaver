"""Offline protocol, deployment binding, and durable receipt/retry checks."""

import hashlib
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import indexnow, seo
from dealradar.publish.site import render_site
from tests.test_publish_site import NOW, make_deal

KEY = "0123456789abcdef0123456789abcdef"
HOST = "https://wowpriced.com"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("DEALRADAR_DURABLE_STATE", raising=False)
    cfg = AppConfig(custom_domain="wowpriced.com", seo_indexnow_key=KEY,
                    seo_indexnow_min_hours=0, seo_indexnow_resubmit_days=0,
                    site_dir=str(tmp_path / "site"))
    db = DB(tmp_path / "receipts.db")
    # A virtual clock retains the host pacing check without sleeping offline.
    real_fetcher = indexnow.PoliteFetcher
    ticks, waits = [0.0], []

    def sleep(seconds):
        waits.append(seconds)
        ticks[0] += seconds

    monkeypatch.setattr(indexnow, "PoliteFetcher", lambda *a, **kw: real_fetcher(
        *a, **kw, sleep_fn=sleep, clock=lambda: ticks[0]))
    yield db, cfg, waits
    db.close()


def stage(db, cfg, run_key="run-1", now=NOW, pages=None):
    """Write a small publish and its final-file manifest, without deployment."""
    out = Path(cfg.site_dir)
    out.mkdir(exist_ok=True)
    pages = pages if pages is not None else {HOST + "/a.html": "facts a", HOST + "/b.html": "facts b"}
    for url, content in pages.items():
        path = indexnow._page_path(out, url, cfg.custom_domain)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (out / f"{KEY}.txt").write_text(KEY)
    indexnow.indexnow_due(db, cfg, now, pages, site_dir=out)
    indexnow.seal_publish(db, cfg, now, out, run_key)
    return pages


def client_for(status=200, *, key=KEY, robots=200, after="", callback=None):
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "POST":
            if callback:
                callback(request)
            if status == "timeout":
                raise httpx.ReadTimeout("private response/key text", request=request)
            return httpx.Response(status, headers={"Retry-After": after} if after else {},
                                  text="private response/key text")
        if request.url.path == "/robots.txt":
            return httpx.Response(robots, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text=key)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def send(db, cfg, client, run_key="run-1", now=NOW, deployed=True):
    return indexnow.submit_indexnow(db, cfg, client, run_key=run_key, now=now, deployed=deployed)


@pytest.mark.parametrize("status", [200, 202, 400, 403, 422, 429, 500, 503, "timeout", 201, 204, 301])
def test_only_200_and_202_record_exact_receipts(env, status, caplog):
    db, cfg, waits = env
    pages = stage(db, cfg)
    client, seen = client_for(status)
    with client, caplog.at_level("INFO"):
        result = send(db, cfg, client)
    accepted = status in (200, 202)
    assert result == (0 if accepted else 1)
    values = indexnow.metrics(db, cfg, NOW + timedelta(hours=2))
    assert values == {"pending": 0 if accepted else 2, "received": 2 if accepted else 0,
                      "key_pending": 2 if status == 202 else 0, "failed": 0 if accepted else 2,
                      "oldest_pending_hours": 0.0 if accepted else 2.0}
    posts = [r for r in seen if r.method == "POST"]
    assert len(posts) == 1
    assert json.loads(posts[0].content)["urlList"] == sorted(pages)
    assert posts[0].headers["user-agent"] == cfg.resolved_user_agent()
    for row in db.indexnow_pages().values():
        assert (row["received_hash"] == row["content_hash"]) == accepted
        assert row["retry_at"] == (None if accepted else (NOW + timedelta(minutes=15)).isoformat())
    if status == 202:
        assert [r.method for r in seen] == ["POST", "GET", "GET"]
        assert waits == [1.0]
        assert json.loads(db.meta_get("indexnow_key_check"))["published"] is True
    assert KEY not in caplog.text and "private response/key text" not in caplog.text


@pytest.mark.parametrize("status", [200, 202, 403, "timeout"])
def test_run_key_never_reposts_or_double_counts_an_attempt(env, status):
    db, cfg, _ = env
    stage(db, cfg)
    client, seen = client_for(status)
    with client:
        first = send(db, cfg, client)
        before = [tuple(r) for r in db.conn.execute("SELECT * FROM indexnow_pages ORDER BY url")]
        # Re-publish and redeploy the same run, even after its retry interval.
        stage(db, cfg, now=NOW + timedelta(days=1))
        assert send(db, cfg, client, now=NOW + timedelta(days=1)) == first
    assert len([r for r in seen if r.method == "POST"]) == 1
    assert [tuple(r) for r in db.conn.execute("SELECT * FROM indexnow_pages ORDER BY url")] == before
    assert db.conn.execute("SELECT COUNT(*) FROM indexnow_batches").fetchone()[0] == 1


def test_backoff_survives_content_changes_and_is_capped(env):
    db, cfg, _ = env
    cfg.seo_indexnow_retry_max_minutes = 40
    pages = stage(db, cfg)
    client, seen = client_for(503)
    with client:
        assert send(db, cfg, client) == 1
        for minute in (1, 14):
            changed = {url: content + str(minute) for url, content in pages.items()}
            stage(db, cfg, f"early-{minute}", NOW + timedelta(minutes=minute), changed)
            assert send(db, cfg, client, f"early-{minute}", NOW + timedelta(minutes=minute)) == 0
        assert len(seen) == 1
        for number, minute, delay in ((2, 15, 30), (3, 45, 40), (4, 85, 40)):
            when = NOW + timedelta(minutes=minute)
            stage(db, cfg, f"retry-{number}", when)
            assert send(db, cfg, client, f"retry-{number}", when) == 1
            for row in db.indexnow_pages().values():
                assert row["retry_at"] == (when + timedelta(minutes=delay)).isoformat()
                assert row["attempts"] == number
                assert row["pending_since"] == NOW.isoformat()
    assert len(seen) == 4


@pytest.mark.parametrize("status", [429, 503, "timeout"])
def test_new_arrivals_cannot_bypass_endpoint_backoff(env, status):
    db, cfg, _ = env
    pages = stage(db, cfg)
    client, seen = client_for(status)
    with client:
        assert send(db, cfg, client) == 1
        pages[HOST + "/new.html"] = "new public facts"
        stage(db, cfg, "too-soon", NOW + timedelta(minutes=1), pages)
        assert send(db, cfg, client, "too-soon", NOW + timedelta(minutes=1)) == 0
        assert len(seen) == 1
        stage(db, cfg, "retry", NOW + timedelta(minutes=15), pages)
        assert send(db, cfg, client, "retry", NOW + timedelta(minutes=15)) == 1
    assert len(seen) == 2
    assert db.indexnow_pages()[HOST + "/new.html"]["retry_at"] == (NOW + timedelta(minutes=45)).isoformat()


@pytest.mark.parametrize("after, delay", [("1800", 30), ("999999999", 360),
    ("bad", 15), ("Fri, 09 Oct 2026 14:30:00 GMT", 30)])
def test_retry_after_is_honored_within_the_config_cap(env, after, delay):
    db, cfg, _ = env
    stage(db, cfg)
    client, _ = client_for(429, after=after)
    with client:
        assert send(db, cfg, client) == 1
    assert {r["retry_at"] for r in db.indexnow_pages().values()} == {
        (NOW + timedelta(minutes=delay)).isoformat()}


@pytest.mark.parametrize("fault", ["deploy-failed", "publish-skipped", "wrong-run", "file-changed",
                                   "key-changed", "wrong-host", "corrupt-manifest"])
def test_deployment_or_manifest_failure_never_acknowledges_pending_urls(env, fault):
    db, cfg, _ = env
    stage(db, cfg)
    path = indexnow.manifest_path(cfg.site_dir)
    if fault == "publish-skipped":
        path.unlink()
    elif fault == "file-changed":
        (Path(cfg.site_dir) / "a.html").write_text("different publish")
    elif fault == "key-changed":
        cfg.seo_indexnow_key = "a-different-valid-key"
    elif fault == "wrong-host":
        manifest = json.loads(path.read_text())
        version = manifest["versions"].pop(HOST + "/a.html")
        manifest["versions"]["https://other.example/a.html"] = version
        path.write_text(json.dumps(manifest))
    elif fault == "corrupt-manifest":
        path.write_text("[]")
    client, seen = client_for()
    with client:
        send(db, cfg, client, run_key="other-run" if fault == "wrong-run" else "run-1",
             deployed=fault != "deploy-failed")
    assert seen == []
    assert all(r["received_at"] is None and r["pending_since"] for r in db.indexnow_pages().values())
    assert db.conn.execute("SELECT COUNT(*) FROM indexnow_batches").fetchone()[0] == 0


def test_failed_deploy_is_recovered_by_the_next_deployed_run(env):
    db, cfg, _ = env
    stage(db, cfg)
    client, seen = client_for()
    with client:
        assert send(db, cfg, client, deployed=False) == 0
        stage(db, cfg, "next-run", NOW + timedelta(minutes=10))
        assert send(db, cfg, client, "next-run", NOW + timedelta(minutes=10)) == 0
    assert len(seen) == 1 and indexnow.metrics(db, cfg, NOW)["received"] == 2


def test_checkpoint_failure_prevents_any_post(env, monkeypatch):
    db, cfg, _ = env
    stage(db, cfg)

    def fail(_):
        raise OSError("private checkpoint error")

    monkeypatch.setattr(indexnow, "checkpoint_state", fail)
    client, seen = client_for()
    with client:
        assert send(db, cfg, client) == 1
    assert seen == []
    assert indexnow.metrics(db, cfg, NOW)["failed"] == 2
    assert all(r["received_at"] is None for r in db.indexnow_pages().values())


def test_claim_is_durable_before_post_and_lost_response_rerun_does_not_post(env):
    db, cfg, _ = env
    stage(db, cfg)

    def inspect_claim(_):
        snapshot = DB(db.path, read_only=True)
        try:
            assert snapshot.indexnow_batch(indexnow.INDEXNOW_ENDPOINT, "run-1")["status"] == "sending"
            assert all(r["received_at"] is None for r in snapshot.indexnow_pages().values())
        finally:
            snapshot.close()
        raise RuntimeError("runner interrupted before receipt")

    client, seen = client_for(callback=inspect_claim)
    with client:
        with pytest.raises(RuntimeError, match="runner interrupted"):
            send(db, cfg, client)
        assert send(db, cfg, client) == 1
    assert len(seen) == 1
    assert indexnow.metrics(db, cfg, NOW)["pending"] == 2


def test_response_cannot_acknowledge_a_newer_unsubmitted_version(env):
    db, cfg, _ = env
    stage(db, cfg)

    def change(_):
        db.indexnow_discover({HOST + "/a.html": "newer-unsubmitted-version"}, NOW, 0)

    client, seen = client_for(callback=change)
    with client:
        assert send(db, cfg, client) == 0
    assert len(seen) == 1
    assert indexnow.metrics(db, cfg, NOW)["received"] == 1
    assert db.indexnow_pages()[HOST + "/a.html"]["received_at"] is None
    # A repeated response cannot double-count or replace another version.
    db.indexnow_finish(indexnow.INDEXNOW_ENDPOINT, "run-1", NOW + timedelta(hours=1), 200)
    assert indexnow.metrics(db, cfg, NOW)["pending"] == 1


@pytest.mark.parametrize("limit, count", [(2, 3), (20000, 10001)])
def test_one_batch_respects_config_and_protocol_caps_and_leaves_remainder_pending(env, limit, count):
    db, cfg, _ = env
    cfg.seo_indexnow_batch_size = limit
    stage(db, cfg, pages={HOST + f"/p{i}.html": f"facts {i}" for i in range(count)})
    client, seen = client_for()
    with client:
        assert send(db, cfg, client) == 0
        assert send(db, cfg, client) == 0
    (request,) = seen
    sent = len(json.loads(request.content)["urlList"])
    assert sent == min(limit, 10000)
    values = indexnow.metrics(db, cfg, NOW)
    assert values["received"] == sent and values["pending"] == count - sent


def test_a_different_run_key_also_honors_the_engine_host_gap(env):
    db, cfg, _ = env
    cfg.seo_indexnow_batch_size = 1
    stage(db, cfg)
    client, seen = client_for()
    with client:
        assert send(db, cfg, client) == 0
        stage(db, cfg, "run-2")
        assert send(db, cfg, client, "run-2") == 0
        assert len(seen) == 1
        when = NOW + timedelta(seconds=1)
        stage(db, cfg, "run-2", when)
        assert send(db, cfg, client, "run-2", when) == 0
    assert len(seen) == 2
    assert indexnow.metrics(db, cfg, NOW)["received"] == 2


@pytest.mark.parametrize("key, robots, published", [(KEY, 200, True), ("wrong-key", 200, False),
                                                   (KEY, 403, False)])
def test_202_keeps_engine_key_pending_and_rechecks_published_key_without_reposting(env, key, robots, published):
    db, cfg, waits = env
    stage(db, cfg)
    client, seen = client_for(202, key=key, robots=robots)
    with client:
        assert send(db, cfg, client) == 0
        assert json.loads(db.meta_get("indexnow_key_check"))["published"] is published
        stage(db, cfg, "key-recheck", NOW + timedelta(hours=7))
        assert send(db, cfg, client, "key-recheck", NOW + timedelta(hours=7)) == 0
    assert len([r for r in seen if r.method == "POST"]) == 1
    assert len([r for r in seen if r.url.path == "/robots.txt"]) == 2
    assert indexnow.metrics(db, cfg, NOW)["key_pending"] == 2
    if robots == 403:
        assert not waits and all(r.url.path != f"/{KEY}.txt" for r in seen)


def test_ended_noindex_and_removed_pages_are_submitted_after_deployment(env):
    db, cfg, _ = env
    cfg.paywall_open_access = True
    cfg.support_enabled = True
    out = Path(cfg.site_dir)
    deal = make_deal(dedupe_key="was-live", item="Previously public item", codes=[])
    render_site(db, cfg, NOW, deals=[deal], last_run={"run_key": "live"})
    client, seen = client_for()
    with client:
        assert send(db, cfg, client, "live") == 0
        seen.clear()
        ended = dict(deal, status="dead", death="confirmed", died_at=NOW.isoformat())
        render_site(db, cfg, NOW + timedelta(hours=1), deals=[ended], last_run={"run_key": "ended"})
        path = seo.deal_path(deal["item"], hashlib.sha256(b"was-live").hexdigest()[:8])
        assert '<meta name="robots" content="noindex">' in (out / path.strip("/") / "index.html").read_text()
        assert HOST + path not in (out / "sitemap.xml").read_text()
        assert send(db, cfg, client, "ended", NOW + timedelta(hours=1)) == 0
        assert HOST + path in json.loads(seen[0].content)["urlList"]
        seen.clear()
        render_site(db, cfg, NOW + timedelta(hours=2), deals=[], last_run={"run_key": "removed"})
        assert not (out / path.strip("/")).exists()
        assert send(db, cfg, client, "removed", NOW + timedelta(hours=2)) == 0
        assert HOST + path in json.loads(seen[0].content)["urlList"]


def test_legacy_publish_before_post_state_is_not_a_receipt(env):
    db, cfg, _ = env
    db.meta_set("indexnow_sent", json.dumps({HOST + "/a.html": ["old-hash", NOW.isoformat()]}))
    stage(db, cfg)
    assert indexnow.metrics(db, cfg, NOW)["received"] == 0
    assert indexnow.metrics(db, cfg, NOW)["pending"] == 2


def test_legacy_urls_preserve_removal_and_noindex_discovery_without_receipts(env):
    db, cfg, _ = env
    db.meta_set("indexnow_sent", json.dumps({url: ["old-hash", NOW.isoformat()] for url in (
        HOST + "/gone.html", HOST + "/ended.html", "https://other.example/old.html")}))
    out = Path(cfg.site_dir)
    out.mkdir()
    (out / "ended.html").write_text('<meta name="robots" content="noindex">Ended facts')
    stage(db, cfg)
    rows = db.indexnow_pages()
    assert rows[HOST + "/gone.html"]["removed"] == 1
    assert rows[HOST + "/ended.html"]["removed"] == 0
    assert "https://other.example/old.html" not in rows
    assert all(row["received_at"] is None for row in rows.values())
    client, seen = client_for()
    with client:
        assert send(db, cfg, client) == 0
    assert HOST + "/gone.html" in json.loads(seen[0].content)["urlList"]
    assert HOST + "/ended.html" in json.loads(seen[0].content)["urlList"]


def test_previously_public_ended_page_is_due_on_first_receipt_publish(env):
    db, cfg, _ = env
    cfg.paywall_open_access = True
    ended = make_deal(dedupe_key="already-ended", status="dead", death="confirmed", died_at=NOW.isoformat())
    path = seo.deal_path(ended["item"], hashlib.sha256(b"already-ended").hexdigest()[:8])
    db.meta_set("seo_deal_paths", json.dumps({hashlib.sha256(b"already-ended").hexdigest()[:8]: path}))
    render_site(db, cfg, NOW, deals=[ended], last_run={"run_key": "migration"})
    assert HOST + path in db.indexnow_pages()
    assert HOST + path not in (Path(cfg.site_dir) / "sitemap.xml").read_text()
    client, seen = client_for()
    with client:
        assert send(db, cfg, client, "migration") == 0
    assert HOST + path in json.loads(seen[0].content)["urlList"]


def test_empty_and_unavailable_receipt_history_are_distinct(env, tmp_path):
    db, cfg, _ = env
    assert indexnow.metrics(db, cfg, NOW)["received"] is None
    stage(db, cfg, pages={})
    assert indexnow.metrics(db, cfg, NOW) == dict.fromkeys(indexnow.METRICS, 0)
    old = tmp_path / "old.db"
    with sqlite3.connect(old) as conn:
        conn.execute("CREATE TABLE meta(k TEXT PRIMARY KEY,v TEXT)")
    snapshot = DB(old, read_only=True)
    try:
        assert indexnow.metrics(snapshot, cfg, NOW)["pending"] is None
        assert not snapshot.conn.execute("SELECT 1 FROM sqlite_master WHERE name='indexnow_pages'").fetchone()
    finally:
        snapshot.close()


def test_config_clamps_batch_size_and_backoff_knobs(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("seo:\n  indexnow_batch_size: 20000\n  indexnow_retry_initial_minutes: 0\n"
                    "  indexnow_retry_max_minutes: -1\n")
    cfg = load_config(path)
    assert cfg.seo_indexnow_batch_size == 10000
    assert cfg.seo_indexnow_retry_initial_minutes == cfg.seo_indexnow_retry_max_minutes == 1
