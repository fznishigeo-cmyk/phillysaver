"""087b: due checks, bounded discovery, then useful overflow rechecks."""

import json
from datetime import timedelta

import httpx
import pytest

from dealradar import pipeline, util, verify
from dealradar.fetch.keepa import KeepaDealsAdapter
from dealradar.http_client import PoliteFetcher
from dealradar.keepa_budget import KeepaBudget
from dealradar.keepa_rechecks import History, Schedule
from tests.test_keepa import FakeKeepa, SRC, _junk_row, _amazon_row, _wh_row
from tests.test_price_accuracy_engine import NOW, api, db, product, seed
from tests.test_value_per_token_rechecks import cfg, train
from tests.test_wall_confirmation_coverage import remember


def setup_pull(cfg, db, monkeypatch, *, tokens=1200, rows=1500):
    cfg.keepa_amazon_pages_per_pull = 1
    cfg.keepa_warehouse_pages_per_pull = 2
    cfg.keepa_focus_categories = []
    cfg.keepa_sweep_enabled = False
    monkeypatch.setenv("KEEPA_KEY", "offline")
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    KeepaBudget(tokens, 20, NOW).save(db)
    fake = FakeKeepa(**{name: [_junk_row(i, prefix) for i in range(rows)]
                       for name, prefix in (("amazon", "B0AAA"), ("fresh", "B0FFF"),
                                            ("deep", "B0DDD"))}, tokens=tokens)
    return fake


def pull(cfg, db, fake, *, deadline=None, image_backfill=False):
    summary = {}
    sleeps = []
    fetcher = PoliteFetcher(db, cfg, transport=httpx.MockTransport(fake), sleep_fn=sleeps.append)
    adapter = KeepaDealsAdapter()

    def recheck(discover, end):
        summary.update(pipeline._recheck_prices(
            db, cfg, NOW, image_backfill=image_backfill, fetcher=fetcher,
            deadline=end, overflow_discovery=discover, rate_limited=adapter.rate_limited))

    adapter.recheck = recheck
    try:
        posts = adapter.fetch_posts(SRC, fetcher, db, cfg, NOW, deadline)
        # A second pull in the same run cannot buy any more pages.
        assert adapter.fetch_posts(SRC, fetcher, db, cfg, NOW, deadline) == []
    finally:
        fetcher.close()
    return posts, summary, sleeps


def test_due_then_bounded_discovery_then_idle_with_polite_shared_client(db, cfg, monkeypatch):
    fake = setup_pull(cfg, db, monkeypatch)
    due = seed(db, cfg, price_rechecked_at=util.iso(NOW - timedelta(hours=2)))
    idle = seed(db, cfg, asin="B000000002", price_rechecked_at=util.iso(NOW - timedelta(minutes=35)))
    remember(db, top=[due, idle])
    fake.products = {d["asin"]: product(2500, asin=d["asin"]) for d in (due, idle)}
    for name, factory in (("amazon", _amazon_row), ("deep", _wh_row)):
        for page in range(1, 8):
            fake.lists[name][page * 150] = factory(page)
    posts, result, sleeps = pull(cfg, db, fake)
    assert fake.calls[:4] == [("amazon", 0), ("fresh", 0), ("deep", 0), ("product", 1)]
    assert len(fake.calls[4:-1]) == cfg.keepa_overflow_extra_pages_max
    assert all(name != "product" and page >= 1 for name, page in fake.calls[4:-1])
    assert fake.calls[-1] == ("product", 1)
    assert result["done"]["top_new"] == result["done"]["idle_top_new"] == 1
    extra = result["overflow_discovery"]
    assert extra["pages"] == 8 and extra["tokens"] == 40
    assert extra["new_candidates"] == len(posts) > 0
    assert len({p.content_hash for p in posts}) == len(posts)
    assert result["tokens"]["bucket_end"] >= cfg.keepa_reserve_tokens
    assert len(sleeps) == len(fake.calls) - 1
    assert all(0 < seconds <= cfg.min_host_interval_s for seconds in sleeps)
    assert all(params["history"] == "0" for params in fake.product_params)


@pytest.mark.parametrize("tokens,limit,reserve,pages", [
    (1200, 2, 300, 2), (1025, 8, 300, 2), (1019, 8, 300, 0),
    (1200, 8, 1180, 1), (1200, 0, 300, 0), (500, 8, 300, 0),
])
def test_overflow_estimate_page_cap_and_reserve(db, cfg, monkeypatch, tokens, limit, reserve, pages):
    fake = setup_pull(cfg, db, monkeypatch, tokens=tokens)
    cfg.keepa_overflow_extra_pages_max = limit
    cfg.keepa_reserve_tokens = reserve
    _, result, _ = pull(cfg, db, fake)
    assert result["overflow_discovery"]["pages"] == pages
    assert result["overflow_discovery"]["tokens"] == pages * 5
    assert result["tokens"]["bucket_end"] >= reserve


def test_short_pages_or_full_intake_caps_leave_overflow_for_rechecks(db, cfg, monkeypatch):
    fake = setup_pull(cfg, db, monkeypatch, rows=10)
    _, result, _ = pull(cfg, db, fake)
    assert result["overflow_discovery"]["pages"] == 0
    db.meta_set("keepa_last_fetch_at", "")
    fake = setup_pull(cfg, db, monkeypatch)
    fake.lists["amazon"] = [_amazon_row(i) for i in range(1500)]
    fake.lists["fresh"] = [_wh_row(i) for i in range(1500)]
    posts, result, _ = pull(cfg, db, fake)
    assert len(posts) == cfg.keepa_amazon_max_new_posts_per_pull + cfg.keepa_warehouse_max_new_posts_per_pull
    assert result["overflow_discovery"]["pages"] == 0


def test_overflow_429_stops_pages_and_rechecks(db, cfg, monkeypatch):
    fake = setup_pull(cfg, db, monkeypatch)
    idle = seed(db, cfg, price_rechecked_at=util.iso(NOW - timedelta(minutes=35)))
    remember(db, top=[idle])
    fake.fail[("amazon", 1)] = httpx.Response(429, json={"tokensLeft": 1100, "refillRate": 20})
    _, result, _ = pull(cfg, db, fake)
    assert fake.calls == [("amazon", 0), ("fresh", 0), ("deep", 0), ("amazon", 1)]
    assert result["overflow_discovery"]["rate_limited"]
    assert result["overflow_discovery"]["tokens"] == 0
    assert result["checked"] == 0


def test_free_overflow_confirmation_prevents_duplicate_paid_reading(db, cfg, monkeypatch):
    fake = setup_pull(cfg, db, monkeypatch)
    idle = seed(db, cfg, price_rechecked_at=util.iso(NOW - timedelta(minutes=35)))
    remember(db, top=[idle])
    fake.lists["amazon"][150] = {"asin": idle["asin"], "current": [2500], "avg": [10000],
        "title": "Widget Pro", "lastUpdate": int(NOW.timestamp() / 60) - verify.KEEPA_EPOCH_MINUTES}
    _, result, _ = pull(cfg, db, fake)
    assert result["done"]["idle_top_new"] == 0
    assert verify.price_confirmed_at(db.get_deal(idle["dedupe_key"])) == NOW


def test_photo_fallback_keeps_a_newer_overflow_feed_clock(db, cfg, monkeypatch):
    fake = setup_pull(cfg, db, monkeypatch)
    deal = seed(db, cfg, price_rechecked_at=util.iso(NOW - timedelta(minutes=35)))
    deal["image_url"] = ""
    db.update_deal(deal["dedupe_key"], payload=deal)
    remember(db, top=[deal])
    minute = int(NOW.timestamp() / 60) - verify.KEEPA_EPOCH_MINUTES
    fake.lists["amazon"][150] = {"asin": deal["asin"], "current": [2500], "avg": [10000],
        "title": "Widget Pro", "lastUpdate": minute}
    fake.products = {deal["asin"]: dict(product(2500), lastUpdate=minute - 30)}
    _, result, _ = pull(cfg, db, fake, image_backfill=True)
    assert result["done"]["photo"] == 1 and result["done"]["idle_top_new"] == 0
    assert verify.price_confirmed_at(db.get_deal(deal["dedupe_key"])) == NOW


@pytest.mark.parametrize("paid_lag,feed_lag,expected", [(0, 0, 26), (10, 20, 26), (20, 10, 25)])
def test_extra_feed_cannot_restore_a_price_the_due_check_just_corrected(
        db, cfg, monkeypatch, paid_lag, feed_lag, expected):
    from dealradar.dedupe import upsert_extracted
    from dealradar.parse.schema import LLMDeal, to_extracted
    from dealradar.savings import compute_savings

    fake = setup_pull(cfg, db, monkeypatch)
    due = seed(db, cfg, price_rechecked_at=util.iso(NOW - timedelta(hours=2)))
    remember(db, top=[due])
    fake.products = {due["asin"]: dict(product(2600), lastUpdate=int(
        (NOW - timedelta(minutes=paid_lag)).timestamp() / 60) - verify.KEEPA_EPOCH_MINUTES)}
    fake.lists["amazon"][150] = {"asin": due["asin"], "current": [2500], "avg": [10000],
        "title": "Widget Pro", "lastUpdate": int(
            (NOW - timedelta(minutes=feed_lag)).timestamp() / 60) - verify.KEEPA_EPOCH_MINUTES}
    posts, _, _ = pull(cfg, db, fake)
    for post in posts:
        extracted = to_extracted(LLMDeal.model_validate(post.structured), post, "UTC", NOW)
        upsert_extracted(db, extracted, compute_savings(extracted), post, cfg, NOW)
    assert db.get_deal(due["dedupe_key"])["final_price"] == expected


@pytest.mark.parametrize("stage", ["feed", "due"])
def test_first_429_also_stops_discovery_when_budget_body_looks_spendable(db, cfg, monkeypatch, stage):
    import time
    fake = setup_pull(cfg, db, monkeypatch)
    due = seed(db, cfg, price_rechecked_at=util.iso(NOW - timedelta(hours=2)))
    remember(db, top=[due])
    fake.fail[("amazon", 0) if stage == "feed" else ("product", None)] = httpx.Response(
        429, json={"tokensLeft": 1100, "refillRate": 20})
    fetcher = PoliteFetcher(db, cfg, transport=httpx.MockTransport(fake), sleep_fn=lambda _: None)
    stats = {}
    try:
        pipeline._fetch_all([SRC], fetcher, db, cfg, NOW,
            time.monotonic() + 1000, False, price_recheck=stats)
    finally:
        fetcher.close()
    assert stats["rate_limited"] and "overflow_discovery" not in stats
    assert fake.calls == ([("amazon", 0)] if stage == "feed" else
                          [("amazon", 0), ("fresh", 0), ("deep", 0), ("product", 1)])


def test_unknown_bucket_does_not_invent_overflow(db, cfg, monkeypatch):
    fake = setup_pull(cfg, db, monkeypatch)
    fake.tokens = None
    db.meta_set("keepa_budget", "")
    _, result, _ = pull(cfg, db, fake)
    assert result["overflow_discovery"]["pages"] == 0


def test_missing_page_receipt_keeps_its_debit_for_the_next_page(db, cfg, monkeypatch):
    fake = setup_pull(cfg, db, monkeypatch, tokens=1025)
    # Both overflow answers are unreadable, so neither can restore the
    # old balance. The ten predicted overflow tokens buy only two pages.
    fake.fail[("amazon", 1)] = httpx.Response(200, text="truncated")
    fake.fail[("deep", 1)] = httpx.Response(200, text="truncated")
    _, result, _ = pull(cfg, db, fake)
    assert result["overflow_discovery"]["tokens"] == 10
    assert result["overflow_discovery"]["estimated_tokens"] == 10
    assert result["tokens"]["bucket_end"] == 1000
    assert len(fake.calls) == 5


def test_wall_max_age_overrides_stable_lengthening_and_zero_acceleration(db, cfg):
    stable = seed(db, cfg, price_rechecked_at=util.iso(NOW - timedelta(hours=3, minutes=50)))
    train(db, cfg, stable)
    cfg.keepa_recheck_adaptive.update(max_wall_h=12, stable_multiplier=100)
    schedule = Schedule(History(db, cfg, NOW), {}, {stable["dedupe_key"]: 0})
    assert schedule.max_age(stable, "wall") == 4
    assert schedule.cadence(stable, "wall", 8) == pytest.approx(4 - 1 / 6)
    cfg.keepa_recheck_wall_h = 0
    assert schedule.cadence(stable, "wall", 8) == pytest.approx(4 - 1 / 6)


def test_wall_deadline_actually_buys_a_reading_before_four_hours(db, cfg, monkeypatch):
    stable = seed(db, cfg, price_rechecked_at=util.iso(NOW - timedelta(hours=3, minutes=50)))
    train(db, cfg, stable)
    remember(db, wall=[stable])
    cfg.keepa_recheck_wall_h = 0
    cfg.keepa_recheck_adaptive.update(max_wall_h=12, stable_multiplier=100)
    calls = api(monkeypatch, {stable["asin"]: product(2500)})
    KeepaBudget(500, 20, NOW).save(db)  # no overflow to disguise a missing deadline
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert len(calls) == result["done"]["wall_new"] == 1
    assert verify.price_confirmed_at(db.get_deal(stable["dedupe_key"])) == NOW


def test_overflow_still_keeps_deal_reserve_with_zero_extra_page_reserve(db, cfg, monkeypatch):
    fake = setup_pull(cfg, db, monkeypatch, tokens=30)
    cfg.keepa_reserve_tokens = 0
    cfg.keepa_recheck_interval_minutes = 60
    _, result, _ = pull(cfg, db, fake)
    assert result["overflow_discovery"]["pages"] == 1
    assert result["tokens"]["bucket_end"] == 10


def test_pipeline_fetch_wires_overflow_into_the_normal_post_batch(db, cfg, monkeypatch):
    import time
    fake = setup_pull(cfg, db, monkeypatch)
    fake.lists["amazon"][150] = _amazon_row(1)
    fetcher = PoliteFetcher(db, cfg, transport=httpx.MockTransport(fake), sleep_fn=lambda _: None)
    stats, confirmed = {}, {"new": set(), "used": set()}
    try:
        posts, errors = pipeline._fetch_all([SRC], fetcher, db, cfg, NOW,
            time.monotonic() + 1000, False, confirmed, stats)
    finally:
        fetcher.close()
    assert errors == {} and len(posts) == stats["overflow_discovery"]["new_candidates"] == 1
    assert not db.post_is_known(posts[0].content_hash)  # parsing still owns this receipt
