"""Private inbox ingestion, evidence, deferred acknowledgements and reruns offline."""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import yaml

from dealradar import pipeline
from dealradar.config import AppConfig, SECRET_NAMES, load_config, load_sources, redact_secrets
from dealradar.db import DB
from dealradar.errors import AdapterNotConfigured, DeadlineExceeded
from dealradar.fetch.base import run_source
from dealradar.fetch.newsletter import NewsletterAdapter, email_posts
from dealradar.fetch.registry import get_adapter
from dealradar.fetch.validate import _validate_one, save_effective_sources
from dealradar.models import Evidence, ExtractedDeal, SourceConfig
from dealradar.parse.extractor import verify_evidence
from tests.test_e2e_offline import _FakeClaudeClient, _FakeMessages

NOW = datetime(2026, 10, 13, 14, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parent.parent
PRIVATE = "PRIVATE_FULL_NEWSLETTER_UNRELATED_FOOTER"


def source(name="bensbargains_newsletter", host="bensbargains.com", address="deals@bensbargains.com", **kwargs):
    return SourceConfig(name=name, method="email", adapter="newsletter",
                        site=f"https://{host}/", url=f"https://{host}/",
                        newsletter_from=[address], **kwargs)


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
            assert request.url.params["since"] == "2026-10-06T14:00:00+00:00"
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


def test_sender_mapping_exact_allowlist_unknown_and_ambiguous_are_ignored(env):
    cfg, db, _ = env
    known = message(**{"from": "Ben's Deals <DEALS@bensbargains.com>"})
    unknown = message(2, **{"from": "deals@bensbargains.com.evil.example"})
    unlisted = message(3, **{"from": "someone@bensbargains.com"})
    inbox = Inbox([known, unknown, unlisted])
    adapter = reader(env, inbox)
    posts = adapter.fetch_posts(source(), None, db, cfg, NOW, None)
    assert len(posts) == 1 and posts[0].source_name == source().name
    assert not inbox.acked  # fetch is read-only, including ignored mail
    assert adapter.acknowledge(db, cfg) == 2
    assert inbox.acked == [unknown["id"], unlisted["id"]]
    ambiguous = reader(env, Inbox([known]), [source(), source(name="another")])
    assert ambiguous.fetch_posts(source(), None, db, cfg, NOW, None) == []


def test_shared_pull_maps_multiple_partners_without_acknowledging_unhandled_source(env):
    cfg, db, _ = env
    other = source("techbargains_newsletter", "techbargains.com", "deals@techbargains.com")
    second = message(2, **{"from": "deals@techbargains.com",
                          "html": "<div><a href='https://www.techbargains.com/deals/monitor'>Monitor</a> was $200 now $40</div>"})
    inbox = Inbox([message(), second])
    adapter = reader(env, inbox, [source(), other])
    first_posts = adapter.fetch_posts(source(), None, db, cfg, NOW, None)
    assert len(inbox.requests) == 1
    db.record_post(first_posts[0], NOW)
    assert adapter.acknowledge(db, cfg) == 1
    assert second["id"] not in inbox.acked
    assert len(adapter.fetch_posts(other, None, db, cfg, NOW, None)) == 1
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
    posts = adapter.fetch_posts(source(), None, db, cfg, NOW, None)
    assert posts[0].content_hash == posts[1].content_hash
    assert db.record_post(posts[0], NOW)
    assert not db.record_post(posts[1], NOW)
    assert adapter.acknowledge(db, cfg) == 2
    assert db.conn.execute("SELECT count(*) FROM posts_seen").fetchone()[0] == 1
    dump = "\n".join(db.conn.iterdump())
    assert PRIVATE not in dump and original["html"] not in dump


@pytest.mark.parametrize("missing", ["token", "endpoint", "sender", "http_endpoint"])
def test_missing_configuration_is_needs_key_never_breaker_failure(env, monkeypatch, missing):
    cfg, db, _ = env
    src = source()
    if missing == "token": monkeypatch.delenv("NEWSLETTER_INBOX_TOKEN")
    if missing == "endpoint": cfg.newsletter_inbox_url = ""
    if missing == "http_endpoint": cfg.newsletter_inbox_url = "http://inbox.example"
    if missing == "sender": src.newsletter_from = []
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
    assert len(adapter.fetch_posts(source(), None, db, cfg, NOW, None)) == 3
    assert len(inbox.requests) == 3 and clock.sleeps == [1.0, 1.0]
    limited = dataclasses.replace(cfg, newsletter_inbox_max_messages=2)
    assert len(reader(env, inbox).fetch_posts(source(), None, db, limited, NOW, None)) == 2
    with pytest.raises(DeadlineExceeded):
        reader(env, inbox).fetch_posts(source(), None, db, cfg, NOW, clock())
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
            adapter.fetch_posts(source(), None, db, limited, NOW, None)
        assert PRIVATE not in str(error.value)
        assert len(calls) == 1


def test_multi_part_email_ack_waits_for_every_post(env):
    cfg, db, _ = env
    html = "".join(f"<div><a href='https://bensbargains.com/bargain/{i}/'>Widget {i}</a> was $100 now $20</div>" for i in (1, 2))
    inbox = Inbox([message(html=html)])
    adapter = reader(env, inbox)
    posts = adapter.fetch_posts(source(), None, db, cfg, NOW, None)
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
    assert len(first.fetch_posts(source(), fetcher, db, cfg, NOW, None)) == 1
    assert len(second.fetch_posts(source(), fetcher, db, cfg, NOW, None)) == 1
    assert calls == 3 and clock.sleeps == [1, 1.0]


def test_email_source_probe_never_visits_its_credit_domain(monkeypatch, capsys):
    from scripts import source_probe
    monkeypatch.setenv("SOURCE_NAMES", source().name)
    monkeypatch.setattr(source_probe, "load_sources", lambda: ([source()], []))
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
    assert len(partners) == 8
    for partner in partners:
        assert partner.adapter == "newsletter" and not partner.enabled and partner.newsletter_from == []
        assert not partner.allowed_html and not partner.image_permission and partner.crawl_permission
        assert any(s.name == partner.name.removesuffix("_newsletter") and s.method == "rss" for s in sources)
    assert isinstance(get_adapter(source(), cfg), NewsletterAdapter)
    assert "NEWSLETTER_INBOX_TOKEN" in SECRET_NAMES
    assert "offline-inbox-token" not in redact_secrets("failure offline-inbox-token")
    assert not load_config().newsletter_enabled
    current = source(address="new@bensbargains.com")
    save_effective_sources(db, [source()], NOW)
    monkeypatch.setattr(pipeline, "load_sources", lambda: ([current], []))
    effective = pipeline._load_effective_sources(db, cfg, None, NOW)
    assert effective[0].newsletter_from == current.newsletter_from
    assert _validate_one(current, None, cfg)[0] == "live"
    custom = tmp_path / "sources.yaml"
    custom.write_text(yaml.safe_dump({"sources": [dataclasses.asdict(current)]}))
    assert load_sources(custom)[0][0].newsletter_from == current.newsletter_from


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
    return db, path, client


def pipeline_message():
    return message(html=(
        "<div><a href='https://bensbargains.com/bargain/fryer/'>Ninja Foodi Air Fryer</a> "
        "regularly $129.99 just $29.99 FRYDAY at checkout</div>"
        "<div><a href='https://bensbargains.com/bargain/headphones/'>Mega Headphones X200</a> "
        "list price $199.99 as low as $39.99 most colors $59.99</div>"
        "<p>" + PRIVATE + "</p>"))


def run_pipeline(pipeline_env, inbox, *, parse=True, monkeypatch=None, fast=False):
    db, path, client = pipeline_env
    if not parse:
        monkeypatch.setattr(pipeline, "_build_parser", lambda *a, **kw: None)
    return pipeline.run(db_path=db.path, config_path=str(path), now=NOW,
                        sources_override=[source(fast_lane=fast)], skip_validation=True,
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
    assert run_pipeline(pipeline_env, inbox)["posts_new"] == 1
    assert not inbox.acked  # second part still deferred by max_posts_per_run
    assert run_pipeline(pipeline_env, inbox)["inbox_acknowledged"] == 1
    assert len(db.all_deals()) == 2 and client.messages.reads == 3


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
