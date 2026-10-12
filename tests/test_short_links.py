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
from tests.test_pipeline_sources import TestProductImageBackfill as PhotoFixture


@pytest.fixture
def setup(tmp_path):
    db = DB(tmp_path / "state.db")
    cfg = AppConfig()
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
        assert stats == {"resolved": 0, "tried": 1}
        assert db.get_deal("blocked")["flags"] == ["link_resolve_tried"]
        assert pipeline._resolve_short_links(db, fetcher, None, cfg) == 0
    finally:
        fetcher.close()
    assert requests == ["https://zdcs.link/click/123", "https://zdcs.link/robots.txt"]


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
    gaps, lines = pipeline._photo_gap_summary(db, {"resolved": 2, "tried": 3})
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


def test_pipeline_fast_lane_and_rerun(tmp_path, monkeypatch):
    step_summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "SMTP_HOST", "DEALS_CLAUDE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text((Path(__file__).resolve().parents[1] / "config.yaml").read_text().replace(
        'site_dir: "site"', f'site_dir: "{tmp_path / "site"}"'))
    db_path = tmp_path / "state.db"
    db = DB(db_path)
    PhotoFixture._seed(db, "stable", "https://on.ltk.com/abc")
    db.close()
    calls = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        calls.append(str(request.url))
        if request.url.host == "on.ltk.com":
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
    first = run(True)
    assert first["short_links"] == 1 and first["product_images"] == 1
    assert first["redirects"] == {"resolved": 1, "tried": 1}
    assert first["photo_gaps"]["alive_without_photo"] == 0
    assert "photo gaps: 0 alive deals without a photo; by canonical host: none" in step_summary.read_text()
    assert "redirects: resolved 1 of 1" in step_summary.read_text()
    before = list(calls)
    assert run(True)["short_links"] == 0
    # The newly stored photo gets one metadata check on the next pass.
    assert calls == before + ["https://images.example/product.jpg"]
    before = list(calls)
    assert run(True)["short_links"] == 0
    assert calls == before  # checked URLs wait 24 h, including on reruns
    db = DB(db_path)
    assert len(db.all_deals()) == 1
    assert db.get_deal("stable")["image_url"] == "https://images.example/product.jpg"
    db.close()
