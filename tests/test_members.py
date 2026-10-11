"""Members (Stripe subscribers) get new verified deals by email — offline.

Stripe is a MockTransport, SMTP is a recording fake; the DB is real.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dealradar.config import AppConfig
from dealradar.db import DB
from dealradar.publish import members as M
from dealradar.util import iso

NOW = datetime(2026, 10, 10, 23, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("STRIPE_SECRET_KEY", "SMTP_HOST", "SMTP_PORT", "SMTP_USER",
                 "SMTP_PASSWORD", "SMTP_FROM"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def db(tmp_path):
    database = DB(tmp_path / "members.db")
    yield database
    database.close()


def _deal(key: str, pct: float = 70.0, price: float = 9.99) -> dict:
    return {"dedupe_key": key, "item": f"Item {key}", "store": "Amazon",
            "final_price": price, "pct_at_checkout": pct,
            "verify_tier": "corroborated", "conditions": [],
            "sightings": [{"source_name": "hip2save",
                           "source_url": "https://hip2save.com/deals/x/"}],
            "canonical_url": "https://www.amazon.com/dp/B0X"}


class FakeSMTP:
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.messages: list = []
        self.to_addrs: list = []
        self.login_args = None
        self.quit_called = False

    def starttls(self):
        pass

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.messages.append(msg)
        self.to_addrs = list(to_addrs or [])

    def quit(self):
        self.quit_called = True


class Factory:
    def __init__(self):
        self.servers: list[FakeSMTP] = []

    def __call__(self, host, port):
        s = FakeSMTP(host, port)
        self.servers.append(s)
        return s


class TestStripeMemberList:
    def test_paginates_both_statuses_and_dedupes(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "rk_test_readonly")
        pages = {
            ("active", None): {"data": [{"id": "sub_1", "customer": {"email": "A@x.com"}}],
                               "has_more": True},
            ("active", "sub_1"): {"data": [{"id": "sub_2", "customer": {"email": "b@x.com"}}],
                                  "has_more": False},
            ("trialing", None): {"data": [{"id": "sub_3", "customer": {"email": "a@x.com"}}],
                                 "has_more": False},
        }

        def handler(req: httpx.Request) -> httpx.Response:
            assert req.url.host == "api.stripe.com"
            assert req.headers["Authorization"].startswith("Basic ")
            assert req.url.params["expand[]"] == "data.customer"
            key = (req.url.params["status"], req.url.params.get("starting_after"))
            return httpx.Response(200, json=pages[key])

        client = httpx.Client(transport=httpx.MockTransport(handler))
        assert M.fetch_member_emails(client) == ["a@x.com", "b@x.com"]

    def test_trialing_subscriber_alone_is_a_member(self, monkeypatch):
        """The free trial is Stripe's (card up front): a subscription still
        in status "trialing" gets the member emails like an active one."""
        monkeypatch.setenv("STRIPE_SECRET_KEY", "rk_test_readonly")
        assert M.MEMBER_STATUSES == ("active", "trialing")
        asked: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            status = req.url.params["status"]
            asked.append(status)
            data = ([{"id": "sub_t", "customer": {"email": "Trial@X.com"}}]
                    if status == "trialing" else [])
            return httpx.Response(200, json={"data": data, "has_more": False})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        assert M.fetch_member_emails(client) == ["trial@x.com"]
        assert asked == ["active", "trialing"]

    def test_unconfigured_or_failing_stripe_keeps_the_cache(self, db, monkeypatch):
        assert M.fetch_member_emails() is None          # no key at all
        db.meta_set(M.META_MEMBERS, json.dumps(["c@x.com"]))
        assert M.member_list(db) == ["c@x.com"]         # cache serves
        monkeypatch.setenv("STRIPE_SECRET_KEY", "rk_test_x")
        client = httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(500, json={"error": "boom"})))
        assert M.member_list(db, client) == ["c@x.com"]  # failure -> cache


class TestMemberEmail:
    def _cfg(self) -> AppConfig:
        cfg = AppConfig()
        cfg.custom_domain = "wowpriced.com"
        cfg.portal_url = "https://billing.stripe.com/p/login/x"
        cfg.members_email_every_minutes = 60
        return cfg

    def test_bundles_once_per_window_and_never_resends(self, db, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example")
        monkeypatch.setenv("SMTP_USER", "u@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "pw")
        db.meta_set(M.META_MEMBERS, json.dumps(["a@x.com", "b@x.com"]))
        cfg = self._cfg()
        factory = Factory()
        deals = [_deal("k1", 91.0), _deal("k2", 60.0)]

        stats = M.send_member_alerts(db, cfg, NOW, deals=deals, smtp_factory=factory)
        assert stats == {"members": 2, "deals": 2, "sent": 2, "skipped": ""}
        server = factory.servers[0]
        assert server.login_args == ("u@example.com", "pw")
        assert server.to_addrs == ["a@x.com", "b@x.com"]  # BCC list
        msg = server.messages[0]
        assert msg["Subject"] == "wowpriced: 2 new verified deals (up to 91% off)"
        raw = msg.as_string()
        assert "a@x.com" not in raw and "b@x.com" not in raw  # no address leaks
        text = msg.get_payload()[0].get_payload(decode=True).decode("utf-8")
        assert "Item k1" in text and "$9.99" in text and "91% off" in text
        assert "Live wall: https://wowpriced.com/" in text
        assert "Manage membership: https://billing.stripe.com/p/login/x" in text

        # Idempotent: same deals again -> nothing to send.
        again = M.send_member_alerts(db, cfg, NOW, deals=deals, smtp_factory=factory)
        assert again["sent"] == 0 and again["deals"] == 0
        assert len(factory.servers) == 1

        # A new deal inside the window waits for the next one...
        later = deals + [_deal("k3", 75.0)]
        s3 = M.send_member_alerts(db, cfg, NOW + timedelta(minutes=20),
                                  deals=later, smtp_factory=factory)
        assert s3["skipped"] == "within email window"
        assert len(factory.servers) == 1
        # ...and goes out after it, alone.
        s4 = M.send_member_alerts(db, cfg, NOW + timedelta(minutes=61),
                                  deals=later, smtp_factory=factory)
        assert s4["deals"] == 1 and s4["sent"] == 2
        assert "1 new verified deal (up to 75% off)" in factory.servers[1].messages[0]["Subject"]

    def test_daily_email_is_one_per_local_morning_with_that_days_deals(self, db, monkeypatch):
        """Operator call: at most one member email a day, at 7 am Eastern
        (ahead of the free 8 am list), never drifting later day by day, and
        carrying what arrived since the last one, best first, with a line for
        the rest (review: a 40-deal cap and a growing backlog made "the
        day's deals" false)."""
        monkeypatch.setenv("SMTP_HOST", "smtp.example")
        db.meta_set(M.META_MEMBERS, json.dumps(["a@x.com"]))
        cfg = self._cfg()
        cfg.members_email_every_minutes = 1440
        cfg.members_email_hour_local = 7
        cfg.members_email_timezone = "America/New_York"
        cfg.members_max_deals_per_email = 2
        factory = Factory()
        # 2026-10-12 07:00 EDT == 11:00 UTC.
        day = datetime(2026, 10, 12, 11, 0, tzinfo=timezone.utc)
        db.meta_set(M.META_LAST_EMAIL, iso(day - timedelta(hours=20)))  # yesterday 11:00 EDT

        def deal(key, pct, seen):
            d = _deal(key, pct)
            d["first_seen_at"] = iso(seen)
            return d
        old = deal("old", 99.0, day - timedelta(hours=30))   # before the last email: backlog
        today = [deal(f"n{i}", 90.0 - i, day - timedelta(hours=3 + i)) for i in range(5)]
        deals = [old] + today

        early = M.send_member_alerts(db, cfg, day - timedelta(minutes=5),
                                     deals=deals, smtp_factory=factory)
        assert early["skipped"] == "within email window" and not factory.servers
        first = M.send_member_alerts(db, cfg, day + timedelta(minutes=7),
                                     deals=deals, smtp_factory=factory)
        assert first["sent"] == 1 and first["deals"] == 2
        text = factory.servers[0].messages[0].get_payload()[0].get_payload(decode=True).decode()
        assert "Item n0" in text and "Item n1" in text and "Item old" not in text
        assert "+3 more new deals on the live wall: https://wowpriced.com/" in text
        # The same local day, more deals: nothing until tomorrow morning.
        later = deals + [deal("n9", 95.0, day + timedelta(hours=2))]
        for h in (1, 6, 12):
            s = M.send_member_alerts(db, cfg, day + timedelta(hours=h),
                                     deals=later, smtp_factory=factory)
            assert s["skipped"] == "within email window"
        assert len(factory.servers) == 1
        # Tomorrow at 7 am: one email with what arrived since, not the
        # three of today's that did not fit (they are on the wall).
        nxt = day + timedelta(days=1, minutes=3)
        s = M.send_member_alerts(db, cfg, nxt, deals=later, smtp_factory=factory)
        assert s["sent"] == 1 and s["deals"] == 1 and len(factory.servers) == 2
        assert "Item n9" in factory.servers[1].messages[0].get_payload()[0].get_payload(
            decode=True).decode()
        # Winter (EST): 7 am local is 12:00 UTC, still the first run after it.
        assert M.send_member_alerts(db, cfg, datetime(2026, 12, 2, 11, 50, tzinfo=timezone.utc),
                                    deals=[deal("w", 80.0, datetime(2026, 12, 2, 1, 0, tzinfo=timezone.utc))],
                                    smtp_factory=factory)["skipped"] == "within email window"

    def test_shipped_config_emails_members_once_a_day(self):
        from dealradar.config import load_config
        cfg = load_config()
        assert cfg.members_email_every_minutes == 1440
        assert cfg.members_email_hour_local == 7 < cfg.newsletter_hour_local
        assert cfg.members_email_timezone == "America/New_York"
        assert cfg.members_max_deals_per_email == 40

    def test_nothing_goes_out_without_members_or_smtp(self, db, monkeypatch):
        cfg = self._cfg()
        factory = Factory()
        s = M.send_member_alerts(db, cfg, NOW, deals=[_deal("k1")], smtp_factory=factory)
        assert s["skipped"] == "no members" and factory.servers == []
        db.meta_set(M.META_MEMBERS, json.dumps(["a@x.com"]))
        s = M.send_member_alerts(db, cfg, NOW, deals=[_deal("k1")], smtp_factory=factory)
        assert s["skipped"] == "smtp not configured" and factory.servers == []
        # Nothing was recorded, so configuring SMTP later still sends.
        assert not db.alert_exists(M.CHANNEL, "k1")

    def test_failed_send_records_nothing_and_retries(self, db, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example")
        db.meta_set(M.META_MEMBERS, json.dumps(["a@x.com"]))

        def boom(host, port):
            raise OSError("smtp down")

        s = M.send_member_alerts(db, self._cfg(), NOW, deals=[_deal("k1")],
                                 smtp_factory=boom)
        assert s["skipped"] == "send failed" and s["sent"] == 0
        assert not db.alert_exists(M.CHANNEL, "k1")
