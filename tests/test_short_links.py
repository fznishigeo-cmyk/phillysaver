"""Offline short-link photo repair through the polite redirect resolver."""
import time
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from dealradar import pipeline, util
from dealradar.config import AppConfig
from dealradar.db import DB
from dealradar.errors import DeadlineExceeded
from dealradar.http_client import PoliteFetcher
from dealradar.publish.site import _serialize_deal
from tests.test_pipeline_sources import TestProductImageBackfill as PhotoFixture
from tests.test_pipeline_sources import NOW as PHOTO_NOW
from tests.test_sovrn_links import KEY


@pytest.fixture
def setup(tmp_path):
    db = DB(tmp_path / "state.db")
    # The repair mechanism (off in config.yaml: permitted sources keep their
    # tracker as affiliate credit) is exercised here with the switch on.
    cfg = AppConfig(affiliate_repair_competitor_trackers=True)
    calls = []
    targets = {}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        calls.append(str(request.url))
        target = targets.get(str(request.url))
        return httpx.Response(301, headers={"Location": target}) if target else httpx.Response(200)

    fetcher = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handler),
                            sleep_fn=lambda _: None)
    yield db, cfg, fetcher, calls, targets
    fetcher.close()
    db.close()


@pytest.mark.parametrize("link,target,asin", [
    ("https://on.ltk.com/+abc", "https://www.walmart.com/ip/123", ""),
    ("https://amzn.to/abc", "https://www.amazon.com/dp/B0GLP6BWD9", "B0GLP6BWD9"),
])
def test_resolves_preserving_identity_and_resetting_photos(setup, link, target, asin):
    db, cfg, fetcher, calls, targets = setup
    targets[link] = target
    PhotoFixture._seed(db, "original-key", link,
                       flags=["product_image_tried", "image_backfill_tried", "unknown_shipping"])
    assert pipeline._resolve_short_links(db, fetcher, None, cfg) == 1
    row = db.get_deal("original-key")
    assert row["canonical_url"] == target
    assert row["dedupe_key"] == "original-key"
    assert row["asin"] == asin
    assert row["flags"] == ["unknown_shipping", "link_resolve_tried"]
    import json
    payload = json.loads(db.conn.execute("SELECT payload FROM deals").fetchone()[0])
    assert payload["canonical_url"] == target
    assert pipeline._resolve_short_links(db, fetcher, None, cfg) == 0


def test_bot_wall_keeps_link_and_is_never_fetched(setup):
    db, cfg, fetcher, calls, targets = setup
    link = "https://goto.target.com/abc"
    wall = "https://www.walmart.com/blocked"
    targets[link] = wall
    PhotoFixture._seed(db, "wall", link)
    assert pipeline._resolve_short_links(db, fetcher, None, cfg) == 0
    assert calls == [link]
    assert db.get_deal("wall")["canonical_url"] == link
    assert db.get_deal("wall")["flags"] == ["link_resolve_tried"]


def test_real_short_link_asin_replaces_title_match_photo(setup):
    db, cfg, fetcher, calls, targets = setup
    link = "https://amzn.to/kettle"
    targets[link] = "https://www.amazon.com/dp/B0GLP6BWD9"
    PhotoFixture._seed(db, "kettle", link, image="https://images.example/guessed.jpg",
                       flags=["image_backfill_tried", "product_image_tried"])
    db.update_deal("kettle", payload=dict(db.get_deal("kettle"), asin="",
        photo_asin="B012345678", asin_source="title_match"))
    assert pipeline._resolve_short_links(db, fetcher, None, cfg) == 1
    row = db.get_deal("kettle")
    assert row["asin"] == "B0GLP6BWD9" and row["asin_source"] == "link"
    assert not row["photo_asin"] and not row["image_url"]
    assert row["canonical_url"] == targets[link]
    assert row["flags"] == ["link_resolve_tried"]
    assert calls == [link]


def test_ineligible_deals_are_never_fetched(setup):
    db, cfg, fetcher, calls, targets = setup
    PhotoFixture._seed(db, "photo", "https://a.co/photo", image="https://images.example/x.jpg")
    PhotoFixture._seed(db, "tried", "https://a.co/tried", flags=["link_resolve_tried"])
    PhotoFixture._seed(db, "other", "https://sub.on.ltk.com/other")
    PhotoFixture._seed(db, "shared", "https://a.co/shared")
    db.update_deal("shared", payload={"link_shared": True})
    PhotoFixture._seed(db, "old", "https://a.co/old",
                       died_at=util.iso(util.now_utc() - timedelta(days=5)))
    assert pipeline._resolve_short_links(db, fetcher, None, cfg) == 0
    assert calls == []


def test_cap_and_one_attempt_per_deal(setup):
    db, cfg, fetcher, calls, targets = setup
    for i in range(65):
        PhotoFixture._seed(db, str(i), f"https://a.co/{i}")
    assert pipeline._resolve_short_links(db, fetcher, None, cfg) == 0
    assert len(calls) == 60
    assert sum("link_resolve_tried" in d["flags"] for d in db.all_deals()) == 60
    pipeline._resolve_short_links(db, fetcher, None, cfg)
    assert len(calls) == 65 and len(set(calls)) == 65
    pipeline._resolve_short_links(db, fetcher, None, cfg)
    assert len(calls) == 65


def credit(db, key, source_url):
    db.add_sighting(db.get_deal(key)["id"], source_name="DealNews",
                    source_url=source_url, content_hash=key,
                    seen_at=util.now_utc(), final_price=9.99)


def test_credited_deal_site_with_steps_resolves_to_amazon(setup):
    db, cfg, fetcher, calls, targets = setup
    link = "https://click.dealnews.com/click/123"
    target = "https://www.amazon.com/dp/B0GLP6BWD9"
    targets[link] = target
    PhotoFixture._seed(db, "credited", link,
                       flags=["product_image_tried", "image_backfill_tried"])
    db.update_deal("credited", payload={"conditions": ["Use code SAVE"]})
    credit(db, "credited", "https://www.dealnews.com/post/123")
    assert pipeline._resolve_short_links(db, fetcher, None, cfg) == 1
    row = db.get_deal("credited")
    assert row["canonical_url"] == target
    assert row["asin"] == "B0GLP6BWD9"
    assert row["conditions"] == ["Use code SAVE"]
    assert row["flags"] == ["link_resolve_tried"]
    assert calls == [link]  # Amazon ASIN stops the redirect chain before a fetch.


@pytest.mark.parametrize("host", pipeline._SHORT_LINK_HOSTS[4:])
def test_added_redirect_host_resolves_to_retailer(setup, host):
    db, cfg, fetcher, calls, targets = setup
    link = f"https://{host}/click/123"
    target = "https://www.macys.com/shop/product/widget?ID=123"
    targets[link] = target
    PhotoFixture._seed(db, "tracker", link, flags=["product_image_tried"])
    assert pipeline._resolve_short_links(db, fetcher, None, cfg) == 1
    row = db.get_deal("tracker")
    assert row["canonical_url"] == target
    assert row["flags"] == ["link_resolve_tried"]


def test_retailers_and_initial_bot_wall_never_fetched_even_if_credited(setup):
    db, cfg, fetcher, calls, targets = setup
    for key, url in [
        ("walmart", "https://www.walmart.com/ip/123"),
        ("amazon", "https://www.amazon.com/dp/B0GLP6BWD9"),
        ("target", "https://www.target.com/p/widget/-/A-123"),
        ("wall", "https://www.walmart.com/blocked"),
        ("captcha", "https://www.dealnews.com/px/captcha"),
        ("uncredited", "https://dealnews.com/click/123"),
    ]:
        PhotoFixture._seed(db, key, url)
        if key != "uncredited":
            credit(db, key, url)
    assert pipeline._resolve_short_links(db, fetcher, None, cfg) == 0
    assert calls == []
    assert all("link_resolve_tried" not in d["flags"] for d in db.all_deals())


def test_robots_blocked_host_is_tried_once(setup):
    db, cfg, old_fetcher, calls, targets = setup
    requests = []

    def handler(request):
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        assert request.method == "HEAD"
        return httpx.Response(405)

    PhotoFixture._seed(db, "blocked", "https://zdcs.link/click/123")
    fetcher = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handler),
                            sleep_fn=lambda _: None)
    try:
        stats = {}
        assert pipeline._resolve_short_links(db, fetcher, None, cfg, stats=stats) == 0
        assert stats == {"resolved": 0, "tried": 1, "trackers_resolved": 0}
        assert db.get_deal("blocked")["flags"] == ["link_resolve_tried"]
        assert pipeline._resolve_short_links(db, fetcher, None, cfg) == 0
    finally:
        fetcher.close()
    assert requests == ["https://zdcs.link/robots.txt"]


def test_newest_first(setup, monkeypatch):
    db, cfg, fetcher, calls, targets = setup
    for key, age in [("middle", 1), ("oldest", 2), ("newest", 0)]:
        PhotoFixture._seed(db, key, f"https://a.co/{key}")
        db.conn.execute("UPDATE deals SET first_seen_at=? WHERE dedupe_key=?",
                        (util.iso(util.now_utc() - timedelta(days=age)), key))
    db.conn.commit()
    monkeypatch.setattr(pipeline, "_SHORT_LINK_PER_RUN", 2)
    pipeline._resolve_short_links(db, fetcher, None, cfg)
    assert calls == ["https://a.co/newest", "https://a.co/middle"]
    assert db.get_deal("oldest")["flags"] == []


@pytest.mark.parametrize("link,source,eligible", [
    ("https://www.dealnews.com/lw/click.html?x=1", "https://dealnews.com/post", True),
    ("https://go.slickdeals.net/x", "https://slickdeals.net/f/123", True),
    ("https://shop.retailer.com/x", "https://dealnews.com/post", False),
    ("https://dealnews.com.evil.com/x", "https://dealnews.com/post", False),
])
def test_source_domains(setup, link, source, eligible):
    db, cfg, fetcher, calls, targets = setup
    PhotoFixture._seed(db, "domain", link)
    credit(db, "domain", source)
    pipeline._resolve_short_links(db, fetcher, None, cfg)
    assert bool(calls) == eligible


def test_photo_gap_diagnostics_count_alive_only_and_top_ten(setup):
    db, cfg, fetcher, calls, targets = setup
    for i in range(12):
        PhotoFixture._seed(db, f"secret-item-{i}", f"https://host{i}.com/private?token=secret")
    PhotoFixture._seed(db, "extra", "https://host0.com/another-private")
    PhotoFixture._seed(db, "photo", "https://with-photo.com/private", image="https://images.example/x.jpg")
    PhotoFixture._seed(db, "dead", "https://dead.com/private",
                       died_at=util.iso(util.now_utc()))
    gaps, lines = pipeline._photo_gap_summary(db.all_deals(status="alive"),
                                              {"resolved": 2, "tried": 3})
    assert gaps["alive_without_photo"] == 13
    assert len(gaps["by_canonical_host"]) == 10
    assert gaps["by_canonical_host"]["host0.com"] == 2
    assert lines.startswith("photo gaps: 13 alive deals without a photo; by canonical host: host0.com: 2")
    assert "redirects: resolved 2 of 3" in lines
    assert all(text not in lines for text in
               ("secret", "private", "https://", "dead.com", "with-photo.com"))


def test_deadline_leaves_deal_unflagged(setup, monkeypatch):
    db, cfg, fetcher, calls, targets = setup
    PhotoFixture._seed(db, "pending", "https://a.co/pending")
    pipeline._resolve_short_links(db, fetcher, time.monotonic() - 1, cfg)
    assert calls == []

    def timeout(*args, **kwargs):
        raise DeadlineExceeded("out of time")

    monkeypatch.setattr("dealradar.http_client.resolve_redirects", timeout)
    assert pipeline._resolve_short_links(db, fetcher, None, cfg) == 0
    assert db.get_deal("pending")["flags"] == []


@pytest.mark.parametrize("host,trackers_resolved", [("on.ltk.com", 0), ("cc.dealsofamerica.com", 1)])
def test_pipeline_fast_lane_and_rerun(tmp_path, monkeypatch, caplog, host, trackers_resolved):
    monkeypatch.setattr(util, "now_utc", lambda: PHOTO_NOW)
    step_summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "SMTP_HOST", "DEALS_CLAUDE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SOVRN_API_KEY", KEY)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text((Path(__file__).resolve().parents[1] / "config.yaml").read_text().replace(
        'site_dir: "site"', f'site_dir: "{tmp_path / "site"}"').replace(
        "repair_competitor_trackers: false", "repair_competitor_trackers: true"))
    db_path = tmp_path / "state.db"
    db = DB(db_path)
    PhotoFixture._seed(db, "stable", f"https://{host}/abc")
    db.close()
    calls = []
    retailer_reads = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        calls.append(str(request.url))
        if request.url.host == "www.walmart.com":
            retailer_reads.append(request.method)
        if request.url.host == host:
            return httpx.Response(301, headers={"Location": "https://www.walmart.com/ip/123"})
        if request.url.host == "images.example":
            assert request.method == "HEAD"
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"})
        return httpx.Response(200, text='<meta property="og:image" content="https://images.example/product.jpg">')

    def run(fast):
        return pipeline.run(fast_lane=fast, db_path=str(db_path), config_path=str(cfg_path),
                            transport=httpx.MockTransport(handler), sources_override=[],
                            skip_validation=True, dry_run_telegram=True)

    assert run(False)["short_links"] == 0
    assert calls == []
    with caplog.at_level("INFO", logger="dealradar.pipeline"):
        first = run(True)
    assert first["short_links"] == 1 and first["product_images"] == 0
    assert first["redirects"] == {"resolved": 1, "tried": 1, "trackers_resolved": trackers_resolved}
    assert first["link_mix"]["trackers_resolved"] == trackers_resolved
    assert first["link_mix"]["earning"] == 1 and first["link_mix"]["not_earning"] == 0
    assert any(r.message == f"link mix: earning 1; not earning 0; trackers resolved {trackers_resolved}"
               for r in caplog.records)
    assert first["photo_gaps"]["alive_without_photo"] == 1
    assert "photo gaps: 1 alive deals without a photo; by canonical host: www.walmart.com: 1" in step_summary.read_text()
    assert "redirects: resolved 1 of 1" in step_summary.read_text()
    before = list(calls)
    rerun = run(True)
    assert rerun["short_links"] == rerun["link_mix"]["trackers_resolved"] == 0
    # Retailer photo pages are no longer fetched without a permitted source/API.
    assert calls == before
    before = list(calls)
    assert run(True)["short_links"] == 0
    assert calls == before  # checked URLs wait 24 h, including on reruns
    db = DB(db_path)
    assert len(db.all_deals()) == 1
    assert not db.get_deal("stable").get("image_url")
    assert "GET" not in retailer_reads  # redirect HEAD metadata is allowed
    db.close()


def test_only_the_given_deals_are_considered(setup):
    """run() passes the site's deals: the cap goes to deals visitors see."""
    db, cfg, fetcher, calls, targets = setup
    PhotoFixture._seed(db, "shown", "https://a.co/shown")
    PhotoFixture._seed(db, "hidden", "https://a.co/hidden")
    pipeline._resolve_short_links(db, fetcher, None, cfg,
                                  deals=[db.get_deal("shown")])
    assert calls == ["https://a.co/shown"]


def test_a_www_tracker_host_is_followed(setup):
    """Live: www.creator.anchor.store links were never followed."""
    db, cfg, fetcher, calls, targets = setup
    PhotoFixture._seed(db, "www", "https://www.creator.anchor.store/x")
    pipeline._resolve_short_links(db, fetcher, None, cfg)
    assert calls == ["https://www.creator.anchor.store/x"]


@pytest.mark.parametrize("host", ["cc.dealsofamerica.com", "go.dealmoon.com", "slickdeals.net",
                                  "links.southernsavers.com", "custom-tracker.example"])
@pytest.mark.parametrize("target", ["https://www.macys.com/shop/product/widget?ID=123",
                                    "https://www.amazon.com/dp/B0GLP6BWD9"])
def test_competitor_with_photo_resolves_to_earning_store_and_keeps_credit(setup, host, target):
    db, cfg, fetcher, calls, targets = setup
    cfg.affiliate_competitor_tracker_hosts = [host]
    link = f"https://{host}/click/123"
    targets[link] = target
    image = "https://images.example/widget.jpg"
    PhotoFixture._seed(db, "competitor", link, image=image)
    credit(db, "competitor", "https://source.example/original-post")
    before_credit = db.sightings_for(db.get_deal("competitor")["id"])
    stats = {}
    assert pipeline._resolve_short_links(db, fetcher, None, cfg, stats=stats) == 1
    row = db.get_deal("competitor")
    assert row["canonical_url"] == target and row["image_url"] == image
    assert row["flags"] == ["link_resolve_tried"]
    assert db.sightings_for(row["id"]) == before_credit
    out = _serialize_deal(row, util.now_utc(), "our-store-20", sovrn_key=KEY,
                          sovrn_enabled=True, competitor_tracker_hosts=[host])
    assert out["earns"] is True
    if "macys" in target:
        assert out["url"].startswith("https://redirect.viglink.com?")
    else:
        assert out["url"] == target + "?tag=our-store-20"
    assert stats == {"resolved": 1, "tried": 1, "trackers_resolved": 1}
    assert pipeline._resolve_short_links(db, fetcher, None, cfg) == 0


@pytest.mark.parametrize("intermediate", [False, True])
def test_competitor_robots_disallow_blocks_head_and_get_and_flags_once(setup, intermediate):
    db, cfg, _fetcher, _calls, _targets = setup
    cfg.affiliate_competitor_tracker_hosts = ["tracker.example", "blocked.example"]
    link = "https://tracker.example/click?utm_source=original"
    PhotoFixture._seed(db, "blocked", link, image="https://images.example/widget.jpg")
    requests = []

    def handler(request):
        requests.append((request.method, str(request.url)))
        if request.url.path == "/robots.txt":
            blocked = request.url.host == "blocked.example" or not intermediate
            return httpx.Response(200, text="User-agent: *\n" + ("Disallow: /\n" if blocked else "Allow: /\n"))
        assert intermediate and request.method == "HEAD" and request.url.host == "tracker.example"
        return httpx.Response(301, headers={"Location": "https://blocked.example/next"})

    fetcher = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handler), sleep_fn=lambda _: None)
    try:
        stats = {}
        assert pipeline._resolve_short_links(db, fetcher, None, cfg, stats=stats) == 0
        assert db.get_deal("blocked")["canonical_url"] == link
        assert db.get_deal("blocked")["flags"] == ["link_resolve_tried"]
        before = requests.copy()
        assert pipeline._resolve_short_links(db, fetcher, None, cfg) == 0
        assert requests == before
        assert stats == {"resolved": 0, "tried": 1, "trackers_resolved": 0}
    finally:
        fetcher.close()
    assert requests == ([("GET", "https://tracker.example/robots.txt"),
                         ("HEAD", link), ("GET", "https://blocked.example/robots.txt")]
                        if intermediate else [("GET", "https://tracker.example/robots.txt")])


@pytest.mark.parametrize("target", ["https://go.dealmoon.com/click/456", "https://a.co/short",
                                    "https://slickdeals.net/f/123", "https://hip2save.com/post",
                                    "https://www.walmart.com/blocked", "https://custom-blog.example/post"])
def test_competitor_unresolved_or_source_target_stays_original(setup, target):
    db, cfg, fetcher, calls, targets = setup
    cfg.affiliate_competitor_tracker_hosts = ["cc.dealsofamerica.com", "go.dealmoon.com", "slickdeals.net"]
    link = "https://cc.dealsofamerica.com/click/123"
    targets[link] = target
    PhotoFixture._seed(db, "original", link)
    credit(db, "original", "https://custom-blog.example/original")
    assert pipeline._resolve_short_links(db, fetcher, None, cfg) == 0
    assert db.get_deal("original")["canonical_url"] == link
    assert db.get_deal("original")["flags"] == ["link_resolve_tried"]
    assert not any("/blocked" in url for url in calls)


def test_competitors_share_cap_with_short_links_and_respect_deadline(setup, monkeypatch):
    db, cfg, fetcher, calls, targets = setup
    cfg.affiliate_competitor_tracker_hosts = ["custom-tracker.example"]
    for key, host, photo in [("newest", "custom-tracker.example", "https://images.example/p.jpg"),
                             ("middle", "a.co", ""), ("oldest", "custom-tracker.example", "")]:
        PhotoFixture._seed(db, key, f"https://{host}/{key}", image=photo)
    db.update_deal("newest", payload=dict(db.get_deal("newest"), link_shared=True))
    monkeypatch.setattr(pipeline, "_SHORT_LINK_PER_RUN", 2)
    assert pipeline._resolve_short_links(db, fetcher, time.monotonic() - 1, cfg) == 0
    assert calls == [] and all("link_resolve_tried" not in row["flags"] for row in db.all_deals())
    assert pipeline._resolve_short_links(db, fetcher, None, cfg) == 0
    assert len(calls) == 2
    assert sum("link_resolve_tried" in row["flags"] for row in db.all_deals()) == 2
    pipeline._resolve_short_links(db, fetcher, None, cfg)
    assert len(calls) == 3 and len(set(calls)) == 3
    pipeline._resolve_short_links(db, fetcher, None, cfg)
    assert len(calls) == 3


def test_production_config_never_repairs_a_permitted_sources_tracker(tmp_path):
    """Deals of America, DealMoon and Southern Savers keep their affiliate credit
    (their crawl-permission condition): their tracker link is never replaced."""
    from dealradar.config import load_config
    cfg = load_config()
    assert cfg.affiliate_repair_competitor_trackers is False
    assert {"cc.dealsofamerica.com", "go.dealmoon.com", "links.southernsavers.com"} <= set(
        cfg.affiliate_competitor_tracker_hosts)
    db = DB(tmp_path / "state.db")
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(301, headers={"Location": "https://www.walmart.com/ip/1"})

    fetcher = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handler), sleep_fn=lambda _: None)
    link = "https://cc.dealsofamerica.com/click/9"
    PhotoFixture._seed(db, "doa", link, image="https://images.example/a.jpg")
    assert pipeline._resolve_short_links(db, fetcher, None, cfg) == 0
    assert calls == [] and db.get_deal("doa")["canonical_url"] == link
    fetcher.close()
    db.close()


def _replaced(db, key, posted, current, flags=("link_resolve_tried",)):
    payload = {"dedupe_key": key, "store": "Walmart", "item": key, "flags": list(flags),
               "conditions": [], "link_as_posted": posted, "canonical_url": current}
    db.insert_deal(dedupe_key=key, store="Walmart", item=key, normalized_item=key,
                   canonical_url=current, payload=payload, final_price=9.99,
                   pct_at_checkout=60.0, abs_savings=15.0, online_only="true",
                   expires_at="", now=util.now_utc())


def test_replaced_source_tracker_links_are_put_back_once(tmp_path):
    from dealradar.config import load_config
    cfg = load_config()
    db = DB(tmp_path / "state.db")
    tracker = "https://go.dealmoon.com/exec/j?d=123"
    _replaced(db, "swapped", tracker, "https://www.walmart.com/ip/123")
    _replaced(db, "never-tried", tracker, "https://www.walmart.com/ip/456", flags=())
    _replaced(db, "store-link", "https://www.target.com/p/x", "https://www.target.com/p/x-canon")
    _replaced(db, "still-tracker", tracker, tracker)
    # Any path that swapped it counts, not only the repair (Codex audit 051).
    assert pipeline._restore_source_credit_links(db, cfg) == 2
    swapped = db.get_deal("swapped")
    assert swapped["canonical_url"] == tracker
    assert db.get_deal("never-tried")["canonical_url"] == tracker
    assert db.get_deal("store-link")["canonical_url"] == "https://www.target.com/p/x-canon"
    assert pipeline._restore_source_credit_links(db, cfg) == 0  # idempotent
    cfg.affiliate_repair_competitor_trackers = True
    _replaced(db, "while-on", tracker, "https://www.walmart.com/ip/789")
    assert pipeline._restore_source_credit_links(db, cfg) == 0
    db.close()


def _granted_tracker_setup(tmp_path):
    from dealradar.config import load_config
    cfg = load_config()
    db = DB(tmp_path / "state.db")
    calls = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        calls.append(str(request.url))
        return httpx.Response(302, headers={
            "Location": "https://www.amazon.com/dp/B012345678?tag=source-20"})

    fetcher = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handler),
                            sleep_fn=lambda _: None)
    return cfg, db, fetcher, calls


@pytest.mark.parametrize("link", [
    "https://cc.dealsofamerica.com/v1/otc/item?affid=source",
    "https://go.dealmoon.com/a?affid=source",
    "https://links.southernsavers.com/a?affid=source",
])
def test_ingest_keeps_a_granted_tracker_exactly_as_posted(tmp_path, link):
    """Codex audit 051 blocker: ingest resolution followed the tracker to
    Amazon, dropped the source's tag and published ours."""
    from types import SimpleNamespace
    cfg, db, fetcher, calls = _granted_tracker_setup(tmp_path)
    ext = SimpleNamespace(product_url=link, link_as_posted="")
    pipeline._resolve_product_urls([(None, ext)], fetcher, cfg, time.monotonic() + 180)
    assert calls == [] and ext.product_url == link and ext.link_as_posted == link
    fetcher.close()
    db.close()


def test_short_link_pass_never_follows_a_photo_less_dealmoon_tracker(tmp_path):
    cfg, db, fetcher, calls = _granted_tracker_setup(tmp_path)
    link = "https://go.dealmoon.com/a?affid=source"
    PhotoFixture._seed(db, "dm", link)
    assert pipeline._resolve_short_links(db, fetcher, None, cfg) == 0
    assert calls == [] and db.get_deal("dm")["canonical_url"] == link
    fetcher.close()
    db.close()

