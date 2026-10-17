"""Private popularity capture, capped homepage boost and offline shadow."""

import copy
import json
import logging
from collections import Counter
from dataclasses import fields
from datetime import timedelta

import httpx
import pytest

from dealradar import pipeline, popular_affordable, rank, verify
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.fetch.keepa import KeepaDealsAdapter, _selection, PRICE_TYPE_AMAZON, MIN_DROP_PCT, DATE_RANGE_90D, SORT_PCT_DROP, AMAZON_MAX_RANK
from dealradar.keepa_budget import KeepaBudget
from dealradar.models import Deal
from dealradar.publish import site
from dealradar.util import iso
from tests.http_helpers import api_transport, mock_keepa_get
from tests.test_appeal import tree
from tests.test_front_page import pick_deal
from tests.test_keepa import SRC, FakeKeepa, _amazon_row
from tests.test_publish_site import NOW, db
from tests.test_quality_trio import NOW as CHECK_NOW, _seed_amazon


ASIN = "B0POPULAR1"


def popularity(**over):
    return {"asin": ASIN, "monthly_sold": 1000, "sold_at": iso(NOW), "sales_rank": 1234, **over}


def deal(**over):
    return pick_deal(**dict({"store": "Amazon", "asin": ASIN,
                            "item": "Synthetic alpha sheets",
                            "canonical_url": f"https://www.amazon.com/dp/{ASIN}",
                            "final_price": 30, "regular_price": 60, "abs_savings": 30,
                            "pct_at_checkout": 50, "keepa_popularity": popularity()}, **over))


def product(**over):
    minutes = int(NOW.timestamp() / 60 - verify.KEEPA_EPOCH_MINUTES)
    return {"asin": ASIN, "monthlySold": 1000, "lastSoldUpdate": minutes,
            "lastUpdate": minutes, "stats": {"current": [3000, -1, -1, 1234]}, **over}


def settings(enabled=False):
    cfg = load_config()
    cfg.front_page["popular_affordable"]["enabled"] = enabled
    # These tests were written against the tilt's original (off) values; the
    # shipped values are pinned in test_shipped_switches_and_model_default.
    cfg.front_page["everyday_price"] = {"enabled": False, "full_until": 60, "floor_from": 200,
                                        "floor_factor": .85}
    return cfg


def test_shipped_switches_and_model_default(tmp_path):
    cfg = load_config()
    assert cfg.front_page["popular_affordable"] == {
        "enabled": False, "shadow": True, "min_monthly_sold": 1000, "max_price": 50,
        "pct_over_floor": 10, "max_age_h": 72, "weight": 1.25, "max_share": .25}
    assert not cfg.keepa_popular_affordable_page
    # Operator 2026-10-17: "$200 items at the top, nobody will buy that" -> on.
    assert cfg.front_page["everyday_price"] == {
        "enabled": True, "full_until": 40, "floor_from": 100, "floor_factor": .55}
    assert not AppConfig().front_page.get("everyday_price", {}).get("enabled")
    assert not AppConfig().front_page.get("popular_affordable", {}).get("enabled")
    assert not AppConfig().keepa_popular_affordable_page
    assert next(f for f in fields(Deal) if f.name == "keepa_popularity").default is None
    path = tmp_path / "config.yaml"
    path.write_text("keepa:\n  popular_affordable_page: true\n")
    assert load_config(path).keepa_popular_affordable_page


def test_exact_capture_and_deterministic_null_replacement():
    d = deal()
    verify._harvest_popularity(d, product())
    assert d["keepa_popularity"] == popularity()
    verify._harvest_popularity(d, {"asin": ASIN})
    assert d["keepa_popularity"] == popularity(monthly_sold=None, sold_at="", sales_rank=None)
    before = copy.deepcopy(d)
    verify._harvest_popularity(d, {"asin": ASIN})
    assert d == before


@pytest.mark.parametrize("sold,at,current", [
    (-1, -1, [0, 0, 0, -1]), (True, True, [0, 0, 0, True]),
    ("1000", "bad", [0, 0, 0, "1"]), (1000.0, None, [0, 0, 0, 1.5]),
    (None, float("inf"), []), (None, -1, None),
])
def test_invalid_capture_values(sold, at, current):
    d = deal()
    verify._harvest_popularity(d, product(monthlySold=sold, lastSoldUpdate=at, stats={"current": current}))
    assert d["keepa_popularity"] == popularity(monthly_sold=None, sold_at="", sales_rank=None)


def test_zero_sold_and_positive_rank_only():
    d = deal()
    verify._harvest_popularity(d, product(monthlySold=0, stats={"current": [-1, -1, -1, 0]}))
    assert d["keepa_popularity"] == popularity(monthly_sold=0, sales_rank=None)


@pytest.mark.parametrize("asin", ["B0OTHER001", "", None])
def test_asin_mismatch_never_writes(asin):
    d = deal()
    before = copy.deepcopy(d)
    verify._harvest_popularity(d, product(asin=asin))
    assert d == before


@pytest.mark.parametrize("used", [False, True])
def test_new_and_used_readings_harvest_even_without_price_opinion(used):
    d = deal(conditions=["used"] if used else [])
    assert verify.judge_live_price(d, product(stats={"current": [-1, -1, -1, 1234]}, lastUpdate=0)) is None
    assert d["keepa_popularity"] == popularity()


def test_cross_check_harvests_without_changing_request(monkeypatch):
    monkeypatch.setenv("KEEPA_KEY", "offline")
    requests = []

    def response(url, params, **kwargs):
        requests.append(params)
        return httpx.Response(200, json={"products": [product()], "tokensConsumed": 1})

    mock_keepa_get(monkeypatch, response)
    d = deal(keepa_popularity=None)
    assert verify.keepa_check(d, settings()) is None  # no historical average
    assert d["keepa_popularity"] == popularity()
    assert requests == [{"key": "offline", "domain": 1, "asin": ASIN, "stats": 90, "history": "0"}]


@pytest.mark.parametrize("used", [False, True])
def test_answered_no_opinion_is_persisted_by_apply(tmp_path, monkeypatch, used):
    monkeypatch.setenv("KEEPA_KEY", "offline")
    cfg = settings()
    cfg.keepa_buybox_top_n = 0
    cfg.keepa_recheck_surplus = cfg.keepa_recheck_top_live_adaptive = False
    cfg.keepa_volatility["enabled"] = cfg.keepa_recheck_adaptive["enabled"] = False
    calls = []

    def response(url, params, **kwargs):
        calls.append(params)
        return httpx.Response(200, json={"products": [product(stats={})],
                                        "tokensConsumed": 13 if used else 1,
                                        "tokensLeft": 1000, "refillRate": 0})

    mock_keepa_get(monkeypatch, response)
    database = DB(tmp_path / "state.db")
    try:
        _seed_amazon(database, "pop", ASIN, used=used)
        result = pipeline._recheck_prices(database, cfg, CHECK_NOW)
        stored = database.get_deal("pop")
        assert stored["keepa_popularity"] == popularity(sales_rank=None)
        assert stored["recheck_tried_at"] == iso(CHECK_NOW)
        assert result["checked"] == 0
        assert calls == [{"key": "offline", "domain": 1, "asin": ASIN, "stats": 1,
                          "history": "0", **({"offers": 20} if used else {})}]
    finally:
        database.close()


@pytest.mark.parametrize("used,live,buybox,cost", [(False, False, False, 1), (False, True, False, 2),
                                                  (False, True, True, 4), (True, False, False, 13)])
def test_product_params_and_token_math_unchanged(db, monkeypatch, used, live, buybox, cost):
    calls = []

    def response(url, params, **kwargs):
        calls.append(params)
        return httpx.Response(200, json={"products": [product()], "tokensConsumed": cost,
                                        "tokensLeft": 1000 - cost, "refillRate": 0})

    mock_keepa_get(monkeypatch, response)
    budget = KeepaBudget(1000, 0, NOW)
    budget.db = db
    result = verify.keepa_products("offline", [ASIN], 1, budget, offers=20 if used else None,
                                   live=live, buybox=buybox)
    assert result[ASIN] == product()
    assert calls == [{"key": "offline", "domain": 1, "asin": ASIN, "stats": 1, "history": "0",
                      **({"offers": 20} if used else {}), **({"update": "0"} if live else {}),
                      **({"buybox": "1"} if buybox else {})}]
    assert budget.usage.consumed == cost
    assert budget.usage.accounted == cost


def test_affordable_popular_drop_beats_expensive_and_appeal_still_multiplies():
    cfg = settings()
    cheap = deal()
    expensive = deal(final_price=200, regular_price=400, abs_savings=200)
    baseline = rank.pick_score(cheap, cfg, NOW)
    assert baseline < rank.pick_score(expensive, cfg, NOW)
    cfg.front_page["popular_affordable"]["enabled"] = True
    assert rank.pick_score(cheap, cfg, NOW) == pytest.approx(baseline * 1.25)
    assert rank.pick_score(cheap, cfg, NOW) > rank.pick_score(expensive, cfg, NOW)
    cfg.front_page["appeal"]["enabled"] = False
    assert rank.pick_score(cheap, cfg, NOW) == pytest.approx(baseline * 1.25 / 1.15)


@pytest.mark.parametrize("changes", [
    {"keepa_popularity": popularity(monthly_sold=900)},
    {"keepa_popularity": popularity(sold_at=iso(NOW - timedelta(hours=72, seconds=1)))},
    {"keepa_popularity": popularity(sold_at="")}, {"keepa_popularity": popularity(sold_at="bad")},
    {"keepa_popularity": popularity(sold_at=123)},
    {"keepa_popularity": popularity(sold_at=iso(NOW + timedelta(hours=1, seconds=1)))},
    {"pct_at_checkout": 49.9}, {"final_price": 50.01}, {"final_price": None},
    {"store": "Target", "canonical_url": "https://target.com/p/item"},
    {"conditions": ["used"]}, {"conditions": ["open-box"]}, {"conditions": ["refurbished"]},
    {"keepa_popularity": popularity(asin="B0OTHER001")},
    {"keepa_popularity": popularity(monthly_sold=True)}, {"keepa_popularity": None},
])
def test_nonqualifying_factor_is_one(changes):
    assert rank.popular_affordable_factor(deal(**changes), settings(True), NOW) == 1


def test_boundaries_and_floor_are_derived():
    cfg = settings(True)
    assert rank.popular_affordable_factor(deal(final_price=50, keepa_popularity=popularity(
        sold_at=iso(NOW - timedelta(hours=72)))), cfg, NOW) == 1.25
    cfg.min_pct = 45
    assert rank.popular_affordable_factor(deal(), cfg, NOW) == 1
    assert rank.popular_affordable_factor(deal(pct_at_checkout=55), cfg, NOW) == 1.25
    cfg.front_page.pop("popular_affordable")
    assert rank.popular_affordable_factor(deal(), cfg, NOW) == 1


@pytest.mark.parametrize("changes", [{"price_unconfirmed": True},
    {"price_rechecked_at": iso(NOW - timedelta(hours=3, seconds=1))},
    {"image_url": "http://img.example/photo.jpg"}, {"category": "auto"}])
def test_boost_never_restores_failed_pick_gate(changes):
    assert rank.pick_score(deal(**changes), settings(True), NOW) is None


def test_window_cap_requeues_at_unboosted_rank_and_keeps_existing_caps():
    cfg = settings(True)
    categories = ["home", "kitchen", "fashion", "beauty", "health", "grocery"]
    qualifying = [deal(dedupe_key=f"pop-{i}", item=f"Popular {i} Thing", brand=f"Brand {i}",
                       category=categories[i % len(categories)]) for i in range(30)]
    ordinary = [deal(dedupe_key=f"ordinary-{i}", item=f"Ordinary {i} Thing", brand=f"Other {i}",
                     keepa_popularity=None, category=categories[i % len(categories)]) for i in range(30)]
    scored = [(d, 100 * 1.25) for d in qualifying] + [(d, 110) for d in ordinary]
    boosted = set()
    picked = rank.top_picks(scored, cfg.front_page, NOW, cfg=cfg, boosted=boosted)
    assert len(picked) == 40  # existing store ceiling
    assert len(boosted) == 6
    assert [d["dedupe_key"] for d in picked[:8]] == [f"pop-{i}" for i in range(6)] + ["ordinary-0", "ordinary-1"]
    assert any(d["dedupe_key"] == "pop-6" for d in picked)
    for start in range(0, len(picked), rank.PICK_WINDOW):
        assert sum(d["dedupe_key"] in boosted for d in picked[start:start + rank.PICK_WINDOW]) <= 6
    assert max(Counter(d["brand"] for d in picked).values()) <= 3
    assert max(Counter(d["category"] for d in picked).values()) <= 24
    # Existing brand/category/twin constraints still reject overflow.
    capped = [deal(dedupe_key=str(i), item=f"Same Title Twin {i}", brand="Shared") for i in range(30)]
    assert len(rank.top_picks([(d, 100) for d in capped], cfg.front_page, NOW, cfg=cfg)) == 2
    capped = [dict(d, item=f"Unique {i} Item", brand=f"Brand {i}") for i, d in enumerate(capped)]
    cfg.front_page["max_same_category_run"] = 30
    assert len(rank.top_picks([(d, 100) for d in capped], cfg.front_page, NOW, cfg=cfg)) == 24


def test_disabled_top_picks_matches_omitted_for_ties_and_all_rows():
    cfg = settings()
    rows = [deal(dedupe_key=str(i), item=f"Unique {i} Item", brand=f"Brand {i}",
                 category="home" if i % 2 else "kitchen") for i in range(50)]
    scored = [(d, 100 if i % 2 else 80) for i, d in enumerate(rows)]
    before = rank.top_picks(scored, cfg.front_page, NOW, cfg=cfg)
    cfg.front_page.pop("popular_affordable")
    assert rank.top_picks(scored, cfg.front_page, NOW, cfg=cfg) == before


@pytest.mark.parametrize("mode", ["membership", "open", "support", "shipped"])
def test_disabled_shadow_and_private_facts_preserve_every_public_byte(db, tmp_path, monkeypatch, mode):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = settings()
    cfg.support_enabled = mode in {"support", "shipped"}
    cfg.paywall_open_access = mode == "open"
    cfg.paywall_teaser_count = 2
    source = [deal(), deal(dedupe_key="expensive", item="Expensive kitchen item", brand="Other",
                          final_price=200, regular_price=400, abs_savings=200, category="kitchen")]
    out = tmp_path / "site"
    options = cfg.front_page.pop("popular_affordable")
    everyday = cfg.front_page.pop("everyday_price")
    baseline = None
    for section in (None, dict(options, shadow=False), options):
        for price_section in (None, everyday):
            for with_facts in (False, True):
                if section is not None:
                    cfg.front_page["popular_affordable"] = section
                if price_section is None:
                    cfg.front_page.pop("everyday_price", None)
                else:
                    cfg.front_page["everyday_price"] = price_section
                rows = copy.deepcopy(source)
                if not with_facts:
                    for d in rows:
                        d.pop("keepa_popularity")
                db.meta_set("teaser_recent", "[]")
                result = site.render_site(db, cfg, NOW, out_dir=out, deals=rows, last_run={"run_key": "byte-pin"})
                current = tree(out)
                if baseline is None:
                    baseline = current
                assert current == baseline
                assert all(word not in data.lower() for data in current.values()
                           for word in (b"monthly_sold", b"keepa_popularity", b"bought", b"everyday_price"))
                assert not {"popular_affordable", "everyday_price"} & result["full_payload"]["meta"]["front_page"].keys()


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("everyday", [False, True])
def test_exact_shadow_counts_and_same_key_double_render(db, tmp_path, monkeypatch, enabled, everyday):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = settings(enabled)
    cfg.front_page["everyday_price"]["enabled"] = everyday
    cfg.paywall_open_access = True
    cfg.front_page["top_picks"]["count"] = 1
    cheap = deal(dedupe_key="cheap", item="Synthetic alpha sheets")
    expensive = deal(dedupe_key="expensive", item="Synthetic beta computer", category="electronics",
                      final_price=200, regular_price=400, abs_savings=200, pct_at_checkout=55,
                      keepa_popularity=popularity(monthly_sold=5000, sold_at=iso(NOW - timedelta(hours=73))))
    rows = [expensive, cheap]
    before = copy.deepcopy(rows)
    out = tmp_path / "site"
    expected = {"amazon_candidates": 2, "with_monthly_sold": 2, "fresh": 1,
                "qualifying_at": {"500": 1, "1000": 1, "5000": 0},
                "boosted_in_picks": 1, "entered_picks": 1, "left_picks": 1,
                "first_window_changed": 1,
                "median_price_top24": {"without": 200, "with": 30},
                "under_60_share_top24": {"without": 0, "with": 1},
                "amazon_in_picks": {"without": 1, "with": 1},
                "tilted_top24": {"without": 0, "with": 1}, "examples": [cheap["item"]]}
    results, snapshots = [], []
    for _ in range(2):
        result = site.render_site(db, cfg, NOW, out_dir=out, deals=rows, last_run={"run_key": "same-key"})
        results.append(result["popular_affordable"])
        snapshots.append((tree(out), db.meta_get(popular_affordable.META),
                          [tuple(r) for r in db.conn.execute("SELECT * FROM price_history")],
                          [tuple(r) for r in db.conn.execute("SELECT * FROM alerts_sent")]))
    assert results == [expected, expected]
    assert snapshots[0] == snapshots[1]
    assert rows == before
    assert set(json.loads(db.meta_get(popular_affordable.META))) == {"same-key"}


def test_shadow_ring_replaces_and_prunes_26_hour_entries(db):
    popular_affordable.record(db, NOW - timedelta(hours=26), "expired", {"n": 1})
    popular_affordable.record(db, NOW - timedelta(hours=25), "retained", {"n": 2})
    popular_affordable.record(db, NOW, "same", {"n": 3})
    popular_affordable.record(db, NOW, "same", {"n": 4})
    assert json.loads(db.meta_get(popular_affordable.META)) == {
        "retained": {"at": iso(NOW - timedelta(hours=25)), "n": 2},
        "same": {"at": iso(NOW), "n": 4}}


class PopularFeed(FakeKeepa):
    def __init__(self):
        super().__init__(amazon=[_amazon_row(i) for i in range(1200)], tokens=1200)
        self.selections = []

    def __call__(self, request):
        if request.url.path == "/deal":
            selection = json.loads(request.url.params["selection"])
            self.selections.append(selection)
            if "currentRange" in selection:
                assert selection["currentRange"] == [0, 5000] and selection["sortType"] == 3
                self.calls.append(("popular", selection["page"]))
                self.tokens -= 5
                return httpx.Response(200, json={"deals": {"dr": [_amazon_row(i) for i in (0, 1201)]},
                                                "tokensLeft": self.tokens, "refillRate": 0})
        return super().__call__(request)


def pull_feed(db, monkeypatch, *, enabled=False, pages=6, cap=10000, tokens=1200, maximum=50):
    monkeypatch.setenv("KEEPA_KEY", "offline")
    cfg = settings()
    cfg.keepa_popular_affordable_page = enabled
    cfg.keepa_focus_categories = []
    cfg.keepa_sweep_enabled = False
    cfg.keepa_amazon_pages_per_pull = pages
    cfg.keepa_amazon_max_new_posts_per_pull = cap
    if maximum is None:
        cfg.front_page["popular_affordable"].pop("max_price")
    fake = PopularFeed()
    fake.tokens = tokens
    KeepaBudget(tokens, 0, NOW).save(db)
    adapter = KeepaDealsAdapter(http_client=httpx.Client(transport=api_transport(fake)), sleep_fn=lambda _: None)
    posts = adapter.fetch_posts(SRC, None, db, cfg, NOW, None)
    return posts, fake, adapter


def test_optional_page_replaces_one_general_page_with_identical_other_selections(db, monkeypatch, caplog):
    _, baseline, _ = pull_feed(db, monkeypatch)
    db.meta_set("keepa_last_fetch_at", "")
    with caplog.at_level(logging.INFO):
        posts, enabled, adapter = pull_feed(db, monkeypatch, enabled=True)
    general = _selection(PRICE_TYPE_AMAZON, (MIN_DROP_PCT, 100), DATE_RANGE_90D, SORT_PCT_DROP, AMAZON_MAX_RANK)
    assert [s for s in baseline.selections if s["priceTypes"] == [0]] == [{**general, "page": n} for n in range(6)]
    amazon = [s for s in enabled.selections if s["priceTypes"] == [0]]
    assert len(amazon) == 6
    assert amazon[0] == {**general, "currentRange": [0, 5000], "sortType": 3}
    assert amazon[1:] == [{**general, "page": n} for n in range(5)]
    assert [s for s in enabled.selections if s["priceTypes"] != [0]] == [
        s for s in baseline.selections if s["priceTypes"] != [0]]
    assert len({p.structured["asin"] for p in posts}) == len(posts)
    assert adapter.pull_summary == {"popular_page_rows": 2, "popular_page_admitted": 2}
    assert all(field not in caplog.text for field in adapter.pull_summary)


def test_optional_page_shares_new_post_cap_and_mapper(db, monkeypatch):
    posts, fake, adapter = pull_feed(db, monkeypatch, enabled=True, cap=3)
    assert len(posts) == 3
    assert len([s for s in fake.selections if s["priceTypes"] == [0]]) == 2
    assert adapter.pull_summary["popular_page_admitted"] == 2
    cfg = settings()
    mapped = KeepaDealsAdapter._map_row(_amazon_row(1201), SRC, NOW,
        floors=tuple(sorted({cfg.min_pct, cfg.used_min_pct, cfg.site_show_from_pct})),
        list_ratio=cfg.keepa_list_price_min_avg_ratio)
    actual = next(p for p in posts if p.structured["asin"] == "B0AMZ01201")
    assert actual.content_hash == mapped.content_hash and actual.text == mapped.text
    assert "keepa_popularity" not in actual.structured


@pytest.mark.parametrize("pages,tokens,maximum", [(1, 1200, 50), (6, 304, 50), (6, 1200, None)])
def test_optional_page_reserve_single_page_and_missing_ceiling_skip(db, monkeypatch, pages, tokens, maximum):
    _, fake, adapter = pull_feed(db, monkeypatch, enabled=True, pages=pages, tokens=tokens, maximum=maximum)
    assert not any("currentRange" in s for s in fake.selections)
    assert adapter.pull_summary == {"popular_page_rows": 0, "popular_page_admitted": 0}


def test_private_field_never_changes_audit_fingerprint_or_critic_prompt():
    from dealradar.legitimacy import fingerprint
    from dealradar.parse.claude_client import _critic_entries
    a = deal()
    b = dict(a, keepa_popularity=None)
    assert fingerprint(a, settings()) == fingerprint(b, settings())
    assert _critic_entries([a]) == _critic_entries([b])


@pytest.mark.parametrize("old", [None, popularity(monthly_sold=5000)])
def test_price_correction_retains_the_current_popularity_record(db, monkeypatch, old):
    from tests.test_price_accuracy_engine import NOW as at, api, bucket, seed, product as paid_product
    cfg = settings()
    cfg.keepa_buybox_top_n = 0
    cfg.keepa_recheck_surplus = cfg.keepa_recheck_top_live_adaptive = False
    cfg.keepa_volatility["enabled"] = cfg.keepa_recheck_adaptive["enabled"] = False
    before = seed(db, cfg, asin=ASIN, keepa_popularity=old)
    record = paid_product(2600, asin=ASIN, current=[2600, -1, -1, 1234])
    record.update(monthlySold=1000, lastSoldUpdate=record["lastUpdate"])
    api(monkeypatch, {ASIN: record})
    bucket(db, 500)
    result = pipeline._recheck_prices(db, cfg, at)
    after = db.get_deal(before["dedupe_key"])
    assert result["drift"]["repriced"] == 1 and after["final_price"] == 26
    assert after["keepa_popularity"] == popularity(sold_at=iso(at))


def test_cap_resets_at_the_browser_window_boundary():
    cfg = settings(True)
    categories = ["home", "kitchen", "fashion", "beauty", "health", "grocery"]
    rows = [deal(dedupe_key=str(i), item=f"Unique {i} Item", brand=f"Brand {i}",
                 category=categories[i % 6], keepa_popularity=None if 6 <= i < 24 else popularity())
            for i in range(30)]
    scores = [(d, 200 if i < 6 else 180 if i < 24 else 150) for i, d in enumerate(rows)]
    boosted = set()
    picked = rank.top_picks(scores, cfg.front_page, NOW, cfg=cfg, boosted=boosted)
    assert picked == rows
    assert [sum(d["dedupe_key"] in boosted for d in picked[start:start + 24])
            for start in (0, 24)] == [6, 6]


def test_popularity_is_private_with_the_checker_enabled(db, tmp_path, monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = settings()
    cfg.paywall_open_access = True
    cfg.checker["enabled"] = True
    snapshots = []
    for facts in (None, popularity()):
        result = site.render_site(db, cfg, NOW, out_dir=tmp_path / "site",
                                  deals=[deal(keepa_popularity=facts)])
        assert result["checker"]["aliases"] > 0
        snapshots.append(tree(tmp_path / "site"))
    assert snapshots[0] == snapshots[1]
    assert all(word not in data.lower() for data in snapshots[1].values()
               for word in (b"monthly_sold", b"keepa_popularity", b"bought"))


@pytest.mark.parametrize("ahead,fresh", [(1, True), (1800, True), (3600, True), (3601, False)])
def test_clock_skew_is_fresh_in_factor_and_shadow(ahead, fresh):
    cfg = settings()
    d = deal(keepa_popularity=popularity(sold_at=iso(NOW + timedelta(seconds=ahead))))
    assert rank.popular_affordable_factor(d, cfg, NOW) == (1.25 if fresh else 1)
    summary = popular_affordable.compare([(d, rank.pick_score(d, cfg, NOW))], cfg, NOW)
    assert summary["fresh"] == int(fresh)
    assert summary["qualifying_at"]["1000"] == int(fresh)


@pytest.mark.parametrize("price,expected", [
    (None, 1), ("100", 1), (True, 1), (-1, 1), (float("nan"), 1), (float("inf"), 1),
    (0, 1), (30, 1), (60, 1), (130, .925), (200, .85), (300, .85),
])
def test_everyday_price_is_a_soft_linear_tilt(price, expected):
    options = settings().front_page["everyday_price"]
    d = deal(final_price=price)
    before = copy.deepcopy(options)
    assert rank.everyday_price_factor(d, options) == pytest.approx(expected)
    assert options == before
    assert rank.everyday_price_factor(d, {}) == 1
    assert rank.everyday_price_factor({}, options) == 1


@pytest.mark.parametrize("store,url", [("Amazon", f"https://amazon.com/dp/{ASIN}"),
                                       ("Walmart", "https://walmart.com/ip/synthetic")])
def test_everyday_price_applies_after_appeal_for_every_store(store, url):
    cfg = settings()
    d = deal(store=store, canonical_url=url, final_price=130)
    baseline = rank.pick_score(d, cfg, NOW)
    cfg.front_page["everyday_price"]["enabled"] = True
    assert rank.pick_score(d, cfg, NOW) == pytest.approx(baseline * .925)
    cfg.front_page["appeal"]["enabled"] = False
    assert rank.pick_score(d, cfg, NOW) == pytest.approx(baseline * .925 / 1.15)
    assert rank.pick_score(dict(d, price_unconfirmed=True), cfg, NOW) is None


@pytest.mark.parametrize("everyday", [False, True])
def test_public_ps_omits_popularity_but_keeps_everyday_price(db, tmp_path, monkeypatch, everyday):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = settings(True)
    cfg.front_page["everyday_price"]["enabled"] = everyday
    cfg.front_page["everyday_price"]["full_until"] = 10
    rows = [deal(), deal(dedupe_key="without-facts", keepa_popularity=None)]
    snapshots = []
    for active in (True, False):
        cfg.front_page["popular_affordable"]["enabled"] = active
        result = site.render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=rows)
        published = result["full_payload"]["deals"]
        assert published[0]["ps"] == published[1]["ps"]
        snapshots.append(published[0]["ps"])
    assert snapshots[0] == snapshots[1]
    cfg.front_page["everyday_price"]["enabled"] = False
    unmodified = rank.pick_score(rows[0], cfg, NOW)
    factor = rank.everyday_price_factor(rows[0], cfg.front_page["everyday_price"]) if everyday else 1
    assert snapshots[0] == round(unmodified * factor, 1)


def test_first_window_counts_new_cards_not_shifted_positions():
    cfg = settings()
    rows = [deal(dedupe_key=str(i), item=f"Synthetic {i} item", brand=f"Brand {i}",
                 category="home" if i % 2 else "kitchen",
                 keepa_popularity=popularity() if i == 24 else None) for i in range(25)]
    scored = [(d, 100 - i * .5) for i, d in enumerate(rows)]
    result = popular_affordable.compare(scored, cfg, NOW)
    assert result["first_window_changed"] == 1
    assert result["entered_picks"] == result["left_picks"] == 0
    # A pure reorder within a full window changes no membership.
    result = popular_affordable.compare(scored[1:], cfg, NOW)
    assert result["first_window_changed"] == 0


def test_shadow_includes_the_everyday_tilt_and_share_cap():
    cfg = settings()
    cfg.front_page["top_picks"]["count"] = 24
    cfg.front_page["appeal"]["enabled"] = False
    rows = [deal(dedupe_key=str(i), item=f"Synthetic {i} item", brand=f"Brand {i}",
                 category="home" if i % 2 else "kitchen",
                 final_price=200 if i < 24 else 30, keepa_popularity=None) for i in range(30)]
    scored = [(d, 100 if i < 24 else 90) for i, d in enumerate(rows)]
    summary = popular_affordable.compare(scored, cfg, NOW)
    assert summary["first_window_changed"] == summary["entered_picks"] == summary["left_picks"] == 6
    assert summary["under_60_share_top24"] == {"without": 0, "with": .25}
    assert summary["boosted_in_picks"] == 0
    # Both tilts keep the popularity cap and all over-cap cards compete again.
    for d in rows:
        d.update(final_price=30, keepa_popularity=popularity())
    summary = popular_affordable.compare(scored, cfg, NOW)
    assert summary["boosted_in_picks"] == 6


@pytest.mark.parametrize("stage", ["compare", "record"])
def test_shadow_failure_never_stops_or_changes_publication(db, tmp_path, monkeypatch, caplog, stage):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = settings()
    rows = [deal()]
    out = tmp_path / "site"
    site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
    baseline, ring = tree(out), db.meta_get(popular_affordable.META)

    def fail(*args, **kwargs):
        raise RuntimeError("popular_affordable monthly_sold qualifying_at Synthetic private example")

    monkeypatch.setattr(popular_affordable, stage, fail)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        result = site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
    assert result.get("popular_affordable") is None
    assert tree(out) == baseline
    assert db.meta_get(popular_affordable.META) == ring
    assert [r.getMessage() for r in caplog.records] == ["RuntimeError"]
    assert all(r.exc_info is None for r in caplog.records)


@pytest.mark.parametrize("failure", [RuntimeError("offline refusal"),
    httpx.ConnectError("offline transport failure"),
    httpx.HTTPStatusError("offline 503", request=httpx.Request("GET", "https://api.keepa.com/deal"),
                         response=httpx.Response(503))])
def test_optional_page_errors_consume_their_slot_and_keep_the_pull(db, monkeypatch, caplog, failure):
    query_rows = KeepaDealsAdapter._query_rows
    attempts = []

    def query(self, pull, selection, label):
        if "currentRange" in selection:
            attempts.append(selection)
            raise failure
        return query_rows(self, pull, selection, label)

    monkeypatch.setattr(KeepaDealsAdapter, "_query_rows", query)
    with caplog.at_level(logging.WARNING):
        posts, fake, adapter = pull_feed(db, monkeypatch, enabled=True)
    assert len(attempts) == 1
    assert len([s for s in fake.selections if s["priceTypes"] == [0]]) == 5
    assert posts and "retried next pull" in caplog.text
    assert adapter.pull_summary == {"popular_page_rows": 0, "popular_page_admitted": 0}


def test_shipped_pipeline_and_cli_never_output_shadow_data(tmp_path, monkeypatch, caplog, capsys):
    from argparse import Namespace
    from dealradar import cli
    from tests.test_e2e_offline import _offline_env, _run

    env = _offline_env(tmp_path, monkeypatch)
    monkeypatch.delenv("KEEPA_KEY", raising=False)
    step_summary = tmp_path / "step-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    cfg = load_config(env["cfg"])
    assert not cfg.front_page["popular_affordable"]["enabled"]
    assert cfg.front_page["popular_affordable"]["shadow"]
    assert cfg.front_page["everyday_price"]["enabled"]  # shipped on 2026-10-17
    assert not cfg.keepa_popular_affordable_page
    database = DB(env["db"])
    rows = [deal(dedupe_key="private-example", item="Synthetic alpha sheets", score=50)]
    rows += [deal(dedupe_key=f"baseline-{i}", item=f"Synthetic beta{i} item", category=category,
                  final_price=200, regular_price=400, abs_savings=200,
                  keepa_popularity=None, score=100)
             for i, category in enumerate(("home", "kitchen", "beauty"))]
    for d in rows:
        database.insert_deal(**{k: d[k] for k in (
            "dedupe_key", "store", "item", "canonical_url", "final_price", "pct_at_checkout",
            "abs_savings", "online_only", "expires_at")}, normalized_item=d["item"].lower(),
            payload=d, now=NOW)
        # Previously delivered cards are still ranking candidates, without dry-run alert copy.
        database.record_alert(deal_key=d["dedupe_key"], channel="telegram",
                              dedup_token=d["dedupe_key"] + "|telegram", now=NOW,
                              price_at_send=d["final_price"], tier_at_send="unverified")
    database.meta_set("recategorized_v2", json.dumps({"keys": [d["dedupe_key"] for d in rows]}))
    database.close()
    with caplog.at_level(logging.INFO):
        summary = _run(env)
        # The CLI also rejects a private field supplied by a future caller.
        monkeypatch.setattr(pipeline, "run", lambda **kw: {
            **summary, "popular_affordable": {"monthly_sold": 1000, "examples": [rows[0]["item"]]}})
        assert cli.cmd_run(Namespace(fast_lane=False, db=env["db"], config=env["cfg"],
                                     dry_run_telegram=True)) == 0
    database = DB(env["db"])
    try:
        ring = json.loads(database.meta_get(popular_affordable.META))
        assert ring[summary["run_key"]]["qualifying_at"]["1000"] >= 1
        assert rows[0]["item"] in ring[summary["run_key"]]["examples"]
    finally:
        database.close()
    assert "popular_affordable" not in summary
    assert any(" done in " in r.getMessage() for r in caplog.records)
    captured = capsys.readouterr()
    output = captured.out + captured.err + caplog.text + step_summary.read_text()
    assert all(word not in output for word in (
        "popular_affordable", "monthly_sold", "qualifying_at", rows[0]["item"]))


def test_huge_integer_prices_stay_neutral_or_floor():
    from dealradar.rank import everyday_price_factor
    options = {"full_until": 60, "floor_from": 200, "floor_factor": 0.85}
    assert everyday_price_factor({"final_price": 10**1000}, options) == 0.85
    assert everyday_price_factor({"final_price": -10**1000}, options) == 1.0


def test_public_ps_rounding_boundary_does_not_reveal_popularity(db, tmp_path, monkeypatch):
    # Review 255b: multiply-then-divide changed the rounded ps (104.7 vs 104.6).
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = settings(True)
    cfg.front_page["popular_affordable"]["enabled"] = True
    facts = dict(final_price=5.29, abs_savings=9, pct_at_checkout=63)
    rows = [deal(**facts), deal(dedupe_key="without-facts", keepa_popularity=None, **facts)]
    published = site.render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=rows)["full_payload"]["deals"]
    assert published[0]["ps"] == published[1]["ps"]
