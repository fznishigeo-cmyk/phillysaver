"""350: real adapter/scheduler over offline Keepa category and deal receipts."""
import json
import time
from contextlib import closing
from datetime import timedelta

import httpx
import pytest

from dealradar import pipeline, util, verify
from dealradar.config import AppConfig, load_config
from dealradar.fetch.keepa import KeepaDealsAdapter
from dealradar.fetch.keepa_focus import (PATHS_META, SEARCH_META, STATE_META, TREE_META,
                                         published_groups)
from dealradar.http_client import PoliteFetcher
from dealradar.keepa_budget import KeepaBudget
from dealradar.keepa_categories import NAMES_META
from dealradar.parse.extractor import verify_evidence
from dealradar.parse.schema import LLMDeal, to_extracted
from dealradar.savings import compute_savings
from dealradar.volatility import Plan, discovery_surplus
from tests.history_pins import pre_amazon_price_cap, pre_keepa_focus_finder, pre_keepa_focus_finder_raw, pre_tools_new_tab
from tests.test_keepa import SRC
from tests.test_price_accuracy_engine import NOW, db, seed, product
from tests.test_wall_confirmation_coverage import remember


@pytest.fixture
def cfg():
    cfg = load_config()
    cfg.keepa_focus_finder.update(enabled=True, groups={
        "women": ["Women > Dresses"], "sexy": ["Women > Sleep & Lounge"],
        "kids_baby": ["Toys & Games"], "everyday": ["Household Supplies"], "gadgets": ["Headphones"]})
    cfg.keepa_focus_categories = []
    cfg.keepa_amazon_pages_per_pull = 1
    cfg.keepa_overflow_extra_pages_max = 0
    cfg.keepa_click_price_rule = "off"
    cfg.keepa_buybox_top_n = 0
    cfg.keepa_recheck_top_live = False
    return cfg


def row(i=1, title="Women's Satin Pajama Set", *, cents=2000, avg=10000, price_type=0, **extra):
    current = [-1] * 34
    current[price_type], current[3], current[16], current[17] = cents, 123, 45, 100
    averages = [-1] * 34
    averages[price_type] = avg
    return dict(asin=f"B0FOC{i:05d}", title=title, current=current,
                avg=[averages[:] for _ in range(4)], rootCat=172282,
                lastUpdate=int(NOW.timestamp() / 60) - verify.KEEPA_EPOCH_MINUTES,
                image="pajamas.jpg", **extra)


class Keepa:
    def __init__(self, *, tokens=3490, rows=None, normal=(), clock=None, fail=None):
        self.tokens, self.rows, self.normal = tokens, rows, normal
        self.calls, self.selections, self.balances = [], [], []
        self.clock, self.fail, self.products = clock, fail, {}
        self.receipts = True

    def __call__(self, request):
        path, params = request.url.path, request.url.params
        cost = 1
        if path == "/deal":
            sel = json.loads(params["selection"])
            focus = "includeCategories" in sel
            self.calls.append(("focus" if focus else "ordinary", sel["page"]))
            cost = 5
            if focus:
                self.selections.append(sel)
                if self.fail:
                    return self.fail
                if self.clock:
                    self.clock[0] = 180
                rows = (self.rows(sel) if callable(self.rows) else self.rows) if self.rows is not None else [row(cents=10000)] * 150
            else:
                rows = self.normal
            body = {"deals": {"dr": rows}}
        elif path == "/product":
            asins = params["asin"].split(",")
            self.calls.append(("product", len(asins)))
            cost = len(asins)
            body = {"products": [self.products[a] for a in asins if a in self.products]}
        elif path == "/search":
            assert params["type"] == "category"
            self.calls.append(("search", params["term"]))
            body = {"categories": {"101": {"name": "Dresses", "parent": 10},
                                    "102": {"name": "Dresses", "parent": 11}}}
        elif path == "/category":
            assert params["parents"] == "1" and len(params["category"].split(",")) <= 10
            self.calls.append(("category", params["category"]))
            body = {"categories": {cid: {"name": "Dresses", "parent": 10 if cid == "101" else 11}
                                   for cid in params["category"].split(",")},
                    "categoryParents": {"10": {"name": "Women", "parent": 0},
                                        "11": {"name": "Girls", "parent": 0}}}
        else:
            raise AssertionError(f"unexpected API path {path}")
        if self.receipts:
            body["tokensConsumed"] = cost
        if self.tokens is not None:
            self.tokens -= cost
            self.balances.append(self.tokens)
            body.update(tokensLeft=self.tokens, refillRate=60)
        return httpx.Response(200, json=body)


def pull(db, cfg, fake, monkeypatch, *, deadline=None, cache=True):
    monkeypatch.setenv("KEEPA_KEY", "offline")
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    if fake.tokens is not None:
        KeepaBudget(fake.tokens, 60, NOW).save(db)
    if cache:
        db.meta_set(PATHS_META, json.dumps({p: [i + 1] for i, paths in enumerate(cfg.keepa_focus_finder["groups"].values()) for p in paths}))
    db.meta_set(NAMES_META, json.dumps({"172282": "Electronics"}))
    sleeps, result = [], {}
    fetcher = PoliteFetcher(db, cfg, transport=httpx.MockTransport(fake), sleep_fn=sleeps.append)
    adapter = KeepaDealsAdapter()

    def recheck(discover, end):
        result.update(pipeline._recheck_prices(db, cfg, NOW, image_backfill=False,
            fetcher=fetcher, deadline=end, overflow_discovery=discover))

    adapter.recheck = recheck
    try:
        posts = adapter.fetch_posts(SRC, fetcher, db, cfg, NOW, deadline)
    finally:
        fetcher.close()
    return posts, adapter, result, sleeps


@pytest.mark.parametrize("value", [0, 1, "true", "false", None, [], {}])
def test_strict_switch(tmp_path, value):
    path = tmp_path / "cfg.yaml"
    path.write_text(json.dumps({"keepa_focus_finder": {"enabled": value}}))
    with pytest.raises(ValueError, match="keepa_focus_finder.enabled"):
        load_config(path)


@pytest.mark.parametrize("options", [{"idle_share": 1.01}, {"idle_share": True}, {"max_pages_per_run": 1.5},
    {"max_new_deals_per_run": -1}, {"max_price": "50"}, {"max_price": float("nan")},
    {"min_rating": 51}, {"min_reviews": False}, {"groups": {"sexy": [""]}},
    {"groups": {"sexy": ["Women >"]}}, {"groups": {"sexy": ["Bras", "Bras"]}}, {"extra": 1}])
def test_invalid_knobs(tmp_path, options):
    path = tmp_path / "cfg.yaml"
    path.write_text(json.dumps({"keepa_focus_finder": options}))
    with pytest.raises(ValueError, match="keepa_focus_finder"):
        load_config(path)


def test_default_off_and_history_chain(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text("{}")
    assert load_config(path).keepa_focus_finder["enabled"] is False
    assert AppConfig().keepa_focus_finder["enabled"] is False
    assert load_config().keepa_focus_finder["enabled"] is True  # operator 2026-10-20
    for helper in (pre_keepa_focus_finder, pre_amazon_price_cap):
        cfg = load_config()
        cfg.keepa_focus_finder["enabled"] = True
        assert helper(cfg).keepa_focus_finder["enabled"] is False
    assert "keepa_focus_finder" not in pre_keepa_focus_finder_raw({"keepa_focus_finder": {"enabled": True}})


# Shipped idle_share 0.9 (2026-10-23, was 0.7): floor(95 * .9 / 5) = 17 pages;
# floor(485 * .9 / 5) = 87, held to the 80-page cap.
@pytest.mark.parametrize("tokens,cap,expected", [(3000, 80, 0), (3100, 80, 17), (3490, 80, 80), (3490, 3, 3), (3490, 0, 0)])
def test_pages_scale_with_shared_prediction_and_cap(db, cfg, monkeypatch, tokens, cap, expected):
    cfg.keepa_focus_finder["max_pages_per_run"] = cap
    fake = Keepa(tokens=tokens)
    _, adapter, _, sleeps = pull(db, cfg, fake, monkeypatch)
    summary = adapter.focus_summary
    assert summary["pages"] == expected
    assert summary["tokens"] == expected * 5
    assert summary["idle_after_estimate"] == max(0, summary["idle_before_estimate"] - expected * 5)
    assert min(fake.balances) >= 650 + cfg.keepa_visitor_reserve_tokens
    assert len(sleeps) == len(fake.calls) - 1 and all(0 < s <= 1 for s in sleeps)


@pytest.mark.parametrize("reserve,visitor,search", [(3400, 150, 0), (300, 3490, 0), (300, 150, 500000)])
def test_reserves_and_search_allowance_block_extras(db, cfg, monkeypatch, reserve, visitor, search):
    cfg.keepa_reserve_tokens, cfg.keepa_visitor_reserve_tokens = reserve, visitor
    cfg.search["tokens_per_day"] = search
    _, adapter, _, _ = pull(db, cfg, Keepa(), monkeypatch)
    assert adapter.focus_summary["pages"] == 0


def test_unknown_bucket_never_invents_idle(db, cfg, monkeypatch):
    _, adapter, _, _ = pull(db, cfg, Keepa(tokens=None), monkeypatch)
    assert adapter.focus_summary["pages"] == 0


def test_due_checks_lead_and_deferred_due_work_blocks_finder(db, cfg, monkeypatch):
    due = seed(db, cfg, price_rechecked_at=util.iso(NOW - timedelta(hours=3)))
    remember(db, top=[due])
    fake = Keepa()
    fake.products[due["asin"]] = product(2500, asin=due["asin"])
    _, adapter, result, _ = pull(db, cfg, fake, monkeypatch)
    assert fake.calls.index(("product", 1)) < next(i for i, call in enumerate(fake.calls) if call[0] == "focus")
    assert result["checked"] == 1 and adapter.focus_summary["pages"] > 0
    db.meta_set("keepa_last_fetch_at", "")
    monkeypatch.setattr(Plan, "batches", lambda *args, **kwargs: ([], 1))
    # Restore a genuinely due row; the planner cannot fit/fund its call.
    db.update_deal(due["dedupe_key"], payload=due)
    _, adapter, _, _ = pull(db, cfg, Keepa(), monkeypatch)
    assert adapter.focus_summary["pages"] == 0


def test_deadline_stops_after_one_page(db, cfg, monkeypatch):
    clock = [100]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    _, adapter, _, _ = pull(db, cfg, Keepa(clock=clock), monkeypatch, deadline=200)
    assert adapter.focus_summary["pages"] == 1


def test_group_rotation_paging_and_same_run_retry(db, cfg, monkeypatch):
    cfg.keepa_focus_finder["max_pages_per_run"] = 1
    groups = list(cfg.keepa_focus_finder["groups"])
    queries = []
    at = NOW
    for i in range(11):
        monkeypatch.setattr("tests.test_keepa_focus_finder.NOW", at + timedelta(minutes=i * 10))
        db.meta_set("keepa_last_fetch_at", "")
        fake = Keepa()
        _, adapter, _, _ = pull(db, cfg, fake, monkeypatch)
        sel = fake.selections[0]
        queries.append(sel)
        assert sel["includeCategories"] == [i % len(groups) + 1]
        assert sel["priceTypes"] == [0 if i % 10 < 5 else 18]
        db.meta_set("keepa_last_fetch_at", "")  # simulate restart before fetch marker save
        again = Keepa()
        _, repeat, _, _ = pull(db, cfg, again, monkeypatch)
        assert not again.selections and repeat.focus_summary["pages"] == 0
    assert queries[0]["page"] == 0 and queries[10]["page"] == 1


def test_filters_new_only_evidence_photos_and_deduplication(db, cfg, monkeypatch):
    cfg.keepa_focus_finder["max_pages_per_run"] = 1
    live = seed(db, cfg)
    bad_rating, bad_reviews, bad_rank = row(10), row(11), row(12)
    bad_rating["current"][16] = 39
    bad_reviews["current"][17] = 49
    bad_rank["current"][3] = 30001
    short_avg = row(13)
    short_avg["avg"] = short_avg["avg"][:3]
    rows = [row(1), row(2, "Men's Boxer Briefs 5-Pack"), row(3, cents=5000),
            row(4, "Super vibrator set"), row(5, "adult toy"), row(6, isAdultProduct=True),
            row(7, cents=5001), row(8, cents=5000, avg=6000), bad_rating, bad_reviews,
            bad_rank, short_avg, dict(row(14), asin=live["asin"]), row(1), row(15),
            row(16, price_type=9)]
    fake = Keepa(rows=rows, normal=[row(15)])
    posts, adapter, _, _ = pull(db, cfg, fake, monkeypatch)
    kept = [p for p in posts if p.structured["asin"] in adapter.focus_groups]
    assert [p.title for p in kept[:2]] == ["Women's Satin Pajama Set", "Men's Boxer Briefs 5-Pack"]
    assert len(kept) == 3 and all(p.structured["final_price"] <= 50 for p in kept)
    assert adapter.focus_summary["dropped"] == dict(adult=3, price=2, drop=2, rank=1, rating=2, dup=3, cap=0)
    assert fake.selections[0]["filterErotic"] is True
    assert fake.selections[0]["isFilterEnabled"] is True
    for post in kept:
        extracted = to_extracted(LLMDeal.model_validate(post.structured), post, "UTC", NOW)
        assert verify_evidence(extracted, post.text)[0]
        assert compute_savings(extracted).pct_at_checkout >= cfg.min_pct
        assert post.image_url.endswith("/pajamas.jpg")
        assert "rating" not in post.structured and "reviews" not in post.structured


def test_buybox_new_uses_its_own_current_and_average(db, cfg, monkeypatch):
    cfg.keepa_focus_finder.update(max_pages_per_run=2, max_new_deals_per_run=1, groups={"sexy": ["Sleep & Lounge"]})
    fake = Keepa(rows=lambda sel: [row(price_type=18)] if sel["priceTypes"] == [18] else [])
    posts, adapter, _, _ = pull(db, cfg, fake, monkeypatch)
    assert len(posts) == adapter.focus_summary["kept"] == 1
    assert posts[0].structured["final_price"] == 20
    assert posts[0].structured["conditions"] == []
    assert [s["priceTypes"] for s in fake.selections] == [[0], [18]]


def test_intake_cap_stops_pages_and_counts_held_rows(db, cfg, monkeypatch):
    cfg.keepa_focus_finder["max_new_deals_per_run"] = 2
    posts, adapter, _, _ = pull(db, cfg, Keepa(rows=[row(i) for i in range(10)]), monkeypatch)
    assert len(posts) == adapter.focus_summary["kept"] == 2
    assert adapter.focus_summary["pages"] == 1 and adapter.focus_summary["dropped"]["cap"] == 8


def test_category_search_verifies_ancestors_and_caches_once(db, cfg, monkeypatch):
    cfg.keepa_focus_finder.update(max_pages_per_run=1, groups={"women": ["Women > Dresses"]})
    fake = Keepa(rows=[])
    _, adapter, _, _ = pull(db, cfg, fake, monkeypatch, cache=False)
    assert fake.selections[0]["includeCategories"] == [101]  # Girls > Dresses (102) excluded
    assert adapter.focus_summary["tokens"] == 7
    assert json.loads(db.meta_get(PATHS_META)) == {"Women > Dresses": [101]}
    assert db.meta_get(SEARCH_META) and db.meta_get(TREE_META)
    db.meta_set("keepa_last_fetch_at", "")
    monkeypatch.setattr("tests.test_keepa_focus_finder.NOW", NOW + timedelta(minutes=10))
    fake = Keepa(rows=[])
    pull(db, cfg, fake, monkeypatch, cache=False)
    assert all(call[0] not in ("search", "category") for call in fake.calls)


def test_missing_receipts_keep_estimated_debits_and_429_stops(db, cfg, monkeypatch):
    fake = Keepa()
    fake.receipts = False
    _, adapter, _, _ = pull(db, cfg, fake, monkeypatch)
    assert adapter.focus_summary["tokens"] == adapter.focus_summary["pages"] * 5
    assert adapter.focus_summary["estimated_tokens"] == adapter.focus_summary["tokens"]
    db.meta_set("keepa_last_fetch_at", "")
    monkeypatch.setattr("tests.test_keepa_focus_finder.NOW", NOW + timedelta(minutes=10))
    fake = Keepa(fail=httpx.Response(429, json={"tokensLeft": 3400, "refillRate": 60}))
    _, adapter, result, _ = pull(db, cfg, fake, monkeypatch)
    assert adapter.focus_summary["pages"] == 1 and result["rate_limited"]
    assert len(fake.selections) == 1


def test_summary_counts_actual_public_asins_once(cfg):
    assert published_groups([{"asin": "A", "status": "alive"}, {"asin": "A", "status": "alive"},
                             {"asin": "B", "status": "dead"}], {"A": "sexy", "B": "women"}, cfg) == {
                                 "women": 0, "sexy": 1, "kids_baby": 0, "everyday": 0, "gadgets": 0}


def test_off_preserves_recorded_public_bytes(tmp_path, monkeypatch):
    from dealradar.publish import site
    from tests.test_support import render, NOW as PUBLISH_NOW
    from tests.test_pwa import tree_hash
    monkeypatch.setattr(site, "util_now", lambda: PUBLISH_NOW)
    # The same day's publication switches are off; the finder itself is on.
    cfg = pre_tools_new_tab(load_config())
    cfg.keepa_focus_finder["enabled"] = True
    out, *_ = render(tmp_path, cfg)
    # Captured from git HEAD in an isolated archive before this lane existed.
    assert tree_hash(out) == "fc1042a5bd2e3d63b75b4bd82b87ffa39eac319064682aa8ec9dcd2874e8d5c0"


def test_off_ignores_every_finder_knob_and_leaves_meta_untouched(db, cfg, monkeypatch):
    cfg.keepa_focus_finder["enabled"] = False
    fake = Keepa(normal=[row()])
    posts, adapter, _, _ = pull(db, cfg, fake, monkeypatch)
    assert len(posts) == 1 and adapter.focus_summary is None
    assert not fake.selections and not db.meta_get(STATE_META)
    cfg.keepa_focus_finder.update(idle_share=1, max_pages_per_run=1000, max_price=1)
    db.meta_set("keepa_last_fetch_at", "")
    again, _, _, _ = pull(db, cfg, Keepa(normal=[row()]), monkeypatch)
    assert [(p.text, p.hash_text, p.content_hash, p.structured) for p in again] == [
        (p.text, p.hash_text, p.content_hash, p.structured) for p in posts]


def test_optional_feed_receipts_never_stretch_recheck_targets(db, cfg):
    from dealradar.keepa_rechecks import History
    for i in range(3):
        at = util.iso(NOW - timedelta(minutes=(i + 1) * 10))
        db.conn.execute("INSERT INTO efficiency_runs(run_key,started_at,observed_at,metrics,pending) VALUES (?,?,?,?,0)",
                        (str(i), at, at, json.dumps({"feed_tokens": 350, "focus_finder_tokens": 300})))
    plan = Plan(History(db, cfg, NOW), [], {}, {}, 10)
    assert plan.discovery == 5  # ordinary 50 tokens/10 min; optional 300 isn't a deadline
    assert discovery_surplus(KeepaBudget(3490, 60, NOW), cfg, 10, NOW) == 490


def test_retry_receipts_preserve_optional_spend_for_the_forecast(db, cfg):
    from dealradar import efficiency
    efficiency.start(db, NOW, "same")
    values = dict.fromkeys(efficiency.COUNTERS, 0)
    values.update(feed_tokens=350, focus_finder_tokens=300,
                  freshness={"latest": {}, "worst": {}})
    efficiency.record(db, NOW, "same", values)
    efficiency.start(db, NOW, "same")
    efficiency.record(db, NOW, "same", dict(values, feed_tokens=5, focus_finder_tokens=0))
    saved = json.loads(db.conn.execute("SELECT metrics FROM efficiency_runs WHERE run_key='same'").fetchone()[0])
    assert saved["feed_tokens"] == 355 and saved["focus_finder_tokens"] == 300


def test_lost_page_bodies_cannot_spend_the_same_idle_twice(db, cfg, monkeypatch):
    fake = Keepa(tokens=3100)
    send = fake.__call__

    def lost(request):
        response = send(request)
        if request.url.path == "/deal" and "includeCategories" in json.loads(request.url.params["selection"]):
            return httpx.Response(200, text="truncated")
        return response

    # Standalone hook uses exactly the shared budget and gates the real run uses.
    monkeypatch.setenv("KEEPA_KEY", "offline")
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    KeepaBudget(3100, 60, NOW).save(db)
    db.meta_set(PATHS_META, json.dumps({p: [i + 1] for i, paths in enumerate(cfg.keepa_focus_finder["groups"].values()) for p in paths}))
    fetcher = PoliteFetcher(db, cfg, transport=httpx.MockTransport(lost), sleep_fn=lambda _: None)
    adapter = KeepaDealsAdapter()
    adapter.recheck = lambda discover, end: discover(KeepaBudget.load(db, cfg), focus_allowed=True)
    try:
        adapter.fetch_posts(SRC, fetcher, db, cfg, NOW, None)
    finally:
        fetcher.close()
    # Ten short query windows end before the allowance; every lost answer is
    # still debited and kept in the shared bucket.
    assert adapter.focus_summary["pages"] == 10
    assert adapter.focus_summary["tokens"] == 50
    assert KeepaBudget.load(db, cfg).available(NOW) == 3045


def test_known_posts_are_deduped_on_the_next_run(db, cfg, monkeypatch):
    cfg.keepa_focus_finder["max_pages_per_run"] = 1
    posts, _, _, _ = pull(db, cfg, Keepa(rows=[row()]), monkeypatch)
    assert len(posts) == 1
    db.record_post(posts[0], NOW)
    db.meta_set("keepa_last_fetch_at", "")
    monkeypatch.setattr("tests.test_keepa_focus_finder.NOW", NOW + timedelta(minutes=10))
    posts, adapter, _, _ = pull(db, cfg, Keepa(rows=[row()]), monkeypatch)
    assert not posts and adapter.focus_summary["dropped"]["dup"] == 1


@pytest.mark.parametrize("click_rule", ["off", "on"])
def test_full_pipeline_keeps_critic_and_affiliate_and_public_policy(tmp_path, cfg, monkeypatch, click_rule):
    from dealradar.db import DB
    from dealradar.publish import site
    from tests.test_e2e_offline import _FakeClaudeClient
    monkeypatch.setenv("KEEPA_KEY", "offline")
    for key in ("SMTP_HOST", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg.site_dir = str(tmp_path / "site")
    cfg.parse_structured_output = True
    cfg.critic_tiering["enabled"] = False
    cfg.appeal_score["enabled"] = False
    cfg.ai_take["enabled"] = False
    cfg.keepa_click_price_rule = click_rule
    cfg.keepa_focus_finder["max_pages_per_run"] = 1
    monkeypatch.setattr(pipeline, "load_config", lambda path=None: cfg)
    database = str(tmp_path / "state.db")
    with closing(DB(database)) as initial:
        KeepaBudget(3490, 60, NOW).save(initial)
        initial.meta_set(PATHS_META, json.dumps({p: [i + 1] for i, paths in enumerate(cfg.keepa_focus_finder["groups"].values()) for p in paths}))
        initial.meta_set(NAMES_META, json.dumps({"172282": "Electronics"}))
    fake = Keepa(rows=[row()])

    def handler(request):
        if request.url.host == "api.keepa.com":
            return fake(request)
        return httpx.Response(404)

    def run():
        return pipeline.run(db_path=database, now=NOW, dry_run_telegram=True,
            transport=httpx.MockTransport(handler), sources_override=[SRC],
            skip_validation=True, claude_client=_FakeClaudeClient())

    first = run()
    assert first["structured_parse"]["posts"] == 1
    assert first["critic"]["judged"] == 1
    assert first["published"] == 1
    assert first["keepa_focus_finder"]["published_by_group"]["women"] == 1
    assert first["spend_by_kind"]["extract"]["calls"] == 0
    assert first["spend_by_kind"]["category"]["calls"] == 0
    public = json.loads((tmp_path / "site" / "deals.json").read_text())
    for deal in public["deals"]:
        assert f"tag={cfg.affiliate_amazon_tag}" in deal["url"]
        assert all(deal.get(key) is None for key in ("rating", "reviews", "avg90", "regular_price", "pct_at_checkout"))
        if click_rule == "on":
            # Existing recovery cards retain the unknown-price treatment:
            # a /deal sighting never manufactures qualified Buy Box proof.
            assert deal["click_price_unknown"] is True and deal["price_unconfirmed"] is True
    count = len(fake.selections)
    again = run()
    assert len(fake.selections) == count
    assert again["posts_new"] == 0 and again["telegram"]["sent"] == 0
    with closing(DB(database)) as final:
        assert len(final.all_deals()) == 1
