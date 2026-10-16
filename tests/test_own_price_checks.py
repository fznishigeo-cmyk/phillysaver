"""Own readings grow exact-product history without changing eligibility rules."""

from contextlib import closing
from dataclasses import replace
from datetime import timedelta, timezone

import httpx
import pytest
import yaml

from dealradar import pipeline, price_checks, util, verify
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.fetch import woot
from dealradar.fetch.keepa import _FeedConfirmations
from dealradar.keepa_rechecks import record_price_check
from dealradar.publish import product_pages, site
from tests.test_free_confirmations import row as feed_row
from tests.test_price_accuracy_engine import (
    ASIN, api as keepa_api, cfg as keepa_cfg, product, seed as keepa_seed,
)
from tests.test_product_entities import insert
from tests.test_product_offer_pages import tree
from tests.test_store_prices import API, NOW, cfg as store_cfg, seed as store_seed
from tests.test_woot import SRC, _offer


@pytest.fixture
def db():
    database = DB(":memory:")
    yield database
    database.close()


@pytest.fixture
def cfg():
    return AppConfig(seo_product_pages_enabled=True, paywall_open_access=True,
                     custom_domain="wowpriced.com")


def stored(db, key="widget", *, now=NOW - timedelta(days=7), **changes):
    return insert(db, key, now=now, **dict(dict(
        store="Woot", canonical_url=f"https://www.woot.com/offers/{key}",
        codes=[], final_price=25, regular_price=100, pct_at_checkout=75,
        flags=[], conditions=[], critic={"plausible": True, "confidence": 1}), **changes))


def reading(key="widget", price=25, **changes):
    return _offer(key, price, 100, EndDate=util.iso(NOW + timedelta(days=2)), **changes)


def checks(db):
    return [dict(row) for row in db.conn.execute(
        "SELECT * FROM price_history WHERE content_hash GLOB 'check:*' ORDER BY id")]


def prepare(db, cfg, rows, now=NOW):
    public = [site._serialize_deal(d, now, cfg=cfg) for d in rows]
    return product_pages.prepare(db, cfg, now, public, rows)


def test_own_check_config_defaults_and_overrides(tmp_path):
    assert AppConfig().seo_product_pages_record_own_checks_enabled
    assert load_config().seo_product_pages_record_own_checks_max_per_offer_per_day == 1  # shipped: one a day
    path = tmp_path / "config.yaml"
    path.write_text("{}")
    assert load_config(path).seo_product_pages_record_own_checks_enabled
    path.write_text("seo: {product_pages: {record_own_checks: {enabled: false, max_per_offer_per_day: 96}}}")
    cfg = load_config(path)
    assert not cfg.seo_product_pages_record_own_checks_enabled
    assert cfg.seo_product_pages_record_own_checks_max_per_offer_per_day == 96


@pytest.mark.parametrize("value", [0, 97, -1, True, 1.5, "24", None])
def test_daily_limit_is_a_validated_integer(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"seo": {"product_pages": {"record_own_checks": {
        "max_per_offer_per_day": value}}}}))
    with pytest.raises(ValueError, match="max_per_offer_per_day"):
        load_config(path)


@pytest.mark.parametrize("value", [None, [], True, "yes", {"enabled": "false"}, {"enabled": 1}])
def test_own_check_settings_fail_closed(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"seo": {"product_pages": {"record_own_checks": value}}}))
    with pytest.raises(ValueError, match="record_own_checks"):
        load_config(path)


def test_six_real_woot_readings_over_seven_days_qualify(db, cfg):
    times = [NOW - timedelta(days=days, hours=5 - i)
             for i, days in enumerate((7, 6, 4, 2, 1, 0))]
    deal = stored(db, now=times[0])
    assert prepare(db, cfg, [deal])[2]["eligible"] == 0
    for at in times:
        stats = woot._recheck_offers(db, [reading()], at, cfg=cfg, source=SRC)
        assert stats["confirmed"] == 1
    rows = checks(db)
    assert len(rows) == 6
    assert {r["price"] for r in rows} == {25}
    assert {r["store"] for r in rows} == {"Woot"}
    assert [r["content_hash"] for r in rows] == [
        f"check:woot:{at:%Y-%m-%dT%H}" for at in times]
    pages, _, report = prepare(db, cfg, [db.get_deal(deal["dedupe_key"])])
    assert report["eligible"] == report["indexable"] == 1
    assert report["own_check_rows_today"] == 1
    assert report["not_eligible"] == dict(few_checks=0, short_span=0, not_recent=0)
    assert len(next(iter(pages.values()))["checks"]) == 6


@pytest.mark.parametrize("kind", ["failed", "sold_out", "ended", "ambiguous", "range",
                                  "mismatch", "suspect", "promo"])
def test_unsuccessful_woot_readings_never_grow_history(db, cfg, kind):
    changes = {"flags": ["suspect_msrp"]} if kind == "suspect" else {"codes": ["SAVE"]} if kind == "promo" else {}
    deal = stored(db, **changes)
    for days in (7, 6, 4, 2, 1, 0):
        items = [reading()]
        if kind == "failed":
            items[0].pop("SalePrice")
        elif kind == "sold_out":
            items[0]["IsSoldOut"] = True
        elif kind == "ended":
            items[0]["EndDate"] = util.iso(NOW - timedelta(days=8))
        elif kind == "ambiguous":
            items *= 2
        elif kind == "range":
            items[0]["SalePrice"] = [20, 30]
        elif kind == "mismatch":
            items = [reading("other")]
        woot._recheck_offers(db, items, NOW - timedelta(days=days), cfg=cfg)
    assert checks(db) == []
    assert prepare(db, cfg, [db.get_deal(deal["dedupe_key"])])[2]["eligible"] == 0


def test_hourly_dedup_utc_day_cap_across_sources_and_reopen(tmp_path, cfg):
    path = tmp_path / "checks.db"
    cfg.seo_product_pages_record_own_checks_max_per_offer_per_day = 2
    with closing(DB(path)) as db:
        deal = stored(db)
        db.record_price(deal["dedupe_key"], deal["store"], 25, NOW, "post:today")
        assert price_checks.record(db, cfg, deal, 25, NOW, NOW, source="woot")
        offset = (NOW + timedelta(minutes=30)).astimezone(timezone(timedelta(hours=12)))
        assert not price_checks.record(db, cfg, deal, 25, offset, offset, source="woot")
        assert price_checks.record(db, cfg, deal, 25, NOW, NOW, source="store")
        assert not price_checks.record(db, cfg, deal, 25, NOW + timedelta(hours=1), NOW + timedelta(hours=1), source="woot")
    with closing(DB(path)) as db:
        assert len(checks(db)) == 2
        assert not price_checks.record(db, cfg, deal, 25, NOW, NOW, source="woot")
        tomorrow = NOW.replace(hour=0) + timedelta(days=1)
        assert price_checks.record(db, cfg, deal, 25, tomorrow, tomorrow, source="woot")
        assert len(checks(db)) == 3


@pytest.mark.parametrize("store", ["bestbuy", "ebay"])
def test_store_api_success_records_read_price_and_same_run_is_idempotent(db, store_cfg, store):
    before = store_seed(db, store_cfg, store)
    api = API(db, store_cfg)
    assert api.run(db, store_cfg, run_key="one")[store]["confirmed"] == 1
    rows = checks(db)
    assert len(rows) == 1
    assert rows[0]["canonical_key"] == before["dedupe_key"]
    assert (rows[0]["price"], rows[0]["observed_at"], rows[0]["content_hash"]) == (
        25, util.iso(NOW), f"check:{store}:{NOW:%Y-%m-%dT%H}")
    assert api.run(db, store_cfg, run_key="one")[store]["checked"] == 0
    assert checks(db) == rows


@pytest.mark.parametrize("store", ["bestbuy", "ebay"])
@pytest.mark.parametrize("status", [403, 429, 500])
def test_store_api_failures_never_add_history(db, store_cfg, store, status):
    store_seed(db, store_cfg, store)
    API(db, store_cfg, status=status).run(db, store_cfg)
    assert not checks(db)


@pytest.mark.parametrize("price", [20, 28])
@pytest.mark.parametrize("source", ["woot", "bestbuy", "ebay"])
def test_successful_reprices_record_the_new_read_price(db, cfg, store_cfg, source, price):
    if source == "woot":
        deal = stored(db)
        woot._recheck_offers(db, [reading(price=price)], NOW, cfg=cfg)
    else:
        deal = store_seed(db, store_cfg, source)
        API(db, store_cfg, change=lambda body: body["products"][0].update(salePrice=price)
            if source == "bestbuy" else body["price"].update(value=str(price))).run(db, store_cfg)
    assert db.get_deal(deal["dedupe_key"])["final_price"] == price
    assert len(checks(db)) == 1 and checks(db)[0]["price"] == price


def test_keepa_paid_read_uses_observation_time_and_records_privately(db, keepa_cfg, monkeypatch):
    # The shared Keepa fixture clock is the previous day; use its own data time.
    from tests.test_price_accuracy_engine import NOW as at
    deal = keepa_seed(db, keepa_cfg)
    record = product(2500)
    keepa_api(monkeypatch, {ASIN: record})
    result = pipeline._recheck_prices(db, keepa_cfg, at, image_backfill=False)
    assert result["checked"] == 1
    rows = checks(db)
    assert len(rows) == 1 and rows[0]["price"] == 25
    assert rows[0]["observed_at"] == util.iso(at)
    assert pipeline._recheck_prices(db, keepa_cfg, at, image_backfill=False)["checked"] == 0
    assert checks(db) == rows
    keepa_cfg.seo_product_pages_enabled = True
    for days in range(1, 8):
        db.record_price(deal["dedupe_key"], "Amazon", 25, at - timedelta(days=days), f"check:keepa:older-{days}")
    keepa_cfg.amazon_history = "show"
    assert prepare(db, keepa_cfg, [db.get_deal(deal["dedupe_key"])], at)[2]["eligible"] == 1
    keepa_cfg.amazon_history = "hide"
    assert prepare(db, keepa_cfg, [db.get_deal(deal["dedupe_key"])], at)[0] == {}
    assert prepare(db, keepa_cfg, [db.get_deal(deal["dedupe_key"])], at)[2]["indexable"] == 0


@pytest.mark.parametrize("used", [False, True])
def test_keepa_feed_records_its_own_hour_once(db, keepa_cfg, used):
    from tests.test_price_accuracy_engine import NOW as at
    deal = keepa_seed(db, keepa_cfg, conditions=["used"] if used else [])
    confirmations = _FeedConfirmations(db, at, keepa_cfg)
    confirmations.note([feed_row(at=at - timedelta(minutes=20))], used=used)
    confirmations.note([feed_row(at=at - timedelta(minutes=10))], used=used)
    assert len(checks(db)) == 1
    assert checks(db)[0]["observed_at"] == util.iso(at - timedelta(minutes=20))
    assert checks(db)[0]["canonical_key"] == deal["dedupe_key"]


@pytest.mark.parametrize("kind", ["missing_time", "future", "stale", "mismatch", "promo", "price_mismatch"])
def test_keepa_unconfirmed_prices_do_not_record(db, keepa_cfg, kind):
    from tests.test_price_accuracy_engine import NOW as at
    deal = keepa_seed(db, keepa_cfg, codes=["SAVE"] if kind == "promo" else [])
    p = product(2600 if kind == "price_mismatch" else 2500)
    if kind == "missing_time":
        p.pop("lastUpdate")
    elif kind == "future":
        p["lastUpdate"] += 60
    elif kind == "stale":
        p["lastUpdate"] -= 60 * 24
    elif kind == "mismatch":
        p["asin"] = "B000000002"
    assert not record_price_check(db, keepa_cfg, deal, p, at)
    assert not checks(db)


@pytest.mark.parametrize("kind", ["no_response", "no_price", "gone", "old", "future", "promo"])
def test_inconclusive_paid_keepa_readings_never_record(db, keepa_cfg, monkeypatch, kind):
    from tests.test_price_accuracy_engine import NOW as at
    keepa_seed(db, keepa_cfg, codes=["SAVE"] if kind == "promo" else [])
    p = product(10000 if kind == "gone" else 2500)
    if kind == "no_price":
        p["stats"] = {}
    elif kind == "gone":
        p["stats"].pop("avg30")
        p["stats"].pop("avg90")
    elif kind == "old":
        p["lastUpdate"] -= 60 * 24
    elif kind == "future":
        p["lastUpdate"] += 60
    keepa_api(monkeypatch, {} if kind == "no_response" else {ASIN: p})
    pipeline._recheck_prices(db, keepa_cfg, at, image_backfill=False)
    assert not checks(db)


def test_not_eligible_counts_each_page_once_and_stays_private(db, cfg, tmp_path, monkeypatch):
    few, short, old, good = [stored(db, key) for key in ("few", "short", "old", "good")]
    for deal, count, span in ((few, 2, 1), (short, 6, 1), (old, 6, 7), (good, 6, 7)):
        for i in range(count):
            db.record_price(deal["dedupe_key"], "Woot", 25,
                            NOW - timedelta(days=span * i / (count - 1)), f"post:{i}")
    prepare(db, cfg, [old], NOW - timedelta(days=91))
    pages, _, report = prepare(db, cfg, [few, short, good])
    assert report["not_eligible"] == dict(few_checks=1, short_span=1, not_recent=1)
    assert sum(report["not_eligible"].values()) + report["eligible"] == len(pages) == 4
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    site.render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=[few, short, good])
    assert all(b"not_eligible" not in data and b"own_check_rows_today" not in data
               for data in tree(tmp_path / "site").values())


def test_check_retention_bounds_rows_and_preserves_post_history(db, cfg):
    deal = stored(db, now=NOW - timedelta(days=100))
    for label, days in (("old", 90 + 1 / 86400), ("boundary", 90), ("recent", 1)):
        at = NOW - timedelta(days=days)
        assert price_checks.record(db, cfg, deal, 25, at, NOW, source="woot")
        db.record_price(deal["dedupe_key"], "Woot", 25, at, "post:" + label)
    db.prune(NOW)
    assert len(checks(db)) == 2
    assert db.conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == 5
    db.prune(NOW)
    assert len(checks(db)) == 2


def test_documented_daily_fixture_bounds_300_woot_and_20_store_offers(db, store_cfg):
    for i in range(300):
        stored(db, f"woot-{i}")
    items = [reading(f"woot-{i}") for i in range(300)]
    for store in ("bestbuy", "ebay"):
        for i in range(10):
            store_seed(db, store_cfg, store, identity=str(100000000 + i))
    for hour in range(24):
        at = NOW.replace(hour=hour)
        woot._recheck_offers(db, items, at, cfg=store_cfg)
        woot._recheck_offers(db, items, at, cfg=store_cfg)  # repeated feed, same hour
        API(db, store_cfg).run(db, store_cfg, at, run_key=f"hour-{hour}")
        assert len(checks(db)) == 320  # shipped cap: one observation per offer per UTC day
    assert prepare(db, store_cfg, [])[2]["own_check_rows_today"] == 320


@pytest.mark.parametrize("audit_mode", ["shadow", "enforce"])
def test_pipeline_woot_same_run_key_preserves_observations_and_output(tmp_path, monkeypatch, audit_mode):
    config = tmp_path / "config.yaml"
    raw = load_config().raw
    raw["publish"]["site_dir"] = str(tmp_path / "site")
    raw["audit"]["mode"] = audit_mode
    raw["parse"]["recategorize_other_per_run"] = 0
    config.write_text(yaml.safe_dump(raw))
    for name in ("KEEPA_KEY", "BESTBUY_API_KEY", "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET",
                 "DEALS_CLAUDE_API_KEY", "TELEGRAM_BOT_TOKEN", "STRIPE_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "state.db"
    with closing(DB(path)) as database:
        stored(database)

    def fetch(sources, fetcher, database, cfg, now, *args):
        woot._recheck_offers(database, [reading()], now, cfg=cfg)
        return [], {}

    monkeypatch.setattr(pipeline, "_fetch_all", fetch)
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    monkeypatch.setattr(site, "util_now", lambda: NOW)

    def run():
        return pipeline.run(db_path=str(path), config_path=str(config), now=NOW,
                            sources_override=[], skip_validation=True, dry_run_telegram=True,
                            transport=httpx.MockTransport(lambda request: httpx.Response(404)))

    first = run()
    assert first["product_pages"]["own_check_rows_today"] == 1
    with closing(DB(path)) as database:
        history = checks(database)
        assert len(history) == 1
        assert len(database.all_deals()) == 1
    public = tree(tmp_path / "site")
    second = run()
    assert first["run_key"] == second["run_key"]
    assert second["product_pages"]["own_check_rows_today"] == 1
    assert tree(tmp_path / "site") == public
    with closing(DB(path)) as database:
        assert checks(database) == history


@pytest.mark.parametrize("mode", ["closed", "open", "support", "shipped"])
def test_disabled_matches_absent_recorder_state_and_all_public_bytes(tmp_path, monkeypatch, mode):
    cfg = load_config() if mode == "shipped" else AppConfig(custom_domain="wowpriced.com")
    cfg.seo_product_pages_enabled = True
    cfg.paywall_open_access = mode == "open"
    cfg.support_enabled = mode in {"support", "shipped"}
    cfg.site_dir = str(tmp_path / "site")
    monkeypatch.setattr(site, "util_now", lambda: NOW)

    def exercise(config):
        with closing(DB(":memory:")) as database:
            stored(database)
            woot._recheck_offers(database, [reading()], NOW, cfg=config)
            site.render_site(database, config, NOW)
            return list(database.conn.iterdump()), tree(tmp_path / "site")

    with monkeypatch.context() as patch:
        patch.setattr(price_checks, "record", lambda *args, **kwargs: False)
        baseline = exercise(cfg)
    disabled = replace(cfg, seo_product_pages_record_own_checks_enabled=False)
    assert exercise(disabled) == baseline
