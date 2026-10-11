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
    for i in range(25):
        PhotoFixture._seed(db, str(i), f"https://a.co/{i}")
    assert pipeline._resolve_short_links(db, fetcher, None, cfg) == 0
    assert len(calls) == 20
    assert sum("link_resolve_tried" in d["flags"] for d in db.all_deals()) == 20
    pipeline._resolve_short_links(db, fetcher, None, cfg)
    assert len(calls) == 25 and len(set(calls)) == 25
    pipeline._resolve_short_links(db, fetcher, None, cfg)
    assert len(calls) == 25


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
        return httpx.Response(200, text='<meta property="og:image" content="https://images.example/product.jpg">')

    def run(fast):
        return pipeline.run(fast_lane=fast, db_path=str(db_path), config_path=str(cfg_path),
                            transport=httpx.MockTransport(handler), sources_override=[],
                            skip_validation=True, dry_run_telegram=True)

    assert run(False)["short_links"] == 0
    assert calls == []
    first = run(True)
    assert first["short_links"] == 1 and first["product_images"] == 1
    before = list(calls)
    assert run(True)["short_links"] == 0
    assert calls == before
    db = DB(db_path)
    assert len(db.all_deals()) == 1
    assert db.get_deal("stable")["image_url"] == "https://images.example/product.jpg"
    db.close()
