"""Tests for dealradar.publish.email_digest — offline, fake SMTP factory."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from dealradar.config import AppConfig
from dealradar.db import DB
from dealradar.publish.email_digest import build_digest, maybe_send_digest

UTC = timezone.utc
# October 2026: America/New_York is EDT (UTC-4).
BEFORE_8_LOCAL = datetime(2026, 10, 9, 11, 30, 0, tzinfo=UTC)  # 07:30 local
AFTER_8_LOCAL = datetime(2026, 10, 9, 13, 0, 0, tzinfo=UTC)    # 09:00 local
LOCAL_DAY = "2026-10-09"  # dedup token for both instants above

RECIPIENT = "digest@example.com"


class FakeSMTPServer:
    """Records the SMTP conversation; never touches the network."""

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.starttls_called = False
        self.login_args: tuple | None = None
        self.sent_messages: list = []
        self.quit_called = False

    def starttls(self):
        self.starttls_called = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, msg):
        self.sent_messages.append(msg)

    def quit(self):
        self.quit_called = True


class FakeSMTPFactory:
    def __init__(self):
        self.servers: list[FakeSMTPServer] = []

    def __call__(self, host, port):
        server = FakeSMTPServer(host, port)
        self.servers.append(server)
        return server


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
                 "SMTP_FROM"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def db(tmp_path):
    database = DB(tmp_path / "email_test.db")
    yield database
    database.close()


@pytest.fixture()
def cfg():
    return AppConfig(digest_recipient=RECIPIENT, digest_hour_local=8,
                     digest_timezone="America/New_York", min_pct=70.0)


def set_smtp_env(monkeypatch, *, user=True):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    if user:
        monkeypatch.setenv("SMTP_USER", "mailer@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "hunter2")


def make_deal(**overrides):
    seen = "2026-10-09T05:00:00+00:00"  # 8 h before AFTER_8_LOCAL -> fresh
    deal = {
        "dedupe_key": "walmart|lego set",
        "store": "Walmart",
        "item": "LEGO Set",
        "canonical_url": "https://www.walmart.com/ip/12345",
        "final_price": 10.0,
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


def bodies(msg) -> dict[str, str]:
    """Return {'plain': ..., 'html': ...} from the multipart/alternative."""
    return {
        part.get_content_subtype(): part.get_payload(decode=True).decode("utf-8")
        for part in msg.get_payload()
    }


# ---------------------------------------------------------------- gating


def test_before_8am_local_no_send(db, cfg, monkeypatch):
    set_smtp_env(monkeypatch)
    factory = FakeSMTPFactory()
    result = maybe_send_digest(db, cfg, BEFORE_8_LOCAL, deals=[make_deal()],
                               smtp_factory=factory)
    assert result is False
    assert factory.servers == []
    assert not db.alert_exists("email_digest", LOCAL_DAY)


def test_after_8am_sends_and_records(db, cfg, monkeypatch):
    set_smtp_env(monkeypatch)
    factory = FakeSMTPFactory()
    result = maybe_send_digest(db, cfg, AFTER_8_LOCAL, deals=[make_deal()],
                               smtp_factory=factory)
    assert result is True
    (server,) = factory.servers
    assert server.host == "smtp.example.com"
    assert server.port == 587  # default when SMTP_PORT is unset
    assert server.starttls_called is True  # port 587 -> STARTTLS
    assert server.login_args == ("mailer@example.com", "hunter2")
    assert server.quit_called is True

    (msg,) = server.sent_messages
    # Single recipient only, straight from cfg.digest_recipient.
    assert msg["To"] == RECIPIENT
    assert "," not in msg["To"]
    assert "1 deal at 70% off or more" in msg["Subject"]
    assert "LEGO Set" in bodies(msg)["plain"]

    assert db.alert_exists("email_digest", LOCAL_DAY)


def test_non_587_port_without_user_skips_starttls_and_login(db, cfg, monkeypatch):
    set_smtp_env(monkeypatch, user=False)
    monkeypatch.setenv("SMTP_PORT", "2525")
    factory = FakeSMTPFactory()
    assert maybe_send_digest(db, cfg, AFTER_8_LOCAL, deals=[make_deal()],
                             smtp_factory=factory) is True
    (server,) = factory.servers
    assert server.port == 2525
    assert server.starttls_called is False  # only port 587 upgrades
    assert server.login_args is None        # no SMTP_USER -> no login
    assert len(server.sent_messages) == 1


# ---------------------------------------------------------------- idempotency


def test_second_call_same_local_day_is_idempotent(db, cfg, monkeypatch):
    set_smtp_env(monkeypatch)
    factory = FakeSMTPFactory()
    assert maybe_send_digest(db, cfg, AFTER_8_LOCAL, deals=[make_deal()],
                             smtp_factory=factory) is True
    result = maybe_send_digest(db, cfg, AFTER_8_LOCAL, deals=[make_deal()],
                               smtp_factory=factory)
    assert result is False
    assert len(factory.servers) == 1  # no second SMTP connection at all


# ---------------------------------------------------------------- unconfigured


def test_missing_smtp_host_returns_false_without_recording(db, cfg, monkeypatch):
    # SMTP_HOST deliberately unset (autouse fixture cleared the env).
    factory = FakeSMTPFactory()
    result = maybe_send_digest(db, cfg, AFTER_8_LOCAL, deals=[make_deal()],
                               smtp_factory=factory)
    assert result is False
    assert factory.servers == []
    assert not db.alert_exists("email_digest", LOCAL_DAY)
    # Configuring later the SAME day still sends.
    set_smtp_env(monkeypatch)
    assert maybe_send_digest(db, cfg, AFTER_8_LOCAL, deals=[make_deal()],
                             smtp_factory=factory) is True
    assert len(factory.servers) == 1
    assert db.alert_exists("email_digest", LOCAL_DAY)


def test_missing_recipient_returns_false_without_recording(db, monkeypatch):
    set_smtp_env(monkeypatch)
    cfg = AppConfig(digest_recipient="", digest_hour_local=8,
                    digest_timezone="America/New_York")
    factory = FakeSMTPFactory()
    result = maybe_send_digest(db, cfg, AFTER_8_LOCAL, deals=[make_deal()],
                               smtp_factory=factory)
    assert result is False
    assert factory.servers == []
    assert not db.alert_exists("email_digest", LOCAL_DAY)


# ---------------------------------------------------------------- content


def test_deals_older_than_24h_excluded_from_body(db, cfg, monkeypatch):
    set_smtp_env(monkeypatch)
    factory = FakeSMTPFactory()
    fresh = make_deal()  # first seen 8 h ago
    stale = make_deal(dedupe_key="target|mug", item="Stale Coffee Mug",
                      store="Target",
                      first_seen_at="2026-10-08T10:00:00+00:00")  # 27 h ago
    assert maybe_send_digest(db, cfg, AFTER_8_LOCAL, deals=[fresh, stale],
                             smtp_factory=factory) is True
    (msg,) = factory.servers[0].sent_messages
    body = bodies(msg)
    assert "LEGO Set" in body["plain"]
    assert "Stale Coffee Mug" not in body["plain"]
    assert "Stale Coffee Mug" not in body["html"]
    assert "1 deal at 70% off or more" in msg["Subject"]  # stale deal not counted either


def test_zero_qualifying_deals_still_sends_valid_digest(db, cfg, monkeypatch):
    set_smtp_env(monkeypatch)
    factory = FakeSMTPFactory()
    result = maybe_send_digest(db, cfg, AFTER_8_LOCAL, deals=[],
                               smtp_factory=factory)
    assert result is True
    (msg,) = factory.servers[0].sent_messages
    assert "price-check update" in msg["Subject"]
    body = bodies(msg)
    assert "no new offers met our deal criteria in the last 24 hours" in body["plain"]
    assert "no new offers met our deal criteria in the last 24 hours" in body["html"]
    assert db.alert_exists("email_digest", LOCAL_DAY)


def test_build_digest_subject_contains_deal_count(cfg):
    deals = [make_deal(),
             make_deal(dedupe_key="target|mug", item="Mug", store="Target")]
    subject, text_body, html_body = build_digest(deals, cfg, AFTER_8_LOCAL)
    assert "2 deals" in subject
    assert "LEGO Set" in text_body
    assert "Mug" in text_body
    subject0, _, _ = build_digest([], cfg, AFTER_8_LOCAL)
    assert "price-check update" in subject0
