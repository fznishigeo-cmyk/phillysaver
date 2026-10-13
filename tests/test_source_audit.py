"""Offline source coverage, state isolation, credit and public-output checks."""
from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import yaml

from dealradar import cli, source_audit as audit
from dealradar.config import AppConfig
from dealradar.db import DB
from dealradar.fetch.validate import save_effective_sources
from dealradar.models import RawPost, SourceConfig
from dealradar.util import iso

NOW = datetime(2026, 10, 13, 14, tzinfo=timezone.utc)
SECRET_TEXT = "PRIVATE PRODUCT DESCRIPTION"
SECRET_IMAGE = "https://images.example/private-product.jpg"


@pytest.fixture
def cfg():
    return AppConfig(min_host_interval_s=0, max_retries=1,
                     affiliate_amazon_tag="ours-20", site_show_from_pct=50)


@pytest.fixture
def db(tmp_path):
    state = DB(tmp_path / "state.db")
    yield state
    state.close()


def source(name="partner", **kwargs):
    return SourceConfig(name=name, method="rss", site=f"https://{name}.example/",
                        url=f"https://{name}.example/feed/", **kwargs)


def feed(n=1, age=0, *, dated=True):
    date = (NOW - timedelta(days=age)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    return "<rss version='2.0'><channel><title>Source</title>" + "".join(
        f"<item><guid>i{i}</guid><title>{SECRET_TEXT}</title>"
        f"<link>https://partner.example/post/{i}</link>"
        f"<description><![CDATA[<img src='{SECRET_IMAGE}'>sale $10 off]]></description>"
        + (f"<pubDate>{date}</pubDate>" if dated else "") + "</item>" for i in range(n)) + "</channel></rss>"


def probe(db, cfg, sources, response=None):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return response(request) if response else httpx.Response(200, text=feed())

    return audit.audit(db, cfg, NOW, sources=sources,
                       transport=httpx.MockTransport(handler), sleep_fn=lambda _: None), requests


@pytest.mark.parametrize("n,age,status,verdict", [
    (1, 0, 200, "OK"), (10, 0, 200, "LOW YIELD"),
    (1, 4, 200, "STALE"), (0, 0, 200, "BROKEN"), (1, 0, 403, "BROKEN"),
])
def test_each_verdict(db, cfg, n, age, status, verdict):
    rows, requests = probe(db, cfg, [source()],
                           lambda _: httpx.Response(status, text=feed(n, age)))
    assert rows[0]["verdict"] == verdict
    assert rows[0]["http"] == str(status)
    assert rows[0]["items"] == (n if status == 200 else 0)
    assert len([r for r in requests if r.url.path == "/feed/"]) == 1
    if status == 403:
        assert "403" in rows[0]["cause"]
    if n == 0:
        assert "feed" in rows[0]["cause"]


def test_tripped_reason_since_trips_and_probe_still_runs(db, cfg):
    for i in range(3):
        db.record_source_result("partner", ok=False,
                                error=f"http_403 {SECRET_TEXT} {SECRET_IMAGE}?key=FAKE_SECRET",
                                items=0, now=NOW - timedelta(minutes=3-i))
    before = list(db.conn.iterdump())
    rows, requests = probe(db, cfg, [source()])
    row = rows[0]
    assert row["verdict"] == "TRIPPED"
    assert row["error"] == row["cause"] == "HTTP 403"
    assert row["since"] == iso(NOW - timedelta(minutes=1))
    assert row["failures"] == 3 and row["trips"] == 1
    assert row["items"] == 1 and row["http"] == "200"
    assert requests and before == list(db.conn.iterdump())
    text = audit.markdown(rows, NOW) + audit.admin_message(rows)
    assert SECRET_TEXT not in text and SECRET_IMAGE not in text and "FAKE_SECRET" not in text


def test_legacy_breaker_since_is_estimated_and_unknown_trips_are_not_zero(db, cfg):
    db.conn.execute("INSERT INTO source_health(source_name,last_error,consecutive_failures,tripped_until)"
                    " VALUES (?,?,?,?)", ("partner", "http_403", 3, iso(NOW + timedelta(hours=5))))
    db.conn.commit()
    rows, _ = probe(db, cfg, [source()])
    assert rows[0]["since"] == iso(NOW - timedelta(hours=1)) + " (estimated)"
    assert rows[0]["trips"] is None
    assert "trips n/a" in audit.markdown(rows, NOW)


def post(uid, when=NOW, name="partner"):
    return RawPost(name, uid, f"https://partner.example/{uid}", SECRET_TEXT,
                   f"{uid} sale $10 off", when, when)


def seed_deal(db, key, *, link="", posted="", pct=80, flags=(), status="alive", when=NOW):
    payload = {"link_as_posted": posted, "flags": list(flags), "conditions": [],
               "currency": "USD", "region": "US", "image_url": SECRET_IMAGE}
    deal_id = db.insert_deal(dedupe_key=key, store="Store", item=SECRET_TEXT,
                             normalized_item=key, canonical_url=link, payload=payload,
                             final_price=10, pct_at_checkout=pct, abs_savings=40,
                             online_only="true", expires_at="", now=when)
    db.add_sighting(deal_id, source_name="partner", source_url=f"https://partner.example/{key}",
                    content_hash=key, seen_at=when, final_price=10)
    if status != "alive":
        db.update_deal(key, status=status)
    return deal_id


def test_period_counts_drops_and_exact_get_deal_credit(db, cfg):
    old = NOW - timedelta(days=2)
    db.meta_set("source_audit_since", iso(NOW - timedelta(days=8)))
    for uid, when, counts in (
        ("recent", NOW, dict(extracted=10, evidence=2, duplicate=3,
                             below_floor=1, critic=2, dead=1, other=1)),
        ("old", old, dict(extracted=4, evidence=1, duplicate=1)),
    ):
        p = post(uid, when)
        db.audit_seen([p], when)
        db.record_post(p, when)
        db.audit_parsed(p, counts, when)
    pending = post("pending")
    db.audit_seen([pending], NOW)
    seed_deal(db, "post")
    seed_deal(db, "tracker", link="https://cc.partner.example/r?url=retailer")
    seed_deal(db, "retained", link="https://store.example/p?affid=partner-id",
              posted="https://store.example/p?affid=partner-id")
    seed_deal(db, "ours", link="https://www.amazon.com/dp/B012345678?tag=partner-20",
              posted="https://www.amazon.com/dp/B012345678?tag=partner-20")
    seed_deal(db, "plain", link="https://store.example/product")
    seed_deal(db, "lower_site_tier", pct=60, when=old)
    seed_deal(db, "below", pct=40)
    seed_deal(db, "critic", flags=["suspect_msrp"])
    seed_deal(db, "dead", status="dead")
    rows, _ = probe(db, cfg, [source(crawl_permission="written grant")])
    row = rows[0]
    assert row["live"] == 6 and row["credited"] == 4
    day, week = row["periods"]["24h"], row["periods"]["7d"]
    assert (day["seen"], day["parsed"], day["extracted"]) == (2, 1, 10)
    assert (week["seen"], week["parsed"], week["extracted"]) == (3, 2, 14)
    assert day["complete"] and week["complete"]
    assert (day["evidence"], day["duplicate"], day["below_floor"], day["critic"], day["dead"]) == (2, 3, 1, 2, 1)
    assert week["evidence"] == 3 and week["duplicate"] == 4
    assert day["gates"] == {"below floor": 1, "critic suspect": 1, "dead": 1}
    assert day["live"] == 5 and week["live"] == 6
    text = audit.markdown(rows, NOW)
    assert "4/6 (67%)" in text
    assert "evidence 2; below floor 1; critic suspect 2; duplicate 3; dead 1" in text
    assert SECRET_TEXT not in text and SECRET_IMAGE not in text


def test_low_yield_uses_most_likely_counted_cause(db, cfg):
    db.meta_set("source_audit_since", iso(NOW - timedelta(days=8)))
    for i in range(10):
        p = post(str(i))
        db.audit_seen([p], NOW)
        db.record_post(p, NOW)
        db.audit_parsed(p, {"extracted": 1, "evidence": 1}, NOW)
    row = probe(db, cfg, [source()])[0][0]
    assert row["verdict"] == "LOW YIELD" and row["cause"] == "evidence"


def test_credit_uses_published_code_deal_destination_with_sovrn_credentials(db, cfg):
    seed_deal(db, "code", link="https://store.example/product")
    payload = json.loads(db.conn.execute("SELECT payload FROM deals WHERE dedupe_key='code'").fetchone()[0])
    payload.update(codes=["SAVE20"], stack_steps=["Use code SAVE20"])
    db.update_deal("code", payload=payload)
    assert probe(db, cfg, [source()])[0][0]["credited"] == 1  # Get deal points to the post
    cfg.affiliate_sovrn_enabled = True
    cfg.affiliate_sovrn_key = "0" * 32
    assert probe(db, cfg, [source()])[0][0]["credited"] == 0  # Same URL as the published wrapper


def test_missing_history_and_unknown_dates_are_explicit(db, cfg):
    p = post("legacy")
    db.record_post(p, NOW)
    db.conn.executescript("DROP TABLE source_audit_posts; DROP TABLE source_audit_breakers;")
    rows, _ = probe(db, cfg, [source()], lambda _: httpx.Response(200, text=feed(dated=False)))
    row = rows[0]
    assert row["newest"] == "n/a" and row["verdict"] == "OK"
    assert row["periods"]["24h"]["extracted"] is None
    assert "evidence n/a" in audit.markdown(rows, NOW)
    assert not audit._table_exists(db, "source_audit_posts")


def test_deferred_post_discovery_is_not_recounted_on_later_parse(db, cfg):
    p = post("deferred")
    db.meta_set("source_audit_since", iso(NOW - timedelta(days=8)))
    db.audit_seen([p], NOW - timedelta(days=2))
    db.audit_seen([p], NOW)
    db.record_post(p, NOW)
    db.audit_parsed(p, {"extracted": 0, "evidence": 0}, NOW)
    rows, _ = probe(db, cfg, [source()])
    assert rows[0]["periods"]["24h"]["seen"] == 0
    assert rows[0]["periods"]["24h"]["parsed"] == 1
    assert rows[0]["periods"]["7d"]["seen"] == 1


def test_grant_never_follows_redirect_to_non_granted_host(db, cfg):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        if request.url.host == "partner.example":
            assert request.url.path != "/robots.txt"
            return httpx.Response(302, headers={"Location": "https://foreign.example/feed/"})
        assert request.url.path == "/robots.txt"
        return httpx.Response(200, text="User-agent: *\nDisallow: /\n")

    rows = audit.audit(db, cfg, NOW, sources=[source(crawl_permission="written")],
                       transport=httpx.MockTransport(handler), sleep_fn=lambda _: None)
    assert requested == ["https://partner.example/feed/", "https://foreign.example/robots.txt"]
    assert rows[0]["verdict"] == "BROKEN" and rows[0]["cause"] == "robots blocked"
    assert rows[0]["access"] == "permission; robots blocked"


def test_saved_moved_feed_does_not_extend_permission(db, cfg):
    saved = dataclasses.replace(source(), url="https://foreign.example/feed/")
    save_effective_sources(db, [saved], NOW)
    rows, requests = probe(db, cfg, [source(crawl_permission="written")],
                           lambda _: httpx.Response(200, text=feed()))
    assert requests[0].url.host == "foreign.example" and requests[0].url.path == "/robots.txt"
    assert rows[0]["access"] == "robots"


def test_permissions_first_all_enabled_including_backups(db, cfg, monkeypatch):
    grant, ordinary, backup, disabled = source("z", crawl_permission="written"), source("a"), source("b"), source("off", enabled=False)
    monkeypatch.setattr(audit, "load_sources", lambda: ([ordinary, grant, disabled], [backup]))
    rows, _ = probe(db, cfg, None)
    assert [r["name"] for r in rows] == ["z", "a", "b"]


def test_read_only_run_summary_and_telegram_never_emit_content(db, cfg, tmp_path, monkeypatch, capsys, caplog):
    db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.store_validators("https://partner.example/feed/", '"cached"', "yesterday", NOW)
    before = Path(db.path).read_bytes()
    dump = list(db.conn.iterdump())
    summary = tmp_path / "summary.md"
    summary.write_text("Existing summary\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "FAKE_BOT_TOKEN")
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "1234")
    messages = []

    def handler(request):
        if request.url.host == "api.telegram.org":
            messages.append(json.loads(request.content)["text"])
            return httpx.Response(200)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        assert "if-none-match" not in request.headers and "if-modified-since" not in request.headers
        logging.getLogger("dealradar.fetch.kcl_wall").warning("markup: %s %s", SECRET_TEXT, SECRET_IMAGE)
        return httpx.Response(200, text=feed(), headers={"ETag": '"fresh"'})

    def no_llm(*args, **kwargs):
        pytest.fail("audit called an LLM")

    monkeypatch.setattr("dealradar.pipeline._build_parser", no_llm)
    caplog.set_level(logging.DEBUG)
    disabled_before = logging.root.manager.disable
    assert audit.run(cfg, db_path=db.path, now=NOW, sources=[source()],
                     transport=httpx.MockTransport(handler), sleep_fn=lambda _: None) == 0
    out = capsys.readouterr().out
    assert summary.read_text() == "Existing summary\n" + out
    assert messages and "OK 1" in messages[0]
    combined = out + summary.read_text() + "".join(messages) + caplog.text
    for private in (SECRET_TEXT, SECRET_IMAGE, "FAKE_BOT_TOKEN"):
        assert private not in combined
    assert Path(db.path).read_bytes() == before and list(db.conn.iterdump()) == dump
    assert logging.root.manager.disable == disabled_before


def test_adapter_writes_are_discarded(db, cfg, monkeypatch):
    class MutatingAdapter:
        def fetch_posts(self, source, fetcher, memory, cfg, now, deadline):
            memory.meta_set("poison", "never persist")
            memory.update_deal("live", status="dead")
            memory.record_post(post("new"), now)
            return [post("probe")]

    seed_deal(db, "live")
    before = list(db.conn.iterdump())
    monkeypatch.setattr(audit, "get_adapter", lambda *args: MutatingAdapter())
    rows, _ = probe(db, cfg, [source()])
    assert rows[0]["live"] == 1
    assert list(db.conn.iterdump()) == before


def test_official_api_status_and_missing_key(db, cfg, monkeypatch):
    monkeypatch.delenv("MEH_API_KEY", raising=False)
    meh = SourceConfig("meh", "api", "https://meh.com/", adapter="meh")
    rows, _ = probe(db, cfg, [meh], lambda _: httpx.Response(403))
    assert rows[0]["http"] == "403" and rows[0]["cause"] == "HTTP 403"
    assert rows[0]["access"] == "robots"
    monkeypatch.delenv("WOOT_API_KEY", raising=False)
    woot = SourceConfig("woot", "api", "https://www.woot.com/", adapter="woot")
    rows, requests = probe(db, cfg, [woot])
    assert rows[0]["http"] == "n/a" and rows[0]["cause"] == "needs_key"
    assert not requests


def test_telegram_reader_is_never_probed(db, cfg, monkeypatch):
    # One login session shared with the fast lane: a second connection at
    # once can get it revoked, so the audit reports state counts only.
    monkeypatch.setattr(audit, "get_adapter", lambda *a: pytest.fail("adapter built"))
    tg = SourceConfig("telegram_channels", "api", "mtproto://channels", adapter="telegram")
    rows, requests = probe(db, cfg, [tg])
    assert not requests
    assert rows[0]["verdict"] == "OK" and rows[0]["items"] == 0
    assert rows[0]["probe_error"] == "probe skipped (shared login session)"


def test_cli_and_missing_state(cfg, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(audit, "run", lambda config, **kwargs: calls.append(kwargs) or 0)
    assert cli.main(["source-audit", "--db", "snapshot.db"]) == 0
    assert calls == [{"db_path": "snapshot.db"}]
    with pytest.raises(Exception, match="unable to open database"):
        DB(tmp_path / "absent.db", read_only=True)
    assert not (tmp_path / "absent.db").exists()


def test_admin_worst_five_counts(db, cfg):
    sources = [source(f"broken{i}") for i in range(7)]
    rows, _ = probe(db, cfg, sources, lambda _: httpx.Response(403))
    message = audit.admin_message(rows)
    assert "BROKEN 7" in message
    assert len(message.splitlines()) == 6


def test_audit_workflow_shape():
    root = Path(__file__).resolve().parents[1]
    path = root / ".github/workflows/source-audit.yml"
    text = path.read_text()
    doc = yaml.safe_load(text)
    triggers = doc.get("on", doc.get(True))
    assert "workflow_dispatch" in triggers
    assert triggers["schedule"] == [{"cron": "17 13 * * 1"}]
    assert doc["concurrency"] == {"group": "source-audit", "cancel-in-progress": False}
    assert doc["permissions"] == {"contents": "read"}
    steps = doc["jobs"]["audit"]["steps"]
    assert not any("commit_state" in s.get("run", "") or "git push" in s.get("run", "") for s in steps)
    restore = next(s for s in steps if "restore_state.sh" in s.get("run", ""))
    assert restore["env"] == {"STATE_DB_KEY": "${{ secrets.STATE_DB_KEY }}"}
    assert not restore.get("continue-on-error", False)
    run = next(s for s in steps if s.get("run") == "python -m dealradar source-audit")
    expected = {"REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT",
                "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION",
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_ADMIN_CHAT_ID", "WOOT_API_KEY",
                "MEH_API_KEY", "KEEPA_KEY", "THREADS_TOKEN", "SOVRN_API_KEY"}
    assert set(run["env"]) == expected
    fast = yaml.safe_load((root / ".github/workflows/fast.yml").read_text())
    fast_env = next(s["env"] for s in fast["jobs"]["run"]["steps"] if s.get("run") == "python -m dealradar run --fast-lane")
    for name in expected:
        assert run["env"][name] == fast_env[name]
        if name == "SOVRN_API_KEY":
            assert run["env"][name] == "${{ secrets.SOVRN_API_KEY2 || secrets.SOVRN_API_KEY }}"
        else:
            assert run["env"][name] == "${{ secrets." + name + " }}"
        assert f"          {name}: {run['env'][name]}" in text
    assert "DEALS_CLAUDE_API_KEY" not in text
