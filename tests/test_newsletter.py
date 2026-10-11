"""Tests for dealradar.publish.newsletter — offline: the subscribe Worker is
an httpx MockTransport, SMTP a recording fake, the DB real."""

from __future__ import annotations

import json
import smtplib
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import httpx2
import pytest
import yaml

from dealradar.config import SECRET_NAMES, AppConfig, load_config
from dealradar.db import DB
from dealradar.parse.claude_client import ClaudeParser
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
    cfg.paywall_api_url = "https://wowpriced-api.example.workers.dev"  # membership offered
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


def send(db, cfg, now, worker, factory, deals=None, deadline=None):
    return N.send_daily_newsletter(
        db, cfg, now, deals=[make_deal()] if deals is None else deals,
        smtp_factory=factory, http_client=worker.client(), deadline=deadline)


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
            assert m["Subject"] == "wowpriced: Today's top 2 deals"
            assert "," not in m["To"]

        a, b = server.messages
        text, html = bodies(a)["plain"], bodies(a)["html"]
        # Facts: item, price, was-price, % off, store, link, credit, disclosure.
        assert "LEGO Star Wars Set" in text and "$10.00" in text
        assert "was $50.00" in text and "80% off" in text and "at Walmart" in text
        assert "Get deal: https://www.walmart.com/ip/12345" in text
        assert "via Slickdeals: https://slickdeals.net/f/1" in text
        assert "Live wall: https://wowpriced.com/" in text
        # The free list's email ends with the membership pitch.
        assert ("Members get every deal. Start the 7-day free trial: "
                "https://wowpriced.com/#signup") in text
        assert 'href="https://wowpriced.com/#signup"' in html
        assert "Members get every deal." in html and "Start the 7-day free trial" in html
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

    def test_run_deadline_stops_the_batch_for_the_next_run(self, db, cfg, monkeypatch):
        """A job killed by its timeout would lose the state.db commit and
        re-send; the loop stops at the run deadline instead (review finding)."""
        import time as _time
        configure(monkeypatch)
        subs = [(f"{c}@x.com", f"tok-{c}") for c in "abc"]
        worker, factory = FakeWorker(subs), Factory()
        past = _time.monotonic() - 1  # already crossed before the first send
        r = send(db, cfg, AFTER_8_LOCAL, worker, factory, deadline=past)
        assert r["sent"] == 0 and factory.messages == []
        future = _time.monotonic() + 60
        r = send(db, cfg, AFTER_8_LOCAL + timedelta(minutes=10), worker, factory,
                 deadline=future)
        assert r["sent"] == 3

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
        assert msg["Subject"] == "wowpriced: Today's top 10 deals"
        text, html = bodies(msg)["plain"], bodies(msg)["html"]
        # No model this run: the top 10 in rank order.
        assert "Item 09" in text and "Item 10" not in text
        assert "Item 09" in html and "Item 10" not in html
        assert text.count("Get deal: ") == 10 and html.count(">Get deal</a>") == 10
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


# ---------------------------------------------------------------- the day's top

HAIKU = "claude-haiku-5-5"
_REQ = httpx2.Request("POST", "https://api.example.test/v1/messages")


class Answer:
    """A messages.create response: an (empty) thinking block, then text."""

    def __init__(self, text: str, stop_reason: str = "end_turn",
                 input_tokens: int = 2000, output_tokens: int = 400):
        self.content = [SimpleNamespace(type="thinking", thinking=""),
                        SimpleNamespace(type="text", text=text)]
        self.usage = SimpleNamespace(input_tokens=input_tokens,
                                     output_tokens=output_tokens)
        self.stop_reason = stop_reason


def ids_answer(*ids) -> Answer:
    return Answer(json.dumps({"ids": list(ids)}))


#: (2000 in x $0.10 + 400 out x $0.50) / 1e6 at claude-haiku-5-5's price.
PICK_COST = 0.0004


class Hang:
    """A hung API: blocks for the request's whole timeout, then times out."""


class FakePickMessages:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        a = self.answers.pop(0)
        if isinstance(a, Hang):
            time.sleep(kwargs["timeout"])
            raise anthropic.APITimeoutError(request=_REQ)
        if isinstance(a, Exception):
            raise a
        return a


class FakeClient:
    """messages, plus the SDK's with_options, which records what was asked."""

    def __init__(self, messages):
        self.messages = messages
        self.options: list[dict] = []

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return self


def make_parser(cfg, *answers, run_cap_usd=None):
    """The real ClaudeParser (spend gate, accounting) over a fake client."""
    cfg.parse_model = HAIKU
    cfg.model_pricing_per_mtok = {"default": {"input": 1.0, "output": 5.0},
                                  HAIKU: {"input": 0.10, "output": 0.50}}
    messages = FakePickMessages(answers)
    parser = ClaudeParser(cfg, client=FakeClient(messages),
                          sleep=lambda s: None, run_cap_usd=run_cap_usd)
    return parser, messages


def ranked(n: int) -> list[dict]:
    """``n`` published deals in rank order: id k in the prompt is Item k."""
    return [make_deal(dedupe_key=f"store|item{i:02d}", item=f"Item {i:02d}",
                      pct_at_checkout=90.0 - i * 0.5) for i in range(1, n + 1)]


def items(deals) -> list[str]:
    return [d["item"] for d in deals]


RANK_TOP10 = [f"Item {i:02d}" for i in range(1, 11)]


class TestPick:
    def test_model_chooses_the_ten_in_its_order_and_spend_is_recorded(self, cfg):
        parser, messages = make_parser(cfg, ids_answer(5, 3, 1, 30, 12, 7, 8, 9, 10, 11))
        picked, how = N.pick_top(ranked(45), cfg, parser)
        assert how == "model"
        assert items(picked) == ["Item 05", "Item 03", "Item 01", "Item 30", "Item 12",
                                 "Item 07", "Item 08", "Item 09", "Item 10", "Item 11"]
        (call,) = messages.calls
        assert call["model"] == HAIKU and call["timeout"] == N.PICK_TIMEOUT_S
        assert parser.client.options == [{"max_retries": 0}]  # no SDK retries
        assert call["max_tokens"] == N.PICK_MAX_TOKENS
        assert '{"ids"' in call["system"] and "10" in call["system"]
        prompt = call["messages"][0]["content"]
        # The best 40 by rank are the candidates, as numbered facts only.
        assert "\n40. Item 40 | Walmart | $10.00, was $50.00, 70% off | corroborated" in prompt
        assert "Item 41" not in prompt
        assert "VERBATIM QUOTE" not in prompt and "slickdeals" not in prompt.lower()
        # Priced by the model actually used, on the run's shared parser.
        assert parser.spend_usd == pytest.approx(PICK_COST)

    def test_unknown_ids_and_repeats_dropped_then_padded_from_rank(self, cfg):
        parser, _ = make_parser(cfg, Answer(
            '```json\n{"ids": [0, 99, "3", 3, 3, true, 2.5, -1, 41, 5]}\n```'))
        picked, how = N.pick_top(ranked(45), cfg, parser)
        assert how == "model"
        assert items(picked) == ["Item 03", "Item 05", "Item 01", "Item 02", "Item 04",
                                 "Item 06", "Item 07", "Item 08", "Item 09", "Item 10"]
        # Too many ids: the first ten count.
        parser, _ = make_parser(cfg, ids_answer(*range(20, 0, -1)))
        picked, _how = N.pick_top(ranked(45), cfg, parser)
        assert items(picked) == [f"Item {i:02d}" for i in range(20, 10, -1)]

    @pytest.mark.parametrize("answers", [
        [Answer("Here are my picks: 1, 2, 3")],                 # not JSON
        [Answer('{"ids": "1,2,3"}')],                            # not a list
        [Answer('{"picks": [1, 2]}')],                           # wrong shape
        [ids_answer(0, 41, 99)],                                 # no valid id
        [Answer('{"ids": [1, 2]}', stop_reason="refusal")],
        [Answer('{"ids": [1, 2', stop_reason="max_tokens")],
        [anthropic.APITimeoutError(request=_REQ)],               # one attempt, no retry
        [anthropic.APIConnectionError(request=_REQ)],
        [anthropic.InternalServerError(
            "down", response=httpx2.Response(500, request=_REQ), body=None)],
        [anthropic.BadRequestError(
            "bad", response=httpx2.Response(400, request=_REQ), body=None)],
    ])
    def test_any_failure_falls_back_to_rank_order(self, cfg, answers):
        parser, messages = make_parser(cfg, *answers)
        picked, how = N.pick_top(ranked(45), cfg, parser)
        assert how == "rank" and items(picked) == RANK_TOP10
        assert messages.answers == []  # every queued answer was consumed

    def test_a_hung_api_costs_one_attempt_inside_the_deadline(self, cfg):
        # The SDK's own retries are off and the parser's retry is not used:
        # one request, its timeout capped at the time left before the deadline.
        parser, messages = make_parser(cfg, Hang(), Hang(), Hang())
        started = time.monotonic()
        picked, how = N.pick_top(ranked(45), cfg, parser, deadline=started + 0.2)
        assert how == "rank" and items(picked) == RANK_TOP10
        assert time.monotonic() - started < 1.0  # not 3 SDK x 2 parser attempts
        (call,) = messages.calls
        assert 0 < call["timeout"] <= 0.2
        assert parser.client.options == [{"max_retries": 0}]
        assert parser.spend_usd == 0.0

    def test_no_parser_or_nothing_to_choose_means_no_call(self, cfg):
        picked, how = N.pick_top(ranked(45), cfg, None)  # no key / daily cap spent
        assert how == "rank" and items(picked) == RANK_TOP10
        parser, messages = make_parser(cfg, ids_answer(1))
        picked, how = N.pick_top(ranked(10), cfg, parser)
        assert how == "rank" and items(picked) == RANK_TOP10
        assert messages.calls == [] and parser.spend_usd == 0.0

    def test_exhausted_cap_means_no_call(self, cfg):
        parser, messages = make_parser(cfg, ids_answer(5, 4, 3), run_cap_usd=0.0)
        picked, how = N.pick_top(ranked(45), cfg, parser)
        assert how == "rank" and items(picked) == RANK_TOP10
        assert messages.calls == [] and parser.spend_usd == 0.0

    def test_spend_counts_toward_the_same_cap(self, cfg):
        # The pick's cost lands on the shared parser, so the next call that
        # would pass the run's remaining cap is never made.
        parser, messages = make_parser(cfg, ids_answer(5), ids_answer(6),
                                       run_cap_usd=PICK_COST / 2)
        assert N.pick_top(ranked(45), cfg, parser)[1] == "model"
        assert parser.spend_usd == pytest.approx(PICK_COST)
        picked, how = N.pick_top(ranked(45), cfg, parser)
        assert how == "rank" and items(picked) == RANK_TOP10
        assert len(messages.calls) == 1


class TestDailyTop:
    def test_pick_is_stored_and_reused_by_retries_and_reruns(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        worker = FakeWorker([("a@x.com", "t1")])
        parser, messages = make_parser(cfg, ids_answer(7, 2, 9, 4, 1, 3, 5, 6, 8, 10),
                                       ids_answer(1, 2))

        def boom(host, port):
            raise OSError("smtp down")

        # First run: picked and stored before the send, which fails.
        stats = N.send_daily_newsletter(db, cfg, AFTER_8_LOCAL, deals=ranked(45),
                                        smtp_factory=boom, http_client=worker.client(),
                                        parser=parser)
        assert stats["skipped"].startswith("smtp connect failed")
        assert len(messages.calls) == 1
        stored = json.loads(db.meta_get(N.pick_key(LOCAL_DAY)))
        assert stored["by"] == "model" and len(stored["keys"]) == 10
        assert stored["keys"][:2] == ["store|item07", "store|item02"]

        # The retry sends that same pick without asking the model again,
        # current facts from today's deals, in the model's order.
        factory = Factory()
        stats = N.send_daily_newsletter(db, cfg, AFTER_8_LOCAL + timedelta(minutes=10),
                                        deals=ranked(45), smtp_factory=factory,
                                        http_client=worker.client(), parser=parser)
        assert stats["sent"] == 1 and len(messages.calls) == 1
        text = bodies(factory.messages[0])["plain"]
        assert factory.messages[0]["Subject"] == "wowpriced: Today's top 10 deals"
        assert text.index("1. Item 07") < text.index("2. Item 02") < text.index("10. Item 10")
        assert "Item 11" not in text

        # A rerun of the same day: nothing sent, no model call.
        again = N.send_daily_newsletter(db, cfg, AFTER_8_LOCAL + timedelta(hours=2),
                                        deals=ranked(45), smtp_factory=factory,
                                        http_client=worker.client(), parser=parser)
        assert again["skipped"] == "everyone already sent today"
        assert len(messages.calls) == 1 and len(factory.messages) == 1

        # The next local day is a fresh pick; yesterday's shrinks to nothing.
        tomorrow = AFTER_8_LOCAL + timedelta(days=1)
        stats = N.send_daily_newsletter(db, cfg, tomorrow, deals=ranked(45),
                                        smtp_factory=factory,
                                        http_client=worker.client(), parser=parser)
        assert stats["sent"] == 1 and len(messages.calls) == 2
        assert db.meta_get(N.pick_key(LOCAL_DAY)) == ""
        assert json.loads(db.meta_get(N.pick_key("2026-10-10")))["keys"][:2] == [
            "store|item01", "store|item02"]

    def test_capped_batch_sends_everyone_the_same_ten(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        cfg.newsletter_max_per_run = 1
        worker, factory = FakeWorker([("a@x.com", "ta"), ("b@x.com", "tb")]), Factory()
        parser, messages = make_parser(cfg, ids_answer(30, 29, 28))
        for i in range(2):
            stats = N.send_daily_newsletter(db, cfg, AFTER_8_LOCAL + timedelta(minutes=10 * i),
                                            deals=ranked(45), smtp_factory=factory,
                                            http_client=worker.client(), parser=parser)
            assert stats["sent"] == 1
        assert len(messages.calls) == 1
        a, b = factory.messages
        unsub_a = f"{WORKER}/unsubscribe?e=a%40x.com&t=ta"
        unsub_b = f"{WORKER}/unsubscribe?e=b%40x.com&t=tb"
        assert bodies(a)["plain"].replace(unsub_a, "") == bodies(b)["plain"].replace(unsub_b, "")
        assert "1. Item 30" in bodies(b)["plain"]

    def test_a_picked_deal_that_died_drops_out(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        worker = FakeWorker([("a@x.com", "t1"), ("b@x.com", "t2")])
        cfg.newsletter_max_per_run = 1
        parser, messages = make_parser(cfg, ids_answer(3, 2, 1))
        send(db, cfg, AFTER_8_LOCAL, worker, Factory(), deals=ranked(45))  # no parser
        later = Factory()
        alive = [d for d in ranked(45) if d["item"] != "Item 02"]
        N.send_daily_newsletter(db, cfg, AFTER_8_LOCAL + timedelta(minutes=10),
                                deals=alive, smtp_factory=later,
                                http_client=worker.client(), parser=parser)
        (msg,) = later.messages
        assert msg["Subject"] == "wowpriced: Today's top 9 deals"
        text = bodies(msg)["plain"]
        assert "Item 02" not in text and "9. Item 10" in text and "Item 11" not in text
        assert messages.calls == []  # the day's pick was rank order, kept all day

    def test_past_the_run_deadline_nothing_is_picked(self, db, cfg, monkeypatch):
        import time as _time
        configure(monkeypatch)
        parser, messages = make_parser(cfg, ids_answer(1))
        stats = N.send_daily_newsletter(db, cfg, AFTER_8_LOCAL, deals=ranked(45),
                                        smtp_factory=Factory(),
                                        http_client=FakeWorker([("a@x.com", "t1")]).client(),
                                        deadline=_time.monotonic() - 1, parser=parser)
        assert stats == {"sent": 0, "skipped": "run deadline reached", "day": LOCAL_DAY}
        assert messages.calls == [] and db.meta_get(N.pick_key(LOCAL_DAY)) is None

    def test_less_than_the_pick_timeout_left_waits_for_the_next_run(self, db, cfg, monkeypatch):
        # A model slower than the time left could run the job past its
        # budget (and its timeout kills the state.db commit): nothing is
        # asked, sent or stored; the next run, with time, picks and sends.
        configure(monkeypatch)
        worker, factory = FakeWorker([("a@x.com", "t1")]), Factory()
        parser, messages = make_parser(cfg, Hang(), ids_answer(3, 2, 1))
        stats = N.send_daily_newsletter(db, cfg, AFTER_8_LOCAL, deals=ranked(45),
                                        smtp_factory=factory, http_client=worker.client(),
                                        deadline=time.monotonic() + N.PICK_TIMEOUT_S - 1,
                                        parser=parser)
        assert stats == {"sent": 0, "day": LOCAL_DAY,
                         "skipped": "run deadline near: today's pick rides the next run"}
        assert messages.calls == [] and factory.messages == []
        assert db.meta_get(N.pick_key(LOCAL_DAY)) is None
        messages.answers.pop(0)  # the Hang was never reached
        stats = N.send_daily_newsletter(db, cfg, AFTER_8_LOCAL + timedelta(minutes=10),
                                        deals=ranked(45), smtp_factory=factory,
                                        http_client=worker.client(),
                                        deadline=time.monotonic() + 600, parser=parser)
        assert stats["sent"] == 1 and len(messages.calls) == 1
        assert "1. Item 03" in bodies(factory.messages[0])["plain"]
        # Once stored, the pick needs no model time: a near deadline still sends.
        worker.subs.append(("b@x.com", "t2"))
        stats = N.send_daily_newsletter(db, cfg, AFTER_8_LOCAL + timedelta(minutes=20),
                                        deals=ranked(45), smtp_factory=factory,
                                        http_client=worker.client(),
                                        deadline=time.monotonic() + 1, parser=parser)
        assert stats["sent"] == 1 and len(messages.calls) == 1


class TestTopEmail:
    def test_ten_lines_cta_and_unsubscribe(self, cfg):
        subject, text, html = N.build_newsletter(ranked(12), cfg, AFTER_8_LOCAL)
        assert subject == "wowpriced: Today's top 10 deals"
        numbered = [ln for ln in text.splitlines() if ln[:1].isdigit()]
        assert len(numbered) == 10 and numbered[-1].startswith("10. Item 10 — $10.00")
        assert "Item 11" not in text and "Item 11" not in html
        assert html.count(">Get deal</a>") == 10
        cta = "Members get every deal. Start the 7-day free trial: https://wowpriced.com/#signup"
        assert cta in text
        assert 'href="https://wowpriced.com/#signup"' in html
        # Unsubscribe mechanics unchanged: the placeholder in both bodies.
        assert f"Unsubscribe any time: {N.UNSUB_PLACEHOLDER}" in text
        assert f'<a href="{N.UNSUB_PLACEHOLDER}"' in html
        # The CTA follows the trial knob, and needs a site to link to.
        cfg.paywall_trial_days = 0
        _s, text, html = N.build_newsletter(ranked(3), cfg, AFTER_8_LOCAL)
        assert "Members get every deal. Become a member: https://wowpriced.com/#signup" in text
        assert "free trial" not in text and "free trial" not in html
        cfg.custom_domain = ""
        _s, text, html = N.build_newsletter(ranked(3), cfg, AFTER_8_LOCAL)
        assert "#signup" not in text and "#signup" not in html

    @pytest.mark.parametrize("api_url, enabled", [("", True), ("https://w.example", False)])
    def test_no_membership_cta_without_the_paywall(self, cfg, api_url, enabled):
        # No paywall Worker: the site has no sign-up for /#signup to open.
        # Paywall off: every deal is already free on the wall.
        cfg.paywall_api_url, cfg.paywall_enabled = api_url, enabled
        _s, text, html = N.build_newsletter(ranked(3), cfg, AFTER_8_LOCAL)
        assert "Members get every deal" not in text and "Members get every deal" not in html
        assert "#signup" not in text and "#signup" not in html
        assert "Live wall: https://wowpriced.com/" in text  # the rest is unchanged


class TestSiteCopy:
    TEMPLATES = Path(N.__file__).resolve().parent / "templates"

    def test_no_daily_email_is_offered_anywhere(self):
        """Operator call (2026-10-11): no daily emails for anyone; the
        membership is website access. No page offers or promises one."""
        for page in self.TEMPLATES.glob("*.html"):
            text = page.read_text(encoding="utf-8").lower()
            assert "free daily email" not in text, page.name
            assert "one email every morning" not in text, page.name
            assert "id=\"nlform\"" not in text, page.name
            assert "top 10" not in text, page.name
        index = (self.TEMPLATES / "index.html").read_text(encoding="utf-8")
        assert "every deal on the wall, new deals every\n     {{DROP_MINUTES}} minutes." in index


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
        assert cfg.newsletter_enabled is False  # operator: no daily emails
        assert cfg.members_email_enabled is False
        # The Worker URL is an operator value (pasted in after the deploy);
        # only its shape is asserted here.
        assert cfg.newsletter_worker_url == "" or cfg.newsletter_worker_url.startswith("https://")
        assert not cfg.newsletter_worker_url.endswith("/")
        assert cfg.newsletter_hour_local == 8
        assert cfg.newsletter_timezone == "America/New_York"
        assert cfg.newsletter_max_per_run == 200
        assert cfg.newsletter_top_n == 10  # the page copy says "top 10"
        assert "NEWSLETTER_SECRET" in SECRET_NAMES

    def test_worker_url_is_trimmed(self, tmp_path):
        p = tmp_path / "config.yaml"
        p.write_text(yaml.safe_dump({"newsletter": {
            "worker_url": "  https://w.example.workers.dev/  ",
            "max_per_run": 0, "top_n": 0, "enabled": False}}))
        cfg = load_config(p)
        assert cfg.newsletter_worker_url == "https://w.example.workers.dev"
        assert cfg.newsletter_max_per_run == 1   # never zero: the list must move
        assert cfg.newsletter_top_n == 1         # never an empty email
        assert cfg.newsletter_enabled is False

    def test_template_and_defaults_exist(self):
        template = Path(N.__file__).resolve().parent / "templates" / "subscribe.html"
        assert template.is_file()
        assert AppConfig().newsletter_top_n == 10 and N.PICK_CANDIDATES == 40

    def test_fingerprint_is_stable_normalised_and_opaque(self):
        fp = N.fingerprint("A@X.com ")
        assert fp == N.fingerprint("a@x.com") and len(fp) == 16
        assert "x.com" not in fp and fp != N.fingerprint("b@x.com")


# ---------------------------------------------------------------- double opt-in


class FakeOptInWorker:
    """GET /list?pending=1 and POST /list/confirm-sent as an httpx
    MockTransport. A successful confirm-sent drops those addresses from the
    pending list, as the real Worker's confirm_sent_at does."""

    def __init__(self, pending: list[tuple[str, str]], list_status: int = 200,
                 mark_status: int = 204):
        self.pending = list(pending)
        self.list_status = list_status
        self.mark_status = mark_status
        self.list_calls = 0
        self.marked: list[list[str]] = []  # the body of every confirm-sent POST

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def __call__(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == f"Bearer {SECRET}"
        if req.method == "GET":
            assert str(req.url) == WORKER + "/list?pending=1"
            self.list_calls += 1
            if self.list_status != 200:
                return httpx.Response(self.list_status, json={"error": "nope"})
            return httpx.Response(200, json={
                "emails": [{"email": e, "token": t} for e, t in self.pending]})
        assert req.method == "POST" and str(req.url) == WORKER + "/list/confirm-sent"
        assert req.headers["Content-Type"].startswith("application/json")
        emails = json.loads(req.content)["emails"]
        self.marked.append(emails)
        if self.mark_status != 204:
            return httpx.Response(self.mark_status, json={"error": "nope"})
        self.pending = [(e, t) for e, t in self.pending if e not in emails]
        return httpx.Response(204)


def confirm(db, cfg, now, worker, factory, deadline=None):
    return N.send_confirmations(db, cfg, now, smtp_factory=factory,
                                http_client=worker.client(), deadline=deadline)


def guarded(db) -> set[str]:
    """Fingerprints the 24 h guard holds (hashes, never addresses)."""
    return set(json.loads(db.meta_get(N.CONFIRM_META_KEY) or "{}"))


class TestConfirmations:
    def test_each_pending_address_gets_one_link_and_is_marked(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        worker = FakeOptInWorker([("a@x.com", "tok-a"), ("b@x.com", "tok-b")])
        factory = Factory()
        # Any hour, any lane: a sign-up never waits for the 08:00 send.
        stats = confirm(db, cfg, BEFORE_8_LOCAL, worker, factory)
        assert stats == {"sent": 2, "skipped": ""}

        (server,) = factory.servers  # one connection for the whole batch
        assert server.host == "smtp.example.com" and server.port == 587
        assert server.starttls_called and server.login_args == ("mailer@example.com", "hunter2")
        assert server.quit_called
        assert [m["To"] for m in server.messages] == ["a@x.com", "b@x.com"]
        a, b = server.messages
        for m in (a, b):
            assert m["Subject"] == "Confirm your wowpriced email"
            assert m["From"] == "deals@wowpriced.com" and "," not in m["To"]
            assert m["List-Unsubscribe"] is None  # transactional, no list headers
        text, html = bodies(a)["plain"], bodies(a)["html"]
        link_a = f"{WORKER}/confirm?e=a%40x.com&t=tok-a"
        assert link_a in text and link_a.replace("&", "&amp;") in html
        assert "If you didn't sign up, ignore this and you won't hear from us" in text
        assert "ignore this and you won" in html
        assert "tok-b" not in text and "tok-b" not in html
        assert f"{WORKER}/confirm?e=b%40x.com&t=tok-b" in bodies(b)["plain"]
        # No deals, prices or unsubscribe links in a confirmation.
        for body in (text, html):
            assert "Get deal" not in body and "$" not in body
            assert "% off" not in body and "unsubscribe" not in body.lower()
        # The Worker is told exactly who got a link; it stops listing them.
        assert worker.marked == [["a@x.com", "b@x.com"]] and worker.pending == []
        # The 24 h guard in state.db (a git branch) holds hashes, not addresses.
        assert guarded(db) == fps("a@x.com", "b@x.com")
        raw = db.meta_get(N.CONFIRM_META_KEY)
        assert "a@x.com" not in raw and "b@x.com" not in raw
        # The daily send's own progress is untouched.
        assert db.meta_get(N.meta_key(LOCAL_DAY)) is None

        again = confirm(db, cfg, BEFORE_8_LOCAL + timedelta(minutes=10), worker, factory)
        assert again == {"sent": 0, "skipped": "no pending sign-ups"}
        assert len(factory.servers) == 1 and worker.marked == [["a@x.com", "b@x.com"]]

    def test_cap_of_fifty_per_run_continues_next_run(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        subs = [(f"u{i:02d}@x.com", f"tok-{i}") for i in range(55)]
        worker, factory = FakeOptInWorker(subs), Factory()
        assert N.MAX_CONFIRMATIONS_PER_RUN == 50
        sent = [confirm(db, cfg, AFTER_8_LOCAL + timedelta(minutes=10 * i), worker,
                        factory)["sent"] for i in range(3)]
        assert sent == [50, 5, 0]
        assert [len(m) for m in worker.marked] == [50, 5]
        recipients = [m["To"] for m in factory.messages]
        assert sorted(recipients) == sorted(e for e, _ in subs)
        assert len(set(recipients)) == 55  # everyone exactly once

    def test_run_deadline_stops_the_batch_for_the_next_run(self, db, cfg, monkeypatch):
        import time as _time
        configure(monkeypatch)
        subs = [(f"{c}@x.com", f"tok-{c}") for c in "abc"]
        worker, factory = FakeOptInWorker(subs), Factory()
        past = _time.monotonic() - 1  # already crossed before the first send
        r = confirm(db, cfg, AFTER_8_LOCAL, worker, factory, deadline=past)
        assert r == {"sent": 0, "skipped": "nothing sent this run"}
        assert factory.messages == [] and worker.marked == [] and guarded(db) == set()
        future = _time.monotonic() + 60
        r = confirm(db, cfg, AFTER_8_LOCAL + timedelta(minutes=10), worker, factory,
                    deadline=future)
        assert r["sent"] == 3 and worker.marked == [["a@x.com", "b@x.com", "c@x.com"]]

    def test_failed_confirm_sent_post_never_means_a_second_email(self, db, cfg, monkeypatch):
        """The Worker keeps listing an address whose confirm-sent POST failed;
        the 24 h guard holds the email back and only the mark is retried."""
        configure(monkeypatch)
        worker = FakeOptInWorker([("a@x.com", "tok-a")], mark_status=500)
        factory = Factory()
        assert confirm(db, cfg, AFTER_8_LOCAL, worker, factory)["sent"] == 1
        assert worker.marked == [["a@x.com"]] and worker.pending == [("a@x.com", "tok-a")]
        later = AFTER_8_LOCAL + timedelta(hours=1)
        stats = confirm(db, cfg, later, worker, factory)
        assert stats == {"sent": 0, "skipped": "every pending sign-up emailed in the last 24 h"}
        assert len(factory.messages) == 1  # no second email
        assert worker.marked == [["a@x.com"], ["a@x.com"]]  # the Worker is told again
        assert len(factory.servers) == 1  # nothing to send: no SMTP connection
        # The Worker recovers: the retried mark lands and the address drops out.
        worker.mark_status = 204
        assert confirm(db, cfg, later + timedelta(hours=1), worker, factory)["sent"] == 0
        assert worker.pending == [] and len(worker.marked) == 3
        assert confirm(db, cfg, later + timedelta(hours=2), worker,
                       factory)["skipped"] == "no pending sign-ups"
        assert len(factory.messages) == 1

    def test_guard_expires_after_24h(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        worker = FakeOptInWorker([("a@x.com", "tok-a")], mark_status=500)
        factory = Factory()
        assert confirm(db, cfg, AFTER_8_LOCAL, worker, factory)["sent"] == 1
        assert confirm(db, cfg, AFTER_8_LOCAL + timedelta(hours=23), worker, factory)["sent"] == 0
        # Still listed a day later (the Worker never took the mark): one more link.
        assert confirm(db, cfg, AFTER_8_LOCAL + timedelta(hours=25), worker, factory)["sent"] == 1
        assert len(factory.messages) == 2
        # A new sign-up alongside a guarded one: only the new one is emailed,
        # both are marked in the one POST.
        worker.pending.append(("b@x.com", "tok-b"))
        stats = confirm(db, cfg, AFTER_8_LOCAL + timedelta(hours=26), worker, factory)
        assert stats["sent"] == 1 and factory.messages[-1]["To"] == "b@x.com"
        assert worker.marked[-1] == ["b@x.com", "a@x.com"]

    def test_refused_recipient_and_connection_drop_retry_next_run(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        worker = FakeOptInWorker([("a@x.com", "t1"), ("bad@x.com", "t2"), ("c@x.com", "t3")])
        factory = Factory(fail_for={"bad@x.com"})
        stats = confirm(db, cfg, AFTER_8_LOCAL, worker, factory)
        assert stats == {"sent": 2, "skipped": ""}
        assert worker.marked == [["a@x.com", "c@x.com"]] and worker.pending == [("bad@x.com", "t2")]
        assert guarded(db) == fps("a@x.com", "c@x.com")  # the refusal is not guarded
        ok = Factory()
        assert confirm(db, cfg, AFTER_8_LOCAL + timedelta(minutes=10), worker, ok)["sent"] == 1
        assert [m["To"] for m in ok.messages] == ["bad@x.com"] and worker.pending == []

        dropping = FakeOptInWorker([("d@x.com", "t4"), ("e@x.com", "t5")])
        drop = Factory(drop_after=1)
        assert confirm(db, cfg, AFTER_8_LOCAL + timedelta(hours=1), dropping, drop)["sent"] == 1
        assert dropping.marked == [["d@x.com"]] and dropping.pending == [("e@x.com", "t5")]
        assert confirm(db, cfg, AFTER_8_LOCAL + timedelta(hours=2), dropping, Factory())["sent"] == 1

    @pytest.mark.parametrize("missing, reason", [
        ("worker_url", "newsletter.worker_url not configured"),
        ("NEWSLETTER_SECRET", "NEWSLETTER_SECRET not configured"),
        ("SMTP_HOST", "SMTP_HOST not configured"),
    ])
    def test_skips_quietly_when_unconfigured(self, db, cfg, monkeypatch, missing, reason):
        configure(monkeypatch)
        if missing == "worker_url":
            cfg.newsletter_worker_url = ""
        else:
            monkeypatch.delenv(missing)
        worker, factory = FakeOptInWorker([("a@x.com", "t1")]), Factory()
        stats = confirm(db, cfg, AFTER_8_LOCAL, worker, factory)
        assert stats == {"sent": 0, "skipped": reason}
        assert worker.list_calls == 0 and factory.servers == []
        assert db.meta_get(N.CONFIRM_META_KEY) is None

    def test_disabled_unreachable_list_and_smtp_failure_skip(self, db, cfg, monkeypatch):
        configure(monkeypatch)
        worker, factory = FakeOptInWorker([("a@x.com", "t1")]), Factory()
        cfg.newsletter_enabled = False
        assert confirm(db, cfg, AFTER_8_LOCAL, worker, factory)["skipped"] == "disabled in config"
        assert worker.list_calls == 0
        cfg.newsletter_enabled = True

        down = FakeOptInWorker([("z@x.com", "t9")], list_status=500)
        stats = confirm(db, cfg, AFTER_8_LOCAL, down, factory)
        assert stats == {"sent": 0, "skipped": "pending list unavailable"}
        assert factory.servers == [] and down.marked == []
        down.list_status = 200  # the Worker is back: the next run sends
        assert confirm(db, cfg, AFTER_8_LOCAL, down, factory)["sent"] == 1
        assert down.marked == [["z@x.com"]]

        def boom(host, port):
            raise OSError("smtp down")

        stats = N.send_confirmations(db, cfg, AFTER_8_LOCAL, smtp_factory=boom,
                                     http_client=worker.client())
        assert stats["skipped"].startswith("smtp connect failed") and stats["sent"] == 0
        assert worker.marked == [] and N.fingerprint("a@x.com") not in guarded(db)

    def test_fetch_subscribers_pending_flag_and_build_confirmation(self):
        urls: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            urls.append(str(req.url))
            return httpx.Response(200, json={"emails": [{"email": "A@x.com", "token": "t"}]})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        assert N.fetch_subscribers(WORKER, "k", client, pending=True) == [
            {"email": "a@x.com", "token": "t"}]
        assert N.fetch_subscribers(WORKER, "k", client) == [{"email": "a@x.com", "token": "t"}]
        assert urls == [WORKER + "/list?pending=1", WORKER + "/list"]

        cfg = AppConfig()
        cfg.custom_domain = "wowpriced.com"
        subject, text, html = N.build_confirmation(cfg, "https://w/confirm?e=a%40x.com&t=1")
        assert subject == "Confirm your wowpriced email"
        assert "https://w/confirm?e=a%40x.com&t=1" in text
        assert 'href="https://w/confirm?e=a%40x.com&amp;t=1"' in html
        assert text.count("https://") == 1 and html.count("href=") == 1  # one link
