"""Free Keepa readings and bounded paid rechecks on the 20-token/min plan."""

import json
from datetime import timedelta

import httpx
import pytest

from dealradar import dedupe, pipeline, rank, savings, util, verify
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.fetch.keepa import KeepaDealsAdapter, _FeedConfirmations
from dealradar.keepa_budget import KeepaBudget, MIN_FOR_DEAL_QUERY
from dealradar.parse.schema import LLMDeal, to_extracted
from dealradar.publish import site
from tests.http_helpers import api_transport, mock_keepa_get
from tests.test_keepa import FakeKeepa, SRC, _adapter
from tests.test_price_accuracy_engine import NOW, ASIN, api, bucket, cfg, db, product, seed


def row(asin=ASIN, cents=2500, at=NOW - timedelta(minutes=20)):
    return {"asin": asin, "title": "Widget Pro " + asin,
            "current": [cents, 10000] + [-1] * 7 + [cents],
            "avg": [[10000] * 10] * 4,
            "lastUpdate": int(at.timestamp() / 60) - verify.KEEPA_EPOCH_MINUTES}


@pytest.mark.parametrize("used", [False, True])
def test_feed_exact_price_condition_and_alive_matches_only(db, cfg, used):
    matching = seed(db, cfg, conditions=["Open Box"] if used else [])
    # Two alive rows for an ASIN can have independent source identities.
    twin = seed(db, cfg, asin="B000000002", conditions=["used"] if used else [])
    twin["asin"] = ASIN
    db.update_deal(twin["dedupe_key"], payload=twin)
    wrong_condition = seed(db, cfg, asin="B000000003", conditions=[] if used else ["used"])
    wrong_condition["asin"] = ASIN
    db.update_deal(wrong_condition["dedupe_key"], payload=wrong_condition)
    dead = seed(db, cfg, asin="B000000004")
    db.update_deal(dead["dedupe_key"], payload=dict(dead, asin=ASIN), status="dead")
    different_price = seed(db, cfg, asin="B000000005")
    db.update_deal(different_price["dedupe_key"], payload=dict(different_price, asin=ASIN), final_price=24)
    other_store = seed(db, cfg, asin="B000000006")
    db.conn.execute("UPDATE deals SET store='Walmart',canonical_url='' WHERE dedupe_key=?",
                    (other_store["dedupe_key"],))
    db.update_deal(other_store["dedupe_key"], payload=dict(other_store, asin=ASIN))
    before = {d["dedupe_key"]: d for d in db.all_deals()}
    confirmations = _FeedConfirmations(db, NOW)
    confirmations.note([row(cents=2500.4)], used=used)  # mapper rounds to $25.00
    expected = {matching["dedupe_key"], twin["dedupe_key"]}
    assert confirmations.confirmed == {"new": set() if used else expected,
                                        "used": expected if used else set()}
    for d in db.all_deals():
        if d["dedupe_key"] in expected:
            assert d["keepa_price_seen_at"] == util.iso(NOW - timedelta(minutes=20))
            assert bool(d.get("keepa_warehouse_seen_at")) == used
            assert not d.get("price_rechecked_at")
        else:
            assert d == before[d["dedupe_key"]]


@pytest.mark.parametrize("stamp", [None, "bad", True, -1, float("nan"), float("inf"), 1e100])
def test_unusable_feed_time_cannot_stamp(db, cfg, stamp):
    before = seed(db, cfg)
    confirmations = _FeedConfirmations(db, NOW)
    confirmations.note([dict(row(), lastUpdate=stamp)], used=False)
    assert db.get_deal(before["dedupe_key"]) == before
    assert confirmations.confirmed == {"new": set(), "used": set()}


def test_feed_time_is_monotone_capped_and_cannot_confirm_an_earlier_price(db, cfg):
    deal = seed(db, cfg, price_since=util.iso(NOW - timedelta(hours=1)))
    confirmations = _FeedConfirmations(db, NOW)
    confirmations.note([row(at=NOW - timedelta(hours=2))], used=False)
    assert not db.get_deal(deal["dedupe_key"]).get("keepa_price_seen_at")
    confirmations.note([row(at=NOW + timedelta(days=2))], used=False)
    after = db.get_deal(deal["dedupe_key"])
    assert after["keepa_price_seen_at"] == util.iso(NOW)
    confirmations.note([row(), row(at=NOW + timedelta(days=2))], used=False)
    assert db.get_deal(deal["dedupe_key"]) == after
    assert len(confirmations.confirmed["new"]) == 1


@pytest.mark.parametrize("lane", ["amazon", "fresh", "deep", "sweep"])
@pytest.mark.parametrize("known", [False, True])
def test_every_fetched_lane_confirms_before_hash_and_post_caps(db, cfg, monkeypatch, lane, known):
    monkeypatch.setenv("KEEPA_KEY", "offline-key")
    cfg.keepa_focus_categories = []
    used = lane != "amazon"
    deal = seed(db, cfg, conditions=["used"] if used else [])
    reading = row()
    if used:
        reading["current"][0] = 10000
    mapper = KeepaDealsAdapter._map_warehouse_row if used else KeepaDealsAdapter._map_row
    if known:
        db.record_post(mapper(reading, SRC, NOW, floors=tuple(sorted(
            {cfg.min_pct, cfg.used_min_pct, cfg.site_show_from_pct}))), NOW)
    cfg.keepa_amazon_max_new_posts_per_pull = 1
    cfg.keepa_warehouse_max_new_posts_per_pull = 1
    cfg.keepa_sweep_enabled = lane == "sweep"
    if lane == "sweep":
        record = product(2500, current=reading["current"])
        record.update(title=reading["title"], lastUpdate=reading["lastUpdate"])
        fake = FakeKeepa(tokens=1200, finder=[ASIN], products={ASIN: record})
    else:
        filler = dict(reading, asin="B000000099", title="Fills the new-post cap")
        fake = FakeKeepa(tokens=1200, **{lane: [filler, reading, dict(reading, title="")]})
    adapter = _adapter(fake, cfg)
    adapter.fetch_posts(SRC, None, db, cfg, NOW, None)
    assert db.get_deal(deal["dedupe_key"])["keepa_price_seen_at"] == util.iso(NOW - timedelta(minutes=20))
    assert adapter.feed_confirmed["used" if used else "new"] == {deal["dedupe_key"]}
    assert len(db.all_deals()) == 1
    expected = [('amazon', 0), ('fresh', 0)] + ([] if lane == "fresh" else [('deep', 0)])
    assert fake.calls == (expected +
                          ([('query', 0), ('product', 1)] if lane == "sweep" else []))


@pytest.mark.parametrize("used", [False, True])
def test_feed_confirmation_skips_paid_cadence_and_restores_hidden_deal(db, cfg, monkeypatch, used):
    deal = seed(db, cfg, seen=NOW - timedelta(days=5), conditions=["used"] if used else [])
    assert rank.site_deals(db, cfg, NOW) == []
    _FeedConfirmations(db, NOW).note([row()], used=used)
    calls = api(monkeypatch, {ASIN: product(2500, current=row()["current"])})
    bucket(db, 500)
    assert pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)["checked"] == 0
    assert calls == [] and len(rank.site_deals(db, cfg, NOW)) == 1
    if used:
        db.meta_set(site.WALL_META, json.dumps([deal["dedupe_key"]]))
    hours = cfg.keepa_recheck_used_h if used else cfg.keepa_recheck_new_h
    due = NOW - timedelta(minutes=20) + timedelta(hours=hours)
    assert pipeline._recheck_prices(db, cfg, due, image_backfill=False)["checked"] == 1
    assert len(calls) == 1


def test_only_exact_warehouse_stamp_skips_used_scans_and_suspects_still_recheck(db, cfg, monkeypatch):
    used = seed(db, cfg, conditions=["used"], keepa_price_seen_at=util.iso(NOW))
    suspect = seed(db, cfg, asin="B000000002", keepa_price_seen_at=util.iso(NOW),
                   gone_suspect_at=util.iso(NOW - timedelta(hours=2)),
                   price_rechecked_at=util.iso(NOW - timedelta(hours=2)))
    db.meta_set(site.WALL_META, json.dumps([used["dedupe_key"]]))
    bucket(db, 500)
    calls = api(monkeypatch, {d["asin"]: product(2500, asin=d["asin"], current=row()["current"])
                              for d in (used, suspect)})
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert stats["checked"] == 2 and len(calls) == 2
    assert calls[-1]["offers"] == 20


def test_new_post_cannot_replace_feed_data_time_with_fetch_time(db, cfg):
    deal = seed(db, cfg)
    post = KeepaDealsAdapter._map_row(row(), SRC, NOW)
    ext = to_extracted(LLMDeal.model_validate(post.structured), post, "UTC", NOW)
    dedupe.upsert_extracted(db, ext, savings.compute_savings(ext), post, cfg, NOW)
    after = db.get_deal(deal["dedupe_key"])
    assert after["keepa_price_seen_at"] == util.iso(NOW - timedelta(minutes=20))
    assert verify.price_confirmed_at(after) == NOW - timedelta(minutes=20)


def test_ingested_feed_without_time_cannot_use_its_sighting_time(db, cfg):
    post = KeepaDealsAdapter._map_row(dict(row(), lastUpdate=None), SRC, NOW)
    ext = to_extracted(LLMDeal.model_validate(post.structured), post, "UTC", NOW)
    result = dedupe.upsert_extracted(db, ext, savings.compute_savings(ext), post, cfg, NOW)
    assert verify.price_confirmed_at(db.get_deal(result["dedupe_key"])) is None


@pytest.mark.parametrize("promo", [{"codes": ["SAVE"]}, {"stack_steps": ["Clip coupon"]},
                                    {"conditions": ["subscribe_save"]}])
def test_promo_drift_separate_and_same_listing_as_judge(db, cfg, monkeypatch, promo):
    hidden = seed(db, cfg, **promo)
    plain = seed(db, cfg, asin="B000000002")
    calls = api(monkeypatch, {hidden["asin"]: product(9000),
                             plain["asin"]: product(9000, asin=plain["asin"], current=[9000, 2520])})
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert stats["drift"] == {"promo_hidden": 1, "within_1pct": 1, "higher_1_5pct": 0,
                               "higher_over_5pct": 0, "lower": 0, "repriced": 1}
    assert db.get_deal(hidden["dedupe_key"])["final_price"] == 25
    assert len(calls) == 1


@pytest.mark.parametrize("malformed", [False, True])
def test_first_429_stops_all_recheck_classes_even_if_bucket_looks_full(db, cfg, monkeypatch, malformed):
    deals = [seed(db, cfg, asin=f"B0{i:08d}", conditions=["used"] if i == 2 else []) for i in range(3)]
    db.meta_set(site.PICKS_META, json.dumps([deals[0]["dedupe_key"]]))
    bucket(db, 1200)
    monkeypatch.setenv("KEEPA_KEY", "offline-key")
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs["params"])
        return httpx.Response(429, **({"text": "unavailable"} if malformed else
                                     {"json": {"tokensLeft": 1000, "refillRate": 20}}),
                              request=httpx.Request("GET", url))

    mock_keepa_get(monkeypatch, get)
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert len(calls) == 1 and stats["checked"] == 0
    assert db.all_deals() == deals


@pytest.mark.parametrize("used,live,first,left,second", [(False, False, 100, 27, 17),
                                                       (True, False, 20, 39, 2),
                                                       (True, True, 20, 37, 1)])
def test_inner_batches_resize_from_latest_response_and_stop_on_429(cfg, monkeypatch, used, live, first, left, second):
    monkeypatch.setenv("KEEPA_KEY", "offline-key")
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    deals = [{"asin": f"B0{i:08d}", "dedupe_key": str(i), "final_price": 25,
              "conditions": ["used"] if used else []} for i in range(240)]
    budget = KeepaBudget(1200, 0, NOW)
    sizes = []

    def get(url, params=None, **kwargs):
        sizes.append(len(params["asin"].split(",")))
        return httpx.Response(200 if len(sizes) == 1 else 429,
                              json={"tokensLeft": left if len(sizes) == 1 else -10},
                              request=httpx.Request("GET", url))

    mock_keepa_get(monkeypatch, get)
    result = verify.recheck_amazon_prices(deals, cfg, budget, live=live)
    assert sizes == [first, second] and result.rate_limited
    assert budget.tokens_left == -10


@pytest.mark.parametrize("share,used_count", [(0, 0), (.35, 5), (1, 13)])
def test_new_classes_before_used_and_used_share_includes_suspects(db, cfg, monkeypatch, share, used_count):
    cfg.keepa_used_scan_budget_share = share
    new_names = ["suspect", "top", "wall", "rest"]
    new = [seed(db, cfg, asin=f"B0{i:08d}", **({"gone_suspect_at": util.iso(NOW - timedelta(hours=2))}
                                             if i == 0 else {})) for i in range(4)]
    used = [seed(db, cfg, asin=f"B0{i + 4:08d}", conditions=["used"],
                 **({"gone_suspect_at": util.iso(NOW - timedelta(hours=2))} if i == 0 else {}))
            for i in range(13)]
    db.meta_set(site.PICKS_META, json.dumps([new[1]["dedupe_key"], used[1]["dedupe_key"]]))
    db.meta_set(site.WALL_META, json.dumps([new[2]["dedupe_key"]] + [d["dedupe_key"] for d in used]))
    bucket(db, 210)
    calls = []

    def check(deals, _cfg, budget=None, **kwargs):
        calls.extend(d["dedupe_key"] for d in deals)
        cost = sum(13 if verify.is_used_deal(d) else 1 for d in deals)
        budget.note({"tokensLeft": budget.available(NOW) - cost, "refillRate": 0}, NOW)
        return {d["dedupe_key"]: True for d in deals}

    monkeypatch.setattr(verify, "recheck_amazon_prices", check)
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    expected = [d["dedupe_key"] for d in new] + [d["dedupe_key"] for d in used[:used_count]]
    assert calls == expected
    assert stats["used_checked"] == used_count
    assert stats["used_skipped_budget"] == 13 - used_count
    assert used_count * 13 <= (210 - MIN_FOR_DEAL_QUERY) * share
    assert KeepaBudget.load(db).available(NOW) >= MIN_FOR_DEAL_QUERY
    assert all(stats["done"][n + "_new"] == 1 for n in new_names)


def test_unknown_bucket_defers_used_scans(db, cfg, monkeypatch):
    deal = seed(db, cfg, conditions=["used"])
    db.meta_set(site.WALL_META, json.dumps([deal["dedupe_key"]]))
    monkeypatch.setattr(verify, "recheck_amazon_prices", lambda *a, **kw: pytest.fail("unknown used allowance"))
    assert pipeline._recheck_prices(db, cfg, NOW)["used_skipped_budget"] == 1


def test_empty_bucket_reports_used_scans_skipped(db, cfg, monkeypatch):
    deal = seed(db, cfg, conditions=["used"])
    db.meta_set(site.WALL_META, json.dumps([deal["dedupe_key"]]))
    bucket(db, 22)  # less than one 13-token scan plus the 10-token reserve
    monkeypatch.setattr(verify, "recheck_amazon_prices", lambda *a, **kw: pytest.fail("empty bucket"))
    result = pipeline._recheck_prices(db, cfg, NOW)
    assert result["tokens_short"] == result["used_skipped_budget"] == 1


@pytest.mark.parametrize("value,expected", [(-1, 0), (.2, .2), (2, 1)])
def test_used_share_config_is_bounded(tmp_path, value, expected):
    assert AppConfig().keepa_used_scan_budget_share == .35
    assert load_config().keepa_used_scan_budget_share == .35
    path = tmp_path / "config.yaml"
    path.write_text(f"keepa:\n  used_scan_budget_share: {value}\n")
    assert load_config(path).keepa_used_scan_budget_share == expected


def test_same_run_key_feed_confirmation_adds_no_rows_or_alerts(tmp_path, monkeypatch):
    cfg = load_config()
    path = tmp_path / "state.db"
    with_db = DB(path)
    deal = seed(with_db, cfg)
    # A row need not map to a new post to confirm its unchanged price.
    fake = FakeKeepa(amazon=[dict(row(), title="")], tokens=500)
    with_db.close()
    config = tmp_path / "config.yaml"
    config.write_text("publish:\n  site_dir: " + str(tmp_path / "site") +
                      "\nkeepa:\n  focus_categories: []\n  sweep_enabled: false\nfeature_flags:\n  keepa: true\n")
    monkeypatch.setenv("KEEPA_KEY", "offline-key")
    monkeypatch.delenv("DEALS_CLAUDE_API_KEY", raising=False)
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    # Pipeline builds its parser even with no new posts; return an inert one.
    monkeypatch.setattr(pipeline, "_build_parser", lambda *a, **kw: None)
    monkeypatch.setattr(verify, "recheck_amazon_prices", lambda *a, **kw: pytest.fail("already confirmed"))

    def run():
        return pipeline.run(db_path=str(path), config_path=str(config), now=NOW,
                            transport=api_transport(fake), sources_override=[SRC], skip_validation=True,
                            dry_run_telegram=True)

    first = run()
    assert (tmp_path / "site" / "deals.json").is_file()
    database = DB(path)
    before = database.all_deals()
    tables = ["deals", "sightings", "price_history", "posts_seen", "alerts_sent"]
    # Delivery tables have their own idempotency coverage; compare all counts.
    tables = [r[0] for r in database.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
              if r[0] in tables or r[0].startswith("outgoing")]
    counts = {t: database.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}
    second = run()
    assert first["run_key"] == second["run_key"]
    assert first["price_recheck"]["feed_confirmed"] == {"new": 1, "used": 0}
    assert second["price_recheck"]["feed_confirmed"] == {"new": 0, "used": 0}
    assert database.all_deals() == before
    assert before[0]["dedupe_key"] == deal["dedupe_key"]
    assert counts == {t: database.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}
    assert first["posts_new"] == second["posts_new"] == 0
    assert first["spend_usd"] == second["spend_usd"] == 0
    database.close()
