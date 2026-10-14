"""Task 087: useful information, observation clocks, and a repeatable day."""

import copy
import json
from contextlib import closing
from datetime import timedelta

import httpx
import pytest

from dealradar import pipeline, rank, util, verify
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.fetch.keepa import KeepaDealsAdapter
from dealradar.http_client import PoliteFetcher
from dealradar.growth import report
from dealradar.keepa_budget import KeepaBudget, MIN_FOR_DEAL_QUERY
from dealradar.keepa_rechecks import History, Schedule, report_data
from dealradar.publish import site
from tests.http_helpers import mock_keepa_get
from tests.test_price_accuracy_engine import NOW, api, db, product, seed
from tests.test_wall_confirmation_coverage import remember
from tests.test_keepa import SRC, _amazon_row, _wh_row, _junk_row


@pytest.fixture
def cfg():
    return load_config()


def minutes(at):
    return int(at.timestamp() / 60) - verify.KEEPA_EPOCH_MINUTES


def train(db, cfg, deal, *, volatile=False, count=12):
    for i in range(count):
        at = NOW - timedelta(hours=count - i)
        before = dict(deal, final_price=26 if volatile and i % 2 else 25)
        History(db, cfg, at).record(before, product(2600 if volatile and not i % 2 else 2500), True,
                                  at, "top_new", 1, 0)


def test_yield_distinct_readings_costs_threshold_death_and_bounded_history(db, cfg):
    deal = seed(db, cfg)
    history = History(db, cfg, NOW)
    history.record(deal, product(2525), True, NOW, "idle_top_new", 1, 0)
    history.record(deal, product(2525), True, NOW, "idle_top_new", 1, 0)
    assert history.yield_stats["overall"]["readings"] == 1
    assert history.yield_stats["overall"]["useful"] == 1  # exact 1% boundary
    later = History(db, cfg, NOW + timedelta(minutes=10))
    later.record(deal, product(2525), True, NOW, "top_new", 1, 0)
    assert later.yield_stats["overall"]["readings"] == 0
    assert later.yield_stats["overall"]["tokens"] == 1
    assert later.yield_stats["overall"]["tokens_per_useful_reading"] is None
    assert len(later.samples[deal["dedupe_key"]]) == 1
    for i, (cents, verdict, dead) in enumerate(((2524, True, False), (-1, False, False),
                                               (-1, False, True), (2500, None, False))):
        at = NOW + timedelta(minutes=20 + i * 10)
        History(db, cfg, at).record(deal, product(cents), verdict, at, "wall_new", 2, 2, dead=dead)
    values = report_data(db, NOW + timedelta(days=1))["yesterday"]
    assert values["overall"] == {"tokens": 10, "estimated_tokens": 8, "readings": 4,
                                 "changed": 1, "deaths": 1, "useful": 2,
                                 "tokens_per_useful_reading": 5}
    assert values["classes"]["top_new"]["tokens_per_useful_reading"] == 2
    train(db, cfg, deal, count=30)
    assert len(History(db, cfg, NOW).samples[deal["dedupe_key"]]) == 12


def test_hidden_promo_and_inconclusive_are_not_useful_price_changes(db, cfg):
    for i, changes in enumerate(({"codes": ["SAVE"]}, {})):
        deal = seed(db, cfg, asin=f"B{i:09}", **changes)
        history = History(db, cfg, NOW)
        history.record(deal, product(10000), True if i == 0 else None, NOW, "rest_new", 1, 1)
        assert history.yield_stats["overall"]["useful"] == 0


def test_scores_cadence_bounds_and_recent_keepa_change(db, cfg):
    stable = seed(db, cfg)
    volatile = seed(db, cfg, asin="B000000002")
    train(db, cfg, stable)
    train(db, cfg, volatile, volatile=True)
    schedule = Schedule(History(db, cfg, NOW), {}, {})
    last = NOW - timedelta(hours=1)
    assert schedule.score(volatile, 1, last, 1) > schedule.score(stable, 1, last, 1)
    assert schedule.score(volatile, 1, last, 13) == pytest.approx(schedule.score(volatile, 1, last, 1) / 13)
    for name, base, maximum in (("top", .75, 1), ("wall", 1.5, 2), ("rest", 8, 12)):
        fast = schedule.cadence(volatile, name, base)
        slow = schedule.cadence(stable, name, base)
        assert .5 <= fast < slow <= maximum - 1 / 6
    used = dict(stable, conditions=["used"])
    assert 3 <= schedule.cadence(used, "wall", 6) <= 12 - 1 / 6
    assert schedule.cadence(stable, "suspect", 1) == 1
    prior = schedule.probability(stable, 1)
    History(db, cfg, NOW).record(stable, dict(product(2500), lastPriceChange=minutes(NOW)),
                               True, NOW, "top_new", 1, 0)
    assert Schedule(History(db, cfg, NOW), {}, {}).probability(stable, 1) > prior


def test_exploration_uses_affordable_batch_and_never_crosses_classes(db, cfg):
    rows = [seed(db, cfg, asin=f"B{i:09}", price_rechecked_at=util.iso(
        NOW - timedelta(minutes=70 if i < 2 else 60))) for i in range(12)]
    for i, d in enumerate(rows):
        train(db, cfg, d, volatile=i >= 2)
    history = History(db, cfg, NOW)
    schedule = Schedule(history, {}, {d["dedupe_key"]: i for i, d in enumerate(rows)})
    base = {d["dedupe_key"]: 1.5 for d in rows}
    batch = schedule.batch(rows, 10, "wall", base, verify.price_confirmed_at, 1)
    assert batch[0] == rows[2]  # value leads; oldest stable gets the reserved slot
    assert rows[0] in batch and rows[1] not in batch
    assert schedule.batch(rows, 10, "suspect", base, verify.price_confirmed_at, 1) == rows[:10]
    # A hard age deadline wins even with exploration disabled.
    cfg.keepa_recheck_adaptive["exploration_share"] = 0
    rows[1]["price_rechecked_at"] = util.iso(NOW - timedelta(hours=2))
    assert schedule.batch(rows, 1, "wall", base, verify.price_confirmed_at, 1) == [rows[1]]


def test_scheduler_spends_on_volatile_rows_before_equal_age_stable_rows(db, cfg, monkeypatch):
    rows = [seed(db, cfg, asin=f"B{i:09}", price_rechecked_at=util.iso(NOW - timedelta(hours=1)))
            for i in range(10)]
    for i, deal in enumerate(rows):
        train(db, cfg, deal, volatile=i >= 5)
    remember(db, top=rows)
    KeepaBudget(15, 20, NOW).save(db)  # only five affordable readings
    calls = api(monkeypatch, {d["asin"]: product(2500, asin=d["asin"]) for d in rows}, tokens=15)
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert calls[0]["asin"].split(",") == [d["asin"] for d in rows[5:]]
    assert result["yield"]["overall"]["tokens"] == 5
    assert result["tokens"]["bucket_end"] == 10


def test_observation_clock_handles_missing_offer_stats():
    record = dict(product(), stats="unknown", offers=[], liveOffersOrder=[])
    assert verify.keepa_observed_at(record, NOW, used=True) == NOW


@pytest.mark.parametrize("stamp,expected", [(minutes(NOW - timedelta(minutes=45)), NOW - timedelta(minutes=45)),
                                           (None, NOW), (-1, NOW), (minutes(NOW + timedelta(hours=1)), NOW)])
@pytest.mark.parametrize("cents", [2500, 2600])
def test_product_observation_time_drives_confirmation_and_reprice(db, cfg, monkeypatch, stamp, expected, cents):
    deal = seed(db, cfg)
    api(monkeypatch, {deal["asin"]: dict(product(cents), lastUpdate=stamp)})
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    row = db.get_deal(deal["dedupe_key"])
    assert verify.price_confirmed_at(row) == expected
    if cents != 2500:
        assert row["price_since"] == util.iso(expected)
        assert row["price_published_at"] == util.iso(NOW)
        assert db.price_observations(row["dedupe_key"], expected, NOW)[-1]["observed_at"] == util.iso(expected)


def test_cached_death_reading_cannot_be_counted_twice(db, cfg, monkeypatch):
    deal = seed(db, cfg)
    records = {deal["asin"]: product(-1)}
    calls = api(monkeypatch, records)
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    later = NOW + timedelta(hours=1)
    summary = pipeline._recheck_prices(db, cfg, later, image_backfill=False)
    assert summary["dead"] == summary["yield"]["overall"]["readings"] == 0
    assert db.get_deal(deal["dedupe_key"])["status"] == "alive"
    assert pipeline._recheck_prices(db, cfg, later, image_backfill=False)["checked"] == 0
    assert len(calls) == 2
    records[deal["asin"]]["lastUpdate"] = minutes(later + timedelta(minutes=10))
    summary = pipeline._recheck_prices(db, cfg, later + timedelta(minutes=10), image_backfill=False)
    assert summary["dead"] == summary["yield"]["overall"]["deaths"] == 1


def test_older_product_cannot_replace_a_newer_confirmation_or_price(db, cfg, monkeypatch):
    confirmed = NOW - timedelta(hours=1)
    deal = seed(db, cfg, price_rechecked_at=util.iso(confirmed))
    remember(db, top=[deal])
    api(monkeypatch, {deal["asin"]: dict(product(2600), lastUpdate=minutes(NOW - timedelta(hours=2)))})
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    row = db.get_deal(deal["dedupe_key"])
    assert row["final_price"] == 25
    assert verify.price_confirmed_at(row) == confirmed
    assert result["yield"]["overall"]["readings"] == 0


def test_new_timestamp_without_new_drift_does_not_inflate_yield(db, cfg):
    deal = seed(db, cfg, conditions=["used"])
    for i in range(2):
        at = NOW + timedelta(minutes=10 * i)
        history = History(db, cfg, at)
        history.record(deal, product(current=[-1, -1, 2600]), True, at, "wall_used", 13, 0)
        assert history.yield_stats["overall"]["useful"] == int(i == 0)


def test_old_cache_cannot_make_rapid_reruns_buy_more_checks(db, cfg, monkeypatch):
    deal = seed(db, cfg)
    remember(db, top=[deal])
    calls = api(monkeypatch, {deal["asin"]: dict(product(2500), lastUpdate=minutes(NOW - timedelta(hours=1)))})
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    pipeline._recheck_prices(db, cfg, NOW + timedelta(minutes=1), image_backfill=False)
    assert len(calls) == 1
    pipeline._recheck_prices(db, cfg, NOW + timedelta(minutes=10), image_backfill=False)
    assert len(calls) == 2


def test_used_offer_timestamp_and_rendered_clocks(db, cfg, monkeypatch, tmp_path):
    deal = seed(db, cfg, conditions=["used"])
    remember(db, wall=[deal])
    at = NOW - timedelta(minutes=40)
    record = dict(product(2500, lastOffersUpdate=minutes(at)),
                  offers=[{"condition": 2, "offerCSV": [minutes(at), 2500, 0]}], liveOffersOrder=[0])
    api(monkeypatch, {deal["asin"]: record}, cost=13)
    KeepaBudget(500, 20, NOW).save(db)
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    site.render_site(db, cfg, NOW, out_dir=tmp_path)
    row = json.loads((tmp_path / "deals.json").read_text())["deals"][0]
    assert row["checked_at"] == util.iso(at)
    assert '40m ago</span>' in (tmp_path / "index.html").read_text()
    page = (tmp_path / row["deal_path"].lstrip("/") / "index.html").read_text()
    assert f'Price checked <time datetime="{util.iso(at)}">' in page


@pytest.mark.parametrize("failure", [None, "429", "transport", "unreadable", "no_key"])
def test_cost_receipts_lost_answers_and_budget_gates(db, cfg, monkeypatch, failure):
    rows = [seed(db, cfg, asin=f"B{i:09}") for i in range(15)]
    remember(db, top=rows[:10], wall=rows)
    KeepaBudget(20, 20, NOW).save(db)
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    monkeypatch.setenv("KEEPA_KEY", "offline")
    calls = []

    def get(url, params=None, **kw):
        calls.append(params)
        assert str(params["history"]) == "0" and "update" not in params
        if failure == "transport":
            raise httpx.ReadError("lost")
        if failure == "unreadable":
            return httpx.Response(200, text="incomplete", request=httpx.Request("GET", url))
        return httpx.Response(429 if failure == "429" else 200, json={
            "tokensLeft": 0 if failure else 10, "refillRate": 20, "tokensConsumed": 0 if failure else 10,
            "products": [] if failure else [product(2600, asin=a) for a in params["asin"].split(",")]},
            request=httpx.Request("GET", url))

    mock_keepa_get(monkeypatch, get)
    if failure == "no_key":
        monkeypatch.delenv("KEEPA_KEY")
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    counts = result["yield"]["overall"]
    assert len(calls) == (0 if failure == "no_key" else 1)
    assert counts["tokens"] == (0 if failure in {"429", "no_key"} else 10)
    assert counts["estimated_tokens"] == (10 if failure in {"transport", "unreadable"} else 0)
    assert counts["readings"] == (0 if failure else 10)
    assert counts["useful"] == (0 if failure else 10)


@pytest.mark.parametrize("charge", [0, 7])
def test_used_actual_charge_replaces_thirteen_token_estimate(db, cfg, monkeypatch, charge):
    deal = seed(db, cfg, conditions=["used"])
    remember(db, wall=[deal])
    KeepaBudget(100, 20, NOW).save(db)
    monkeypatch.setenv("KEEPA_KEY", "offline")
    monkeypatch.setattr(util, "now_utc", lambda: NOW)

    def get(url, params=None, **kw):
        assert params["offers"] == 20
        return httpx.Response(200, json={"tokensLeft": 100 - charge, "refillRate": 20,
            "tokensConsumed": charge, "products": [product(current=[10000, 10000, 2500])]},
            request=httpx.Request("GET", url))

    mock_keepa_get(monkeypatch, get)
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    values = result["yield"]["classes"]["wall_used"]
    assert values["tokens"] == charge and values["estimated_tokens"] == 0
    assert values["readings"] == 1
    assert result["tokens"]["bucket_end"] == 100 - charge


def test_private_growth_report_reads_only_saved_totals(db, cfg, tmp_path):
    deal = seed(db, cfg)
    History(db, cfg, NOW).record(deal, product(2600), True, NOW, "top_new", 1, 0)
    path = tmp_path / "snapshot.db"
    snapshot = DB(path)
    db.conn.backup(snapshot.conn)
    snapshot.close()
    data = report._price_recheck(path, NOW + timedelta(days=1))
    assert data == report_data(db, NOW + timedelta(days=1))
    _, text = report.build_report(cfg, {"price_recheck": data}, {}, NOW.date())
    assert "yesterday top_new: 1.0 tokens (0.0 estimated), 1 readings, 1 useful" in text
    assert "1.00 tokens/useful reading" in text


def test_adaptive_knobs_load_and_can_restore_fixed_cadence(cfg, tmp_path):
    assert cfg.keepa_recheck_adaptive["enabled"]
    assert cfg.keepa_recheck_adaptive["exploration_share"] == .1
    path = tmp_path / "config.yaml"
    path.write_text("keepa:\n  recheck_adaptive:\n    enabled: false\n    exploration_share: 0.2\n")
    loaded = load_config(path)
    assert loaded.keepa_recheck_adaptive == {"enabled": False, "exploration_share": .2}


def test_simulated_day_improves_yield_at_equal_coverage(cfg, monkeypatch):
    """Same population, price paths, lag and 20/min budget for both schedulers.

    189 top, 40 wall-only new, 85 used wall, 62 rest new; 80% stable.
    The 085 baseline also receives honest timestamps so stale-cache clocks
    cannot make either coverage or yield look better than it really is.
    """
    results = []
    for adaptive in (False, True):
        database = DB(":memory:")
        config = copy.deepcopy(cfg)
        config.keepa_recheck_adaptive["enabled"] = adaptive
        config.keepa_sweep_enabled = False
        config.keepa_focus_categories = []
        config.keepa_amazon_pages_per_pull = 3
        config.keepa_warehouse_pages_per_pull = 2
        groups = []
        population = {}
        for group, size in enumerate((189, 40, 85, 62)):
            rows = []
            for i in range(size):
                d = seed(database, config, asin=f"B{group}{i:08}", seen=NOW - timedelta(days=4),
                         conditions=["used"] if group == 2 else [],
                         price_rechecked_at=util.iso(NOW - timedelta(minutes=30)))
                volatile = i % 5 == 0
                # A shared modest observation history; the baseline ignores
                # it for scheduling but uses identical yield accounting.
                train(database, config, d, volatile=volatile, count=4)
                population[d["dedupe_key"]] = (volatile, 3 + i % 4)
                rows.append(d)
            groups.append(rows)
        top, wall_new, used, rest = groups
        wall = top[:100] + wall_new + used
        remember(database, top=top, wall=wall)
        clock = [NOW]
        KeepaBudget(650, 20, NOW).save(database)
        paid = useful = readings = lost = discovery = new_candidates = extra_pages = 0
        server_tokens = [650]
        discovered = set()
        top_shares, wall_shares, ages = [], [], []

        def check(deals, config, budget, **options):
            now = clock[0]
            result = verify.PriceRechecks()
            cost = sum(13 if verify.is_used_deal(d) else 1 for d in deals)
            remaining = budget.available(now) - cost
            assert remaining >= MIN_FOR_DEAL_QUERY
            budget.note({"tokensLeft": remaining, "refillRate": 20}, now)
            server_tokens[0] = remaining
            for deal in deals:
                volatile, period = population[deal["dedupe_key"]]
                # Cached data is ten minutes old for BOTH schedulers.
                observed = now - timedelta(minutes=10)
                tick = int((observed - NOW).total_seconds() / 600)
                cents = 2600 if volatile and (tick // period) % 2 else 2500
                record = product(cents, asin=deal["asin"])
                record["lastUpdate"] = minutes(observed)
                record["lastPriceChange"] = minutes(
                    NOW + timedelta(minutes=(tick // period) * period * 10) if volatile
                    else NOW - timedelta(days=3))
                if verify.is_used_deal(deal):
                    record["stats"]["lastOffersUpdate"] = minutes(observed)
                    record.update(offers=[{"condition": 2, "offerCSV": [minutes(observed), cents, 0]}],
                                  liveOffersOrder=[0])
                key = deal["dedupe_key"]
                result.products[key] = record
                result.costs[key] = (13 if verify.is_used_deal(deal) else 1, 0)
                result[key] = verify.judge_live_price(deal, record, config.keepa_recheck_tolerance_pct)
            return result

        monkeypatch.setattr(verify, "recheck_amazon_prices", check)
        monkeypatch.setenv("KEEPA_KEY", "offline")
        monkeypatch.setattr(util, "now_utc", lambda: clock[0])

        def deal_page(request):
            assert request.url.host == "api.keepa.com" and request.url.path == "/deal"
            sel = json.loads(request.url.params["selection"])
            used_lane = sel["priceTypes"] == [9]
            shape = 1 if not used_lane else 2 if sel["dateRange"] == 0 else 3
            tick = int((clock[0] - NOW).total_seconds() / 600)
            number = tick * 10000 + shape * 1000 + sel["page"] * 2
            # Two distinct, evidence-backed candidates/page, the remaining
            # rows fail the existing price gate. Candidates are NOT counted
            # as verified public deals or added to the fixed recheck cohort.
            rows = [(_wh_row if used_lane else _amazon_row)(number + i) for i in range(2)]
            for i, row in enumerate(rows):
                row["asin"] = f"B8{number + i:08d}"
            rows += [_junk_row(i) for i in range(148)]
            server_tokens[0] -= 5
            return httpx.Response(200, json={"deals": {"dr": rows}, "tokensConsumed": 5,
                                           "tokensLeft": server_tokens[0], "refillRate": 20})

        for tick in range(144):
            now = clock[0] = NOW + timedelta(minutes=10 * tick)
            bucket = KeepaBudget.load(database)
            lost += max(0, bucket.tokens_left + (200 if tick else 0) - 1200)
            server_tokens[0] = bucket.available(now)
            summary = {}
            with closing(PoliteFetcher(database, config, transport=httpx.MockTransport(deal_page),
                                       sleep_fn=lambda _: None)) as fetcher:
                adapter = KeepaDealsAdapter()

                def recheck(discover, deadline):
                    summary.update(pipeline._recheck_prices(
                        database, config, now, image_backfill=False, fetcher=fetcher,
                        overflow_discovery=discover if adaptive else None))

                adapter.recheck = recheck
                posts = adapter.fetch_posts(SRC, fetcher, database, config, now, None)
            extra = summary.get("overflow_discovery", {})
            discovery += 25 + extra.get("tokens", 0)
            extra_pages += extra.get("pages", 0)
            for post in posts:
                assert post.content_hash not in discovered
                discovered.add(post.content_hash)
                database.record_post(post, now)
            new_candidates += len(posts)
            stats = summary["yield"]["overall"]
            paid += stats["tokens"]
            useful += stats["useful"]
            readings += stats["readings"]
            assert MIN_FOR_DEAL_QUERY <= summary["tokens"]["bucket_end"] <= 1200
            rows = {d["dedupe_key"]: d for d in database.all_deals()}
            top_age = verify.confirmation_coverage([rows[d["dedupe_key"]] for d in top], now)
            wall_age = verify.confirmation_coverage([rows[d["dedupe_key"]] for d in wall], now)
            top_shares.append(top_age["within_1h"])
            wall_shares.append(wall_age["new"]["within_2h"])
            ages.append(wall_age["oldest_h"])
            assert top_shares[-1] >= .95 and wall_shares[-1] >= .90
            assert ages[-1] <= 12, (adaptive, tick, summary["planned"], summary["done"])
            shown = rank.site_deals(database, config, now)
            assert len(shown) == sum(map(len, groups))  # no hiding to manufacture coverage
            assert all(not verify.price_unconfirmed(d, config, now) for d in shown)
            remember(database, top=top, wall=wall)
        assert paid + discovery <= 650 + 143 * 200
        results.append({"adaptive": adaptive, "recheck_tokens": paid, "readings": readings,
                        "useful": useful, "tokens_per_useful_reading": round(paid / useful, 3),
                        "discovery_tokens": discovery, "new_candidates": new_candidates,
                        "extra_pages": extra_pages, "total_tokens_with_feeds": paid + discovery,
                        "min_top_within_1h": min(top_shares), "min_wall_new_within_2h": min(wall_shares),
                        "max_wall_age_h": max(ages), "refill_lost": lost})
        database.close()
    print(json.dumps(results, sort_keys=True))
    before, after = results
    assert after["tokens_per_useful_reading"] < before["tokens_per_useful_reading"]
    assert after["min_top_within_1h"] >= before["min_top_within_1h"]
    assert after["min_wall_new_within_2h"] >= before["min_wall_new_within_2h"]
    assert after["new_candidates"] > before["new_candidates"]
    # Discovery has first claim on overflow. It can replace some useful
    # rechecks; the fixed plan's objective includes the new candidates too.
    assert after["useful"] + after["new_candidates"] > before["useful"] + before["new_candidates"]
    assert after["refill_lost"] <= before["refill_lost"]
    assert after["max_wall_age_h"] <= before["max_wall_age_h"]
