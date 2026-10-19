"""319: Amazon used suppression, new discovery and two-hour shopper proof."""

import json
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
import yaml

from dealradar import amazon_used, buybox, click_price, pipeline, rank, util, verify
from dealradar.config import AppConfig, load_config
from dealradar.fetch.keepa import KeepaDealsAdapter, DEAL_PAGE_SIZE
from dealradar.keepa_budget import KeepaBudget
from dealradar.keepa_categories import NAMES_META
from dealradar.keepa_rechecks import History, Schedule
from dealradar.publish import site, social
from dealradar.volatility import Plan
from tests.http_helpers import api_transport
from tests.test_keepa import SRC, _amazon_row
from tests.test_price_accuracy_engine import NOW, db, seed, product


@pytest.fixture
def cfg():
    return load_config()


@pytest.mark.parametrize("condition", ["used", "open_box", "refurbished", "Used – Like New",
    "Used – Very Good", "Used – Good", "Used – Acceptable", "Amazon Warehouse", "Amazon Resale"])
def test_amazon_only_gate_and_rollback(db, cfg, condition):
    row = seed(db, cfg, conditions=[condition], price_rechecked_at=util.iso(NOW))
    assert not rank.is_publishable(row, cfg)[0]
    assert not rank.site_deals(db, cfg, NOW, include_unconfirmed=True)
    assert not rank.published_deals(db, cfg, NOW)
    assert not social.plenty_of_stock(db, row, cfg)
    assert rank.is_publishable(row, replace(cfg, amazon_used_enabled=True))[0]
    assert rank.site_deals(db, replace(cfg, amazon_used_enabled=True), NOW)
    woot = dict(row, store="Woot", canonical_url="https://www.woot.com/offers/widget")
    target = dict(row, store="Target", canonical_url="https://www.target.com/p/widget")
    assert rank.is_publishable(woot, cfg)[0] and rank.is_publishable(target, cfg)[0]
    assert not amazon_used.excluded(dict(row, store="Woot"), cfg)
    assert db.get_deal(row["dedupe_key"])["status"] == "alive"


@pytest.mark.parametrize("changes", [dict(store="Amazon Warehouse"), dict(store="Amazon Resale"),
    dict(item="Widget Used – Very Good"), dict(item="Widget Sold by Amazon Resale"),
    dict(item="Widget open-box")])
def test_legacy_missing_conditions_are_suppressed(db, cfg, changes):
    row = dict(seed(db, cfg), **changes)
    assert amazon_used.excluded(row, cfg)
    assert not amazon_used.excluded(dict(row, store="Woot", canonical_url="https://www.woot.com/offers/x"), cfg)


def test_all_public_artifacts_and_old_dead_page_are_removed(db, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    used = seed(db, cfg, conditions=["used"], price_rechecked_at=util.iso(NOW))
    from tests.test_product_entities import insert
    woot = insert(db, "woot|319", now=NOW, store="Woot", item="Woot used kettle",
                  asin="B000000002", conditions=["open_box"],
                  canonical_url="https://www.woot.com/offers/remaining-used", price_rechecked_at=util.iso(NOW),
                  image_url=used["image_url"], final_price=25, regular_price=100,
                  pct_at_checkout=75, abs_savings=75, critic=used["critic"])
    cfg.keepa_click_price_rule = "off"
    cfg.amazon_used_enabled = True
    site.render_site(db, cfg, NOW, out_dir=tmp_path)
    first = json.loads((tmp_path / "deals.json").read_text())
    old = next(d for d in first["deals"] if d["dedupe_key"] == used["dedupe_key"])
    path = tmp_path / old["deal_path"].strip("/") / "index.html"
    assert path.exists()
    cfg.amazon_used_enabled = False
    site.render_site(db, cfg, NOW, out_dir=tmp_path)
    assert not path.exists()
    public = json.loads((tmp_path / "deals.json").read_text())
    assert [d["dedupe_key"] for d in public["deals"]] == [woot["dedupe_key"]]
    for file in tmp_path.rglob("*"):
        if file.is_file() and file.suffix in {".html", ".json", ".xml"}:
            assert used["asin"] not in file.read_text(), file
    assert db.get_deal(used["dedupe_key"])["status"] == "alive"
    report = amazon_used.summary(db, cfg)
    assert report["live_rows_suppressed"] == 1 and report["used_tokens_spent"] == 0
    assert report["discovery_tokens_avoided_ceiling_per_pull"] == 171
    db.update_deal(used["dedupe_key"], payload=dict(used, status="dead", death="confirmed", died_at=util.iso(NOW)), status="dead")
    site.render_site(db, cfg, NOW, out_dir=tmp_path)
    assert not path.exists()  # remembered dead offers cannot reappear via SEO


@pytest.mark.parametrize("planner", [False, True])
@pytest.mark.parametrize("surplus", [False, True])
def test_disabled_used_never_rechecks_or_boosts(db, cfg, monkeypatch, planner, surplus):
    monkeypatch.setenv("KEEPA_KEY", "offline-key")
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    row = seed(db, cfg, conditions=["used"])
    cfg.keepa_volatility["enabled"] = planner
    cfg.keepa_recheck_surplus = surplus
    for name in (site.PICKS_META, site.WALL_META, click_price.PICKS_META, click_price.WALL_META):
        db.meta_set(name, json.dumps([row["dedupe_key"]]))
    KeepaBudget(3500, 60, NOW).save(db)
    def forbidden(*args, **kwargs):
        pytest.fail("suppressed Amazon used offer bought a Keepa request")
    monkeypatch.setattr(verify, "keepa_products", forbidden)
    monkeypatch.setattr(verify, "_keepa_product", forbidden)
    assert not verify.recheck_amazon_prices([row], cfg)
    assert not verify.recheck_amazon_prices([row], cfg, used_offers=False)
    assert verify.keepa_check(row, cfg) is None
    assert verify.recheck_amazon_price(row, cfg) is None
    for _ in range(2):
        result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=True)
        assert result["checked"] == 0
    plan = Plan(History(db, cfg, NOW), [row], {row["dedupe_key"]: 0}, {}, 20)
    assert not plan.rows
    assert db.get_deal(row["dedupe_key"])["status"] == "alive"


def test_new_queries_focus_on_genuine_ranked_drops_without_used_calls(db, cfg, monkeypatch):
    monkeypatch.setenv("KEEPA_KEY", "offline-key")
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    cfg.keepa_amazon_max_new_posts_per_pull = 100
    KeepaBudget(3500, 60, NOW).save(db)
    roots = {str(i + 10): name for i, name in enumerate(cfg.keepa_focus_categories)}
    db.meta_set(NAMES_META, json.dumps(roots))
    db.meta_set("keepa_root_categories_loaded", "1")
    queries = []
    def handler(request):
        assert request.url.path == "/deal", "used finder/product requests must be disabled"
        selection = json.loads(request.url.params["selection"])
        queries.append(selection)
        assert selection["priceTypes"] == [0]
        assert selection["dateRange"] == 3 and selection["salesRankRange"] == [1, 30000]
        assert selection["deltaPercentRange"][0] >= cfg.min_pct
        number = len(queries) * 10
        good = dict(_amazon_row(number), title="Women's satin lingerie loungewear",
                    current=[1000, -1, -1, 1000], avg=[[5000]] * 4)
        weak = dict(good, asin=f"B0BAD{number:05d}", current=[4500, -1, -1, 1000, 20000])
        no_rank = dict(good, asin=f"B0BAD{number+1:05d}", current=[1000])
        adult = dict(good, asin=f"B0BAD{number+2:05d}", title="Sex toy vibrator")
        rows = [good, weak, no_rank, adult] + [{}] * (DEAL_PAGE_SIZE - 4)
        return httpx.Response(200, json={"deals": {"dr": rows}, "tokensLeft": 3500-len(queries)*5,
                                         "refillRate": 60})
    adapter = KeepaDealsAdapter(http_client=httpx.Client(transport=api_transport(handler)), sleep_fn=lambda _: None)
    adapter.recheck = lambda discover, deadline: discover(KeepaBudget.load(db))
    posts = adapter.fetch_posts(SRC, None, db, cfg, NOW, None)
    assert len(posts) >= 24 and all(p.structured["conditions"] == [] for p in posts)
    assert all(p.title == "Women's satin lingerie loungewear" for p in posts)
    focus = [q for q in queries if q.get("includeCategories")]
    assert {(q["includeCategories"][0], q["page"]) for q in focus} == {
        (int(cid), page) for cid in roots for page in (0, 1)}
    assert not db.meta_get("keepa_deep_page") and not db.meta_get("keepa_sweep_page")
    assert adapter.feed_confirmed["used"] == set()
    assert not adapter.fetch_posts(SRC, None, db, cfg, NOW, None)  # same pull key
    # A full first category must not monopolize the shared intake forever.
    cfg.keepa_amazon_max_new_posts_per_pull = 1
    previous_count = len(queries)
    later = NOW + timedelta(minutes=cfg.keepa_recheck_interval_minutes)
    assert len(adapter.fetch_posts(SRC, None, db, cfg, later, None)) == 1
    assert len(queries) == previous_count + 1
    assert queries[previous_count]["includeCategories"] != focus[0]["includeCategories"]


@pytest.mark.parametrize("age,known", [(60, True), (119, True), (120, True), (121, False)])
def test_buybox_and_click_freshness_agree_at_two_hours(db, cfg, age, known):
    at = NOW - timedelta(minutes=age)
    row = seed(db, cfg, price_rechecked_at=util.iso(at))
    stamp = int(at.timestamp()/60) - verify.KEEPA_EPOCH_MINUTES
    record = product(2500, lastBuyBoxUpdate=stamp, buyBoxPrice=2500, buyBoxShipping=0)
    click_price.remember(row, record, at, NOW)
    assert (buybox.fresh_reading(row, record, cfg, NOW) is not None) == known
    assert (click_price.status(row, cfg, NOW) == "known_ok") == known
    assert verify.price_unconfirmed(row, cfg, NOW) != known
    if not known:
        # The display-only fallback must omit expired price numbers.
        db.update_deal(row["dedupe_key"], payload=row)
        assert not rank.published_deals(db, cfg, NOW)


def test_shipped_cadences_and_discovery_forecast(db, cfg):
    assert cfg.keepa_buybox_rule == cfg.keepa_click_price_rule == "on"
    assert cfg.keepa_buybox_interval_min == 120
    assert cfg.keepa_recheck_idle_min_age_h == 12
    for tier, values in cfg.keepa_volatility["targets"].items():
        assert values["top"] <= 2 and values["wall"] <= 6 and 12 <= values["rest"] <= 24
        assert values["hidden"] == (48 if tier == "FAST" else 72)
    row = seed(db, cfg)
    history = History(db, cfg, NOW)
    plan = Plan(history, [row], {row["dedupe_key"]: 0}, {}, 10, cadence_minutes=10)
    assert plan.discovery_rate() == (16, "configured_feed_ceiling")  # (12+6*2+8)*5/10
    cfg.keepa_volatility["enabled"] = False
    schedule = Schedule(history, {row["dedupe_key"]: 0}, {})
    assert schedule.max_age(row, "top") == 2
    assert schedule.max_age(row, "wall") == 6
    assert schedule.max_age(row, "rest") == 24


@pytest.mark.parametrize("raw", [
    {"amazon_used": {"enabled": "false"}}, {"amazon_used": False},
    {"keepa": {"genuine_drops_only": 1}}, {"keepa": {"focus_pages_per_category": True}},
    {"keepa": {"focus_pages_per_category": 0}}, {"keepa": {"focus_categories": "Beauty"}},
    {"keepa": {"buybox_interval_min": float("nan")}}, {"keepa": {"recheck_top_h": "2"}},
    {"keepa": {"volatility": {"targets": {"NORMAL": {"top": -1}}}}},
    {"keepa": {"recheck_adaptive": {"max_rest_h": float("inf")}}},
])
def test_strict_knobs(tmp_path, raw):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError):
        load_config(path)


def test_missing_knobs_preserve_previous_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("{}")
    for cfg in (AppConfig(), load_config(path)):
        assert cfg.amazon_used_enabled and not cfg.keepa_genuine_drops_only
        assert cfg.keepa_focus_pages_per_category == 1 and cfg.keepa_buybox_interval_min == 60


def test_used_photo_search_cannot_buy_finder_or_product_calls(db, cfg, monkeypatch):
    from dealradar import photo_recovery
    monkeypatch.setenv("KEEPA_KEY", "offline-key")
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    row = seed(db, cfg, conditions=["used"], flags=["post_link_tried"])
    row.update(asin="", image_url="")
    db.update_deal(row["dedupe_key"], payload=row)
    KeepaBudget(3500, 60, NOW).save(db)
    class Fetcher:
        def get(self, *args, **kwargs):
            pytest.fail("Amazon used photo recovery must not buy Keepa calls")
    result = photo_recovery.search_photos(db, [row], Fetcher(), cfg, NOW, None, "319")
    assert result["searched"] == result["tokens"] == 0


def test_standard_fixture_suppression_estimate(cfg):
    from tests.phone_budget_fixture import fixture_offers
    rows = fixture_offers()
    assert sum(amazon_used.excluded(d, cfg) for d in rows) == 34
    assert sum(d["store"] == "Woot" and d["conditions"] == ["used"]
               and not amazon_used.excluded(d, cfg) for d in rows) == 34


@pytest.mark.parametrize("age,requested", [(0.5, False), (6, False), (12, True), (24, True)])
def test_surplus_leaves_healthy_rest_prices_until_twelve_hours(db, cfg, monkeypatch, age, requested):
    from tests.test_price_accuracy_engine import api
    row = seed(db, cfg, seen=NOW - timedelta(days=2), codes=["SAVE"],
               price_rechecked_at=util.iso(NOW - timedelta(hours=age)))
    # Promo prices retain their existing separate confirmation rule; this
    # isolates the rest cadence from the two-hour shopper-price gate.
    calls = api(monkeypatch, {row["asin"]: product(2500)}, tokens=3500)
    KeepaBudget(3500, 60, NOW).save(db)
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert bool(calls) == requested
