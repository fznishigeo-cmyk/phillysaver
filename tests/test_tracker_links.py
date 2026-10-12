"""Offline tracker decoding, identity preservation and photo retries."""
from urllib.parse import quote

import pytest

from dealradar import dedupe, pipeline
from dealradar.db import DB
from dealradar.http_client import tracker_target
from tests.test_dedupe import NOW, make_cfg, make_extracted, make_post, make_savings
from tests.test_pipeline_sources import TestBotWallRepair as BotWallFixture, TestProductImageBackfill as PhotoFixture

AMAZON = "https://www.amazon.com/gp/product/B0GLP6BWD9?tag=xyz"
WALMART = "https://www.walmart.com/ip/Spatula/123456"


def tracker(target):
    return "https://cc.dealsofamerica.com/v1/otc/abc?url=" + quote(target, safe="")


@pytest.mark.parametrize("target", [AMAZON, WALMART])
def test_valid_targets(target):
    assert tracker_target(tracker(target)) == target


def test_linksynergy_murl():
    link = "https://click.linksynergy.com/deeplink?id=abc&murl=" + quote(AMAZON, safe="")
    assert tracker_target(link) == AMAZON


@pytest.mark.parametrize("url", [
    "https://cc.dealsofamerica.com/v1/otc/abc", tracker("http://walmart.com/ip/1"),
    tracker("javascript:alert(1)"), tracker("https://"), tracker("https://[bad"),
    tracker("https://walmart.com/blocked"), tracker("https://evil.com/\\path"),
    tracker("https://evil.com/\npath"), tracker("https://evil.com/\x7fpath"),
    "https://other.example/?url=" + quote(AMAZON), "garbage", "https://[bad", "", None,
])
def test_invalid_targets(url):
    assert tracker_target(url) == ""


def test_ingest_keeps_affiliate_identity_and_rerun_adds_no_rows(tmp_path):
    db = DB(tmp_path / "state.db")
    link = tracker(AMAZON)
    ext = make_extracted(store="Amazon", product_url=link, link_as_posted=link)
    post = make_post(source="dealsofamerica")
    result = dedupe.upsert_extracted(db, ext, make_savings(), post, make_cfg(), NOW)
    row = db.get_deal(result["dedupe_key"])
    assert row["asin"] == "B0GLP6BWD9"
    assert row["canonical_url"] == link
    assert row["dedupe_key"] == dedupe.canonical_key(ext.store, link, ext.item)
    assert row["link_as_posted"] == link
    rerun = dedupe.upsert_extracted(db, ext, make_savings(), post, make_cfg(), NOW)
    assert not rerun["is_new_deal"]
    assert len(db.all_deals()) == 1
    assert len(db.sightings_for(row["id"])) == 1
    db.close()


def test_repair_all_statuses_once_keeps_identity(tmp_path):
    db = DB(tmp_path / "state.db")
    links = [tracker(AMAZON), tracker(WALMART), "https://other.example/product"]
    keys = [BotWallFixture._seed(db, link, BotWallFixture.TRIED) for link in links]
    db.update_deal(keys[0], status="dead")
    before = db.get_deal(keys[2])
    assert pipeline._repair_tracker_asins(db) == 2
    for key, link in zip(keys[:2], links[:2]):
        row = db.get_deal(key)
        assert row["canonical_url"] == link and row["dedupe_key"] == key
        assert row["flags"] == ["unknown_shipping"]
    assert db.get_deal(keys[0])["asin"] == "B0GLP6BWD9"
    assert db.get_deal(keys[0])["status"] == "dead"
    assert db.get_deal(keys[2]) == before
    rows = BotWallFixture._rows(db)
    assert pipeline._repair_tracker_asins(db) == 0
    assert BotWallFixture._rows(db) == rows
    db.close()


def test_photo_fetches_target_only(tmp_path):
    db = DB(tmp_path / "state.db")
    link = tracker(WALMART)
    PhotoFixture._seed(db, "tracker", link)
    fetcher = PhotoFixture._Fetcher({
        WALMART: b'<meta property="og:image" content="https://i5.walmartimages.com/product.jpg">',
    })
    assert pipeline._backfill_product_images(db, fetcher, None) == 1
    assert fetcher.calls == [WALMART]
    assert db.get_deal("tracker")["canonical_url"] == link
    assert pipeline._backfill_product_images(db, fetcher, None) == 0
    db.close()
