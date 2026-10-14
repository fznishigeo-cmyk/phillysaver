"""Offline checks for source grants, attribution and channel policies."""

import dataclasses
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import pytest
import yaml

from dealradar import dedupe, pipeline, verify
from dealradar.config import SECRET_NAMES, load_config, load_sources
from dealradar.db import DB
from dealradar.fetch.base import SourceAdapter, run_source
from dealradar.fetch.meh import MehAdapter
from dealradar.fetch.rss import RssAdapter, _permission_host
from dealradar.http_client import PoliteFetcher, resolve_redirects
from dealradar.models import Evidence, ExtractedDeal, RawPost
from dealradar.parse.extractor import verify_evidence
from dealradar.publish import email_digest, members, newsletter, pinterest, seo, site, social
from dealradar.savings import compute_savings
from dealradar.util import iso
from tests.test_pinterest import candidate, published
from tests.test_publish_site import make_deal
from tests.test_workflows import RESTORE, _git, repo, run_script  # local-only git operations

NOW = datetime(2026, 10, 13, 14, tzinfo=timezone.utc)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for name in SECRET_NAMES + ["STATE_DB_KEY", "GITHUB_STEP_SUMMARY"]:
        monkeypatch.delenv(name, raising=False)
    config = load_config()
    config.site_dir = str(tmp_path / "site")
    config.feature_flags["keepa"] = False
    config.non_amazon_freshness_enabled = False  # days-old fixtures; the age gate has its own tests
    return config


@pytest.fixture
def db(tmp_path):
    database = DB(tmp_path / "state.db")
    yield database
    database.close()


def source(name):
    primary, backup = load_sources()
    return next(s for s in primary + backup if s.name == name)


def seed(db, cfg, url, name="hip2save"):
    post = RawPost(name, "widget", source(name).site + "widget/", "Acme widget",
                   "Acme widget: regular $100.00, now $30.00.", NOW, NOW)
    ext = ExtractedDeal(item=post.title, store="Amazon", source_name=name,
                        source_url=post.url, product_url=url, link_as_posted=url,
                        online_only="true", category="home", regular_price=100.0,
                        final_price=30.0, evidence=Evidence(
                            regular_price_quote="regular $100.00",
                            final_price_quote="now $30.00"))
    assert verify_evidence(ext, post.text)[0]
    result = dedupe.upsert_extracted(db, ext, compute_savings(ext), post, cfg, NOW)
    return db.get_deal(result["dedupe_key"])


@pytest.mark.parametrize("name,link", [
    ("dealsofamerica", "https://cc.dealsofamerica.com/v1/otc/item?affid=source"),
    ("dealmoon_us", "https://go.dealmoon.com/a?affid=source"),
    ("southernsavers", "https://links.southernsavers.com/a?affid=source"),
])
def test_ingest_keeps_source_tracker(db, cfg, name, link):
    row = seed(db, cfg, link, name)
    post = RawPost(name, "other", source(name).site + "widget/", row["item"],
                   "regular $100.00, now $30.00.", NOW, NOW)
    ext = ExtractedDeal(item=row["item"], store="Amazon", source_name=name,
                        source_url=post.url, product_url=link)
    fetcher = PoliteFetcher(db, cfg, sleep_fn=lambda _: None,
        transport=httpx.MockTransport(lambda req: httpx.Response(302, headers={
            "Location": "https://www.amazon.com/dp/B012345678?tag=source-20"})))
    try:
        pipeline._resolve_product_urls([(post, ext)], fetcher, cfg, time.monotonic() + 180)
        assert ext.product_url == link
        pipeline._resolve_short_links(db, fetcher, None, cfg)
        assert db.get_deal(row["dedupe_key"])["canonical_url"] == link
    finally:
        fetcher.close()


@pytest.mark.parametrize("steps", [False, True])
def test_source_tracker_survives_public_and_member_overrides(db, cfg, steps):
    link = "https://go.dealmoon.com/item?affid=source&tag=source-20"
    cfg.affiliate_link_overrides = {"go.dealmoon.com/item": "https://sovrn.co/ours"}
    deal = make_deal(canonical_url=link, link_as_posted=link,
                     codes=["SAVE"] if steps else [],
                     sightings=[{"source_name": "dealmoon_us", "source_url": source("dealmoon_us").site + "widget/"}])
    result = site.render_site(db, cfg, NOW, deals=[deal])
    public = json.loads((Path(cfg.site_dir) / "deals.json").read_text())
    for payload in (public, result["full_payload"]):
        assert payload["deals"][0]["url"] == link
        assert payload["deals"][0]["earns"] is False
    assert not social._candidates(db, cfg, {})
    assert site.rewrite_affiliate(link, cfg.affiliate_amazon_tag,
        sovrn_key=cfg.affiliate_sovrn_key, sovrn_enabled=True,
        competitor_tracker_hosts=cfg.affiliate_competitor_tracker_hosts) == link


@pytest.mark.parametrize("host", ["hip2save.com", "cdn.hip2save.com", "thefreebieguy.com"])
def test_social_skips_source_hosted_images(db, cfg, host):
    published(db, cfg, [candidate("credit", 1, image_url=f"https://{host}/widget.jpg",
        credits=[{"name": "Hip2Save", "url": "https://hip2save.com/widget/"}])])
    assert not social._candidates(db, cfg, {})


@pytest.mark.parametrize("channel", ["newsletter", "member", "digest"])
def test_email_retains_every_credit_and_disclosure(cfg, channel):
    d = make_deal(codes=[], sightings=[
        {"source_name": "independent", "source_url": "https://example.org/widget"},
        {"source_name": "hip2save", "source_url": "https://hip2save.com/widget/"}])
    parts = email_parts([d], cfg, channel)
    for body in parts[1:]:
        assert "https://hip2save.com/widget/" in body
        assert "https://example.org/widget" in body
        assert cfg.ftc_disclosure in body
    assert 'href="https://hip2save.com/widget/"' in parts[2]
    assert 'href="https://example.org/widget"' in parts[2]


def email_parts(deals, cfg, channel):
    if channel == "newsletter":
        return newsletter.build_newsletter(deals, cfg, NOW)
    if channel == "member":
        return members.build_member_email(deals, cfg)
    return email_digest.build_digest(deals, cfg, NOW)


@pytest.mark.parametrize("channel", ["newsletter", "member", "digest"])
def test_amazon_email_has_no_price_or_tag(cfg, channel):
    d = make_deal(store="Amazon", item="Acme widget now $30.00", final_price=30.0,
                  codes=[], canonical_url="https://www.amazon.com/dp/B012345678?tag=source-20")
    parts = email_parts([d], cfg, channel)
    for body in parts[1:]:
        assert "$30.00" not in body
        assert "$50.00" not in body
        assert "tag=source-20" not in body


@pytest.mark.parametrize("channel", ["newsletter", "member", "digest"])
def test_email_keeps_source_tracker_exactly(cfg, channel):
    link = "https://cc.dealsofamerica.com/click?url=" + quote(
        "https://www.amazon.com/dp/B012345678?tag=source-20", safe="")
    d = make_deal(store="Amazon", codes=[], canonical_url=link,
                  sightings=[{"source_name": "dealsofamerica", "source_url": "https://www.dealsofamerica.com/widget/"}])
    parts = email_parts([d], cfg, channel)
    assert link in parts[1]
    assert "$10.00" not in parts[1]


def test_gone_card_keeps_credit_link():
    d = site._serialize_deal(make_deal(status="dead", death="confirmed", died_at=iso(NOW)), NOW)
    html = seo.card_html(d, now=NOW)
    assert 'href="https://hip2save.com/p/2"' in html
    assert "Get deal" not in html


@pytest.mark.parametrize("redirect", [False, True])
def test_photo_pass_rejects_ungranted_source_image(db, cfg, redirect):
    link = "https://thefreebieguy.com/widget-deal/"
    row = seed(db, cfg, "https://walmart.com/ip/12345" if redirect else link, "the_freebie_guy")
    class Fetcher:
        def get(self, url, **kwargs):
            return SimpleNamespace(status=200, blocked_by_robots=False, final_url=link,
                body=b'<meta property="og:image" content="https://thefreebieguy.com/widget.jpg">')
    pipeline._backfill_product_images(db, Fetcher(), None, cfg)
    assert not db.get_deal(row["dedupe_key"]).get("image_url")


def test_publisher_rechecks_source_image_grant(db, cfg):
    result = site.render_site(db, cfg, NOW, deals=[make_deal(
        image_url="https://thefreebieguy.com/widget.jpg", codes=[],
        sightings=[{"source_name": "the_freebie_guy", "source_url": "https://thefreebieguy.com/widget/"}])])
    assert not result["full_payload"]["deals"][0]["image_url"]


def test_discovery_does_not_expand_grant_host():
    moved = dataclasses.replace(source("hip2save"), url="https://external.example/feed/")
    assert _permission_host(moved) in (None, "hip2save.com")


def test_off_host_feed_uses_its_own_robots(db, cfg):
    moved = dataclasses.replace(source("hip2save"), url="https://external.example/feed/")
    requests = []
    def handle(req):
        requests.append(req)
        return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    f = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handle), sleep_fn=lambda _: None)
    try:
        with pytest.raises(RuntimeError, match="blocked_by_robots"):
            RssAdapter().fetch_posts(moved, f, db, cfg, NOW, None)
        assert [(r.url.host, r.url.path) for r in requests] == [("external.example", "/robots.txt")]
    finally:
        f.close()


def test_conditional_grant_limit_warns_at_startup(cfg, tmp_path, caplog):
    raw = dict(cfg.raw)
    raw["growth"] = {**raw["growth"], "monthly_price_usd": 19.99}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    with caplog.at_level(logging.WARNING):
        loaded = load_config(path)
    assert loaded.growth_monthly_price_usd == 19.99
    assert "source grants" in caplog.text and "disabled" in caplog.text


def test_conditional_source_images_follow_price_and_credit(db, cfg):
    src = source("couponztime")
    deal = make_deal(image_url=src.site + "widget.jpg", codes=[],
                     sightings=[{"source_name": src.name, "source_url": src.site + "widget/"}])
    cfg.growth_monthly_price_usd = 19.98
    assert site.render_site(db, cfg, NOW, deals=[deal])["full_payload"]["deals"][0]["image_url"]
    cfg.growth_monthly_price_usd = 19.99
    assert not site.render_site(db, cfg, NOW, deals=[deal])["full_payload"]["deals"][0]["image_url"]
    cfg.growth_monthly_price_usd = 9.99
    deal["sightings"] = []
    assert not site.render_site(db, cfg, NOW, deals=[deal])["full_payload"]["deals"][0]["image_url"]


@pytest.mark.parametrize("monthly", [19.99, 20.0])
def test_conditional_grant_falls_back_to_robots(db, cfg, monthly):
    cfg.growth_monthly_price_usd = monthly
    requests = []
    def handle(req):
        requests.append(req.url.path)
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        return httpx.Response(200, content=b'<rss><channel><item><title>Widget</title></item></channel></rss>')
    f = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handle), sleep_fn=lambda _: None)
    try:
        with pytest.raises(RuntimeError, match="blocked_by_robots"):
            RssAdapter().fetch_posts(source("couponztime"), f, db, cfg, NOW, None)
        assert requests == ["/robots.txt"]
    finally:
        f.close()


@pytest.mark.parametrize("operation", ["head", "resolve"])
def test_metadata_requests_honor_robots(db, cfg, operation):
    requests = []
    def handle(req):
        requests.append((req.method, req.url.path))
        return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    f = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handle), sleep_fn=lambda _: None)
    try:
        if operation == "head":
            f.head("https://blocked.example/private")
        else:
            resolve_redirects(f, "https://blocked.example/private")
        assert requests == [("GET", "/robots.txt")]
    finally:
        f.close()


@pytest.mark.parametrize("status", [429, 500, 503])
def test_robots_error_does_not_allow_content(db, cfg, status):
    requests = []
    def handle(req):
        requests.append(req.url.path)
        return httpx.Response(status, headers={"Retry-After": "30"})
    f = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handle), sleep_fn=lambda _: None)
    try:
        assert f.get("https://busy.example/article").blocked_by_robots
        assert f.get("https://busy.example/other").blocked_by_robots
        assert requests == ["/robots.txt"]
    finally:
        f.close()


def test_meh_public_feed_honors_robots(db, cfg):
    requests = []
    def handle(req):
        requests.append(req.url.path)
        return (httpx.Response(200, text="User-agent: *\nDisallow: /\n")
                if req.url.path == "/robots.txt" else httpx.Response(200, json={"items": []}))
    transport = httpx.MockTransport(handle)
    f = PoliteFetcher(db, cfg, transport=transport, sleep_fn=lambda _: None)
    try:
        with httpx.Client(transport=transport) as client:
            with pytest.raises((RuntimeError, httpx.HTTPError)):
                MehAdapter(http_client=client).fetch_posts(source("meh"), f, db, cfg, NOW, None)
        assert requests == ["/robots.txt"]
    finally:
        f.close()


def test_pinterest_alt_text_has_no_price():
    payload = pinterest.pin_payload(candidate("price", 1, item="Acme widget now $9.99"),
                                    "/deal/widget-12345678/", "board", "#ad")
    assert "$9.99" not in payload["alt_text"]


@pytest.mark.parametrize("sentinel", ["synthetic-credential", "synthetic+credential/value"])
def test_source_failure_logs_type_only(db, cfg, caplog, sentinel):
    class FailingAdapter(SourceAdapter):
        def fetch_posts(self, *args):
            req = httpx.Request("GET", "https://api.example/feed?key=" + quote(sentinel, safe=""))
            resp = httpx.Response(403, request=req)
            resp.raise_for_status()
    with caplog.at_level(logging.WARNING):
        run_source(source("meh"), FailingAdapter(), None, db, cfg, NOW)
    assert "HTTPStatusError" in caplog.text
    assert sentinel not in caplog.text
    assert quote(sentinel, safe="") not in caplog.text
    assert "Traceback" not in caplog.text


def test_bootstrap_requires_state_key(repo):
    db = DB(repo / "state.db")
    db.meta_set("pinterest_auth", json.dumps({"access_token": "synthetic-credential"}))
    db.close()
    assert run_script(repo, key=None).returncode != 0


@pytest.mark.parametrize("script", [None, RESTORE])
def test_state_scripts_fail_on_unreachable_origin(repo, script):
    (repo / "state.db").write_bytes(b"local state stays intact")
    _git(repo, "remote", "set-url", "origin", str(repo / "missing-origin.git"))
    args = {} if script is None else {"script": script}
    assert run_script(repo, key="synthetic-encryption-key", **args).returncode != 0
    assert (repo / "state.db").read_bytes() == b"local state stays intact"
    assert not (repo / ".state-worktree").exists()


def test_restore_rejects_data_branch_without_state(repo):
    _git(repo, "push", "origin", "main:data")
    assert run_script(repo, RESTORE, key="synthetic-encryption-key").returncode != 0
    assert not (repo / "state.db").exists()
