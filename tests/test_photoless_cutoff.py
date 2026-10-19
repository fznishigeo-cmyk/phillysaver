"""Task 283: publication-only photo cutoff, with no live transports."""

import hashlib
import json
import socket
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import yaml

from dealradar import rank
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import indexnow, manifest_refresh, site, social
from dealradar.util import iso
from tests.test_e2e_offline import env, _run, test_rerun_is_idempotent as check_rerun
from tests.test_product_entities import insert
from tests.test_publish_site import NOW, make_deal
from tests.test_seo_deal_pages import _page, _path

PIC = "https://images.example.invalid/product.jpg"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task283 live network unavailable")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(site, "util_now", lambda: NOW)


@pytest.fixture
def db():
    database = DB(":memory:")
    yield database
    database.close()


@pytest.fixture
def cfg(tmp_path):
    return AppConfig(custom_domain="wowpriced.com", site_dir=str(tmp_path / "site"),
                     max_photoless_h=3, paywall_open_access=True, static_photos=True,
                     seo_hub_min_items=1, seo_store_page_min_deals=1,
                     seo_price_page_min_deals=1, seo_deal_pages_indexable=True,
                     seo_indexnow_key="task283offlinekey", seo_indexnow_min_hours=0,
                     seo_indexnow_resubmit_days=0)


def test_missing_is_off_and_shipped_cutoff_and_floor(tmp_path):
    assert AppConfig().max_photoless_h is None
    for raw in ({}, {"publish": {}}, {"publish": {"photos": {}}}):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(raw))
        assert load_config(path).max_photoless_h is None
    shipped = load_config()
    assert shipped.max_photoless_h == 1
    assert shipped.raw["site_check"]["min_photo_pct"] == 98


@pytest.mark.parametrize("value", [1, 1.5, 3, 24])
def test_valid_cutoffs(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"publish": {"photos": {"max_photoless_h": value}}}))
    assert load_config(path).max_photoless_h == value


@pytest.mark.parametrize("value", [None, False, True, 0, -1, .99, "3", [], {},
                                  float("nan"), float("inf"), -float("inf")])
def test_invalid_cutoffs(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"publish": {"photos": {"max_photoless_h": value}}}))
    with pytest.raises(ValueError, match=r"publish.photos.max_photoless_h"):
        load_config(path)


@pytest.mark.parametrize("value", [None, False, 3, "3", []])
def test_photos_must_be_a_mapping(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"publish": {"photos": value}}))
    with pytest.raises(ValueError, match=r"publish.photos must be a mapping"):
        load_config(path)


@pytest.mark.parametrize("age,hidden", [(timedelta(hours=2, minutes=59), False),
                                       (timedelta(hours=3) - timedelta(microseconds=1), False),
                                       (timedelta(hours=3), True), (timedelta(days=30), True)])
def test_exact_boundary_and_old_deals(db, cfg, age, hidden):
    deal = make_deal(first_seen_at=(NOW - age).isoformat(), image_url="")
    result = site.render_site(db, cfg, NOW, deals=[deal])
    assert result["unpublished_photoless"] == int(hidden)
    assert bool(result["public_deals"]) is not hidden
    assert bool(result["full_payload"]["deals"]) is not hidden
    assert _page(Path(cfg.site_dir), _path(deal)).exists() is not hidden


@pytest.mark.parametrize("image,hidden", [("", True), (None, True),
    ("http://images.example.invalid/product.jpg", True), ("/store-tile.svg", True),
    ("data:image/svg+xml,store", True), (PIC, False),
    ("https://slickdeals.net/ungranted.jpg", True)])
def test_public_card_photo_definition(cfg, image, hidden):
    deal = make_deal(first_seen_at=iso(NOW - timedelta(hours=3)), image_url=image)
    projected = site._serialize_deal(deal, NOW, cfg=cfg)
    has_photo = str(projected["image_url"]).startswith("https://")
    assert has_photo is not hidden
    assert rank.photoless_expired(deal, cfg, NOW) is hidden


@pytest.mark.parametrize("seen", ["", "invalid", iso(NOW + timedelta(hours=1))])
def test_unknown_or_future_age_is_not_expired(cfg, seen):
    assert not rank.photoless_expired(make_deal(first_seen_at=seen), cfg, NOW)


def test_confirmed_gone_retention_is_unchanged(db, cfg):
    deal = make_deal(status="dead", death="confirmed", died_at=iso(NOW),
                     first_seen_at=iso(NOW - timedelta(days=10)), image_url="")
    result = site.render_site(db, cfg, NOW, deals=[deal])
    assert result["unpublished_photoless"] == 0
    assert result["public_deals"][0]["gone"]
    assert _page(Path(cfg.site_dir), _path(deal)).exists()


@pytest.mark.parametrize("posted", [False, True])
def test_removal_all_public_surfaces_and_photo_republication(db, cfg, tmp_path, posted):
    out = tmp_path / "site"
    cfg.manifest_refresh["enabled"] = True
    cfg.seo_posted_deal_redirects_enabled = True
    cfg.front_page = load_config().front_page
    cfg.front_page["appeal"] = {}
    cfg.social_require_earning_link = cfg.social_require_stock_evidence = False
    hidden = insert(db, "task283-hidden", now=NOW - timedelta(hours=2, minutes=59),
                    item="Unique photoless kitchen organizer", category="kitchen",
                    canonical_url="https://www.walmart.com/ip/283", image_url="",
                    price_rechecked_at=iso(NOW))
    visible = insert(db, "task283-photo", now=NOW - timedelta(days=5),
                     item="Visible kitchen organizer", category="kitchen",
                     canonical_url="https://www.walmart.com/ip/284", image_url=PIC,
                     price_rechecked_at=iso(NOW))
    site.render_site(db, cfg, NOW, last_run={"run_key": "young"})
    path, url = _path(hidden), "https://wowpriced.com" + _path(hidden)
    assert _page(out, path).exists()
    assert any(url in p.read_text() for p in out.glob("sitemap*.xml"))
    if posted:
        db.record_alert(deal_key=hidden["dedupe_key"], channel="telegram",
                        dedup_token="task283-hidden|telegram", now=NOW)
    rows_before = [tuple(row) for row in db.conn.execute("SELECT * FROM deals ORDER BY id")]
    later = NOW + timedelta(minutes=1)
    result = site.render_site(db, cfg, later, last_run={"run_key": "cutoff"})
    assert result["unpublished_photoless"] == 1
    assert {d["dedupe_key"] for d in result["public_deals"]} == {visible["dedupe_key"]}
    assert not _page(out, path).exists()
    assert db.indexnow_pages()[url]["removed"] == 1
    assert json.loads(indexnow.manifest_path(out).read_text())["versions"][url] == "removed"
    assert url in json.loads(indexnow.due_list_path(out).read_text())
    for file in out.rglob("*"):
        if file.is_file():
            assert hidden["item"] not in file.read_text()
    assert not any(url in p.read_text() for p in out.glob("sitemap*.xml"))
    if posted:
        assert (out / "_redirects").read_text() == f"{path} /deals/home-kitchen/ 301\n"
    assert [d["dedupe_key"] for d, _ in social._candidates(db, cfg, {})] == [visible["dedupe_key"]]
    # Recovery still sees the row; alerts and digests use the filtered set.
    assert hidden["dedupe_key"] in {d["dedupe_key"] for d in rank.site_deals(db, cfg, later)}
    assert hidden["dedupe_key"] not in {d["dedupe_key"] for d in rank.published_deals(db, cfg, later)}
    assert [tuple(row) for row in db.conn.execute("SELECT * FROM deals ORDER BY id")] == rows_before
    body = (out / "deals.json").read_bytes()
    manifest = json.loads((out / manifest_refresh.NAME).read_text())
    assert manifest["sha256"] == hashlib.sha256(body).hexdigest()
    assert manifest["bytes"] == len(body)
    files = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    state = list(db.conn.iterdump())
    site.render_site(db, cfg, later, last_run={"run_key": "cutoff"})
    assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == files
    assert list(db.conn.iterdump()) == state
    db.update_deal(hidden["dedupe_key"], payload=dict(db.get_deal(hidden["dedupe_key"]), image_url=PIC))
    result = site.render_site(db, cfg, later + timedelta(minutes=1), last_run={"run_key": "photo"})
    assert result["unpublished_photoless"] == 0
    assert {d["dedupe_key"] for d in result["public_deals"]} == {hidden["dedupe_key"], visible["dedupe_key"]}
    assert _page(out, path).exists()
    assert db.indexnow_pages()[url]["removed"] == 0
    assert any(url in p.read_text() for p in out.glob("sitemap*.xml"))
    assert not (out / "_redirects").exists()


def test_pipeline_summary_counts_only_unpublished_deals(env):
    database = DB(env["db"])
    try:
        insert(database, "task283-pipeline-hidden", now=NOW - timedelta(hours=3),
               item="Photoless pipeline kitchen organizer", category="kitchen", image_url="",
               price_rechecked_at=iso(NOW), flags=["post_link_tried", "link_resolve_tried"],
               critic={"plausible": True, "confidence": .9, "reason": "Fixture basis"})
        database.meta_set("recategorized_v2", json.dumps({"keys": ["task283-pipeline-hidden"]}))
    finally:
        database.close()
    first, second = _run(env), _run(env)
    assert first["unpublished_photoless"] == second["unpublished_photoless"] == 1
    assert type(first["unpublished_photoless"]) is int
    assert "unpublished_photoless" not in (env["site"] / "deals.json").read_text()


@pytest.mark.parametrize("audit_mode", ["shadow", "enforce"])
def test_enabled_pipeline_rerun_is_idempotent(env, capsys, audit_mode):
    assert load_config(env["cfg"]).max_photoless_h == 1
    check_rerun(env, capsys, audit_mode)
