"""Tests for dealradar.dedupe: keys, fuzzy matching, idempotent upserts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dealradar import dedupe, util
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
              text: str = "Air fryer 75% off", url: str = "") -> RawPost:
    return RawPost(
        source_name=source,
        uid=uid,
        url=url or f"https://example.com/{source}/{uid}",
        title="post title",
        text=text,
        published_at=NOW - timedelta(hours=1),
        fetched_at=NOW,
    )


def make_extracted(item: str = "Ninja Foodi 8qt Air Fryer Black",
                   store: str = "Kohls", product_url: str = "",
                   online_only: str = "true", **kw) -> ExtractedDeal:
    return ExtractedDeal(
        item=item, store=store,
        source_url="https://example.com/post", source_name="hip2save",
        product_url=product_url, online_only=online_only, **kw,
    )


def make_savings(final: float | None = 25.0, regular: float | None = 100.0,
                 pct: float | None = 75.0, abs_s: float | None = 75.0,
                 basis: str = "quoted_regular", **kw) -> SavingsResult:
    return SavingsResult(
        computable=True, final_price=final, regular_price=regular,
        pct_at_checkout=pct, abs_savings=abs_s, basis=basis, **kw,
    )


def test_canonical_key_variants() -> None:
    url_key = dedupe.canonical_key("Kohls", "https://kohls.com/p/1", "Item")
    assert url_key == "url|https://kohls.com/p/1"
    si_key = dedupe.canonical_key("  Kohls ", "", "Ninja Foodi Air Fryer")
    assert si_key == "si|kohls|" + util.normalize_item("Ninja Foodi Air Fryer")


def test_same_post_twice_is_idempotent(tmp_path) -> None:
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    post = make_post()
    ext = make_extracted(
        product_url="https://www.amazon.com/dp/B0TEST1234", codes=["SAVE10"])
    sav = make_savings()

    res1 = dedupe.upsert_extracted(db, ext, sav, post, cfg, NOW)
    res2 = dedupe.upsert_extracted(db, ext, sav, post, cfg, NOW)

    assert res1["is_new_deal"] is True
    assert res2["is_new_deal"] is False
    assert res2["price_improved"] is False
    assert res1["dedupe_key"] == res2["dedupe_key"] \
        == "url|https://www.amazon.com/dp/B0TEST1234"

    deals = db.all_deals()
    assert len(deals) == 1
    assert len(db.sightings_for(res1["deal_id"])) == 1
    count, median = db.price_stats(res1["dedupe_key"])
    assert count == 1
    assert median == 25.0
    # ASIN derived from the canonical URL, facts only in the payload.
    assert deals[0]["asin"] == "B0TEST1234"
    db.close()


def test_two_sources_same_canonical_url_merge(tmp_path) -> None:
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    url = "https://www.target.com/p/widget"

    post_a = make_post(source="hip2save", uid="a", text="widget deal A")
    post_b = make_post(source="slickdeals_frontpage", uid="b",
                       text="widget deal B different wording")
    ext_a = make_extracted(item="Widget Pro 2000", product_url=url,
                           codes=["SAVE10"], online_only="unknown",
                           expires_at="2026-10-15T00:00:00+00:00")
    ext_b = make_extracted(item="Widget Pro 2000 (Black)", product_url=url,
                           codes=["SAVE10", "EXTRA5"], online_only="true",
                           expires_at="2026-10-12T00:00:00+00:00")

    res_a = dedupe.upsert_extracted(db, ext_a, make_savings(), post_a, cfg,
                                    NOW - timedelta(hours=2))
    res_b = dedupe.upsert_extracted(db, ext_b, make_savings(), post_b, cfg,
                                    NOW - timedelta(hours=1))

    assert res_a["dedupe_key"] == res_b["dedupe_key"]
    assert res_b["is_new_deal"] is False
    assert len(db.all_deals()) == 1

    deal = db.get_deal(res_a["dedupe_key"])
    assert deal is not None
    assert len(db.sightings_for(deal["id"])) == 2
    # list facts unioned, ordered and deduped
    assert deal["codes"] == ["SAVE10", "EXTRA5"]
    # online_only upgrades from unknown
    assert deal["online_only"] == "true"
    # earliest non-empty expiry kept
    assert deal["expires_at"] == "2026-10-12T00:00:00+00:00"
    db.close()


def test_online_only_false_wins_and_first_nonunknown_sticks(tmp_path) -> None:
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    url = "https://www.example.com/p/thing"

    def up(uid: str, online: str) -> dict:
        post = make_post(source=f"src_{uid}", uid=uid, text=f"thing {uid}")
        return dedupe.upsert_extracted(
            db, make_extracted(item="Thing", product_url=url,
                               online_only=online),
            make_savings(), post, cfg, NOW)

    key = up("1", "true")["dedupe_key"]
    up("2", "unknown")
    assert db.get_deal(key)["online_only"] == "true"  # unknown never downgrades
    up("3", "false")
    assert db.get_deal(key)["online_only"] == "false"  # explicit false wins
    up("4", "true")
    assert db.get_deal(key)["online_only"] == "false"  # false sticks
    db.close()


def test_fuzzy_merge_same_store(tmp_path) -> None:
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    assert cfg.fuzzy_threshold == 0.90

    post_a = make_post(source="hip2save", uid="a", text="ninja A")
    post_b = make_post(source="krazy_coupon_lady", uid="b", text="ninja B")
    ext_a = make_extracted(item="Ninja Foodi 8qt Air Fryer Black",
                           store="Kohls")
    ext_b = make_extracted(item="Ninja Foodi Air Fryer 8 qt", store="Kohls")

    res_a = dedupe.upsert_extracted(db, ext_a, make_savings(), post_a, cfg, NOW)
    res_b = dedupe.upsert_extracted(db, ext_b, make_savings(), post_b, cfg, NOW)

    assert res_b["is_new_deal"] is False
    assert res_b["dedupe_key"] == res_a["dedupe_key"]
    assert len(db.all_deals()) == 1
    assert len(db.sightings_for(res_a["deal_id"])) == 2

    # A clearly different item at the same store stays separate.
    post_c = make_post(source="hip2save", uid="c", text="instant pot")
    ext_c = make_extracted(
        item="Instant Pot Duo Crisp 6 Quart Pressure Cooker", store="Kohls")
    res_c = dedupe.upsert_extracted(db, ext_c, make_savings(), post_c, cfg, NOW)
    assert res_c["is_new_deal"] is True
    assert len(db.all_deals()) == 2
    db.close()


def test_find_existing_lookup_order(tmp_path) -> None:
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    url = "https://www.bestbuy.com/p/headphones"
    post = make_post()
    ext = make_extracted(item="Sony WH-1000XM5 Headphones", store="Best Buy",
                         product_url=url)
    dedupe.upsert_extracted(db, ext, make_savings(), post, cfg, NOW)

    # exact key
    assert dedupe.find_existing(db, "Best Buy", url,
                                "Sony WH-1000XM5 Headphones", 0.90)
    # canonical URL with a different item name
    assert dedupe.find_existing(db, "Best Buy", url, "totally different", 0.90)
    # no url, fuzzy same-store name
    assert dedupe.find_existing(db, "Best Buy", "",
                                "Sony Headphones WH-1000XM5", 0.90)
    # no match: other store, no url
    assert dedupe.find_existing(db, "Walmart", "",
                                "Sony WH-1000XM5 Headphones", 0.90) is None
    db.close()


def test_lower_price_replaces_higher_does_not(tmp_path) -> None:
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    url = "https://www.example.com/p/vacuum"

    def up(uid: str, final: float, pct: float, basis: str) -> dict:
        post = make_post(source=f"s{uid}", uid=uid, text=f"vacuum {uid}")
        return dedupe.upsert_extracted(
            db, make_extracted(item="Robot Vacuum", product_url=url),
            make_savings(final=final, pct=pct,
                         abs_s=100.0 - final, basis=basis),
            post, cfg, NOW)

    res1 = up("1", 50.0, 50.0, "quoted_regular")
    key = res1["dedupe_key"]
    assert res1["price_improved"] is False  # new deal, nothing to improve on

    res2 = up("2", 40.0, 60.0, "stated_pct")
    assert res2["price_improved"] is True
    deal = db.get_deal(key)
    assert deal["final_price"] == 40.0
    assert deal["pct_at_checkout"] == 60.0
    assert deal["abs_savings"] == 60.0
    assert deal["basis"] == "stated_pct"

    res3 = up("3", 45.0, 55.0, "quoted_regular")
    assert res3["price_improved"] is False
    deal = db.get_deal(key)
    assert deal["final_price"] == 40.0  # best price kept
    assert deal["basis"] == "stated_pct"  # matching basis kept with it

    # 3 sightings, 3 price-history rows (one per distinct post)
    assert len(db.sightings_for(deal["id"])) == 3
    count, _ = db.price_stats(key)
    assert count == 3
    db.close()


def test_confidence_max_and_canonical_url_fill(tmp_path) -> None:
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()

    post_a = make_post(source="hip2save", uid="a", text="lamp a")
    ext_a = make_extracted(item="Desk Lamp LED Deluxe", store="Wayfair",
                           product_url="", confidence=0.9)
    res_a = dedupe.upsert_extracted(db, ext_a, make_savings(), post_a, cfg, NOW)

    post_b = make_post(source="dealnews", uid="b", text="lamp b")
    ext_b = make_extracted(item="Deluxe LED Desk Lamp", store="Wayfair",
                           product_url="https://www.wayfair.com/p/lamp",
                           confidence=0.4)
    res_b = dedupe.upsert_extracted(db, ext_b, make_savings(), post_b, cfg, NOW)

    assert res_b["dedupe_key"] == res_a["dedupe_key"]
    assert res_b["payload"]["confidence"] == 0.9
    # Later-resolved canonical URL is filled into the payload (the frozen DB
    # API has no column setter for it).
    assert res_b["payload"]["canonical_url"] == "https://www.wayfair.com/p/lamp"
    db.close()


def test_amazon_asin_recovered_from_post_text(tmp_path) -> None:
    """Slickdeals/RetailMeNot posts hand the LLM a tracker product_url while
    the body links the real Amazon product. One ASIN in the text => the deal
    gets it (Keepa price checks + photo follow); a roundup with several =>
    no guess."""
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    post = make_post(
        source="slickdeals_frontpage", uid="s1",
        text="Tarte lip plump $12.15 Shop (https://www.amazon.com/dp/B0TARTE123?tag=x) "
             "via Slickdeals (https://slickdeals.net/f/1)")
    ext = make_extracted(item="tarte Maracuja Juicy Lip Plump", store="Amazon",
                         product_url="https://slickdeals.net/go/123")
    res = dedupe.upsert_extracted(db, ext, make_savings(), post, cfg, NOW)
    row = db.get_deal(res["dedupe_key"])
    assert row["asin"] == "B0TARTE123"
    assert row["canonical_url"] == "https://www.amazon.com/dp/B0TARTE123"
    assert res["dedupe_key"] == "url|https://www.amazon.com/dp/B0TARTE123"
    assert dedupe._asin_from_text(
        "a (https://amazon.com/dp/B0AAAAAAAA) b (https://www.amazon.com/dp/B0BBBBBBBB)") == ""
    assert dedupe._asin_from_text("no links here $5") == ""
    db.close()


def test_new_sighting_revives_a_dead_deal(tmp_path) -> None:
    """Live wart (B0BKQSDF5C): the price recheck killed a Warehouse deal and
    a fresh sighting minutes later merged into the dead row and left it
    dead. A NEW sighting revives it — even at the same price, where the
    merge keeps the old flags; replaying a known post never does."""
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    ext = make_extracted(item="Steiner binoculars", store="Amazon",
                         product_url="https://www.amazon.com/dp/B0BKQSDF5C",
                         conditions=["used", "open_box"])
    sav = make_savings(final=744.50, regular=1599.99, pct=53.5, abs_s=855.49)
    res1 = dedupe.upsert_extracted(
        db, ext, sav, make_post("keepa_amazon_drops", "B0BKQSDF5C:wh:744.50"),
        cfg, NOW)
    assert res1["revived"] is False
    key = res1["dedupe_key"]

    def kill() -> None:  # what the recheck does on a False verdict
        payload = dict(db.get_deal(key), flags=["limited_qty", "price_moved"],
                       died_at=util.iso(NOW),
                       price_rechecked_at=util.iso(NOW))
        db.update_deal(key, payload=payload, status="dead")

    kill()
    later = NOW + timedelta(minutes=15)
    other = make_post("slickdeals_frontpage", "sd1", "Steiner binoculars $744.50")
    res2 = dedupe.upsert_extracted(db, ext, sav, other, cfg, later)
    assert res2["revived"] is True and res2["price_improved"] is False
    row = db.get_deal(key)
    assert row["status"] == "alive"
    assert row["flags"] == ["limited_qty"]
    assert row["died_at"] == "" and row["price_rechecked_at"] == ""

    kill()
    res3 = dedupe.upsert_extracted(db, ext, sav, other, cfg, later)
    assert res3["revived"] is False
    assert db.get_deal(key)["status"] == "dead"
    assert len(db.all_deals()) == 1
    assert len(db.sightings_for(res1["deal_id"])) == 2
    db.close()


def test_dearer_sighting_never_revives_a_dead_deal(tmp_path) -> None:
    """A dead $50 deal re-posted at $180 backs no stored price: the
    sighting is recorded, the deal stays dead at $50. Within the recheck's
    10% (or cheaper) it comes back, stamped so older Keepa data cannot
    kill it again."""
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    ext = make_extracted(item="Widget deluxe", store="Amazon",
                         product_url="https://www.amazon.com/dp/B0CCCCCCCC")
    res = dedupe.upsert_extracted(
        db, ext, make_savings(final=50.0, regular=200.0, pct=75.0, abs_s=150.0),
        make_post("a", "a1"), cfg, NOW)
    key = res["dedupe_key"]

    def kill() -> None:
        payload = dict(db.get_deal(key), flags=["price_moved"],
                       died_at=util.iso(NOW), price_rechecked_at=util.iso(NOW))
        db.update_deal(key, payload=payload, status="dead")

    kill()
    later = NOW + timedelta(hours=1)
    dear = make_savings(final=180.0, regular=200.0, pct=10.0, abs_s=20.0)
    res = dedupe.upsert_extracted(db, ext, dear, make_post("b", "b1"), cfg,
                                  later)
    assert res["revived"] is False
    row = db.get_deal(key)
    assert row["status"] == "dead" and row["final_price"] == 50.0
    assert "price_moved" in row["flags"] and row["price_rechecked_at"]
    assert len(row["sightings"]) == 2
    near = make_savings(final=54.99, regular=200.0, pct=72.5, abs_s=145.01)
    res = dedupe.upsert_extracted(db, ext, near, make_post("c", "c1"), cfg,
                                  later)
    assert res["revived"] is True
    row = db.get_deal(key)
    assert row["status"] == "alive" and row["final_price"] == 50.0
    assert row["price_since"] == util.iso(later)
    db.close()
