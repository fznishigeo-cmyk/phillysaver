"""Live-review fixes on the open-box recheck + supply change (2026-10-11):
an inconclusive Keepa answer waits a retry window instead of being re-paid
every run; a token shortfall spreads across deals instead of starving the
newest; the tier cross-check settles and is bounded per pass; a revival
needs a recheck kill and a post written after the death; the daily spend
ledger never loses a run's spend."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

from dealradar import dedupe, pipeline, util, verify
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.models import RawPost
from tests.test_dedupe import make_cfg, make_extracted, make_savings

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def _cfg():
    cfg = load_config()
    cfg.feature_flags["keepa"] = True
    return cfg


def _seed(db, key, *, used=True, stamp="", tried="", status="alive",
          flags=None, died=""):
    d = {"dedupe_key": key, "store": "Amazon", "item": key,
         "regular_price": 100.0, "final_price": 30.0, "pct_at_checkout": 70.0,
         "abs_savings": 70.0, "online_only": "true", "currency": "USD",
         "region": "US", "status": status, "flags": flags or [],
         "conditions": ["used", "open_box"] if used else [],
         "category": "home", "verify_tier": "unverified",
         "first_seen_at": "2026-10-10T06:00:00+00:00",
         "asin": "B0" + key[-8:].upper().rjust(8, "X"),
         "image_url": "https://m.media-amazon.com/images/I/x.jpg",
         "price_rechecked_at": stamp, "recheck_tried_at": tried,
         "died_at": died}
    db.insert_deal(dedupe_key=key, store="Amazon", item=key,
                   normalized_item=key, canonical_url="", payload=d,
                   final_price=30.0, pct_at_checkout=70.0, abs_savings=70.0,
                   online_only="true", expires_at="", now=NOW)
    if status != "alive":
        db.update_deal(key, payload=d, status=status)


class TestInconclusiveWaitsItsWindow:
    def test_answered_none_is_not_repaid_every_run(self, tmp_path, monkeypatch):
        db = DB(tmp_path / "state.db")
        _seed(db, "url|stuck0001")
        calls = []

        def fake(deals, _cfg, budget=None, used_offers=True):
            calls.extend(d["dedupe_key"] for d in deals)
            return {d["dedupe_key"]: None for d in deals}  # answered, no verdict

        monkeypatch.setattr(verify, "recheck_amazon_prices", fake)
        cfg = _cfg()
        pipeline._recheck_prices(db, cfg, NOW)
        assert calls == ["url|stuck0001"]
        row = db.get_deal("url|stuck0001")
        assert row["recheck_tried_at"] == util.iso(NOW)
        assert row["status"] == "alive" and not row["price_rechecked_at"]
        for minutes in (10, 60, 300):  # fast runs inside the 6 h window
            pipeline._recheck_prices(db, cfg, NOW + timedelta(minutes=minutes))
        assert calls == ["url|stuck0001"]
        pipeline._recheck_prices(db, cfg, NOW + timedelta(hours=6, minutes=1))
        assert calls == ["url|stuck0001", "url|stuck0001"]
        db.close()

    def test_a_miss_without_an_answer_retries_next_run(self, tmp_path, monkeypatch):
        db = DB(tmp_path / "state.db")
        _seed(db, "url|miss00001")
        calls = []

        def fake(deals, _cfg, budget=None, used_offers=True):
            calls.extend(d["dedupe_key"] for d in deals)
            return {}  # 429 / transport: no entry at all

        monkeypatch.setattr(verify, "recheck_amazon_prices", fake)
        pipeline._recheck_prices(db, _cfg(), NOW)
        pipeline._recheck_prices(db, _cfg(), NOW + timedelta(minutes=10))
        assert calls == ["url|miss00001", "url|miss00001"]
        assert not db.get_deal("url|miss00001")["recheck_tried_at"]
        db.close()

    def test_new_condition_deal_retries_hourly(self, tmp_path, monkeypatch):
        db = DB(tmp_path / "state.db")
        _seed(db, "url|newd00001", used=False)
        calls = []

        def fake(deals, _cfg, budget=None, used_offers=True):
            calls.extend(d["dedupe_key"] for d in deals)
            return {d["dedupe_key"]: None for d in deals}

        monkeypatch.setattr(verify, "recheck_amazon_prices", fake)
        for minutes in (0, 10, 50, 61):
            pipeline._recheck_prices(db, _cfg(), NOW + timedelta(minutes=minutes))
        assert calls == ["url|newd00001", "url|newd00001"]
        db.close()


class TestFairOrder:
    def test_longest_unchecked_used_deals_go_first(self, tmp_path, monkeypatch):
        db = DB(tmp_path / "state.db")
        # Inserted newest-checked first, so rowid order is the wrong order.
        _seed(db, "url|recent001", stamp=util.iso(NOW - timedelta(hours=7)))
        _seed(db, "url|older0001", stamp=util.iso(NOW - timedelta(hours=30)))
        _seed(db, "url|never0001")
        seen = []

        def fake(deals, _cfg, budget=None, used_offers=True):
            seen.append([d["dedupe_key"] for d in deals])
            return {d["dedupe_key"]: True for d in deals}

        monkeypatch.setattr(verify, "recheck_amazon_prices", fake)
        pipeline._recheck_prices(db, _cfg(), NOW)
        assert seen[0] == ["url|never0001", "url|older0001", "url|recent001"]
        db.close()


class TestTierCrossCheck:
    def test_used_deals_never_cross_checked(self, tmp_path, monkeypatch):
        db = DB(tmp_path / "state.db")
        _seed(db, "url|usedx0001")
        called = []
        monkeypatch.setattr(verify, "keepa_check",
                            lambda *a, **k: called.append(1) or True)
        deal = db.get_deal("url|usedx0001")
        assert verify.assign_tier(db, deal, _cfg(), NOW) == "unverified"
        assert called == []
        db.close()

    def test_answer_without_amazon_average_settles(self, tmp_path, monkeypatch):
        import httpx
        db = DB(tmp_path / "state.db")
        _seed(db, "url|noavg0001", used=False)
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        n = []

        def get(url, params=None, timeout=None):
            n.append(1)
            return httpx.Response(200, json={"products": [{"stats": {"avg90": [-1, 5000]}}],
                                             "tokensLeft": 500, "refillRate": 20},
                                  request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "get", get)
        cfg = _cfg()
        verify.refresh_tiers(db, cfg, NOW)
        verify.refresh_tiers(db, cfg, NOW + timedelta(minutes=10))
        assert len(n) == 1
        assert db.get_deal("url|noavg0001")["keepa_checked"] is True
        db.close()

    def test_cross_checks_bounded_per_pass(self, tmp_path, monkeypatch):
        db = DB(tmp_path / "state.db")
        for i in range(verify.KEEPA_CHECKS_PER_PASS + 10):
            _seed(db, f"url|bound{i:04d}", used=False)
        calls = []
        monkeypatch.setattr(verify, "keepa_check",
                            lambda deal, cfg, budget=None, answered=None:
                            calls.append(deal["dedupe_key"]) or None)
        verify.refresh_tiers(db, _cfg(), NOW)
        assert len(calls) == verify.KEEPA_CHECKS_PER_PASS
        db.close()


def _ext():
    return make_extracted(item="Mainstays Shelf", store="Walmart",
                          product_url="https://www.walmart.com/ip/123")


def _post(source, uid, published_at):
    return RawPost(source_name=source, uid=uid, url=f"https://ex.com/{uid}",
                   title="t", text=f"Mainstays Shelf $39 {uid}",
                   published_at=published_at, fetched_at=NOW)


class TestCarefulRevival:
    def _setup(self, tmp_path, flags):
        db = DB(tmp_path / "state.db")
        cfg = make_cfg()
        sav = make_savings(final=39.0, regular=99.0, pct=60.6, abs_s=60.0)
        res = dedupe.upsert_extracted(db, _ext(), sav, _post("blogA", "a1", NOW - timedelta(hours=2)),
                                      cfg, NOW - timedelta(hours=2))
        key = res["dedupe_key"]
        payload = dict(db.get_deal(key), flags=flags, died_at=util.iso(NOW))
        db.update_deal(key, payload=payload, status="dead")
        return db, cfg, sav, key

    def test_post_written_before_the_death_never_revives(self, tmp_path):
        db, cfg, sav, key = self._setup(tmp_path, ["price_moved"])
        older = _post("blogC", "c1", NOW - timedelta(hours=4))
        assert dedupe.upsert_extracted(db, _ext(), sav, older, cfg, NOW)["revived"] is False
        assert db.get_deal(key)["status"] == "dead"
        newer = _post("blogD", "d1", NOW + timedelta(minutes=30))
        assert dedupe.upsert_extracted(db, _ext(), sav, newer, cfg,
                                       NOW + timedelta(minutes=30))["revived"] is True
        db.close()

    def test_a_death_declared_by_a_post_is_not_undone(self, tmp_path):
        db, cfg, sav, key = self._setup(tmp_path, [])  # killed by "sold out" text
        newer = _post("blogD", "d1", NOW + timedelta(minutes=30))
        assert dedupe.upsert_extracted(db, _ext(), sav, newer, cfg,
                                       NOW + timedelta(minutes=30))["revived"] is False
        assert db.get_deal(key)["status"] == "dead"
        db.close()


def test_two_runs_sharing_a_key_both_count_toward_the_daily_cap(tmp_path):
    db = DB(tmp_path / "state.db")
    for spend in (0.165, 0.17):  # fast runs at :01 and :11 share :00:fast
        db.start_run("2026-10-10T12:00:fast", "fast", NOW)
        db.finish_run("2026-10-10T12:00:fast", now=NOW, items_fetched=1,
                      posts_new=1, deals_parsed=1, published=0, spend_usd=spend)
    assert abs(db.spend_usd_for_day(NOW) - 0.335) < 1e-9
    db.close()


def test_daily_cap_boost_expires_on_its_own():
    """Operator-approved $15/day for three days, then the $6 base again
    without anyone editing the config."""
    from dealradar.config import AppConfig, daily_spend_cap

    cfg = AppConfig()
    cfg.spend_cap_usd_per_day = 6.0
    cfg.spend_cap_boost_usd_per_day = 15.0
    cfg.spend_cap_boost_until = "2026-10-14"
    utc = timezone.utc
    assert daily_spend_cap(cfg, datetime(2026, 10, 11, 9, 0, tzinfo=utc)) == 15.0
    assert daily_spend_cap(cfg, datetime(2026, 10, 13, 23, 59, tzinfo=utc)) == 15.0
    assert daily_spend_cap(cfg, datetime(2026, 10, 14, 0, 0, tzinfo=utc)) == 6.0
    # US evening on the 13th is already the 14th in UTC: base cap.
    est = timezone(timedelta(hours=-4))
    assert daily_spend_cap(cfg, datetime(2026, 10, 13, 21, 0, tzinfo=est)) == 6.0
    for broken in ("", "soon", "2026-13-40"):
        cfg.spend_cap_boost_until = broken
        assert daily_spend_cap(cfg, datetime(2026, 10, 11, tzinfo=utc)) == 6.0
    cfg.spend_cap_boost_until, cfg.spend_cap_boost_usd_per_day = "2026-10-14", 0.0
    assert daily_spend_cap(cfg, datetime(2026, 10, 11, tzinfo=utc)) == 6.0


def test_shipped_config_boost_is_bounded():
    """The shipped boost is the operator's $15 and ends on 2026-10-14."""
    cfg = load_config()
    assert cfg.spend_cap_usd_per_day == 6.0
    assert cfg.spend_cap_boost_usd_per_day == 15.0
    assert cfg.spend_cap_boost_until == "2026-10-14"


def test_pipeline_parses_with_the_boosted_cap(tmp_path, monkeypatch):
    """With $7 already spent today, the base $6 cap would skip parsing; the
    boost lets the run parse with the $8 left."""
    from dealradar.config import daily_spend_cap

    cfg = load_config()
    db = DB(tmp_path / "state.db")
    db.start_run("2026-10-11T08", "hourly", NOW)
    db.finish_run("2026-10-11T08", now=NOW, items_fetched=1, posts_new=1,
                  deals_parsed=1, published=0, spend_usd=7.0)
    later = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    assert daily_spend_cap(cfg, later) - db.spend_usd_for_day(later) > 7.9
    db.close()


def test_prune_never_removes_a_live_deal_for_its_age(tmp_path):
    """Operator call: Amazon "used - like new" offers stay up until someone
    buys them, so retention deletes only DEAD deals, deals_days after they
    died, and never a live deal however long ago it was last sighted."""
    from dealradar.util import iso

    db = DB(tmp_path / "state.db")
    long_ago = NOW - timedelta(days=30)

    def add(key, status, died_at=None):
        db.insert_deal(dedupe_key=key, store="Amazon", item=key, normalized_item=key,
                       canonical_url="", payload={"died_at": died_at or ""},
                       final_price=5.0, pct_at_checkout=80.0, abs_savings=20.0,
                       online_only="true", expires_at="", now=long_ago)
        if status == "dead":
            db.update_deal(key, payload={"died_at": died_at or ""}, status="dead")

    add("live_old", "alive")                                    # last seen 30 days ago
    add("dead_long_ago", "dead", iso(NOW - timedelta(days=20)))
    add("dead_recently", "dead", iso(NOW - timedelta(days=2)))  # last seen 30 d ago
    add("dead_no_stamp", "dead")                                # falls back to last_seen
    db.prune(NOW, deals_days=14)
    left = {d["dedupe_key"] for d in db.all_deals()}
    assert left == {"live_old", "dead_recently"}
    db.close()
