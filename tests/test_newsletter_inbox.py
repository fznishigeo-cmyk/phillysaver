"""Private inbox ingestion, evidence, deferred acknowledgements and reruns offline."""
from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import yaml

from dealradar import pipeline
from dealradar.config import AppConfig, SECRET_NAMES, load_config, load_sources, redact_secrets
from dealradar.db import DB
from dealradar.errors import AdapterNotConfigured, DeadlineExceeded
from dealradar.fetch.base import run_source
from dealradar.fetch.newsletter import (UNKNOWN_SENDERS_META_KEY, NewsletterAdapter,
                                       email_posts, match_sender, partner_mail,
                                       registrable_domain, unknown_sender_counts)
from dealradar.fetch.registry import get_adapter
from dealradar.fetch.validate import _validate_one, save_effective_sources
from dealradar.models import Evidence, ExtractedDeal, SourceConfig
from dealradar.parse.extractor import verify_evidence
from tests.test_e2e_offline import _FakeClaudeClient, _FakeMessages

NOW = datetime(2026, 10, 13, 14, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parent.parent
PRIVATE = "PRIVATE_FULL_NEWSLETTER_UNRELATED_FOOTER"


def source(name="bensbargains", host="bensbargains.com", address="deals@bensbargains.com", **kwargs):
    return SourceConfig(name=name, method="rss", crawl_permission="written permission",
                        site=f"https://{host}/", url=f"https://{host}/feed/",
                        newsletter_from=[address], **kwargs)


def inbox_source(**kwargs):
    return SourceConfig(name="partner_newsletters", method="email", adapter="newsletter",
                        url="", **kwargs)


def message(number=1, **kwargs):
    return dict(id="msg:" + f"{number:064x}", **{
        "from": "deals@bensbargains.com", "subject": "Today's deals",
        "date": "2026-10-13T12:00:00Z", "message_id": "<newsletter-1>",
        "received_at": "2026-10-13T13:00:00Z", "text": "", "html": (
            "<p>Daily deals</p><table><tr><td><a href='https://bensbargains.com/bargain/widget-1/'>"
            "Widget</a><p>was $100 now $20</p><a href='https://store.example/widget'>Buy</a>"
            "</td></tr></table><p>" + PRIVATE + "</p>"), **kwargs})


class Clock:
    def __init__(self): self.t, self.sleeps = 0.0, []
    def __call__(self): return self.t
    def sleep(self, delay): self.sleeps.append(delay); self.t += delay


class Inbox:
    def __init__(self, messages):
        self.messages = list(messages)
        self.now = NOW
        self.requests, self.acked = [], []
        self.fail_ack = False
        self.page_size = 10
        self.on_ack = None

    def __call__(self, request):
        self.requests.append(request)
        assert request.url.host == "inbox.example", "partner/retailer server must never be contacted"
        assert request.headers["Authorization"] == "Bearer offline-inbox-token"
        if request.method == "GET":
            assert request.url.path == "/inbox"
            assert request.url.params["since"] == (self.now - timedelta(days=7)).isoformat()
            offset = int(request.url.params.get("cursor") or 0)
            size = min(self.page_size, int(request.url.params["limit"]))
            page = self.messages[offset:offset + size]
            return httpx.Response(200, json={"messages": page,
                "next_cursor": str(offset + size) if offset + size < len(self.messages) else None})
        assert request.method == "POST" and request.url.path == "/inbox/ack"
        ids = json.loads(request.content)["ids"]
        if self.on_ack: self.on_ack(ids)
        if self.fail_ack: return httpx.Response(503)
        self.acked.extend(ids)
        self.messages = [m for m in self.messages if m["id"] not in ids]
        return httpx.Response(200, json={"acknowledged": len(ids)})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWSLETTER_INBOX_TOKEN", "offline-inbox-token")
    cfg = AppConfig(newsletter_inbox_url="https://inbox.example", max_retries=0)
    db = DB(tmp_path / "state.db")
    clock = Clock()
    yield cfg, db, clock
    db.close()


def reader(env, inbox, sources=None):
    cfg, db, clock = env
    client = httpx.Client(transport=httpx.MockTransport(inbox))
    return NewsletterAdapter(http_client=client, sources=sources or [source()],
                             sleep_fn=clock.sleep, clock=clock)


def test_sender_mapping_exact_allowlist_domain_unknown_and_ambiguous(env):
    cfg, db, _ = env
    known = message(**{"from": "Ben's Deals <DEALS@bensbargains.com>"})
    unknown = message(2, **{"from": "deals@bensbargains.com.evil.example"})
    unlisted = message(3, **{"from": "someone@bensbargains.com"})
    inbox = Inbox([known, unknown, unlisted])
    adapter = reader(env, inbox)
    posts = adapter.fetch_posts(inbox_source(), None, db, cfg, NOW, None)
    assert len(posts) == 2 and all(p.source_name == source().name for p in posts)
    assert not inbox.acked  # sender counts are durable before mail is acknowledged
    assert adapter.acknowledge(db, cfg) == 1
    assert inbox.acked == [unknown["id"]]
    ambiguous = reader(env, Inbox([known]), [source(), source(name="another")])
    assert ambiguous.fetch_posts(inbox_source(), None, db, cfg, NOW, None) == []


@pytest.mark.parametrize("host, expected", [
    ("deals.mail.hip2save.com", "hip2save.com"),
    ("MAIL.HIP2SAVE.COM.", "hip2save.com"),
    ("hip2save.com.evil.example", "evil.example"),
    ("mail.partner.co.uk", "partner.co.uk"),
    ("mail.partner.com.au", "partner.com.au"),
    ("mail.partner.co.nz", "partner.co.nz"),
    ("mail.partner.com.br", "partner.com.br"),
    ("mail.partner.github.io", "partner.github.io"),
    ("mail.partner.blogspot.com", "partner.blogspot.com"),
    ("a.b.ck", "a.b.ck"),  # wildcard suffix *.ck
    ("mail.www.ck", "www.ck"),  # exception !www.ck
    ("mail.city.kawasaki.jp", "city.kawasaki.jp"),
    ("mail.bücher.de", "xn--bcher-kva.de"),
    ("co.uk", ""), ("localhost", ""), ("127.0.0.1", ""),
    ("hip2save.com/path", ""), ("hip2save.com..", ""),
])
def test_registrable_domains_offline(host, expected, monkeypatch):
    def no_network(*args, **kwargs): pytest.fail("domain matching must stay offline")
    monkeypatch.setattr(httpx.Client, "request", no_network)
    assert registrable_domain(host) == expected


def test_sender_domains_exact_addresses_permissions_and_ambiguity():
    hip = source("hip2save", "hip2save.com", "publisher@mailer.example")
    assert match_sender("deals@mail.hip2save.com", [hip]) is hip
    assert match_sender("PUBLISHER@MAILER.EXAMPLE", [hip]) is hip
    for address in ("deals@hip2save.com.evil.example", "deals@fakehip2save.com", "bad"):
        assert match_sender(address, [hip]) is None
    assert match_sender("deals@mail.hip2save.com", [dataclasses.replace(hip, crawl_permission="")]) is None
    assert match_sender("deals@mail.hip2save.com", [dataclasses.replace(hip, enabled=False)]).name == hip.name
    uk = source("uk_partner", "www.partner.co.uk")
    assert match_sender("deals@mail.partner.co.uk", [hip, uk]) is uk
    assert match_sender("deals@mail.other.co.uk", [hip, uk]) is None
    # Exact/domain matches are a union: a second candidate is always unknown.
    # One site with several feeds is one partner: its first (enabled) entry.
    assert match_sender("deals@mail.hip2save.com", [hip, dataclasses.replace(hip, name="hip_amazon")]) is hip
    paused = dataclasses.replace(hip, enabled=False)
    amazon = dataclasses.replace(hip, name="hip_amazon")
    assert match_sender("deals@mail.hip2save.com", [paused, amazon]) is amazon
    other = source("other", "other.com", "publisher@mailer.example")
    assert match_sender("publisher@mailer.example", [hip, other]) is None
    # A listed address wins over a domain match.
    assert match_sender("publisher@mailer.example", [other, source("mailer", "mailer.example")]) is other


def test_unknown_senders_store_only_counts_dates_and_idempotency_ids(env):
    cfg, db, _ = env
    unknown = message(**{"from": "Unknown <sender@unmapped.example>", "subject": PRIVATE,
                         "html": PRIVATE, "text": PRIVATE})
    inbox = Inbox([unknown, message(2, **{"from": "sender@unmapped.example"})])
    adapter = reader(env, inbox)
    assert adapter.fetch_posts(inbox_source(), None, db, cfg, NOW, None) == []
    state = json.loads(db.meta_get(UNKNOWN_SENDERS_META_KEY))
    assert state["senders"] == {"sender@unmapped.example": {"count": 2, "last_seen": "2026-10-13"}}
    assert set(state) == {"senders", "messages"}
    assert set(state["messages"]) == {m["id"] for m in inbox.messages}
    assert PRIVATE not in "\n".join(db.conn.iterdump())
    inbox.fail_ack = True
    with pytest.raises(RuntimeError):
        adapter.acknowledge(db, cfg)
    retry = reader(env, inbox)
    assert retry.fetch_posts(inbox_source(), None, db, cfg, NOW, None) == []
    assert json.loads(db.meta_get(UNKNOWN_SENDERS_META_KEY)) == state
    inbox.fail_ack = False
    assert retry.acknowledge(db, cfg) == 2
    inbox.now = NOW + timedelta(days=1)
    inbox.messages = [message(3, **{"from": "sender@unmapped.example"})]
    reader(env, inbox).fetch_posts(inbox_source(), None, db, cfg, inbox.now, None)
    assert json.loads(db.meta_get(UNKNOWN_SENDERS_META_KEY))["senders"] == {
        "sender@unmapped.example": {"count": 3, "last_seen": "2026-10-14"}}


def test_partner_mail_counts_each_delivery_once_for_the_resubscribe_list(env):
    """Operator, 2026-10-14: which partners' newsletters arrive at all."""
    cfg, db, _ = env
    inbox = Inbox([message(), message(2), message(3, **{"from": "sender@unmapped.example"})])
    reader(env, inbox).fetch_posts(inbox_source(), None, db, cfg, NOW, None)
    expected = {source().name: {"count": 2, "first_seen": "2026-10-13", "last_seen": "2026-10-13"}}
    assert partner_mail(db) == expected
    reader(env, inbox).fetch_posts(inbox_source(), None, db, cfg, NOW, None)  # unacked retry
    assert partner_mail(db) == expected
    inbox.now = NOW + timedelta(days=1)
    inbox.messages = [message(4)]
    reader(env, inbox).fetch_posts(inbox_source(), None, db, cfg, inbox.now, None)
    assert partner_mail(db) == {source().name: {"count": 3, "first_seen": "2026-10-13",
                                                "last_seen": "2026-10-14"}}


def test_partner_mail_without_posts_logs_link_shapes_never_urls_or_tokens(env, caplog):
    """Live 2026-10-22: 19 Slickdeals alerts gave no posts. Actions logs are public."""
    cfg, db, _ = env
    slick = source("slickdeals_frontpage", "slickdeals.net", "alerts@slickdeals.net")
    alert = message(2, **{"from": "Slickdeals <alerts@slickdeals.net>", "subject": PRIVATE, "html": (
        "<p>Deal alert: Widget $19.99 (was $49.99)</p>"
        "<a href='https://slickdeals.net/click?lno=123&u=SECRETTOKEN'>Widget</a>"
        "<a href='https://slickdeals.net/click?lno=456&u=SECRETTOKEN#frag'>See deal</a>"
        "<a href='https://links.e.slickdeals.net/u/click?upn=SECRETTOKEN'>View</a>"
        "<a href='https://url9934.slickdeals.net/Xk29aQ7Zp/SECRETTOKEN'>Open</a>"
        "<a href='mailto:deals@wowpriced.com'>Reply</a><p>" + PRIVATE + "</p>")})
    text_alert = message(3, **{"from": "alerts@slickdeals.net", "html": "",
                               "text": "Widget $5\nhttps://slickdeals.net/click?lno=9&u=SECRETTOKEN."})
    caplog.set_level(logging.INFO, logger="dealradar.fetch.newsletter")
    inbox = Inbox([message(), alert, text_alert])
    posts = reader(env, inbox, [source(), slick]).fetch_posts(inbox_source(), None, db, cfg, NOW, None)
    assert [p.source_name for p in posts] == [source().name]
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert [line for line in logged.splitlines() if "gave no posts" in line] == [
        "partner newsletter slickdeals_frontpage: 2/2 message(s) gave no posts, 2 with a price; "
        "link shapes: slickdeals.net/click (3), links.e.slickdeals.net/u (1), *.slickdeals.net/* (1)"]
    for private in ("SECRETTOKEN", "lno", "123", "9934", "Xk29", "?", "#", "http", "@",
                    "wowpriced", "Widget", PRIVATE):
        assert private not in logged
    caplog.clear()  # a partner email that yields posts adds no diagnostic line
    reader(env, Inbox([message(4)])).fetch_posts(inbox_source(), None, db, cfg, NOW, None)
    assert "partner newsletters: 1 message(s)" in caplog.text and "gave no posts" not in caplog.text


def test_mapped_senders_leave_the_unknown_report(env, monkeypatch):
    cfg, db, _ = env
    adapter = reader(env, Inbox([message(**{"from": "publisher@mailer.example"})]))
    adapter.fetch_posts(inbox_source(), None, db, cfg, NOW, None)
    assert unknown_sender_counts(db) == {"publisher@mailer.example": 1}
    monkeypatch.setattr("dealradar.fetch.newsletter.load_sources", lambda: (
        [source(address="publisher@mailer.example")], []))
    assert unknown_sender_counts(db) == {}


def test_shared_pull_maps_multiple_partners_without_acknowledging_unhandled_source(env):
    cfg, db, _ = env
    other = source("techbargains", "techbargains.com", "deals@techbargains.com")
    second = message(2, **{"from": "deals@techbargains.com",
                          "html": "<div><a href='https://www.techbargains.com/deals/monitor'>Monitor</a> was $200 now $40</div>"})
    inbox = Inbox([message(), second])
    adapter = reader(env, inbox, [source(), other])
    first_posts = adapter.fetch_posts(inbox_source(), None, db, cfg, NOW, None)
    assert [p.source_name for p in first_posts] == [source().name, other.name]
    assert len(inbox.requests) == 1
    db.record_post(first_posts[0], NOW)
    assert adapter.acknowledge(db, cfg) == 1
    assert second["id"] not in inbox.acked
    db.record_post(first_posts[1], NOW)
    assert adapter.acknowledge(db, cfg) == 1
    assert len([r for r in inbox.requests if r.method == "GET"]) == 1


def test_html_cards_keep_each_own_credit_and_verbatim_price_evidence():
    html = ("<table><tr><td><a href='https://bensbargains.com/bargain/widget/'>Widget now $20</a>"
            "<p>was $100</p></td></tr><tr><td><a href='https://bensbargains.com/bargain/monitor/'>Monitor</a>"
            "<p>was $200 now $40</p><a href='https://tracker.example/click'>Buy</a></td></tr></table>"
            "<a href='https://bensbargains.com/unsubscribe?email=test'>unsubscribe</a>"
            "<script>Fake was $999 now $1</script><template>Fake was $777 now $2</template>")
    posts = email_posts(message(html=html), source(), NOW)
    assert [p.url for p in posts] == ["https://bensbargains.com/bargain/widget/", "https://bensbargains.com/bargain/monitor/"]
    assert "Monitor" not in posts[0].text and "Widget" not in posts[1].text
    assert "https://tracker.example/click" in posts[1].text
    assert all("Fake" not in p.text and p.via_email for p in posts)
    deal = ExtractedDeal("Widget", "Store", posts[0].url, posts[0].source_name,
                         regular_price=100, final_price=20,
                         evidence=Evidence(regular_price_quote="was $100", final_price_quote="now $20"))
    assert verify_evidence(deal, posts[0].text) == (True, "")
    assert verify_evidence(deal, posts[1].text)[0] is False
    assert posts[0].published_at == datetime(2026, 10, 13, 12, tzinfo=timezone.utc)


def test_text_only_and_tracking_only_confirmation_or_ambiguous_email():
    text = "Widget was $100 now $20\nhttps://bensbargains.com/bargain/widget/?credit=partner\n\nMonitor was $200 now $40\nhttps://bensbargains.com/bargain/monitor/"
    posts = email_posts(message(html="", text=text), source(), NOW)
    assert len(posts) == 2
    assert posts[0].url.endswith("?credit=partner")
    for html in ["<p>was $100 now $20</p><a href='https://tracker.example/?u=https%3A%2F%2Fbensbargains.com%2Fbargain%2Fx'>Read deal</a>",
                 "<p>Confirm your subscription</p><a href='https://bensbargains.com/confirm'>Confirm</a>",
                 "<a href='https://wowpriced.com/deal/x/'>Our link was $100 now $20</a>"]:
        assert email_posts(message(html=html), source(), NOW) == []


def test_delivery_headers_do_not_change_content_hash_and_no_body_is_stored(env):
    cfg, db, _ = env
    original = message()
    resent = message(2, subject="Different subject", date="2026-10-13T13:00:00Z", message_id="<resent>")
    inbox = Inbox([original, resent])
    adapter = reader(env, inbox)
    posts = adapter.fetch_posts(inbox_source(), None, db, cfg, NOW, None)
    assert posts[0].content_hash == posts[1].content_hash
    assert db.record_post(posts[0], NOW)
    assert not db.record_post(posts[1], NOW)
    assert adapter.acknowledge(db, cfg) == 2
    assert db.conn.execute("SELECT count(*) FROM posts_seen").fetchone()[0] == 1
    dump = "\n".join(db.conn.iterdump())
    assert PRIVATE not in dump and original["html"] not in dump


@pytest.mark.parametrize("missing", ["token", "endpoint", "http_endpoint"])
def test_missing_configuration_is_needs_key_never_breaker_failure(env, monkeypatch, missing):
    cfg, db, _ = env
    src = inbox_source()
    if missing == "token": monkeypatch.delenv("NEWSLETTER_INBOX_TOKEN")
    if missing == "endpoint": cfg.newsletter_inbox_url = ""
    if missing == "http_endpoint": cfg.newsletter_inbox_url = "http://inbox.example"
    inbox = Inbox([])
    adapter = reader(env, inbox)
    for _ in range(4):
        assert run_source(src, adapter, None, db, cfg, NOW) == ([], False, "needs_key")
    assert not db.is_tripped(src.name, NOW)
    assert db.all_source_health()[0]["consecutive_failures"] == 0
    assert not inbox.requests
    assert _validate_one(src, None, cfg)[:2][0] == "needs_key"


def test_pagination_response_cap_deadline_and_private_auth_redirect(env):
    cfg, db, clock = env
    inbox = Inbox([message(1), message(2), message(3)])
    inbox.page_size = 1
    adapter = reader(env, inbox)
    assert len(adapter.fetch_posts(inbox_source(), None, db, cfg, NOW, None)) == 3
    assert len(inbox.requests) == 3 and clock.sleeps == [1.0, 1.0]
    limited = dataclasses.replace(cfg, newsletter_inbox_max_messages=2)
    assert len(reader(env, inbox).fetch_posts(inbox_source(), None, db, limited, NOW, None)) == 2
    with pytest.raises(DeadlineExceeded):
        reader(env, inbox).fetch_posts(inbox_source(), None, db, cfg, NOW, clock())
    responses = [httpx.Response(200, content=b"x" * 1100),
                 httpx.Response(302, headers={"Location": "https://partner.example/private"}),
                 httpx.Response(200, text=PRIVATE), httpx.Response(401)]
    limited.newsletter_inbox_max_response_bytes = 1024
    for response in responses:
        calls = []
        def handler(request):
            calls.append(request)
            return response
        adapter = NewsletterAdapter(http_client=httpx.Client(transport=httpx.MockTransport(handler)),
                                    sources=[source()], sleep_fn=clock.sleep, clock=clock)
        with pytest.raises((RuntimeError, AdapterNotConfigured)) as error:
            adapter.fetch_posts(inbox_source(), None, db, limited, NOW, None)
        assert PRIVATE not in str(error.value)
        assert len(calls) == 1


def test_multi_part_email_ack_waits_for_every_post(env):
    cfg, db, _ = env
    html = "".join(f"<div><a href='https://bensbargains.com/bargain/{i}/'>Widget {i}</a> was $100 now $20</div>" for i in (1, 2))
    inbox = Inbox([message(html=html)])
    adapter = reader(env, inbox)
    posts = adapter.fetch_posts(inbox_source(), None, db, cfg, NOW, None)
    db.record_post(posts[0], NOW)
    assert adapter.acknowledge(db, cfg) == 0
    assert not inbox.acked
    db.record_post(posts[1], NOW)
    assert adapter.acknowledge(db, cfg) == 1


def test_retry_and_rate_limit_are_shared_with_other_inbox_readers(env):
    from types import SimpleNamespace
    cfg, db, clock = env
    cfg.max_retries = 1
    inbox = Inbox([message()])
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503) if calls == 1 else inbox(request)
    fetcher = SimpleNamespace(_last_request_at={})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    first = NewsletterAdapter(http_client=client, sources=[source()],
                              sleep_fn=clock.sleep, clock=clock)
    second = NewsletterAdapter(http_client=client, sources=[source()],
                               sleep_fn=clock.sleep, clock=clock)
    assert len(first.fetch_posts(inbox_source(), fetcher, db, cfg, NOW, None)) == 1
    assert len(second.fetch_posts(inbox_source(), fetcher, db, cfg, NOW, None)) == 1
    assert calls == 3 and clock.sleeps == [1, 1.0]


def test_email_source_probe_never_visits_its_credit_domain(monkeypatch, capsys):
    from scripts import source_probe
    monkeypatch.setenv("SOURCE_NAMES", inbox_source().name)
    monkeypatch.setattr(source_probe, "load_sources", lambda: ([inbox_source()], []))
    class NoCrawl:
        def __init__(self, *args): pass
        def get(self, *args, **kwargs): pytest.fail("email source must never be probed as a page")
    monkeypatch.setattr(source_probe, "PoliteFetcher", NoCrawl)
    assert source_probe.main() == 0
    assert "email inbox only" in capsys.readouterr().out


def test_source_registry_config_updates_and_secret_redaction(env, monkeypatch, tmp_path):
    cfg, db, _ = env
    sources, _ = load_sources()
    partners = [s for s in sources if s.method == "email"]
    assert len(partners) == 1 and partners[0].name == "partner_newsletters"
    assert partners[0].adapter == "newsletter" and partners[0].enabled
    assert partners[0].newsletter_from == [] and not partners[0].allowed_html
    assert not partners[0].image_permission and not partners[0].crawl_permission
    assert not any(s.name.endswith("_newsletter") for s in sources)
    assert isinstance(get_adapter(inbox_source(), cfg), NewsletterAdapter)
    assert "NEWSLETTER_INBOX_TOKEN" in SECRET_NAMES
    assert "offline-inbox-token" not in redact_secrets("failure offline-inbox-token")
    assert not load_config().newsletter_enabled
    current = source(address="new@bensbargains.com")
    save_effective_sources(db, [source()], NOW)
    monkeypatch.setattr(pipeline, "load_sources", lambda: ([current], []))
    effective = pipeline._load_effective_sources(db, cfg, None, NOW)
    assert effective[0].newsletter_from == current.newsletter_from
    assert _validate_one(inbox_source(), None, cfg)[0] == "live"
    custom = tmp_path / "sources.yaml"
    custom.write_text(yaml.safe_dump({"sources": [dataclasses.asdict(current)]}))
    assert load_sources(custom)[0][0].newsletter_from == current.newsletter_from


def test_cached_legacy_email_entries_do_not_rejoin_the_effective_sources(env, monkeypatch):
    cfg, db, _ = env
    legacy = dataclasses.replace(inbox_source(), name="bensbargains_newsletter",
                                 url=source().site, newsletter_from=source().newsletter_from)
    save_effective_sources(db, [source(), legacy], NOW)
    monkeypatch.setattr(pipeline, "load_sources", lambda: ([source(), inbox_source()], []))
    effective = pipeline._load_effective_sources(db, cfg, None, NOW)
    assert {s.name for s in effective} == {source().name, inbox_source().name}


def test_feed_and_email_share_one_source_for_corroboration_and_one_credit(env):
    from dealradar.dedupe import upsert_extracted
    from dealradar.publish.email_policy import email_credits
    from dealradar.publish.site import _serialize_deal
    from dealradar.savings import compute_savings
    from dealradar.verify import assign_tier
    cfg, db, _ = env
    email = email_posts(message(), source(), NOW)[0]
    feed = dataclasses.replace(email, uid="rss-post-guid", url=email.url + "?channel=feed", via_email=False)
    extracted = ExtractedDeal("Widget", "Store", email.url, email.source_name,
                              product_url="https://store.example/widget", online_only="true",
                              regular_price=100, final_price=20,
                              evidence=Evidence(regular_price_quote="was $100", final_price_quote="now $20"))
    for post in (feed, email):
        assert verify_evidence(extracted, post.text) == (True, "")
        upsert_extracted(db, extracted, compute_savings(extracted), post, cfg, NOW)
    assert len(db.all_deals()) == 1
    deal = db.all_deals()[0]
    assert len(db.sightings_for(deal["id"])) == 2
    assert db.distinct_sources_within(deal["id"], NOW - timedelta(hours=24), now=NOW) == 1
    assert assign_tier(db, deal, cfg, NOW) == "unverified"
    public = _serialize_deal(dict(deal, sightings=db.sightings_for(deal["id"])), NOW, cfg=cfg)
    assert public["credits"] == [{"name": "Ben's Bargains", "url": feed.url}]
    assert email_credits(dict(deal, sightings=db.sightings_for(deal["id"]))) == [(source().name, feed.url)]
    other = dataclasses.replace(feed, source_name="other_partner", uid="other-guid",
                                url="https://other-partner.example/widget/")
    upsert_extracted(db, dataclasses.replace(extracted, source_name=other.source_name),
                     compute_savings(extracted), other, cfg, NOW)
    assert assign_tier(db, db.all_deals()[0], cfg, NOW) == "corroborated"


def test_static_checklist_covers_every_permission_entry_and_preserves_ticks(tmp_path, monkeypatch):
    from scripts import partner_newsletters
    primary, backup = load_sources()
    generated = partner_newsletters.checklist(primary + backup)
    assert generated == (ROOT / "docs/partner-newsletters.md").read_text()
    permitted = [s for s in primary + backup if s.crawl_permission and s.method != "email"]
    assert len(permitted) == 67  # + the three Slickdeals feeds (2026-10-22)
    for partner in permitted:
        assert f"(`{partner.name}`)" in generated
        assert f"]({partner.site or partner.url})" in generated
    updated = generated.replace("- [ ] [Ben's Bargains]", "- [x] [Ben's Bargains]").replace(
        "(`bensbargains`) — Matched sender: —", "(`bensbargains`) — Matched sender: deals@mail.bensbargains.com")
    assert partner_newsletters.checklist(primary + backup, updated) == updated
    mapped = dataclasses.replace(source(), newsletter_from=["publisher@mailer.example"])
    assert "Matched sender: publisher@mailer.example" in partner_newsletters.checklist([mapped])
    output = tmp_path / "checklist.md"
    output.write_text(updated)
    monkeypatch.setattr("sys.argv", ["partner_newsletters.py", "--output", str(output)])
    assert partner_newsletters.main() == 0
    assert output.read_text() == updated


def test_growth_report_reads_unknown_sender_counts_privately_from_snapshot(env, monkeypatch, caplog):
    from dealradar.growth import report
    cfg, db, _ = env
    inbox = Inbox([message(**{"from": "sender@unmapped.example", "subject": PRIVATE})])
    reader(env, inbox).fetch_posts(inbox_source(), None, db, cfg, NOW, None)
    before = "\n".join(db.conn.iterdump())
    for key in SECRET_NAMES:
        monkeypatch.delenv(key, raising=False)
    cfg.custom_domain = ""
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("no report API configured"))) as client:
        data, status = report.collect(cfg, NOW, client, db_path=db.path)
    assert status["partner_newsletters"] == "ok"
    assert data["partner_newsletters"] == {"sender@unmapped.example": 1}
    _, body = report.build_report(cfg, data, status, NOW.date() - timedelta(days=1))
    assert body.count("New newsletter senders to map: sender@unmapped.example (1)") == 1
    assert PRIVATE not in body and "Today's deals" not in body
    assert "sender@unmapped.example" not in caplog.text
    assert "\n".join(db.conn.iterdump()) == before


class SpyMessages(_FakeMessages):
    def __init__(self): self.reads = 0
    def parse(self, **kwargs):
        from dealradar.parse.schema import CriticVerdicts
        if kwargs["output_format"] is not CriticVerdicts: self.reads += 1
        return super().parse(**kwargs)


@pytest.fixture
def pipeline_env(env, monkeypatch, tmp_path):
    cfg, db, _ = env
    raw = yaml.safe_load((ROOT / "config.yaml").read_text())
    raw["partner_newsletters"]["inbox_url"] = cfg.newsletter_inbox_url
    raw["fetch"]["max_retries"] = 0
    raw["parse"].update(structured_output=True, max_posts_per_run=1, spend_pacing=False)
    raw["publish"]["site_dir"] = str(tmp_path / "site")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    client = _FakeClaudeClient()
    client.messages = SpyMessages()
    monkeypatch.setattr("dealradar.util.now_utc", lambda: NOW)
    for key in ("KEEPA_KEY", "SMTP_HOST", "TELEGRAM_BOT_TOKEN", "STRIPE_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    db.meta_set("recategorized_v2", "1")
    monkeypatch.setattr(pipeline, "load_sources", lambda: ([source()], []))
    return db, path, client


def pipeline_message():
    return message(html=(
        "<div><a href='https://bensbargains.com/bargain/fryer/'>Ninja Foodi Air Fryer</a> "
        "regularly $129.99 just $29.99 FRYDAY at checkout; free shipping on orders over $25</div>"
        "<div><a href='https://bensbargains.com/bargain/headphones/'>Mega Headphones X200</a> "
        "list price $199.99 as low as $39.99 most colors $59.99</div>"
        "<p>" + PRIVATE + "</p>"))


def run_pipeline(pipeline_env, inbox, *, parse=True, monkeypatch=None, fast=False):
    db, path, client = pipeline_env
    if not parse:
        monkeypatch.setattr(pipeline, "_build_parser", lambda *a, **kw: None)
    return pipeline.run(db_path=db.path, config_path=str(path), now=NOW,
                        sources_override=[inbox_source(fast_lane=fast)], skip_validation=True,
                        transport=httpx.MockTransport(inbox), claude_client=client,
                        dry_run_telegram=True, fast_lane=fast)


def test_real_pipeline_defers_multi_deal_ack_then_idempotently_retries_failed_ack(pipeline_env):
    db, path, client = pipeline_env
    inbox = Inbox([pipeline_message()])
    def assert_durable(ids):
        assert ids == [pipeline_message()["id"]]
        persisted = DB(db.path)
        assert len(persisted.all_deals()) == 2
        assert persisted.conn.execute("SELECT count(*) FROM posts_seen").fetchone()[0] == 2
        persisted.close()
    inbox.on_ack = assert_durable
    first = run_pipeline(pipeline_env, inbox)
    assert first["posts_new"] == 1 and first["inbox_acknowledged"] == 0
    assert not inbox.acked and client.messages.reads == 1
    inbox.fail_ack = True
    second = run_pipeline(pipeline_env, inbox)
    assert second["posts_new"] == 1 and second["inbox_acknowledged"] == 0
    assert not inbox.acked and client.messages.reads == 2
    counts = {table: db.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
              for table in ("deals", "sightings", "price_history", "posts_seen", "alerts_sent", "run_stats", "source_audit_posts")}
    inbox.fail_ack = False
    third = run_pipeline(pipeline_env, inbox)
    assert third["posts_new"] == 0 and third["inbox_acknowledged"] == 1
    assert client.messages.reads == 2 and not inbox.messages
    assert counts == {table: db.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in counts}
    dump = "\n".join(db.conn.iterdump())
    assert PRIVATE not in dump and pipeline_message()["html"] not in dump
    credits = {s["source_url"] for d in db.all_deals() for s in db.sightings_for(d["id"])}
    assert credits == {"https://bensbargains.com/bargain/fryer/", "https://bensbargains.com/bargain/headphones/"}
    assert all("newsletter" in d["flags"] for d in db.all_deals())
    # Redelivery with different mail headers is already handled, with no LLM read.
    inbox.on_ack = None
    inbox.messages = [dict(pipeline_message(), id="msg:" + "f" * 64, subject="Resent")]
    assert run_pipeline(pipeline_env, inbox)["posts_new"] == 0
    assert not inbox.messages and client.messages.reads == 2


def test_parser_disabled_leaves_known_sender_pending_but_acks_ignored_mail(pipeline_env, monkeypatch):
    inbox = Inbox([pipeline_message(), message(2, **{"from": "unknown@example.com"})])
    result = run_pipeline(pipeline_env, inbox, parse=False, monkeypatch=monkeypatch)
    assert result["posts_new"] == 0 and result["inbox_acknowledged"] == 1
    assert inbox.acked == [message(2)["id"]]
    assert len(inbox.messages) == 1


def test_unknown_sender_pipeline_rerun_after_failed_ack_is_idempotent(pipeline_env, monkeypatch):
    db, _, _ = pipeline_env
    inbox = Inbox([message(**{"from": "sender@unmapped.example", "subject": PRIVATE})])
    inbox.fail_ack = True
    first = run_pipeline(pipeline_env, inbox, parse=False, monkeypatch=monkeypatch)
    assert first["inbox_acknowledged"] == 0
    metadata = db.meta_get(UNKNOWN_SENDERS_META_KEY)
    assert json.loads(metadata)["senders"]["sender@unmapped.example"]["count"] == 1
    alerts = db.conn.execute("SELECT count(*) FROM alerts_sent").fetchone()[0]
    inbox.fail_ack = False
    second = run_pipeline(pipeline_env, inbox, parse=False, monkeypatch=monkeypatch)
    assert second["inbox_acknowledged"] == 1
    assert db.meta_get(UNKNOWN_SENDERS_META_KEY) == metadata
    assert db.conn.execute("SELECT count(*) FROM alerts_sent").fetchone()[0] == alerts
    assert not db.all_deals()


def test_pipeline_matches_paused_partner_outside_active_run_set(pipeline_env, monkeypatch):
    db, _, _ = pipeline_env
    monkeypatch.setattr(pipeline, "load_sources", lambda: ([source(enabled=False, address="")], []))
    inbox = Inbox([pipeline_message()])
    assert run_pipeline(pipeline_env, inbox)["posts_new"] == 1
    assert {s["source_name"] for d in db.all_deals() for s in db.sightings_for(d["id"])} == {source().name}


def test_fast_lane_never_enriches_email_deals_from_partner_servers(pipeline_env):
    db, path, _ = pipeline_env
    raw = yaml.safe_load(path.read_text())
    raw["parse"]["max_posts_per_run"] = 10
    path.write_text(yaml.safe_dump(raw))
    inbox = Inbox([pipeline_message()])
    result = run_pipeline(pipeline_env, inbox, fast=True)
    assert result["posts_new"] == 2 and result["inbox_acknowledged"] == 1
    assert all(r.url.host == "inbox.example" for r in inbox.requests)
    assert all("newsletter" in d["flags"] for d in db.all_deals())


def test_failed_pipeline_run_keeps_email_pending_until_facts_are_handled(pipeline_env, monkeypatch):
    from dealradar.publish import site
    original = site.render_site
    inbox = Inbox([pipeline_message()])
    def failed_publish(*args, **kwargs): raise RuntimeError("offline publish failed")
    monkeypatch.setattr(site, "render_site", failed_publish)
    with pytest.raises(RuntimeError, match="offline publish failed"):
        run_pipeline(pipeline_env, inbox)
    db, _, client = pipeline_env
    assert db.conn.execute("SELECT count(*) FROM posts_seen").fetchone()[0] == 0
    assert not inbox.acked and inbox.messages
    monkeypatch.setattr(site, "render_site", original)
    retry = run_pipeline(pipeline_env, inbox)
    assert retry["posts_new"] == 1
    assert retry["parse"]["reused_posts"] == 1
    assert retry["spend_by_kind"]["extract"]["calls"] == 0
    assert not inbox.acked  # second part still deferred by max_posts_per_run
    assert run_pipeline(pipeline_env, inbox)["inbox_acknowledged"] == 1
    assert len(db.all_deals()) == 2 and client.messages.reads == 2


def test_inbox_deployment_is_manual_and_supplies_only_repository_secrets():
    doc = yaml.safe_load((ROOT / ".github/workflows/deploy-newsletter-inbox.yml").read_text())
    assert doc.get("on", doc.get(True)) == {"workflow_dispatch": None}
    assert doc["permissions"] == {"contents": "read"}
    steps = doc["jobs"]["deploy"]["steps"]
    secret_step = next(s for s in steps if s["name"] == "Put the inbox and forwarding secrets on the Worker")
    assert secret_step["env"] == {name: "${{ secrets." + name + " }}" for name in
                                  ("NEWSLETTER_INBOX_TOKEN", "NEWSLETTER_FORWARD_TO")}
    commands = "\n".join(s.get("run", "") for s in steps)
    assert "npm test" in commands and "wrangler deploy --config wrangler.deploy.toml" in commands
    assert "kv namespace create wowpriced_newsletter_inbox" in commands
    assert "git push" not in commands
