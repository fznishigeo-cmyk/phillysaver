"""Task 085: visibility, real scheduler/budget boundaries and a 20/min day."""

import json
from datetime import timedelta

import pytest

from dealradar import pipeline, rank, util, verify
from dealradar.fetch.keepa import _FeedConfirmations
from dealradar.keepa_budget import KeepaBudget, MIN_FOR_DEAL_QUERY
from dealradar.publish import site
from tests.test_price_accuracy_engine import NOW, cfg, db, seed


def remember(db, top=(), wall=()):
    for name, deals in ((site.PICKS_META, top), (site.WALL_META, wall)):
        db.meta_set(name, json.dumps([d["dedupe_key"] for d in deals]))


def charged_checks(monkeypatch, clock):
    calls = []

    def check(deals, cfg, budget, **options):
        cost = sum(13 if verify.is_used_deal(d) else 1 for d in deals)
        cost += len(deals) if options.get("live") else 0
        left = budget.available(clock[0]) - cost
        assert left >= MIN_FOR_DEAL_QUERY
        calls.append(([d["dedupe_key"] for d in deals], cost))
        budget.note({"tokensLeft": left, "refillRate": 20}, clock[0])
        return {d["dedupe_key"]: True for d in deals}

    monkeypatch.setattr(verify, "recheck_amazon_prices", check)
    return calls


def test_wall_gap_overlap_condition_mix_and_cadence(db, cfg, monkeypatch):
    # A possible composition of the supplied aggregate, NOT a claim about
    # the unavailable live rows: 96/185 = 52% at 1 h, 124/185 = 67% at 3 h.
    new = [seed(db, cfg, asin=f"B0{i:08}", seen=NOW - timedelta(days=3),
                price_rechecked_at=util.iso(NOW - timedelta(hours=.5 if i < 96 else 2)))
           for i in range(100)]
    used = [seed(db, cfg, asin=f"B1{i:08}", seen=NOW - timedelta(days=3), conditions=["used"],
                 price_rechecked_at=util.iso(NOW - timedelta(hours=2 if i < 24 else 8 if i < 45 else 23.1)))
            for i in range(85)]
    remember(db, top=new, wall=new + used)
    before = verify.confirmation_coverage(new + used, NOW)
    assert before["within_1h"] == pytest.approx(96 / 185)
    assert before["within_3h"] == pytest.approx(124 / 185)
    assert before["new"]["within_2h"] == 1
    KeepaBudget(650, 20, NOW).save(db)
    charged_checks(monkeypatch, [NOW])
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert stats["planned"]["wall_new"] == 0  # all new wall rows belong to top
    assert stats["planned"]["top_new"] == 4
    assert stats["wall_schedule"]["top_overlap"] == 100
    assert stats["wall_schedule"]["new"] == 100 and stats["wall_schedule"]["used"] == 85
    # Before task 085 every one of these <24 h used rows was skipped.
    assert stats["planned"]["wall_used"] == 61
    assert stats["done"]["wall_used"] > 0
    assert stats["confirmed_age"]["wall"]["new"]["within_1h"] == 1


@pytest.mark.parametrize("tokens,counts", [(15, (5, 0, 0)), (20, (5, 5, 0)), (25, (5, 5, 5))])
def test_nearly_empty_bucket_top_then_wall_then_rest_and_stop(db, cfg, monkeypatch, tokens, counts):
    groups = [[seed(db, cfg, asin=f"B{j}{i:08}") for i in range(5)] for j in range(3)]
    remember(db, top=groups[0], wall=groups[0] + groups[1])
    KeepaBudget(tokens, 20, NOW).save(db)
    calls = charged_checks(monkeypatch, [NOW])
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert [stats["done"][name + "_new"] for name in ("top", "wall", "rest")] == list(counts)
    assert [key for keys, cost in calls for key in keys] == [d["dedupe_key"] for group, n in zip(groups, counts) for d in group[:n]]
    assert stats["tokens"]["bucket_start"] == tokens
    assert stats["tokens"]["bucket_end"] == 10


def test_used_suspects_cannot_starve_new_and_off_wall_never_scans(db, cfg, monkeypatch):
    new = [seed(db, cfg, asin=f"B0{i:08}") for i in range(10)]
    used = [seed(db, cfg, asin=f"B1{i:08}", conditions=["used"],
                 gone_suspect_at=util.iso(NOW - timedelta(hours=2))) for i in range(50)]
    remember(db, top=new[:5], wall=new + used[:2])
    KeepaBudget(60, 20, NOW).save(db)
    calls = charged_checks(monkeypatch, [NOW])
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert [k for keys, cost in calls for k in keys][:10] == [d["dedupe_key"] for d in new]
    assert stats["used_off_wall"] == 48 and stats["planned"]["rest_used"] == 0
    assert stats["used_checked"] == 1 and stats["used_skipped_budget"] == 1
    assert stats["tokens"]["requested"] == 23


def test_bucket_never_exceeds_plan_capacity_and_unknown_overflow_is_unknown():
    bucket = KeepaBudget(1190, 20, NOW)
    assert bucket.available(NOW + timedelta(hours=6)) == 1200
    assert bucket.available(NOW + timedelta(minutes=10)) == 1200
    assert bucket.overflow(10, NOW) == 190
    assert KeepaBudget().overflow(10, NOW) is None


@pytest.mark.parametrize("age,expected_done,end,waste", [(0, 0, 1050, 300), (.6, 50, 1000, 0)])
def test_overflow_spends_oldest_visible_top_first_not_just_confirmed(db, cfg, monkeypatch, age, expected_done, end, waste):
    new = [seed(db, cfg, asin=f"B0{i:08}", price_rechecked_at=util.iso(
        NOW - timedelta(hours=age))) for i in range(60)]
    # The wall-only row is older, but top still has priority.
    older = seed(db, cfg, asin="B100000000", price_rechecked_at=util.iso(NOW - timedelta(hours=1)))
    remember(db, top=new, wall=new + [older])
    KeepaBudget(1050, 20, NOW).save(db)
    calls = charged_checks(monkeypatch, [NOW])
    if not age:
        # No eligible visible confirmations: report the forecast honestly.
        remember(db, top=new, wall=new)
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert stats["done"]["idle_top_new"] == expected_done
    assert stats["done"]["idle_wall_new"] == 0
    assert stats["tokens"]["bucket_end"] == end
    assert stats["tokens"]["unused_per_hour_estimate"] == waste
    assert sum(cost for keys, cost in calls) == expected_done
    # A same-time rerun adds no calls, rows or checks.
    count = len(calls)
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert len(calls) == count


def test_recent_attempts_and_free_readings_are_not_paid_again(db, cfg, monkeypatch):
    free = seed(db, cfg, keepa_price_seen_at=util.iso(NOW - timedelta(minutes=10)))
    retry = seed(db, cfg, asin="B000000002", recheck_tried_at=util.iso(NOW - timedelta(minutes=10)))
    remember(db, top=[free, retry], wall=[free, retry])
    KeepaBudget(1200, 20, NOW).save(db)
    calls = charged_checks(monkeypatch, [NOW])
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert calls == []
    assert stats["wall_schedule"]["cadence_new"] == 1
    assert stats["wall_schedule"]["retry_new"] == 1


def test_wall_metadata_round_trips_and_stale_prices_hide_alive(db, cfg, tmp_path):
    recent = seed(db, cfg, price_rechecked_at=util.iso(NOW))
    old = seed(db, cfg, asin="B000000002", price_rechecked_at=util.iso(NOW - timedelta(hours=12, seconds=1)))
    old_used = seed(db, cfg, asin="B000000003", conditions=["used"],
                    price_rechecked_at=util.iso(NOW - timedelta(hours=24, seconds=1)))
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path)
    assert json.loads(db.meta_get(site.WALL_META)) == [recent["dedupe_key"]]
    assert result["confirmed_age"]["wall"]["new"]["within_2h"] == 1
    assert {d["dedupe_key"] for d in rank.site_deals(db, cfg, NOW)} == {recent["dedupe_key"]}
    assert db.get_deal(old["dedupe_key"])["status"] == db.get_deal(old_used["dedupe_key"])["status"] == "alive"


@pytest.mark.parametrize("free_feed", [False, True])
def test_day_of_144_runs_meets_targets_inside_20_per_minute(db, cfg, monkeypatch, free_feed):
    # 189 top, 185 wall (100 overlap/new + 85 used), 62 rest new,
    # 535 off-wall used. Even zero matching feeds must meet steady targets.
    top = [seed(db, cfg, asin=f"B0{i:08}", seen=NOW - timedelta(days=4),
                price_rechecked_at=util.iso(NOW - timedelta(hours=.5 if i < 176 else 13.6)))
           for i in range(189)]
    wall_used = [seed(db, cfg, asin=f"B1{i:08}", conditions=["used"], seen=NOW - timedelta(days=4),
                      price_rechecked_at=util.iso(NOW - timedelta(hours=8 + (i % 24) / 8))) for i in range(85)]
    rest_new = [seed(db, cfg, asin=f"B2{i:08}", seen=NOW - timedelta(days=4)) for i in range(62)]
    for i in range(535):
        seed(db, cfg, asin=f"B3{i:08}", seen=NOW - timedelta(days=4), conditions=["used"])
    wall = top[:100] + wall_used
    remember(db, top=top, wall=wall)
    clock = [NOW]
    calls = charged_checks(monkeypatch, clock)
    KeepaBudget(650, 20, NOW).save(db)
    paid = 0
    lost = 0
    hidden = []
    top_shares, wall_shares, wall_oldest = [], [], []
    for tick in range(144):
        now = clock[0] = NOW + timedelta(minutes=tick * 10)
        bucket = KeepaBudget.load(db)
        # Model five ordinary feed pages (Amazon/Fresh/Deep + 2 focus),
        # 25 tokens/run, before the real scheduler; all use the same bucket.
        lost += max(0, bucket.tokens_left + (200 if tick else 0) - 1200)
        start = bucket.available(now)
        bucket.note({"tokensLeft": start - 25, "refillRate": 20}, now)
        bucket.save(db)
        paid += 25
        if free_feed:
            # Deliberately conservative: ~195 new + 58 used per HOUR, not
            # the live per-pass counts; exact price and data-time matching.
            selected = (top + rest_new)[tick % 6::6]
            selected_used = wall_used[tick % 6:58:6]
            confirms = _FeedConfirmations(db, now)
            stamp = int(now.timestamp() / 60) - verify.KEEPA_EPOCH_MINUTES
            for deals, used in ((selected[:33], False), (selected_used, True)):
                confirms.note([{"asin": d["asin"], "current": [2500] * 10,
                                "lastUpdate": stamp} for d in deals], used=used)
        before = sum(cost for keys, cost in calls)
        stats = pipeline._recheck_prices(db, cfg, now, image_backfill=False)
        paid += sum(cost for keys, cost in calls) - before
        assert 10 <= stats["tokens"]["bucket_end"] <= 1200
        assert stats["done"]["rest_used"] == 0
        hidden.append(stats["hidden_unconfirmed"]["new"])
        rows = {d["dedupe_key"]: d for d in db.all_deals()}
        shown = rank.site_deals(db, cfg, now)
        # Recovered off-wall used offers retain their 24 h display limit;
        # the visible wall's stronger age target is asserted separately.
        assert all(not verify.price_unconfirmed(d, cfg, now) for d in shown)
        ages = verify.confirmation_coverage([rows[d["dedupe_key"]] for d in wall], now)
        wall_shares.append(ages["new"]["within_2h"])
        top_shares.append(verify.confirmed_age([rows[d["dedupe_key"]] for d in top], now)["within_1h"])
        assert wall_shares[-1] >= .90 and top_shares[-1] >= .95
        if tick >= 12:  # allow the initially old used checks to catch up
            wall_oldest.append(ages["oldest_h"])
            assert ages["oldest_h"] <= 12  # no hiding needed for the fixture wall
            assert len([d for d in wall if not verify.price_unconfirmed(rows[d["dedupe_key"]], cfg, now)]) == 185
        # Re-publish membership after each run; no stale row can keep
        # receiving paid scans by being artificially left in WALL_META.
        shown_keys = {d["dedupe_key"] for d in shown}
        remember(db, top=top, wall=[d for d in wall if d["dedupe_key"] in shown_keys])
    assert hidden[-1] == 0
    assert paid <= 650 + 143 * 200
    print({"free_feed": free_feed, "paid_day": paid, "refill_lost": lost,
           "ending_bucket": KeepaBudget.load(db).available(clock[0]), "hidden_new": hidden[-1],
           "min_top_within_1h": min(top_shares), "min_wall_new_within_2h": min(wall_shares),
           "max_wall_age_after_warmup_h": max(wall_oldest)})


@pytest.mark.parametrize("failure", ["unreadable", "transport"])
def test_lost_answers_cannot_spend_the_same_bucket_twice(db, cfg, monkeypatch, failure):
    import httpx
    from tests.http_helpers import mock_keepa_get

    rows = [seed(db, cfg, asin=f"B0{i:08}") for i in range(100)]
    remember(db, top=rows[:10], wall=rows[:20])
    KeepaBudget(75, 20, NOW).save(db)
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    monkeypatch.setenv("KEEPA_KEY", "offline")
    sizes = []

    def get(url, params=None, **kwargs):
        assert str(params["history"]) == "0"
        sizes.append(len(params["asin"].split(",")))
        if failure == "transport":
            raise httpx.ReadError("fixture response lost")
        return httpx.Response(200, text="cut-off response", request=httpx.Request("GET", url))

    mock_keepa_get(monkeypatch, get)
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert sizes == [10, 10, 45]
    assert stats["checked"] == 0 and stats["tokens"]["bucket_end"] == 10
    assert stats["tokens"]["requested"] == 65


def test_visible_used_wall_is_due_before_twelve_hours(db, cfg, monkeypatch):
    deal = seed(db, cfg, conditions=["used"],
                price_rechecked_at=util.iso(NOW - timedelta(hours=8)))
    remember(db, wall=[deal])
    KeepaBudget(650, 20, NOW).save(db)
    charged_checks(monkeypatch, [NOW])
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert stats["planned"]["wall_used"] == stats["done"]["wall_used"] == 1
    assert verify.price_confirmed_at(db.get_deal(deal["dedupe_key"])) == NOW
