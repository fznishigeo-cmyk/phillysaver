"""Tests for dealradar.publish.telegram_pub — offline, fake http + fake sleep."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from dealradar.config import AppConfig
from dealradar.db import DB
from dealradar.publish.telegram_pub import format_message, send_alerts, tier_rank

NOW = datetime(2026, 10, 9, 14, 0, 0, tzinfo=timezone.utc)


class FakeHttpClient:
    """Records POSTs; returns a canned status code."""

    def __init__(self, status_code: int = 200):
        self.status_code = status_code
        self.posts: list[tuple[str, dict]] = []

    def post(self, url, json=None, **kwargs):
        self.posts.append((url, json))
        return SimpleNamespace(status_code=self.status_code, text="")


class FakeSleep:
    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID",
                 "TELEGRAM_ADMIN_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def db(tmp_path):
    database = DB(tmp_path / "tg_test.db")
    yield database
    database.close()


@pytest.fixture()
def cfg():
    return AppConfig(telegram_messages_per_minute=15, resend_price_drop_pct=10.0,
                     telegram_dry_run=False)


def make_deal(**overrides):
    seen = "2026-10-09T12:00:00+00:00"
    deal = {
        "dedupe_key": "walmart|lego set",
        "store": "Walmart",
        "item": "LEGO Set",
        "brand": "LEGO",
        "canonical_url": "https://www.walmart.com/ip/12345",
        "final_price": 10.0,
        "regular_price": 50.0,
        "pct_at_checkout": 80.0,
        "abs_savings": 40.0,
        "conditions": [],
        "verify_tier": "unverified",
        "status": "alive",
        "first_seen_at": seen,
        "last_seen_at": seen,
        "sightings": [
            {"source_name": "Slickdeals", "source_url": "https://slickdeals.net/f/1",
             "seen_at": seen, "final_price": 10.0},
        ],
    }
    deal.update(overrides)
    return deal


def set_creds(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@testchan")


def test_tier_rank_ordering():
    assert tier_rank("unverified") == 0
    assert tier_rank("history-checked") == 1
    assert tier_rank("corroborated") == 2
    assert tier_rank("") == 0
    assert tier_rank("garbage") == 0


def test_format_message_full():
    msg = format_message(make_deal(conditions=["online only", "max 2"]))
    assert msg == (
        "LEGO Set — $10.00 (80% off) at Walmart"
        " · online only, max 2"
        " · via Slickdeals https://slickdeals.net/f/1"
        " · https://www.walmart.com/ip/12345"
    )


def test_format_message_omits_empty_conditions():
    msg = format_message(make_deal(conditions=[]))
    assert "online only" not in msg
    assert " · via Slickdeals" in msg
    assert msg.startswith("LEGO Set — $10.00 (80% off) at Walmart")


def test_format_message_names_corroboration_in_plain_words():
    assert "(80% off, 2+ sources) at" in format_message(
        make_deal(verify_tier="corroborated"))
    assert "(80% off, price history) at" in format_message(
        make_deal(verify_tier="history-checked"))
    assert "unverified" not in format_message(make_deal(verify_tier="unverified"))


def test_first_sighting_sends(db, cfg, monkeypatch):
    set_creds(monkeypatch)
    client = FakeHttpClient()
    result = send_alerts(db, cfg, NOW, deals=[make_deal()], dry_run=False,
                         http_client=client, sleep_fn=FakeSleep())
    assert result == {"sent": 1, "skipped": 0, "dry_run": False}
    assert len(client.posts) == 1
    url, payload = client.posts[0]
    assert url == "https://api.telegram.org/bottest-token/sendMessage"
    assert payload["chat_id"] == "@testchan"
    assert payload["disable_web_page_preview"] is True
    assert "LEGO Set" in payload["text"]
    last = db.last_alert("walmart|lego set", "telegram")
    assert last is not None
    assert last["price_at_send"] == 10.0
    assert last["tier_at_send"] == "unverified"


def test_identical_rerun_is_idempotent(db, cfg, monkeypatch):
    set_creds(monkeypatch)
    client = FakeHttpClient()
    send_alerts(db, cfg, NOW, deals=[make_deal()], dry_run=False,
                http_client=client, sleep_fn=FakeSleep())
    result = send_alerts(db, cfg, NOW, deals=[make_deal()], dry_run=False,
                         http_client=client, sleep_fn=FakeSleep())
    assert result["sent"] == 0
    assert result["skipped"] == 1
    assert len(client.posts) == 1  # no second POST


def test_ten_pct_price_drop_resends(db, cfg, monkeypatch):
    set_creds(monkeypatch)
    client = FakeHttpClient()
    send_alerts(db, cfg, NOW, deals=[make_deal()], dry_run=False,
                http_client=client, sleep_fn=FakeSleep())
    result = send_alerts(db, cfg, NOW, deals=[make_deal(final_price=9.0)],
                         dry_run=False, http_client=client, sleep_fn=FakeSleep())
    assert result["sent"] == 1
    assert len(client.posts) == 2


def test_five_pct_price_drop_does_not_resend(db, cfg, monkeypatch):
    set_creds(monkeypatch)
    client = FakeHttpClient()
    send_alerts(db, cfg, NOW, deals=[make_deal()], dry_run=False,
                http_client=client, sleep_fn=FakeSleep())
    result = send_alerts(db, cfg, NOW, deals=[make_deal(final_price=9.5)],
                         dry_run=False, http_client=client, sleep_fn=FakeSleep())
    assert result["sent"] == 0
    assert result["skipped"] == 1
    assert len(client.posts) == 1


def test_tier_improvement_resends(db, cfg, monkeypatch):
    set_creds(monkeypatch)
    client = FakeHttpClient()
    send_alerts(db, cfg, NOW, deals=[make_deal()], dry_run=False,
                http_client=client, sleep_fn=FakeSleep())
    result = send_alerts(db, cfg, NOW,
                         deals=[make_deal(verify_tier="corroborated")],
                         dry_run=False, http_client=client, sleep_fn=FakeSleep())
    assert result["sent"] == 1
    assert len(client.posts) == 2
    assert db.last_alert("walmart|lego set", "telegram")["tier_at_send"] == "corroborated"


def test_fallback_dry_run_prints_but_never_consumes_alerts(db, cfg, capsys, caplog):
    # No credentials set (autouse fixture cleared env) -> FALLBACK dry run.
    # It must print the message but record NOTHING: otherwise every deal first
    # sighted before the bot is configured would permanently lose its one
    # first-sighting alert.
    with caplog.at_level(logging.INFO, logger="dealradar.publish.telegram"):
        result = send_alerts(db, cfg, NOW, deals=[make_deal()])
    assert result["dry_run"] is True
    assert result["sent"] == 1
    out = capsys.readouterr().out
    assert "TELEGRAM DRY-RUN: " in out
    assert "LEGO Set" in out
    assert any("TELEGRAM DRY-RUN:" in r.getMessage() for r in caplog.records)
    assert not db.alert_exists("telegram", "walmart|lego set|10.00|unverified")
    # Once credentials appear, the first-sighting alert still fires for real.


def test_explicit_dry_run_records_for_idempotent_reruns(db, cfg, capsys):
    result = send_alerts(db, cfg, NOW, deals=[make_deal()], dry_run=True)
    assert result["sent"] == 1
    assert db.alert_exists("telegram", "walmart|lego set|10.00|unverified")
    rerun = send_alerts(db, cfg, NOW, deals=[make_deal()], dry_run=True)
    assert rerun["sent"] == 0
    assert capsys.readouterr().out.count("TELEGRAM DRY-RUN:") == 1


def test_explicit_dry_run_arg_wins_over_creds(db, cfg, monkeypatch, capsys):
    set_creds(monkeypatch)
    client = FakeHttpClient()
    result = send_alerts(db, cfg, NOW, deals=[make_deal()], dry_run=True,
                         http_client=client)
    assert result["dry_run"] is True
    assert len(client.posts) == 0
    assert "TELEGRAM DRY-RUN: " in capsys.readouterr().out


def test_rate_limiter_sleeps_4s_between_two_real_sends(db, cfg, monkeypatch):
    set_creds(monkeypatch)
    client = FakeHttpClient()
    sleeper = FakeSleep()
    deals = [make_deal(),
             make_deal(dedupe_key="target|mug", item="Mug", store="Target")]
    result = send_alerts(db, cfg, NOW, deals=deals, dry_run=False,
                         http_client=client, sleep_fn=sleeper)
    assert result["sent"] == 2
    assert sleeper.calls == [pytest.approx(4.0)]  # 60 / 15 between the two sends


def test_dry_run_never_sleeps(db, cfg):
    sleeper = FakeSleep()
    deals = [make_deal(),
             make_deal(dedupe_key="target|mug", item="Mug", store="Target")]
    send_alerts(db, cfg, NOW, deals=deals, dry_run=True, sleep_fn=sleeper)
    assert sleeper.calls == []


def test_failed_post_not_recorded_then_retried(db, cfg, monkeypatch):
    set_creds(monkeypatch)
    failing = FakeHttpClient(status_code=500)
    result = send_alerts(db, cfg, NOW, deals=[make_deal()], dry_run=False,
                         http_client=failing, sleep_fn=FakeSleep())
    assert result["sent"] == 0
    assert result["skipped"] == 1
    assert db.last_alert("walmart|lego set", "telegram") is None
    # Next run retries and succeeds.
    ok = FakeHttpClient(status_code=200)
    result = send_alerts(db, cfg, NOW, deals=[make_deal()], dry_run=False,
                         http_client=ok, sleep_fn=FakeSleep())
    assert result["sent"] == 1
    assert db.last_alert("walmart|lego set", "telegram") is not None
