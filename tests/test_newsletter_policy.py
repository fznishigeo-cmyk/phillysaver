"""Best-deals newsletter policy, cadence and previews; every transport is offline."""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser

import httpx
import pytest
import yaml

from dealradar import cli, rank, util
from dealradar.config import AppConfig, load_config
from dealradar.publish import newsletter as N
from dealradar.publish import email_digest, members
from .test_newsletter import (
    AFTER_8_LOCAL, Factory, FakeOptInWorker, FakeWorker, WORKER, bodies,
    cfg, clean_env, configure, db, ids_answer, make_deal, make_parser,
)

UTC = timezone.utc
TUESDAY = datetime(2026, 11, 10, 14, tzinfo=UTC)
ADDRESS = "PO Box 247, Philadelphia, PA 19101"


def forbidden(*args, **kwargs):
    raise AssertionError("external call forbidden")


def mixed_deals():
    variants = [
        {"store": "Amazon"},
        {"asin": "B0ABCDEF12"},
        {"canonical_url": "https://amzn.to/short"},
        {"canonical_url": "https://a.co/short"},
        {"canonical_url": "https://www.amazon.com/dp/B0ABCDEF12"},
        {"item": "Amazon Basics sold at Walmart"},
        {"conditions": ["aMaZoN account required"]},
        {"store": "eBay", "canonical_url": "https://www.ebay.com/itm/123"},
    ]
    dropped = [make_deal(dedupe_key=f"excluded-{i}", item=f"Excluded product {i}",
                         final_price=1011.23 + i, **v)
               for i, v in enumerate(variants) if "item" not in v]
    dropped.append(make_deal(dedupe_key="basics", item="Amazon Basics sold at Walmart",
                              final_price=2022.34))
    safe = [make_deal(dedupe_key="bestbuy", item="Best Buy speakers", store="Best Buy",
                      canonical_url="https://www.bestbuy.com/site/123",
                      deal_path="/deal/speakers-12345678/"),
            make_deal(dedupe_key="target", item="Target kettle", store="Target",
                      canonical_url="https://www.target.com/p/123",
                      deal_path="/deal/kettle-23456789/")]
    return dropped, safe


def test_exclusions_apply_before_model_prompt_and_to_stored_picks(db, cfg):
    dropped, safe = mixed_deals()
    cfg.newsletter_top_n = 1
    parser, messages = make_parser(cfg, ids_answer(2))
    # Excluded rows must not consume the 40 candidate slots.
    picked = N.todays_top(db, cfg, dropped * 6 + safe, TUESDAY.date(), parser)
    assert [d["dedupe_key"] for d in picked] == ["target"]
    prompt = messages.calls[0]["messages"][0]["content"]
    assert "Best Buy speakers" in prompt and "Target kettle" in prompt
    assert "amazon" not in prompt.casefold() and "ebay" not in prompt.casefold()
    for d in dropped:
        assert d["item"] not in prompt and f"${d['final_price']:,.2f}" not in prompt
    key = N.pick_key(N._period(cfg, TUESDAY.date()))
    db.meta_set(key, json.dumps({"keys": [d["dedupe_key"] for d in dropped + safe]}))
    assert [d["dedupe_key"] for d in N.todays_top(db, cfg, dropped + safe, TUESDAY.date())] == ["bestbuy", "target"]


def test_no_excluded_product_facts_or_marks_in_any_body(cfg):
    dropped, safe = mixed_deals()
    subject, text, html = N.build_newsletter(dropped + safe, cfg, TUESDAY)
    assert subject == "wowpriced: Today's top 2 deals"
    for body in (subject, text, html):
        lowered = body.casefold()
        assert all(term not in lowered for term in ("amazon", "ebay", "amazon.", "amzn.to", "a.co/", "ebay."))
        for d in dropped:
            assert d["item"] not in body and f"${d['final_price']:,.2f}" not in body
    assert "Best Buy speakers" in text and "Target kettle" in html


@pytest.mark.parametrize("channel", ["newsletter", "member", "digest"])
def test_source_display_names_remove_excluded_ids_and_deduplicate(cfg, channel):
    deal = make_deal(store="Target", sightings=[
        {"source_name": name, "source_url": "https://example.org/deal"}
        for name in ("hip2save_amazon", "kcl_amazon", "kcl_wall_amazon")])
    if channel == "newsletter":
        parts = N.build_newsletter([deal], cfg, TUESDAY)
    elif channel == "member":
        parts = members.build_member_email([deal], cfg)
    else:
        parts = email_digest.build_digest([deal], cfg, TUESDAY)
    assert "amazon" not in "".join(parts).casefold()
    for body in parts[1:]:
        assert "via Hip2Save" in body
        assert body.count("via The Krazy Coupon Lady") == 1


@pytest.mark.parametrize("change", [
    {"store": "ebay.com", "canonical_url": "https://cc.dealsofamerica.com/click?url=https%3A%2F%2Fwww.ebay.com%2Fitm%2F123"},
    {"store": "eBay Daily Deals", "canonical_url": "https://example.org/x"},
    {"store": "Unrelated", "canonical_url": "https://cc.dealsofamerica.com/click?url=https%3A%2F%2Fwww.ebay.com%2Fitm%2F123"},
    {"item": "eBay Daily Deals kettle"},
    *[{"item": item} for item in ("Echo Dot (5th Gen) with Alexa", "Kindle Paperwhite",
        "Echo (4th Gen)",
        "Fire TV Stick", "Fire tablet", "Fire 7 tablet", "Fire Kids tablet", "Fire HD 10 tablet", "Fire Max 11",
        "Ring Video Doorbell", "Ring Spotlight Cam", "Ring Alarm", "Blink Mini camera",
        "Blink Outdoor 4", "eero router", "Smart plug works with Alexa")],
])
@pytest.mark.parametrize("channel", ["newsletter", "member", "digest"])
def test_all_email_channels_exclude_stores_trackers_and_product_marks(cfg, change, channel):
    deal = make_deal(**change)
    safe = make_deal(item="Safe kettle", dedupe_key="safe")
    if channel == "newsletter":
        assert N.eligible_deals([deal], cfg) == []
        parts = N.build_newsletter([deal, safe], cfg, TUESDAY)
    elif channel == "member":
        parts = members.build_member_email([deal, safe], cfg)
    else:
        parts = email_digest.build_digest([deal, safe], cfg, TUESDAY)
    assert deal["item"] not in parts[1] + parts[2]
    assert "Safe kettle" in parts[1] + parts[2]


@pytest.mark.parametrize("item", ["Gold ring", "Blinking lights", "Echoes board game"])
def test_ordinary_words_are_not_product_marks(cfg, item):
    assert N.eligible_deals([make_deal(item=item)], cfg)


def test_remembered_but_unwritten_pages_are_excluded(db, cfg, tmp_path):
    cfg.site_dir = str(tmp_path)
    rows = [make_deal(dedupe_key=key, deal_path="") for key in ("gone", "here")]
    paths = {hashlib.sha256(key.encode()).hexdigest()[:8]: f"/deal/{key}-12345678/"
             for key in ("gone", "here")}
    db.meta_set("seo_deal_paths", json.dumps(paths))
    page = tmp_path / "deal/here-12345678/index.html"
    page.parent.mkdir(parents=True)
    page.write_text("written")
    assert [d["dedupe_key"] for d in N.eligible_deals(rows, cfg, db)] == ["here"]
    page.unlink()
    assert N.eligible_deals(rows, cfg, db) == []


@pytest.mark.parametrize("token", ["Amazon", "AMZN.to/short", "a.co/short", "eBay"])
@pytest.mark.parametrize("part", [0, 1, 2])
@pytest.mark.parametrize("channel", ["newsletter", "member", "digest", "confirmation"])
def test_final_copy_scan_prevents_delivery_claims(db, cfg, monkeypatch, token, part, channel):
    configure(monkeypatch)
    factory = Factory()
    names = {"newsletter": (N, "build_newsletter"), "confirmation": (N, "build_confirmation"),
             "member": (members, "build_member_email"), "digest": (email_digest, "build_digest")}
    module, name = names[channel]
    build = getattr(module, name)

    def contaminated(*args, **kwargs):
        parts = list(build(*args, **kwargs))
        parts[part] += " " + token
        return tuple(parts)

    monkeypatch.setattr(module, name, contaminated)
    if channel == "newsletter":
        worker = FakeWorker([("a@x.com", "t")])
        result = N.send_daily_newsletter(db, cfg, AFTER_8_LOCAL, deals=[make_deal()],
                    http_client=worker.client(), smtp_factory=factory)
        assert result["sent"] == 0 and result["skipped"] == "excluded store token in email"
    elif channel == "confirmation":
        worker = FakeOptInWorker([("a@x.com", "t")])
        assert N.send_confirmations(db, cfg, AFTER_8_LOCAL,
                    http_client=worker.client(), smtp_factory=factory)["sent"] == 0
        assert worker.marked == [] and db.meta_get(N.CONFIRM_META_KEY) is None
    elif channel == "member":
        db.meta_set(members.META_MEMBERS, json.dumps(["a@x.com"]))
        assert members.send_member_alerts(db, cfg, AFTER_8_LOCAL, deals=[make_deal()],
                    smtp_factory=factory)["sent"] == 0
    else:
        cfg.digest_recipient = "a@x.com"
        assert not email_digest.maybe_send_digest(db, cfg, AFTER_8_LOCAL,
                    deals=[make_deal()], smtp_factory=factory)
    assert factory.messages == []
    assert db.conn.execute("SELECT COUNT(*) FROM outgoing").fetchone()[0] == 0


def test_subscriber_specific_links_are_scanned_before_claim(db, cfg, monkeypatch):
    configure(monkeypatch)
    worker = FakeWorker([("amazon-fan@x.com", "t"), ("safe@x.com", "t")])
    factory = Factory()
    result = N.send_daily_newsletter(db, cfg, AFTER_8_LOCAL, deals=[make_deal()],
                http_client=worker.client(), smtp_factory=factory)
    assert result["sent"] == 1
    assert [m["To"] for m in factory.messages] == ["safe@x.com"]
    assert not db.outgoing_exists("newsletter", f"{result['day']}|{N.fingerprint('amazon-fan@x.com')}")


@pytest.mark.parametrize("channel", ["member", "digest"])
def test_member_and_digest_filter_before_delivery_accounting(db, cfg, monkeypatch, channel):
    from .test_members import Factory as MemberFactory

    configure(monkeypatch)
    blocked, safe = mixed_deals()
    safe = [dict(safe[0], first_seen_at=AFTER_8_LOCAL.isoformat())]
    if channel == "member":
        db.meta_set(members.META_MEMBERS, json.dumps(["a@x.com"]))
        factory = MemberFactory()
        assert members.send_member_alerts(db, cfg, AFTER_8_LOCAL, deals=blocked + safe,
                                         smtp_factory=factory)["sent"] == 1
        messages = factory.servers[0].messages
        assert db.outgoing_exists(members.CHANNEL, "bestbuy")
        assert all(not db.outgoing_exists(members.CHANNEL, d["dedupe_key"]) for d in blocked)
    else:
        cfg.digest_recipient = "a@x.com"
        factory = Factory()
        assert email_digest.maybe_send_digest(db, cfg, AFTER_8_LOCAL, deals=blocked + safe,
                                            smtp_factory=factory)
        messages = factory.messages
    assert len(messages) == 1
    msg = messages[0]
    assert "amazon" not in (str(msg["Subject"]) + "".join(bodies(msg).values())).casefold()
    assert "Best Buy speakers" in bodies(msg)["plain"]


@pytest.mark.parametrize("block, match", [
    ({"enabled": "false"}, "enabled"),
    ({"exclude_stores": ["ebay"]}, "exclude_stores"),
    ({"exclude_stores": "amazon"}, "exclude_stores"),
    ({"exclude_stores": ["amazon", None]}, "exclude_stores"),
    ({"exclude_stores": ["amazon", "Best Buy"]}, "exclude_stores"),
    ({"exclude_stores": ["amazon", "costco"]}, "exclude_stores"),
    ({"cadence": "monthly"}, "cadence"),
    ({"cadence": None}, "cadence"),
    ({"weekday": "Tuesday"}, "weekday"),
    ({"weekday": []}, "weekday"),
    ({"confirmations_enabled": "false"}, "confirmations_enabled"),
    ({"mailing_address": 123}, "mailing_address"),
])
def test_invalid_config_rejected(tmp_path, block, match):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"newsletter": block}))
    with pytest.raises(ValueError, match=f"newsletter.{match}"):
        load_config(path)


def test_shipped_config_is_off_and_requires_address():
    cfg = load_config()
    assert not cfg.newsletter_enabled and not cfg.newsletter_confirmations_enabled
    assert cfg.newsletter_mailing_address == ""
    assert (cfg.newsletter_cadence, cfg.newsletter_weekday) == ("weekly", "tue")
    assert cfg.newsletter_exclude_stores == ["amazon", "ebay"]
    assert not AppConfig().newsletter_enabled and not AppConfig().newsletter_confirmations_enabled
    assert AppConfig().newsletter_exclude_stores == ["amazon", "ebay"]


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.hrefs, self.images = [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "href" in values:
            self.hrefs.append(values["href"])
        if tag == "img":
            self.images.append(values["src"])


def test_links_credits_thumbnails_and_missing_pages(db, cfg, monkeypatch, tmp_path):
    monkeypatch.setattr("dealradar.source_policy.sources_for_url", lambda url: [])
    cfg.support_enabled = True
    cfg.site_dir = str(tmp_path)
    page = tmp_path / "deal/written-34567890/index.html"
    page.parent.mkdir(parents=True)
    page.write_text("written")
    first = make_deal(image_url="https://wowpriced.com/images/lego.jpg")
    other = make_deal(dedupe_key="other", item="Other deal", deal_path="/deal/other-23456789/")
    missing = make_deal(dedupe_key="missing", item="No page", deal_path="")
    resolved = make_deal(dedupe_key="resolved", item="Written page", deal_path="")
    hashed = hashlib.sha256(b"resolved").hexdigest()[:8]
    db.meta_set("seo_deal_paths", json.dumps({hashed: "/deal/written-34567890/"}))
    deals = N.todays_top(db, cfg, [first, other, missing, resolved], TUESDAY.date())
    _, text, html = N.build_newsletter(deals, cfg, TUESDAY)
    unsub = WORKER + "/unsubscribe?e=a%40x.com&t=test"
    text = text.replace(N.UNSUB_PLACEHOLDER, unsub)
    html = html.replace(N.UNSUB_PLACEHOLDER, unsub.replace("&", "&amp;"))
    links = Links(html)
    for url in links.hrefs + re.findall(r"https://\S+", text):
        assert url == unsub or url.startswith("https://wowpriced.com/") and url.endswith("?ref=email")
    first_url = "https://wowpriced.com/deal/lego-set-12345678/?ref=email"
    assert links.hrefs.count(first_url) == 3  # title, thumbnail, button
    assert links.images == ["https://wowpriced.com/images/lego.jpg"]
    assert "No page" not in text and "No page" not in html
    assert "Written page" in text
    assert "via Slickdeals" in text and "via Slickdeals" in html
    assert "slickdeals.net" not in text + html


@pytest.mark.parametrize("path", ["", "//evil.example/deal/x/", "https://evil.example/deal/x/", "/deal/x/?next=https://evil.example", "/deal/x/\\evil", "/deal/x/\n"])
def test_unsafe_or_absent_page_is_skipped(cfg, path):
    assert N.eligible_deals([make_deal(deal_path=path)], cfg) == []


@pytest.mark.parametrize("sender", [N.send_daily_newsletter, N.send_confirmations])
@pytest.mark.parametrize("enabled", [False, True])
def test_address_guard_precedes_all_transports(db, cfg, monkeypatch, sender, enabled):
    cfg.newsletter_enabled = enabled
    cfg.newsletter_confirmations_enabled = enabled
    cfg.newsletter_mailing_address = "  "
    monkeypatch.setattr(N, "fetch_subscribers", forbidden)
    monkeypatch.setattr(N, "smtp_connect", forbidden)
    before = list(db.conn.iterdump())
    result = sender(db, cfg, TUESDAY)
    assert result["sent"] == 0
    assert result["skipped"] == "newsletter.mailing_address not configured"
    assert list(db.conn.iterdump()) == before


@pytest.mark.parametrize("cadence, phrase", [("daily", "the day's top 10 deals, once a day"), ("weekly", "the week's top 10 deals, every Tuesday")])
def test_support_body_as_of_disclosures_and_cadence(cfg, cadence, phrase):
    cfg.newsletter_cadence = cadence
    cfg.support_enabled = True
    subject, text, html = N.build_newsletter([make_deal()], cfg, TUESDAY)
    assert subject == f"wowpriced: {'This week\'s' if cadence == 'weekly' else 'Today\'s'} top 1 deal"
    as_of = "Prices as of 9:00 AM ET, Nov 10. Prices change fast; the deal page shows the latest check."
    assert text.startswith(as_of + "\nWe may earn a commission when you buy through links on wowpriced.com.")
    assert as_of in html
    for body in (text, html):
        assert "trial" not in body.lower() and "members" not in body.lower()
        assert phrase in unescape(body) and ADDRESS in body and cfg.ftc_disclosure in body


def test_weekly_schedule_keys_next_week_and_rerun_rows(db, cfg, monkeypatch):
    configure(monkeypatch)
    cfg.newsletter_cadence = "weekly"
    worker, factory = FakeWorker([("a@x.com", "t")]), Factory()

    def send(at, run_key=None):
        return N.send_daily_newsletter(db, cfg, at, deals=[make_deal()],
                                      http_client=worker.client(), smtp_factory=factory,
                                      run_key=run_key)

    assert send(TUESDAY - timedelta(days=1))["sent"] == 0
    assert send(TUESDAY - timedelta(hours=2))["skipped"].startswith("before 08:00")
    assert worker.calls == 0 and factory.messages == []
    assert send(TUESDAY, "week-46")["sent"] == 1
    before = list(db.conn.iterdump())
    assert send(TUESDAY, "week-46")["sent"] == 0
    assert list(db.conn.iterdump()) == before
    assert send(TUESDAY + timedelta(hours=1), "later")["sent"] == 0
    assert send(TUESDAY + timedelta(days=1))["sent"] == 0
    assert db.meta_get("newsletter:2026-W46")
    assert json.loads(db.meta_get("newsletter:pick:2026-W46"))["keys"] == ["walmart|lego set"]
    assert send(TUESDAY + timedelta(days=7), "week-47")["sent"] == 1
    assert db.meta_get("newsletter:2026-W47")
    assert len(factory.messages) == 2
    for msg in factory.messages:
        assert msg["Subject"] == "wowpriced: This week's top 1 deal"
        assert msg["List-Unsubscribe"].startswith(f"<{WORKER}/unsubscribe?")
        assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        assert "amazon" not in msg["From"].lower() and "ebay" not in msg["From"].lower()


def test_from_display_name_is_our_brand(db, cfg, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv("SMTP_FROM", "Amazon eBay Best Buy <deals@wowpriced.com>")
    worker, factory = FakeWorker([("a@x.com", "t")]), Factory()
    assert N.send_daily_newsletter(db, cfg, TUESDAY, deals=[make_deal()],
                                   http_client=worker.client(), smtp_factory=factory)["sent"] == 1
    assert factory.messages[0]["From"] == "wowpriced <deals@wowpriced.com>"


def test_iso_week_uses_iso_year(cfg):
    cfg.newsletter_cadence = "weekly"
    assert N._period(cfg, datetime(2027, 1, 1).date()) == "2026-W53"


@pytest.mark.parametrize("cadence, phrase", [("weekly", "the week's top 10 deals, every Tuesday"), ("daily", "the day's top 10 deals, once a day")])
def test_confirmation_only_launch_includes_signup_date_once(db, cfg, monkeypatch, cadence, phrase):
    configure(monkeypatch)
    cfg.newsletter_enabled = False
    cfg.newsletter_confirmations_enabled = True
    cfg.newsletter_cadence = cadence
    worker, factory = FakeOptInWorker([("a@x.com", "tok")]), Factory()

    def handler(req):
        response = worker(req)
        if req.method == "GET":
            data = response.json()
            for row in data["emails"]:
                row["ts"] = "2026-10-16T14:00:00Z"
            return httpx.Response(200, json=data)
        return response

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert N.send_confirmations(db, cfg, TUESDAY, http_client=client, smtp_factory=factory)["sent"] == 1
    assert N.send_confirmations(db, cfg, TUESDAY + timedelta(days=2), http_client=client, smtp_factory=factory)["sent"] == 0
    assert len(factory.messages) == 1
    for body in bodies(factory.messages[0]).values():
        assert "This address was entered at wowpriced.com on Oct 16, 2026." in body
        assert phrase in unescape(body) and ADDRESS in body


@pytest.mark.parametrize("stored", [False, True])
def test_cli_preview_is_read_only_and_has_no_transports(db, cfg, tmp_path, monkeypatch, stored):
    cfg.newsletter_cadence = "weekly"
    deals = [make_deal(dedupe_key="first", item="First choice"),
             make_deal(dedupe_key="second", item="Second choice")]
    # Friday's preview chooses next Tuesday's ISO-week issue.
    now = TUESDAY - timedelta(days=4)
    cfg.newsletter_top_n = 1
    if stored:
        db.meta_set("newsletter:pick:2026-W46", json.dumps({"keys": ["second"], "by": "model"}))
    monkeypatch.setattr(rank, "published_deals", lambda *args: deals)
    monkeypatch.setattr(cli, "cmd_build_extension", forbidden)
    monkeypatch.setattr("dealradar.config.load_config", lambda *args: cfg)
    monkeypatch.setattr(util, "now_utc", lambda: now)
    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(N.smtplib, "SMTP", forbidden)
    monkeypatch.setattr(N.smtplib, "SMTP_SSL", forbidden)
    monkeypatch.setattr(N, "_ask_model", forbidden)
    monkeypatch.setattr(N, "fetch_subscribers", forbidden)
    before = list(db.conn.iterdump())
    output = tmp_path / "preview"
    assert cli.main(["newsletter-preview", "--db", db.path, "--out", str(output)]) == 0
    for suffix in ("html", "txt"):
        body = (output / f"newsletter-preview.{suffix}").read_text()
        assert ("Second choice" if stored else "First choice") in body
        assert ("First choice" if stored else "Second choice") not in body
        assert N.UNSUB_PLACEHOLDER in body
    assert list(db.conn.iterdump()) == before
