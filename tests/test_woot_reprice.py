"""Woot corrections reuse feed evidence and the same-row Keepa correction path."""

from datetime import timedelta
import json
from pathlib import Path

import httpx
import pytest
import yaml

from dealradar import dedupe, non_amazon_freshness as fresh, pipeline, rank, savings, util
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.fetch import woot
from dealradar.parse.extractor import verify_evidence
from dealradar.parse.schema import LLMDeal, to_extracted
from dealradar.publish import site
from tests.test_non_amazon_freshness import NOW, seed
from tests.test_woot import SRC, _offer


@pytest.fixture
def cfg(tmp_path):
    cfg = load_config()
    cfg.site_dir = str(tmp_path / "site")
    return cfg


@pytest.fixture
def db():
    db = DB(":memory:")
    yield db
    db.close()


def stored(db, cfg, *, key="jeans", price=15.99, **changes):
    row, _, _ = seed(db, cfg, key=key, price=price, hours=60, store="Woot",
                     source="hip2save", product_url=f"https://www.woot.com/offers/{key}?ref=partner")
    if changes:
        db.update_deal(row["dedupe_key"], payload=dict(row, **changes))
    return db.get_deal(row["dedupe_key"])


def offer(price=9.99, regular=29.90, *, key="jeans", **changes):
    return _offer(key, price, regular, EndDate=util.iso(NOW + timedelta(days=2)), **changes)


def check(db, cfg, items, now=NOW):
    return woot._recheck_offers(db, items, now, cfg=cfg, source=SRC)


def counts(db):
    return {table: db.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("deals", "sightings", "price_history", "posts_seen", "llm_calls")}


@pytest.mark.parametrize("sale", [9.99, "$9.99", [9.99, 9.99],
                                 {"Minimum": 9.99, "Maximum": 9.99}])
def test_lower_price_corrects_card_math_history_and_keeps_source(db, cfg, monkeypatch, sale):
    # A recent check must not suppress a correction from the next already-fetched feed.
    before = stored(db, cfg, price_rechecked_at=util.iso(NOW - timedelta(minutes=10)))
    initial = counts(db)
    evidence = []

    def checked(ext, text):
        evidence.append(ext)
        return verify_evidence(ext, text)

    monkeypatch.setattr("dealradar.parse.extractor.verify_evidence", checked)
    stats = check(db, cfg, [offer(sale)])
    after = db.get_deal(before["dedupe_key"])
    assert stats == dict(confirmed=1, repriced=1, range_skipped=0, above_range=0)
    assert after["id"] == before["id"] and after["canonical_url"] == before["canonical_url"]
    for field in ("sightings", "critic", "conditions", "image_url", "first_seen_at"):
        assert after[field] == before[field]
    assert after["final_price"] == 9.99 and after["regular_price"] == 29.90
    assert after["price_rechecked_at"] == after["price_since"] == after["woot_listed_at"] == util.iso(NOW)
    assert not after["gone_suspect_at"] and after["status"] == "alive"
    assert after["regular_price_basis"] == "List price on Woot" and after["audit_reference"] is None
    assert len(evidence) == 1
    assert evidence[0].evidence.final_price_quote == "Sale price: $9.99"
    computed = savings.compute_savings(evidence[0])
    assert after["pct_at_checkout"] == computed.pct_at_checkout == 66.6
    assert after["abs_savings"] == computed.abs_savings == 19.91
    assert counts(db) == dict(initial, price_history=initial["price_history"] + 1)
    assert [p["price"] for p in db.price_observations(after["dedupe_key"], NOW - timedelta(days=3), NOW)] == [15.99, 9.99]
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    site.render_site(db, cfg, NOW)
    card = json.loads((Path(cfg.site_dir) / "deals.json").read_text())["deals"][0]
    assert card["final_price"] == 9.99 and card["pct_at_checkout"] == computed.pct_at_checkout
    assert card["credits"][0]["name"] == "Hip2Save"
    assert card["regular_price_basis"] == "List price on Woot"


def test_equal_price_only_confirms_and_repeating_the_read_adds_no_rows(db, cfg):
    before = stored(db, cfg)
    initial = counts(db)
    assert check(db, cfg, [offer(15.99)])["repriced"] == 0
    after = db.get_deal(before["dedupe_key"])
    assert after["woot_listed_at"] == after["price_rechecked_at"] == util.iso(NOW)
    for field in ("final_price", "regular_price", "pct_at_checkout", "price_since", "sightings", "critic"):
        assert after.get(field) == before.get(field)
    check(db, cfg, [offer(15.99)])
    assert db.get_deal(before["dedupe_key"]) == after and counts(db) == initial


@pytest.mark.parametrize("shown,sale,changed", [
    (20, 19.90, False), (20, 19.91, False), (20, 19.89, True),
    (20, 20.10, False), (20, 20.11, True), (1, .99, False), (1, .98, True),
])
def test_change_must_exceed_both_one_cent_and_half_percent(db, cfg, shown, sale, changed):
    before = stored(db, cfg, price=shown)
    stats = check(db, cfg, [offer(sale)])
    assert stats["repriced"] == int(changed)
    assert db.get_deal(before["dedupe_key"])["final_price"] == (sale if changed else shown)


def test_higher_price_corrects_like_keepa_and_publish_floors_hide_it_alive(db, cfg):
    before = stored(db, cfg)
    stats = check(db, cfg, [offer(28)])
    after = db.get_deal(before["dedupe_key"])
    assert stats["repriced"] == stats["confirmed"] == 1
    assert after["final_price"] == 28 and after["pct_at_checkout"] == 6.4
    assert after["status"] == "alive" and not after["gone_suspect_at"]
    assert after["critic"] == before["critic"]
    assert rank.site_deals(db, cfg, NOW) == []


def test_higher_without_a_mappable_basis_keeps_two_reading_death_path(db, cfg):
    before = stored(db, cfg)
    item = offer(25, None)
    assert check(db, cfg, [item])["repriced"] == 0
    after = db.get_deal(before["dedupe_key"])
    assert after["final_price"] == 15.99 and after["gone_suspect_at"] == util.iso(NOW)
    assert after["status"] == "alive" and not after.get("woot_listed_at")
    check(db, cfg, [item], NOW + timedelta(minutes=59))
    assert db.get_deal(before["dedupe_key"])["status"] == "alive"
    check(db, cfg, [item], NOW + timedelta(hours=1))
    assert db.get_deal(before["dedupe_key"])["death"] == "confirmed"


@pytest.mark.parametrize("low,high,shown,confirmed,above", [
    (9, 10, 9.99, True, False), (9, 10, 15.99, False, True),
    (20, 21, 15.99, False, False), (9, 30, 15.99, True, False),
    (9, 10, 10.01, True, False), (9, 10, 8.99, True, False),
    (9, 10, 10.02, False, True), (9, 10, 8.98, False, False),
])
def test_ranges_only_refresh_prices_within_the_bounds(db, cfg, low, high, shown, confirmed, above):
    before = stored(db, cfg, price=shown)
    initial = counts(db)
    stats = check(db, cfg, [offer({"Minimum": low, "Maximum": high})])
    after = db.get_deal(before["dedupe_key"])
    assert stats == dict(confirmed=int(confirmed), repriced=0, range_skipped=1, above_range=int(above))
    expected = dict(before, woot_listed_at=util.iso(NOW)) if confirmed else before
    assert after == expected and counts(db) == initial
    assert fresh.hidden(after, cfg, NOW) is not confirmed
    assert after["status"] == "alive"


@pytest.mark.parametrize("changes", [{"codes": ["SAVE10"]}, {"conditions": ["subscribe_save"]},
                                    {"stack_steps": ["Clip coupon"]}])
@pytest.mark.parametrize("sale", [9.99, 25, [9, 10]])
def test_checkout_promos_are_unchanged(db, cfg, changes, sale):
    before = stored(db, cfg, **changes)
    initial = counts(db)
    assert check(db, cfg, [offer(sale)])["repriced"] == 0
    assert db.get_deal(before["dedupe_key"]) == before and counts(db) == initial


def test_sold_out_and_api_end_keep_existing_death_rules(db, cfg):
    before = stored(db, cfg)
    item = offer(IsSoldOut=True)
    assert check(db, cfg, [item])["repriced"] == 0
    after = db.get_deal(before["dedupe_key"])
    assert after["gone_suspect_at"] == util.iso(NOW) and after["final_price"] == 15.99
    check(db, cfg, [item], NOW + timedelta(hours=1))
    assert db.get_deal(before["dedupe_key"])["death"] == "confirmed"
    ended = stored(db, cfg, key="ended")
    item = offer(key="ended")
    item["EndDate"] = util.iso(NOW)
    check(db, cfg, [item])
    assert db.get_deal(ended["dedupe_key"])["death"] == "api_ended"


def test_suspect_is_not_repriced_but_retains_existing_live_verdict(db, cfg):
    before = stored(db, cfg, gone_suspect_at=util.iso(NOW - timedelta(hours=2)))
    initial = counts(db)
    assert check(db, cfg, [offer()])["repriced"] == 0
    after = db.get_deal(before["dedupe_key"])
    assert after["final_price"] == 15.99 and not after["gone_suspect_at"]
    assert not after.get("woot_listed_at") and counts(db) == initial


@pytest.mark.parametrize("regular", [None, 0, [29.90, 30], [29.90, 29.90]])
def test_lower_without_existing_mapper_structured_evidence_cannot_refresh(db, cfg, regular):
    before = stored(db, cfg)
    initial = counts(db)
    item = offer(regular=regular)
    mapped = woot.WootAdapter._map_item(item, SRC, NOW)
    if mapped.structured is not None:
        ext = to_extracted(LLMDeal.model_validate(mapped.structured), mapped, "UTC", NOW)
        assert not savings.compute_savings(ext).computable
    assert check(db, cfg, [item])["confirmed"] == 0
    assert db.get_deal(before["dedupe_key"]) == before and counts(db) == initial


def test_used_offer_keeps_its_condition_and_floor(db, cfg):
    before = stored(db, cfg, conditions=["used", "open_box"])
    item = offer(14.99, condition="New - Open Box")
    assert check(db, cfg, [item])["repriced"] == 1
    after = db.get_deal(before["dedupe_key"])
    assert after["conditions"] == before["conditions"] and after["pct_at_checkout"] == 49.9
    assert rank.site_deals(db, cfg, NOW) == []


@pytest.mark.parametrize("broken", ["quote", "identity", "source"])
def test_shared_reprice_still_requires_api_evidence_and_matching_offer(db, cfg, broken):
    before = stored(db, cfg)
    post = woot.WootAdapter._map_item(offer(), SRC, NOW)
    if broken == "quote":
        post.text = post.text.replace("Sale price: $9.99", "Sale price unavailable")
    elif broken == "identity":
        post.structured["product_url"] = "https://woot.com/offers/another"
    else:
        post.source_name = "partner"
    assert not dedupe.reprice_existing(db, before, post, cfg, NOW)
    assert db.get_deal(before["dedupe_key"]) == before


def test_absent_or_ambiguous_offers_never_change_deals(db, cfg):
    before = stored(db, cfg)
    for items in ([], [offer(key="other")], [offer(), offer()]):
        assert check(db, cfg, items) == dict(confirmed=0, repriced=0, range_skipped=0, above_range=0)
        assert db.get_deal(before["dedupe_key"]) == before


def test_pipeline_summary_and_same_run_key_rerun_use_only_existing_feed(tmp_path, monkeypatch):
    config = yaml.safe_load(Path("config.yaml").read_text())
    config["publish"]["site_dir"] = str(tmp_path / "site")
    config["woot"].update(named_feeds=[], max_items_per_run=0, max_new_items_per_run=0)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config))
    cfg = load_config(config_path)
    db_path = tmp_path / "state.db"
    db = DB(str(db_path))
    before = stored(db, cfg)
    initial = counts(db)
    db.close()
    monkeypatch.setenv("WOOT_API_KEY", "offline-woot-key")
    monkeypatch.setenv("DEALRADAR_RUN_KEY", "woot-correction-rerun")
    for name in ("KEEPA_KEY", "DEALS_CLAUDE_API_KEY", "BESTBUY_API_KEY", "EBAY_CLIENT_ID",
                 "EBAY_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "SMTP_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    calls = []

    def handle(request):
        calls.append(str(request.url))
        assert str(request.url) == woot.FEED_URL
        return httpx.Response(200, json={"Items": [offer()]})

    for index in range(2):
        result = pipeline.run(db_path=str(db_path), config_path=str(config_path), now=NOW,
                              sources_override=[SRC], skip_validation=True, dry_run_telegram=True,
                              transport=httpx.MockTransport(handle))
        assert result["store_checks"]["woot"] == dict(
            confirmed=1, repriced=int(index == 0), range_skipped=0, above_range=0)
        db = DB(str(db_path))
        assert counts(db) == dict(initial, price_history=initial["price_history"] + 1)
        assert db.get_deal(before["dedupe_key"])["final_price"] == 9.99
        assert db.conn.execute("SELECT count(*) FROM run_stats").fetchone()[0] == 1
        db.close()
    assert calls == [woot.FEED_URL, woot.FEED_URL]
