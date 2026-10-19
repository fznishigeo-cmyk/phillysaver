"""Integrated public projections and private evidence, using offline facts only."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import json
import subprocess

import pytest

from dealradar import dedupe, pipeline, rank, savings, util, verify
from dealradar.fetch.keepa import KeepaDealsAdapter, _FeedConfirmations
from dealradar.parse.schema import LLMDeal, to_extracted
from dealradar.parse.extractor import verify_evidence
from dealradar.publish import seo, site
from tests.history_pins import pre_drop_fresh
from tests.test_free_confirmations import row
from tests.test_price_accuracy_engine import ASIN, NOW, api, bucket, cfg, db, product, seed


@pytest.mark.parametrize("age,listed", [(48, None), (None, None), (-1, None), (.25, None), (0, 40)])
def test_buybox_cannot_refresh_the_list_prices_own_clock(db, cfg, monkeypatch, age, listed):
    cfg.keepa_click_price_rule = "on"
    cfg.keepa_buybox_top_n = 120
    deal = seed(db, cfg, regular_price_basis="90-day average on Amazon")
    db.meta_set(site.PICKS_META, '["' + deal["dedupe_key"] + '"]')
    record = product(2500, current=[2500, -1, -1, -1, 4000],
                     buyBoxPrice=2500, buyBoxShipping=0,
                     lastBuyBoxUpdate=product()["lastUpdate"])
    record["lastUpdate"] = (None if age is None else
        int((NOW - timedelta(hours=age)).timestamp() / 60) - verify.KEEPA_EPOCH_MINUTES)
    calls = api(monkeypatch, {ASIN: record}, cost=4)
    bucket(db, 500)
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    stored = db.get_deal(deal["dedupe_key"])
    public = site._serialize_deal(stored, NOW, cfg=cfg)
    assert result["buybox_top"]["with_buybox"] == 1 and len(calls) == 1
    assert verify.price_confirmed_at(stored) == NOW
    assert public["regular_price"] == listed
    assert public["pct_at_checkout"] == (37.5 if listed else None)
    assert stored["regular_price"] == 100 and stored["pct_at_checkout"] == 75
    assert (stored.get("keepa_list_price") or {}).get("observed_at", "") == (
        "" if age is None or age < 0 else util.iso(NOW - timedelta(hours=age)))
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert len(calls) == 1


@pytest.mark.parametrize("ingest", [False, True])
@pytest.mark.parametrize("clock", ["price_rechecked_at", "list_reading"])
def test_older_feed_cannot_replace_a_newer_paid_list_price(db, cfg, clock, ingest):
    newer = {"asin": ASIN, "price": 40, "observed_at": util.iso(NOW)}
    deal = seed(db, cfg, regular_price_basis="90-day average on Amazon",
                keepa_list_price=newer,
                price_rechecked_at=util.iso(NOW if clock == "price_rechecked_at" else NOW - timedelta(hours=1)))
    feed = row(at=NOW - timedelta(minutes=20))
    feed["current"][4] = 8000
    confirmations = _FeedConfirmations(db, NOW, cfg)
    def note():
        confirmations.note([feed], used=False)
        if ingest:
            from tests.test_keepa import SRC
            post = KeepaDealsAdapter._map_row(feed, SRC, NOW)
            extracted = to_extracted(LLMDeal.model_validate(post.structured), post, "UTC", NOW)
            assert verify_evidence(extracted, post.text)[0]
            dedupe.upsert_extracted(db, extracted, savings.compute_savings(extracted), post, cfg, NOW)
    note()
    stored = db.get_deal(deal["dedupe_key"])
    assert stored["keepa_list_price"] == newer
    # Feed evidence still has its own monotone confirmation clock.
    assert stored["keepa_price_seen_at"] == util.iso(NOW - timedelta(minutes=20))
    snapshot = deepcopy(stored)
    note()
    assert db.get_deal(deal["dedupe_key"]) == snapshot
    feed["lastUpdate"] += 20
    note()
    assert db.get_deal(deal["dedupe_key"])["keepa_list_price"]["price"] == 80


def test_invalid_future_reference_cannot_block_a_valid_feed_reading(db, cfg):
    deal = seed(db, cfg, regular_price_basis="90-day average on Amazon",
                price_rechecked_at=util.iso(NOW), keepa_list_price=dict(
                    asin=ASIN, price=100, observed_at=util.iso(NOW + timedelta(days=1))))
    feed = row(at=NOW)
    feed["current"][4] = 4000
    _FeedConfirmations(db, NOW, cfg).note([feed], used=False)
    stored = db.get_deal(deal["dedupe_key"])
    assert stored["keepa_list_price"] == dict(asin=ASIN, price=40, observed_at=util.iso(NOW))
    assert site._serialize_deal(stored, NOW, cfg=cfg)["regular_price"] == 40


@pytest.mark.parametrize("ingest", [False, True])
def test_future_feed_clock_cannot_supply_a_public_list_price(db, cfg, ingest):
    from tests.test_keepa import SRC

    deal = seed(db, cfg, regular_price_basis="90-day average on Amazon")
    feed = row(at=NOW + timedelta(days=1))
    feed["current"][4] = 4000
    _FeedConfirmations(db, NOW, cfg).note([feed], used=False)
    if ingest:
        post = KeepaDealsAdapter._map_row(feed, SRC, NOW)
        extracted = to_extracted(LLMDeal.model_validate(post.structured), post, "UTC", NOW)
        assert verify_evidence(extracted, post.text)[0]
        dedupe.upsert_extracted(db, extracted, savings.compute_savings(extracted), post, cfg, NOW)
    stored = db.get_deal(deal["dedupe_key"])
    assert stored["keepa_price_seen_at"] == util.iso(NOW)  # Existing feed confirmation contract.
    assert site._serialize_deal(stored, NOW, cfg=cfg)["regular_price"] is None


@pytest.mark.parametrize("mode", ["history", "list_price"])
def test_critic_keeps_its_private_reason_out_of_shipping_logs(db, cfg, monkeypatch, caplog, mode):
    cfg.amazon_reference = mode
    cfg.audit_mode = "shadow"
    cfg.critic_tiering["enabled"] = False
    deal = seed(db, cfg)
    db.update_deal(deal["dedupe_key"], payload=dict(deal, critic=None))
    reason = "The $100.00 regular price is inflated compared with the historical average."
    verdict = {"plausible": False, "confidence": .9, "reason": reason}
    monkeypatch.setattr("dealradar.parse.claude_client.critique_deals", lambda *a, **kw: [verdict])
    parser = SimpleNamespace(critic_calls=0)
    with caplog.at_level("WARNING", logger="dealradar.pipeline"):
        result = pipeline._run_critic(db, cfg, parser, NOW)
    assert result["suspect"] == result["judged"] == 1
    stored = db.get_deal(deal["dedupe_key"])
    assert stored["critic"]["reason"] == reason and "suspect_msrp" in stored["flags"]
    assert "suspect MSRP" in caplog.text
    assert (reason in caplog.text) is (mode == "history")
    if mode == "list_price":
        assert all(reason not in record.args for record in caplog.records)


@pytest.mark.parametrize("mode,history,basis,listed,expected", [
    ("history", "show", "90-day average on Amazon", None, 75),
    ("history", "hide", "90-day average on Amazon", None, None),
    ("list_price", "hide", "90-day average on Amazon", None, None),
    ("list_price", "hide", "90-day average on Amazon", 40, 38),
    ("list_price", "hide", "historical median on Amazon", None, None),
])
@pytest.mark.parametrize("mixed", [False, True])
def test_database_hub_statistics_use_only_public_comparisons(
        db, cfg, tmp_path, monkeypatch, mode, history, basis, listed, expected, mixed):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg.amazon_reference, cfg.amazon_history = mode, history
    hidden = seed(db, cfg, seen=NOW - timedelta(hours=1), price_rechecked_at=util.iso(NOW),
                  regular_price_basis=basis,
                  keepa_list_price={"asin": ASIN, "price": listed, "observed_at": util.iso(NOW)})
    if mixed:
        seed(db, cfg, asin="B000000002", seen=NOW - timedelta(hours=1),
             price_rechecked_at=util.iso(NOW))  # A source-stated 75% comparison stays public.
    before = deepcopy(db.all_deals())
    stats = seo.week_stats(db, cfg, NOW)["home-kitchen"]
    if mixed:
        expected = 56 if listed else 75
    assert stats == {"count": 2 if mixed else 1, "median_pct": expected, "corroborated": 0}
    site.render_site(db, cfg, NOW, out_dir=tmp_path)
    html = (tmp_path / "deals/home-kitchen/index.html").read_text()
    assert (f"median {expected}% off" in html) if expected is not None else "median" not in html
    assert db.all_deals() == before


def test_hidden_comparisons_keep_their_backend_pick_score(db, cfg, tmp_path, monkeypatch):
    cfg.keepa_click_price_rule = "on"
    cfg.front_page["popular_affordable"]["enabled"] = True
    deal = seed(db, cfg, price_rechecked_at=util.iso(NOW),
                regular_price_basis="90-day average on Amazon", keepa_list_price=None,
                keepa_popularity=dict(asin=ASIN, monthly_sold=1000, sold_at=util.iso(NOW)),
                click_price_check=dict(asin=ASIN, final=25, buybox=25, at=util.iso(NOW)))
    old = deepcopy(cfg)
    old.amazon_reference, old.amazon_history = "history", "show"
    assert rank.pick_score(deal, cfg, NOW) == rank.pick_score(deal, old, NOW)
    assert rank.pick_score(deal, cfg, NOW) is not None
    assert rank.pick_score(deal, cfg, NOW) > rank.pick_score(deal, cfg, NOW, popularity=False)
    public = site._serialize_deal(deal, NOW, cfg=cfg)
    assert public["regular_price"] is public["pct_at_checkout"] is public["abs_savings"] is None
    assert db.get_deal(deal["dedupe_key"])["pct_at_checkout"] == 75
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path)
    assert result["popular_affordable"]["amazon_candidates"] == 1
    assert result["popular_affordable"]["qualifying_at"]["1000"] == 1
    assert result["public_deals"][0]["pick"] == 1
    assert result["public_deals"][0]["ps"] == 1


@pytest.mark.parametrize("mode", ["history", "list_price"])
@pytest.mark.parametrize("strip", [False, True])
def test_suppressed_discounts_have_no_false_ticker_or_department_claim(
        db, cfg, tmp_path, monkeypatch, mode, strip):
    from tests.test_front_page import browser

    pre_drop_fresh(cfg)  # This harness executes the pre-drop department/ticker functions.

    cfg.amazon_reference, cfg.amazon_history = mode, "hide"
    cfg.strip_comments, cfg.support_enabled, cfg.engagement = strip, False, {}
    cfg.keepa_click_price_rule = "on"
    deal = seed(db, cfg, price_rechecked_at=util.iso(NOW),
                regular_price_basis="90-day average on Amazon", keepa_list_price=None,
                click_price_check=dict(asin=ASIN, final=25, buybox=25, at=util.iso(NOW)))
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    site.render_site(db, cfg, NOW, out_dir=tmp_path)
    public = json.loads((tmp_path / "deals.json").read_text())["deals"]
    results = browser(tmp_path / "browser", cfg, db, [dict(rows=public, options=cfg.front_page,
        state={"catTab": 3, "sortKey": "fresh"})])
    assert results[0]["rendered"] == [public[0]["key"]]
    assert "under " not in results[0]["html"] and "% off" not in results[0]["html"]
    script = '''
const fs = require('node:fs'), vm = require('node:vm');
const root = process.argv[1], home = fs.readFileSync(root + '/index.html', 'utf8');
const deals = JSON.parse(fs.readFileSync(root + '/deals.json', 'utf8')).deals;
const ctx = {state:{deals}, num:v=>v == null ? null : Number(v),
  isLocked:()=>false, dealKey:d=>d.key, esc:String, offer:()=>({trial:false})};
vm.createContext(ctx);
if (home.includes('  function usedOrOpenBox(')) {
  vm.runInContext(home.slice(home.indexOf('  function usedOrOpenBox('), home.indexOf('  function searchNewFirst(')), ctx);
}
vm.runInContext(home.slice(home.indexOf('  var TICKER_MAX'), home.indexOf('  var tickerPending')), ctx);
console.log(JSON.stringify(ctx.tickerItems()));
'''
    result = subprocess.run(["node", "-e", script, str(tmp_path)], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    ticker = json.loads(result.stdout)
    assert len(ticker) == 1
    assert "%" not in ticker[0]["stamp"] + ticker[0]["plain"]
    assert db.get_deal(deal["dedupe_key"])["pct_at_checkout"] == 75
