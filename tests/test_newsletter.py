"""Tests for dealradar.publish.newsletter — offline: the subscribe Worker is
an httpx MockTransport, SMTP a recording fake, the DB real."""

from __future__ import annotations

import json
import smtplib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import yaml

from dealradar.config import SECRET_NAMES, AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import email_digest
from dealradar.publish import newsletter as N

UTC = timezone.utc
# October 2026: America/New_York is EDT (UTC-4).
BEFORE_8_LOCAL = datetime(2026, 10, 9, 11, 30, 0, tzinfo=UTC)  # 07:30 local
AFTER_8_LOCAL = datetime(2026, 10, 9, 13, 0, 0, tzinfo=UTC)    # 09:00 local
LOCAL_DAY = "2026-10-09"
WORKER = "https://wowpriced-subscribe.example.workers.dev"
SECRET = "s3cret-bearer-token"
DISCLOSURE = "Disclosure: some outbound links may be affiliate links."


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("NEWSLETTER_SECRET", "SMTP_HOST", "SMTP_PORT", "SMTP_USER",
                 "SMTP_PASSWORD", "SMTP_FROM"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def db(tmp_path):
    database = DB(tmp_path / "newsletter.db")
    yield database
    database.close()


@pytest.fixture()
def cfg():
    cfg = AppConfig(ftc_disclosure=DISCLOSURE, min_pct=70.0)
    cfg.custom_domain = "wowpriced.com"
    cfg.newsletter_worker_url = WORKER
    cfg.newsletter_hour_local = 8
    cfg.newsletter_timezone = "America/New_York"
    cfg.newsletter_max_per_run = 200
    return cfg


def configure(monkeypatch):
    monkeypatch.setenv("NEWSLETTER_SECRET", SECRET)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "mailer@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "hunter2")
    monkeypatch.setenv("SMTP_FROM", "deals@wowpriced.com")


class FakeWorker:
    """The subscribe Worker's GET /list as an httpx MockTransport."""

    def __init__(self, subs: list[tuple[str, str]], status: int = 200):
        self.subs = subs
        self.status = status
        self.calls = 0

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls += 1
        assert str(req.url) == WORKER + "/list"
        assert req.headers["Authorization"] == f"Bearer {SECRET}"
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "nope"})
        return httpx.Response(200, json={
            "emails": [{"email": e, "token": t} for e, t in self.subs]})


class FakeSMTP:
    def __init__(self, host, port, fail_for=(), drop_after=None):
        self.host, self.port = host, port
        self.fail_for = set(fail_for)
        self.drop_after = drop_after
        self.messages: list = []
        self.starttls_called = False
        self.login_args = None
        self.quit_called = False

    def starttls(self):
        self.starttls_called = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, msg):
        if self.drop_after is not None and len(self.messages) >= self.drop_after:
            raise smtplib.SMTPServerDisconnected("connection lost")
        if msg["To"] in self.fail_for:
            raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no such user")})
        self.messages.append(msg)

    def quit(self):
        self.quit_called = True


class Factory:
    def __init__(self, **server_kwargs):
        self.server_kwargs = server_kwargs
        self.servers: list[FakeSMTP] = []

    def __call__(self, host, port):
        s = FakeSMTP(host, port, **self.server_kwargs)
        self.servers.append(s)
        return s

    @property
    def messages(self) -> list:
        return [m for s in self.servers for m in s.messages]


def make_deal(**overrides):
    seen = "2026-10-09T05:00:00+00:00"
    deal = {
        "dedupe_key": "walmart|lego set",
        "store": "Walmart",
        "item": "LEGO Star Wars Set",
        "canonical_url": "https://www.walmart.com/ip/12345",
        "image_url": "https://i5.walmartimages.com/asr/lego.jpg",
        "final_price": 10.0,
        "regular_price": 50.0,
        "pct_at_checkout": 80.0,
        "abs_savings": 40.0,
        "conditions": [],
        "verify_tier": "corroborated",
        "status": "alive",
        "first_seen_at": seen,
        "last_seen_at": seen,
        # Never published: only facts, a link and the credit leave the module.
        "evidence": [{"quote": "VERBATIM QUOTE FROM THE SOURCE POST"}],
        "sightings": [
            {"source_name": "Slickdeals", "source_url": "https://slickdeals.net/f/1",
             "seen_at": seen, "final_price": 10.0},
        ],
    }
    deal.update(overrides)
    return deal


def bodies(msg) -> dict[str, str]:
    return {part.get_content_subtype(): part.get_payload(decode=True).decode("utf-8")
            for part in msg.get_payload()}


def sent_today(db) -> list[str]:
    return json.loads(db.meta_get(N.meta_key(LOCAL_DAY)) or "[]")


def fps(*emails: str) -> set[str]:
    """Progress is recorded as fingerprints, never as addresses."""
    return {N.fingerprint(e) for e in emails}


def send(db, cfg, now, worker, factory, deals=None):
    return N.send_daily_newsletter(
        db, cfg, now, deals=[make_deal()] if deals is None else deals,
        smtp_factory=factory, http_client=worker.client())


# ---------------------------------------------------------------- gating


class TestGating:
    def test_before_send_hour_nothing_happens(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        worker, factory = FakeWorker([("a@x.com", "t1")]), Factory()
        stats = send(db, cfg, BEFORE_8_LOCAL, worker, factory)
        assert stats == {"sent": 0, "skipped": "before 08:00 America/New_York",
                         "day": LOCAL_DAY}
        assert worker.calls == 0 and factory.servers == []

    def test_disabled_in_config(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        cfg.newsletter_enabled = False
        worker, factory = FakeWorker([("a@x.com", "t1")]), Factory()
        assert send(db, cfg, AFTER_8_LOCAL, worker, factory)["skipped"] == "disabled in config"
        assert worker.calls == 0 and factory.servers == []

    @pytest.mark.parametrize("missing, reason", [
        ("worker_url", "newsletter.worker_url not configured"),
        ("NEWSLETTER_SECRET", "NEWSLETTER_SECRET not configured"),
        ("SMTP_HOST", "SMTP_HOST not configured"),
    ])
    def test_skips_cleanly_when_unconfigured(self, db, cfg, monkeypatch, missing, reason):
        configure(monkeypatch)
        if missing == "worker_url":
            cfg.newsletter_worker_url = ""
        else:
            monkeypatch.delenv(missing)
        worker, factory = FakeWorker([("a@x.com", "t1")]), Factory()
        stats = send(db, cfg, AFTER_8_LOCAL, worker, factory)
        assert stats["skipped"] == reason and stats["sent"] == 0
        assert worker.calls == 0 and factory.servers == []
        # Nothing recorded: configuring later the same day still sends.
        assert db.meta_get(N.meta_key(LOCAL_DAY)) is None

    def test_all_three_missing_named_together(self, db, cfg):
        cfg.newsletter_worker_url = ""
        stats = send(db, cfg, AFTER_8_LOCAL, FakeWorker([]), Factory())
        assert stats["skipped"] == ("newsletter.worker_url, NEWSLETTER_SECRET, "
                                    "SMTP_HOST not configured")

    def test_unreachable_list_skips_and_retries_next_run(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        worker, factory = FakeWorker([("a@x.com", "t1")], status=500), Factory()
        stats = send(db, cfg, AFTER_8_LOCAL, worker, factory)
        assert stats["skipped"] == "subscriber list unavailable" and stats["sent"] == 0
        assert factory.servers == []
        worker.status = 200  # the Worker is back: the same run key sends
        assert send(db, cfg, AFTER_8_LOCAL, worker, factory)["sent"] == 1

    def test_no_subscribers(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        factory = Factory()
        stats = send(db, cfg, AFTER_8_LOCAL, FakeWorker([]), factory)
        assert stats["skipped"] == "no subscribers" and factory.servers == []

    def test_no_deals_yet_waits_for_a_later_run(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        worker, factory = FakeWorker([("a@x.com", "t1")]), Factory()
        stats = send(db, cfg, AFTER_8_LOCAL, worker, factory, deals=[])
        assert stats["skipped"] == "no published deals yet today"
        assert factory.servers == [] and sent_today(db) == []
        later = AFTER_8_LOCAL + timedelta(hours=2)
        assert send(db, cfg, later, worker, factory)["sent"] == 1

    def test_smtp_connect_failure_skips(self, db, cfg, monkeypatch):
        configure(monkeypatch)

        def boom(host, port):
            raise OSError("smtp down")

        stats = N.send_daily_newsletter(db, cfg, AFTER_8_LOCAL, deals=[make_deal()],
                                        smtp_factory=boom,
                                        http_client=FakeWorker([("a@x.com", "t1")]).client())
        assert stats["skipped"].startswith("smtp connect failed") and stats["sent"] == 0
        assert sent_today(db) == []


# ---------------------------------------------------------------- sending


class TestSending:
    def test_one_personalised_message_per_subscriber(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        worker = FakeWorker([("a@x.com", "tok-a"), ("b@x.com", "tok-b")])
        factory = Factory()
        deals = [make_deal(), make_deal(dedupe_key="target|mug", item="Coffee Mug",
                                        store="Target", final_price=4.5,
                                        regular_price=None, pct_at_checkout=91.0,
                                        image_url="", canonical_url="",
                                        sightings=[{"source_name": "hip2save",
                                                    "source_url": "https://hip2save.com/p/2"}])]
        stats = send(db, cfg, AFTER_8_LOCAL, worker, factory, deals=deals)
        assert stats == {"sent": 2, "skipped": "", "day": LOCAL_DAY}

        (server,) = factory.servers  # one connection for the whole batch
        assert server.host == "smtp.example.com" and server.port == 587
        assert server.starttls_called and server.login_args == ("mailer@example.com", "hunter2")
        assert server.quit_called
        assert [m["To"] for m in server.messages] == ["a@x.com", "b@x.com"]
        for m in server.messages:
            assert m["From"] == "deals@wowpriced.com"
            assert m["Subject"] == "wowpriced: 2 verified deals, up to 91% off"
            assert "," not in m["To"]

        a, b = server.messages
        text, html = bodies(a)["plain"], bodies(a)["html"]
        # Facts: item, price, was-price, % off, store, link, credit, disclosure.
        assert "LEGO Star Wars Set" in text and "$10.00" in text
        assert "was $50.00" in text and "80% off" in text and "at Walmart" in text
        assert "Get deal: https://www.walmart.com/ip/12345" in text
        assert "via Slickdeals: https://slickdeals.net/f/1" in text
        assert "Live wall: https://wowpriced.com/" in text
        assert DISCLOSURE in text and DISCLOSURE in html
        assert "You get this because you subscribed at wowpriced.com" in text
        assert "You get this because you subscribed at wowpriced.com" in html
        # Thumbnail only when the deal has an image; the mug has none.
        assert '<img src="https://i5.walmartimages.com/asr/lego.jpg"' in html
        assert html.count("<img ") == 1
        assert "Coffee Mug" in html and "$4.50" in html and "91% off" in html
        # No product URL -> the source post is the link (as on the site).
        assert 'href="https://hip2save.com/p/2"' in html
        assert "Get deal: https://hip2save.com/p/2" in text
        # Personalised only by the unsubscribe link.
        unsub_a = f"{WORKER}/unsubscribe?e=a%40x.com&t=tok-a"
        unsub_b = f"{WORKER}/unsubscribe?e=b%40x.com&t=tok-b"
        assert unsub_a in text and unsub_a.replace("&", "&amp;") in html
        assert a["List-Unsubscribe"] == f"<{unsub_a}>"
        assert a["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        assert b["List-Unsubscribe"] == f"<{unsub_b}>"
        assert "tok-b" not in text and "tok-b" not in html
        assert bodies(b)["plain"].replace(unsub_b, "") == text.replace(unsub_a, "")
        assert N.UNSUB_PLACEHOLDER not in text and N.UNSUB_PLACEHOLDER not in html
        # Progress in state.db (a git branch) holds hashes, never addresses.
        assert set(sent_today(db)) == fps("a@x.com", "b@x.com")
        raw_meta = db.meta_get(N.meta_key(LOCAL_DAY))
        assert "a@x.com" not in raw_meta and "b@x.com" not in raw_meta

    def test_same_day_rerun_is_idempotent(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        worker, factory = FakeWorker([("a@x.com", "t1"), ("b@x.com", "t2")]), Factory()
        assert send(db, cfg, AFTER_8_LOCAL, worker, factory)["sent"] == 2
        again = send(db, cfg, AFTER_8_LOCAL, worker, factory)
        assert again == {"sent": 0, "skipped": "everyone already sent today",
                         "day": LOCAL_DAY}
        assert len(factory.servers) == 1  # no second SMTP connection at all
        # Later the same local day: still nothing.
        assert send(db, cfg, AFTER_8_LOCAL + timedelta(hours=9), worker, factory)["sent"] == 0
        # The next local day is a fresh send.
        tomorrow = AFTER_8_LOCAL + timedelta(days=1)
        stats = send(db, cfg, tomorrow, worker, factory)
        assert stats["sent"] == 2 and stats["day"] == "2026-10-10"
        assert len(factory.messages) == 4
        # Yesterday's progress list shrinks to nothing once today's starts.
        assert db.meta_get(N.meta_key(LOCAL_DAY)) == "[]"
        assert set(json.loads(db.meta_get(N.meta_key("2026-10-10")))) == fps("a@x.com", "b@x.com")

    def test_cap_carries_over_to_the_next_run(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        cfg.newsletter_max_per_run = 2
        subs = [(f"{c}@x.com", f"tok-{c}") for c in "abcde"]
        worker, factory = FakeWorker(subs), Factory()
        sent = [send(db, cfg, AFTER_8_LOCAL + timedelta(minutes=10 * i), worker,
                     factory)["sent"] for i in range(4)]
        assert sent == [2, 2, 1, 0]
        recipients = [m["To"] for m in factory.messages]
        assert sorted(recipients) == sorted(e for e, _ in subs)
        assert len(set(recipients)) == 5  # everyone exactly once
        assert set(sent_today(db)) == fps(*(e for e, _ in subs))

    def test_failed_recipient_is_retried_next_run(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        worker = FakeWorker([("a@x.com", "t1"), ("bad@x.com", "t2"), ("c@x.com", "t3")])
        factory = Factory(fail_for={"bad@x.com"})
        stats = send(db, cfg, AFTER_8_LOCAL, worker, factory)
        assert stats["sent"] == 2 and stats["skipped"] == ""
        assert set(sent_today(db)) == fps("a@x.com", "c@x.com")
        # The mailbox is back: only the failed address is sent again.
        ok = Factory()
        assert send(db, cfg, AFTER_8_LOCAL + timedelta(minutes=10), worker, ok)["sent"] == 1
        assert [m["To"] for m in ok.messages] == ["bad@x.com"]
        assert set(sent_today(db)) == fps("a@x.com", "bad@x.com", "c@x.com")

    def test_connection_drop_stops_the_batch_and_resumes(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        worker = FakeWorker([("a@x.com", "t1"), ("b@x.com", "t2"), ("c@x.com", "t3")])
        factory = Factory(drop_after=1)
        assert send(db, cfg, AFTER_8_LOCAL, worker, factory)["sent"] == 1
        assert set(sent_today(db)) == fps("a@x.com")
        ok = Factory()
        assert send(db, cfg, AFTER_8_LOCAL + timedelta(minutes=10), worker, ok)["sent"] == 2
        assert [m["To"] for m in ok.messages] == ["b@x.com", "c@x.com"]

    def test_new_subscriber_mid_day_gets_todays_email(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        worker, factory = FakeWorker([("a@x.com", "t1")]), Factory()
        assert send(db, cfg, AFTER_8_LOCAL, worker, factory)["sent"] == 1
        worker.subs.append(("new@x.com", "t9"))
        stats = send(db, cfg, AFTER_8_LOCAL + timedelta(hours=3), worker, factory)
        assert stats["sent"] == 1
        assert [m["To"] for m in factory.messages] == ["a@x.com", "new@x.com"]
        # Someone who unsubscribed in between simply drops out of the list.
        worker.subs = [("new@x.com", "t9")]
        assert send(db, cfg, AFTER_8_LOCAL + timedelta(hours=4), worker, factory)["sent"] == 0

    def test_body_is_facts_only_and_capped(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        deals = [make_deal(dedupe_key=f"store|item{i}", item=f"Item {i:02d}",
                           pct_at_checkout=70.0 + i % 20) for i in range(30)]
        worker, factory = FakeWorker([("a@x.com", "t1")]), Factory()
        assert send(db, cfg, AFTER_8_LOCAL, worker, factory, deals=deals)["sent"] == 1
        (msg,) = factory.messages
        assert msg["Subject"] == "wowpriced: 25 verified deals, up to 89% off"
        text, html = bodies(msg)["plain"], bodies(msg)["html"]
        assert "Item 24" in text and "Item 25" not in text  # top 25 by rank order
        assert "Item 24" in html and "Item 25" not in html
        for body in (text, html):
            assert "VERBATIM QUOTE" not in body
            assert "evidence" not in body

    def test_port_465_uses_implicit_tls_without_starttls(self, monkeypatch):
        """The shared transport helper: 465 -> SMTP_SSL, 587 -> STARTTLS."""
        monkeypatch.setenv("SMTP_USER", "u@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "pw")
        made: list[tuple[str, FakeSMTP]] = []

        def fake_ssl(host, port, timeout=None):
            s = FakeSMTP(host, port)
            made.append(("ssl", s))
            return s

        def fake_plain(host, port, timeout=None):
            s = FakeSMTP(host, port)
            made.append(("plain", s))
            return s

        monkeypatch.setattr(email_digest.smtplib, "SMTP_SSL", fake_ssl)
        monkeypatch.setattr(email_digest.smtplib, "SMTP", fake_plain)
        s465 = email_digest.smtp_connect("smtp.example.com", 465)
        assert made[-1][0] == "ssl" and s465.starttls_called is False
        assert s465.login_args == ("u@example.com", "pw")
        s587 = email_digest.smtp_connect("smtp.example.com", 587)
        assert made[-1][0] == "plain" and s587.starttls_called is True
        s25 = email_digest.smtp_connect("smtp.example.com", 2525)
        assert made[-1][0] == "plain" and s25.starttls_called is False

        # A failed login closes the socket and surfaces the error.
        class Refuses(FakeSMTP):
            def login(self, user, password):
                raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

        refusing = Refuses("smtp.example.com", 587)
        with pytest.raises(smtplib.SMTPAuthenticationError):
            email_digest.smtp_connect("smtp.example.com", 587,
                                      smtp_factory=lambda h, p: refusing)
        assert refusing.quit_called


# ---------------------------------------------------------------- list + config


class TestListAndConfig:
    def test_fetch_subscribers_normalises_and_dedupes(self):
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.headers["Authorization"] == "Bearer k"
            return httpx.Response(200, json={"emails": [
                {"email": " B@X.com ", "token": "t2"},
                {"email": "a@x.com", "token": "t1"},
                {"email": "b@x.com", "token": "dupe"},   # first token wins
                {"email": "", "token": "t3"},            # junk rows ignored
                {"email": "c@x.com"},
            ]})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        assert N.fetch_subscribers(WORKER + "/", "k", client) == [
            {"email": "a@x.com", "token": "t1"},
            {"email": "b@x.com", "token": "t2"},
        ]
        bad = httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(401, json={"error": "unauthorized"})))
        assert N.fetch_subscribers(WORKER, "wrong", bad) is None
        odd = httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, text="not json")))
        assert N.fetch_subscribers(WORKER, "k", odd) is None

    def test_repo_config_has_the_newsletter_block(self):
        cfg = load_config()
        assert cfg.newsletter_enabled is True
        assert cfg.newsletter_worker_url == ""   # pasted in after the deploy
        assert cfg.newsletter_hour_local == 8
        assert cfg.newsletter_timezone == "America/New_York"
        assert cfg.newsletter_max_per_run == 200
        assert "NEWSLETTER_SECRET" in SECRET_NAMES

    def test_worker_url_is_trimmed(self, tmp_path):
        p = tmp_path / "config.yaml"
        p.write_text(yaml.safe_dump({"newsletter": {
            "worker_url": "  https://w.example.workers.dev/  ",
            "max_per_run": 0, "enabled": False}}))
        cfg = load_config(p)
        assert cfg.newsletter_worker_url == "https://w.example.workers.dev"
        assert cfg.newsletter_max_per_run == 1   # never zero: the list must move
        assert cfg.newsletter_enabled is False

    def test_template_and_defaults_exist(self):
        template = Path(N.__file__).resolve().parent / "templates" / "subscribe.html"
        assert template.is_file()
        assert N.MAX_DEALS == 25

    def test_fingerprint_is_stable_normalised_and_opaque(self):
        fp = N.fingerprint("A@X.com ")
        assert fp == N.fingerprint("a@x.com") and len(fp) == 16
        assert "x.com" not in fp and fp != N.fingerprint("b@x.com")
