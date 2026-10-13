"""Tests for dealradar.verify (tiers, liveness) and dealradar.rank."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.http_helpers import mock_keepa_get, api_transport

import pytest

from dealradar import dedupe, rank, util, verify
from dealradar.config import AppConfig
from dealradar.db import DB
from dealradar.models import ExtractedDeal, RawPost, SavingsResult

NOW = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)


def make_cfg() -> AppConfig:
    cfg = AppConfig()
    cfg.tier_boost = {"corroborated": 15.0, "history-checked": 10.0,
                      "unverified": 0.0}
    return cfg


def make_post(source: str = "hip2save", uid: str = "p1",
              text: str = "deal text") -> RawPost:
    return RawPost(
        source_name=source, uid=uid,
        url=f"https://example.com/{source}/{uid}",
        title="post title", text=text,
        published_at=NOW - timedelta(hours=1), fetched_at=NOW,
    )


def make_extracted(item: str = "Widget Pro 2000", store: str = "Target",
                   product_url: str = "", online_only: str = "true",
                   **kw) -> ExtractedDeal:
    return ExtractedDeal(
        item=item, store=store,
        source_url="https://example.com/post", source_name="hip2save",
        product_url=product_url, online_only=online_only, **kw,
    )


def make_savings(final: float | None = 25.0, regular: float | None = 100.0,
                 pct: float | None = 75.0, abs_s: float | None = 75.0,
                 basis: str = "quoted_regular") -> SavingsResult:
    return SavingsResult(
        computable=True, final_price=final, regular_price=regular,
        pct_at_checkout=pct, abs_savings=abs_s, basis=basis,
    )


def upsert(db, cfg, *, source: str, uid: str, now: datetime,
           item: str = "Widget Pro 2000",
           url: str = "https://www.target.com/p/widget",
           final: float = 25.0) -> dict:
    post = make_post(source=source, uid=uid, text=f"deal {source} {uid}")
    ext = make_extracted(item=item, product_url=url)
    sav = make_savings(final=final, abs_s=100.0 - final,
                       pct=100.0 - final)
    return dedupe.upsert_extracted(db, ext, sav, post, cfg, now)


def insert_plain_deal(db, key: str, *, store: str = "Target",
                      item: str = "Widget", pct: float | None = 75.0,
                      abs_s: float | None = 20.0, final: float | None = 25.0,
                      online: str = "true", status: str = "alive",
                      tier: str = "unverified", expires: str = "",
                      conditions: list[str] | None = None,
                      currency: str = "USD", region: str = "US",
                      first_seen: datetime = NOW) -> dict:
    payload = {
        "dedupe_key": key, "store": store, "item": item, "brand": "",
        "canonical_url": "", "asin": "", "final_price": final,
        "regular_price": 100.0, "pct_at_checkout": pct,
        "pct_with_rebates": None, "abs_savings": abs_s, "basis": "quoted_regular",
        "flags": [], "codes": [], "stack_steps": [], "rebates": [],
        "deferred_value": "", "conditions": conditions or [],
        "online_only": online, "shipping_cost_or_threshold": "",
        "quantity_or_unit": "", "region": region, "currency": currency,
        "expires_at": expires, "verify_tier": tier, "status": "alive",
        "first_seen_at": util.iso(first_seen), "last_seen_at": util.iso(first_seen),
        "confidence": 0.9, "sightings": [],
    }
    db.insert_deal(dedupe_key=key, store=store, item=item,
                   normalized_item=util.normalize_item(item),
                   canonical_url="", payload=payload, final_price=final,
                   pct_at_checkout=pct, abs_savings=abs_s, online_only=online,
                   expires_at=expires, now=first_seen)
    if tier != "unverified":
        db.update_deal(key, verify_tier=tier)
    if status != "alive":
        db.update_deal(key, status=status)
    return db.get_deal(key)


# ---------------------------------------------------------------- verify


def test_two_sources_within_window_corroborated(tmp_path) -> None:
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    res = upsert(db, cfg, source="hip2save", uid="a", now=NOW - timedelta(hours=2))
    upsert(db, cfg, source="slickdeals_frontpage", uid="b",
           now=NOW - timedelta(hours=1))
    assert len(db.all_deals()) == 1  # merged on canonical_url
    deal = db.get_deal(res["dedupe_key"])
    assert verify.assign_tier(db, deal, cfg, NOW) == "corroborated"
    verify.refresh_tiers(db, cfg, NOW)
    assert db.get_deal(res["dedupe_key"])["verify_tier"] == "corroborated"
    db.close()


def test_source_outside_window_does_not_corroborate(tmp_path) -> None:
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    assert cfg.corroborate_window_h == 24.0
    res = upsert(db, cfg, source="hip2save", uid="a",
                 now=NOW - timedelta(hours=30))  # outside the 24 h window
    upsert(db, cfg, source="slickdeals_frontpage", uid="b", now=NOW)
    deal = db.get_deal(res["dedupe_key"])
    assert verify.assign_tier(db, deal, cfg, NOW) == "unverified"
    # a third source inside the window tips it over
    upsert(db, cfg, source="dealnews", uid="c", now=NOW - timedelta(hours=1))
    deal = db.get_deal(res["dedupe_key"])
    assert verify.assign_tier(db, deal, cfg, NOW) == "corroborated"
    db.close()


def seed_history(db, key: str, prices: list[float]) -> None:
    for i, p in enumerate(prices):
        assert db.record_price(key, "Target", p,
                               NOW - timedelta(days=i + 1), f"seedhash{i}")


def test_history_checked_tier(tmp_path) -> None:
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    assert cfg.history_min_observations == 5

    # >=5 observations, median 100, final 25 <= 0.30 * median => history-checked
    res = upsert(db, cfg, source="hip2save", uid="a", now=NOW, final=25.0)
    key = res["dedupe_key"]
    seed_history(db, key, [90.0, 95.0, 100.0, 105.0, 110.0])
    deal = db.get_deal(key)
    assert verify.assign_tier(db, deal, cfg, NOW) == "history-checked"
    db.close()


def test_history_final_40_stays_unverified(tmp_path) -> None:
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    res = upsert(db, cfg, source="hip2save", uid="a", now=NOW, final=40.0)
    key = res["dedupe_key"]
    seed_history(db, key, [90.0, 95.0, 100.0, 105.0, 110.0])
    deal = db.get_deal(key)
    assert verify.assign_tier(db, deal, cfg, NOW) == "unverified"
    db.close()


def test_history_too_few_points_stays_unverified(tmp_path) -> None:
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    res = upsert(db, cfg, source="hip2save", uid="a", now=NOW, final=25.0)
    key = res["dedupe_key"]
    seed_history(db, key, [100.0, 100.0, 100.0])  # 3 + own = 4 < 5
    deal = db.get_deal(key)
    assert verify.assign_tier(db, deal, cfg, NOW) == "unverified"
    db.close()


def test_mark_expired_dead(tmp_path) -> None:
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    insert_plain_deal(db, "si|target|expired one",
                      expires=util.iso(NOW - timedelta(hours=1)))
    insert_plain_deal(db, "si|target|future one",
                      expires=util.iso(NOW + timedelta(hours=1)))
    insert_plain_deal(db, "si|target|no expiry", expires="")

    assert verify.mark_expired_dead(db, cfg, NOW) == 1
    assert db.get_deal("si|target|expired one")["status"] == "dead"
    assert db.get_deal("si|target|future one")["status"] == "alive"
    assert db.get_deal("si|target|no expiry")["status"] == "alive"
    # idempotent second pass: nothing new flips
    assert verify.mark_expired_dead(db, cfg, NOW) == 0
    db.close()


@pytest.mark.parametrize("text,expected", [
    ("This deal is EXPIRED", True),
    ("dead, mods please remove", False),
    ("Dead Sea salt", False),
    ("the deal is dead", True),
    ("a dead deal", True),
    ("Sold Out at checkout", True),
    ("sold   out everywhere", True),
    ("currently Out of Stock", True),
    ("OOS as of 3pm", True),
    ("great deal, still live", False),
    ("the deadline is friday", False),      # 'dead' only on word boundary
    ("expires tomorrow night", False),      # 'expired' only, not 'expires'
    ("choose whatever you like", False),    # no bare-'oos' inside words
    ("", False),
])
def test_text_marks_dead(text: str, expected: bool) -> None:
    assert verify.text_marks_dead(text) is expected


def test_keepa_check_stub(tmp_path, monkeypatch) -> None:
    cfg = make_cfg()
    deal = {"dedupe_key": "url|x", "asin": "B0TEST1234"}

    monkeypatch.delenv("KEEPA_KEY", raising=False)
    cfg.feature_flags = {}
    assert verify.keepa_check(deal, cfg) is None   # flag off

    cfg.feature_flags = {"keepa": True}
    assert verify.keepa_check(deal, cfg) is None   # flag on, no key

    monkeypatch.setenv("KEEPA_KEY", "k-test")
    assert verify.keepa_check(deal, cfg) is None   # stub: never a verdict


def test_keepa_cross_check_waits_for_tokens(tmp_path, monkeypatch) -> None:
    """assign_tier's Keepa cross-check spends from the shared token budget:
    held back (deal stays unverified, retried later) while the estimate is
    under a recheck's worth, and its response updates the budget."""
    import json

    import httpx

    from dealradar.keepa_budget import META_KEY

    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    cfg.feature_flags = {"keepa": True}
    monkeypatch.setenv("KEEPA_KEY", "k-test")
    res = upsert(db, cfg, source="hip2save", uid="a", now=NOW,
                 url="https://www.amazon.com/dp/B0TESTASN1", final=25.0)
    deal = db.get_deal(res["dedupe_key"])
    assert deal["asin"] == "B0TESTASN1"
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params["asin"])
        return httpx.Response(
            200, json={"products": [{"stats": {"avg90": [14999]}}],
                       "tokensLeft": 41, "refillRate": 5},
            request=httpx.Request("GET", url))

    mock_keepa_get(monkeypatch, fake_get)
    db.meta_set(META_KEY, json.dumps({
        "tokens_left": 5, "refill_rate": 5, "seen_at": util.iso(NOW)}))
    assert verify.assign_tier(db, deal, cfg, NOW) == "unverified"
    assert calls == []                                    # held back
    assert not db.get_deal(res["dedupe_key"]).get("keepa_checked")
    # Two minutes of refill later (15 tokens) the check runs once.
    later = NOW + timedelta(minutes=2)
    assert verify.assign_tier(db, deal, cfg, later) == "history-checked"
    assert calls == ["B0TESTASN1"]
    assert db.get_deal(res["dedupe_key"])["keepa_checked"] is True
    assert json.loads(db.meta_get(META_KEY))["tokens_left"] == 41
    db.close()


def test_configured_floors_used_50_everything_else_40() -> None:
    """The owner's floors: used/open-box publish at >= 50% off, everything
    else at >= 40%, the site's second tier starts at 40 too."""
    from dealradar.config import load_config

    cfg = load_config()
    assert (cfg.min_pct, cfg.used_min_pct, cfg.site_show_from_pct) == \
        (40.0, 50.0, 40.0)
    assert rank.is_publishable(base_deal(pct_at_checkout=40.0), cfg)[0]
    ok, reason = rank.is_publishable(base_deal(pct_at_checkout=39.9), cfg)
    assert not ok and reason.startswith("pct_at_checkout")
    used = dict(conditions=["open_box"])
    assert rank.is_publishable(base_deal(pct_at_checkout=50.0, **used), cfg)[0]
    ok, reason = rank.is_publishable(base_deal(pct_at_checkout=49.9, **used),
                                     cfg)
    assert not ok and "used_min_pct 50" in reason
    # The verification ratio follows min_pct: "well below typical" = 60%.
    assert verify._history_ratio(cfg) == pytest.approx(0.60)


# ---------------------------------------------------------------- rank


def base_deal(**over) -> dict:
    d = {
        "id": 1, "dedupe_key": "si|target|widget", "store": "Target",
        "item": "Widget", "status": "alive", "online_only": "true",
        "currency": "USD", "region": "US", "pct_at_checkout": 75.0,
        "abs_savings": 20.0, "final_price": 25.0, "conditions": [],
        "verify_tier": "unverified", "first_seen_at": util.iso(NOW),
        "expires_at": "",
    }
    d.update(over)
    return d


def test_is_publishable_gates() -> None:
    cfg = make_cfg()
    assert cfg.min_pct == 70.0 and cfg.min_abs_savings == 5.0

    ok, reason = rank.is_publishable(base_deal(), cfg)
    assert ok and reason == ""

    for over, gate in [
        ({"status": "dead"}, "status"),
        ({"online_only": "unknown"}, "online_only"),
        ({"online_only": "false"}, "online_only"),
        ({"currency": "CAD"}, "currency"),
        ({"region": "CA"}, "region"),
        ({"pct_at_checkout": 69.9}, "pct_at_checkout"),
        ({"pct_at_checkout": None}, "pct_at_checkout"),
        ({"abs_savings": 4.0}, "abs_savings"),
        ({"abs_savings": None}, "abs_savings"),
    ]:
        ok, reason = rank.is_publishable(base_deal(**over), cfg)
        assert ok is False, over
        assert reason.startswith(gate), (over, reason)

    # payload fields default ok when absent
    d = base_deal()
    del d["currency"], d["region"]
    ok, _ = rank.is_publishable(d, cfg)
    assert ok


def test_score_tier_boost_beats_raw_pct() -> None:
    cfg = make_cfg()
    corroborated_75 = base_deal(verify_tier="corroborated",
                                pct_at_checkout=75.0, abs_savings=0.0)
    unverified_80 = base_deal(verify_tier="unverified",
                              pct_at_checkout=80.0, abs_savings=0.0)
    s_c = rank.score(corroborated_75, cfg, NOW)
    s_u = rank.score(unverified_80, cfg, NOW)
    assert s_c == pytest.approx(90.0)   # 75 + 15
    assert s_u == pytest.approx(80.0)   # 80 + 0
    assert s_c > s_u


def test_score_penalties_and_abs_bonus() -> None:
    cfg = make_cfg()
    plain = base_deal(abs_savings=0.0)
    s0 = rank.score(plain, cfg, NOW)

    with_conditions = base_deal(abs_savings=0.0,
                                conditions=["clip coupon", "new customers"])
    assert rank.score(with_conditions, cfg, NOW) == pytest.approx(
        s0 - 2 * cfg.condition_penalty)

    aged = base_deal(abs_savings=0.0,
                     first_seen_at=util.iso(NOW - timedelta(hours=10)))
    assert rank.score(aged, cfg, NOW) == pytest.approx(
        s0 - 10 * cfg.age_penalty_per_hour)

    # abs-savings bonus is capped at $100
    big_abs = base_deal(abs_savings=500.0)
    assert rank.score(big_abs, cfg, NOW) == pytest.approx(
        s0 + 100.0 * cfg.abs_savings_weight)


def test_published_deals_filters_and_orders(tmp_path) -> None:
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()

    insert_plain_deal(db, "k_corroborated", item="Alpha", pct=75.0,
                      tier="corroborated")
    insert_plain_deal(db, "k_unverified", item="Bravo", pct=80.0)
    insert_plain_deal(db, "k_unknown_online", item="Charlie", online="unknown")
    insert_plain_deal(db, "k_low_pct", item="Delta", pct=69.9)
    insert_plain_deal(db, "k_low_abs", item="Echo", abs_s=4.0)
    insert_plain_deal(db, "k_dead", item="Foxtrot", status="dead")

    out = rank.published_deals(db, cfg, NOW)
    assert [d["dedupe_key"] for d in out] == ["k_corroborated", "k_unverified"]
    assert out[0]["score"] > out[1]["score"]
    assert all("verify_tier" in d for d in out)
    db.close()
