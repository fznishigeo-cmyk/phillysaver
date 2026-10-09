"""Tests for dealradar.publish.admin_alerts — offline, fake http client."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from dealradar.config import AppConfig
from dealradar.db import DB
from dealradar.publish.admin_alerts import check_and_alert

NOW = datetime(2026, 10, 9, 14, 0, 0, tzinfo=timezone.utc)
HOUR_TOKEN = "2026-10-09T14|admin"


class FakeHttpClient:
    """Records POSTs; returns a canned status code."""

    def __init__(self, status_code: int = 200):
        self.status_code = status_code
        self.posts: list[tuple[str, dict]] = []

    def post(self, url, json=None, **kwargs):
        self.posts.append((url, json))
        return SimpleNamespace(status_code=self.status_code, text="")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID",
                 "TELEGRAM_ADMIN_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def db(tmp_path):
    database = DB(tmp_path / "admin_test.db")
    yield database
    database.close()


@pytest.fixture()
def cfg():
    return AppConfig(min_items_ratio=0.5, tripped_sources_warn=5)


def set_admin_creds(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "admin-token")
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "999888")
    # Set the public channel too, to prove warnings never go there.
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@publicchannel")


def seed_hourly_runs(db, now, *, items: int = 100, n: int = 7):
    """Finished hourly runs 1..n hours ago (all older than the 30-min cutoff)."""
    for i in range(n):
        started = now - timedelta(hours=i + 1)
        key = f"run-{i}"
        db.start_run(key, "hourly", started)
        db.finish_run(key, now=started + timedelta(minutes=5),
                      items_fetched=items, posts_new=0, deals_parsed=0,
                      published=0, spend_usd=0.0)


def trip_sources(db, now, n: int):
    for i in range(n):
        db.record_source_result(f"src{i}", ok=False, error="boom", items=0,
                                now=now, trip_threshold=1, cooldown_h=6.0)


# ---------------------------------------------------------------- triggers


def test_low_volume_warns_and_posts_to_admin_chat(db, cfg, monkeypatch):
    set_admin_creds(monkeypatch)
    seed_hourly_runs(db, NOW, items=100)  # 7-day hourly avg = 100
    client = FakeHttpClient()
    # 10 < 0.5 * 100 -> low volume
    warnings = check_and_alert(db, cfg, NOW, items_fetched=10, http_client=client)
    assert warnings == ["low volume: 10 vs 7-day avg 100.0"]
    assert len(client.posts) == 1
    url, payload = client.posts[0]
    assert url == "https://api.telegram.org/botadmin-token/sendMessage"
    assert payload["chat_id"] == "999888"          # the ADMIN chat...
    assert payload["chat_id"] != "@publicchannel"  # ...never the channel
    assert payload["disable_web_page_preview"] is True
    assert "low volume: 10 vs 7-day avg 100.0" in payload["text"]
    assert db.alert_exists("admin", HOUR_TOKEN)


def test_items_at_threshold_do_not_warn(db, cfg, monkeypatch):
    set_admin_creds(monkeypatch)
    seed_hourly_runs(db, NOW, items=100)
    client = FakeHttpClient()
    # Exactly 0.5 * avg is NOT below the ratio -> quiet.
    warnings = check_and_alert(db, cfg, NOW, items_fetched=50, http_client=client)
    assert warnings == []
    assert client.posts == []


def test_tripped_sources_trigger_at_threshold(db, cfg, monkeypatch):
    set_admin_creds(monkeypatch)
    trip_sources(db, NOW, cfg.tripped_sources_warn)  # exactly 5 tripped
    client = FakeHttpClient()
    # No run_stats seeded -> avg is None -> no low-volume warning possible.
    warnings = check_and_alert(db, cfg, NOW, items_fetched=100, http_client=client)
    assert warnings == ["5 sources tripped"]
    assert len(client.posts) == 1
    _, payload = client.posts[0]
    assert payload["chat_id"] == "999888"
    assert "5 sources tripped" in payload["text"]
    assert db.alert_exists("admin", HOUR_TOKEN)


def test_all_quiet_returns_empty_and_sends_nothing(db, cfg, monkeypatch):
    set_admin_creds(monkeypatch)
    seed_hourly_runs(db, NOW, items=100)
    trip_sources(db, NOW, cfg.tripped_sources_warn - 1)  # 4 tripped: below warn
    client = FakeHttpClient()
    warnings = check_and_alert(db, cfg, NOW, items_fetched=80, http_client=client)
    assert warnings == []
    assert client.posts == []
    assert not db.alert_exists("admin", HOUR_TOKEN)


# ---------------------------------------------------------------- idempotency


def test_idempotent_within_same_hour(db, cfg, monkeypatch):
    set_admin_creds(monkeypatch)
    seed_hourly_runs(db, NOW, items=100)
    client = FakeHttpClient()
    first = check_and_alert(db, cfg, NOW, items_fetched=10, http_client=client)
    assert first == ["low volume: 10 vs 7-day avg 100.0"]
    assert len(client.posts) == 1
    # Later the same hour: warnings still computed, nothing sent again.
    later = NOW + timedelta(minutes=25)
    second = check_and_alert(db, cfg, later, items_fetched=10, http_client=client)
    assert second == ["low volume: 10 vs 7-day avg 100.0"]
    assert len(client.posts) == 1  # no second POST


# ---------------------------------------------------------------- credentials


def test_missing_creds_logs_only_but_still_records(db, cfg, caplog):
    # Autouse fixture cleared TELEGRAM_* -> log-only path.
    seed_hourly_runs(db, NOW, items=100)
    client = FakeHttpClient()
    with caplog.at_level(logging.WARNING, logger="dealradar.publish.admin"):
        warnings = check_and_alert(db, cfg, NOW, items_fetched=10,
                                   http_client=client)
    assert warnings == ["low volume: 10 vs 7-day avg 100.0"]
    assert client.posts == []  # no HTTP call without credentials
    assert any("no credentials" in r.getMessage() for r in caplog.records)
    # Still recorded, so the hour stays idempotent.
    assert db.alert_exists("admin", HOUR_TOKEN)


def test_failed_post_not_recorded_then_retried(db, cfg, monkeypatch):
    set_admin_creds(monkeypatch)
    seed_hourly_runs(db, NOW, items=100)
    failing = FakeHttpClient(status_code=500)
    warnings = check_and_alert(db, cfg, NOW, items_fetched=10, http_client=failing)
    assert warnings == ["low volume: 10 vs 7-day avg 100.0"]
    assert len(failing.posts) == 1
    assert not db.alert_exists("admin", HOUR_TOKEN)  # retry stays possible
    ok = FakeHttpClient(status_code=200)
    check_and_alert(db, cfg, NOW, items_fetched=10, http_client=ok)
    assert len(ok.posts) == 1
    assert db.alert_exists("admin", HOUR_TOKEN)
