"""Tests for dealradar.dedupe: keys, fuzzy matching, idempotent upserts."""

from __future__ import annotations

import dataclasses
import itertools

from datetime import datetime, timedelta, timezone

import pytest

from dealradar import dedupe, util
from dealradar.config import AppConfig
from dealradar.db import DB
from dealradar.fetch.rss import post_photos
from dealradar.models import ExtractedDeal, PostPhotos, RawPost, SavingsResult
from dealradar.product_images import name_words

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
    post_b.published_at = post_a.published_at + timedelta(minutes=1)
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
    # the later post's stated expiry wins
    assert deal["expires_at"] == "2026-10-12T00:00:00+00:00"
    db.close()


def test_online_only_most_recent_explicit_value_wins(tmp_path) -> None:
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    url = "https://www.example.com/p/thing"

    def up(uid: str, online: str) -> dict:
        post = make_post(source=f"src_{uid}", uid=uid, text=f"thing {uid}")
        post.published_at += timedelta(minutes=int(uid))
        return dedupe.upsert_extracted(
            db, make_extracted(item="Thing", product_url=url,
                               online_only=online),
            make_savings(), post, cfg, NOW)

    key = up("1", "true")["dedupe_key"]
    up("2", "unknown")
    assert db.get_deal(key)["online_only"] == "true"  # unknown never downgrades
    up("3", "false")
    assert db.get_deal(key)["online_only"] == "false"
    up("4", "true")
    assert db.get_deal(key)["online_only"] == "true"
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
    other = dataclasses.replace(
        make_post("slickdeals_frontpage", "sd1", "Steiner binoculars $744.50"),
        published_at=later)  # written after the death
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
    res = dedupe.upsert_extracted(
        db, ext, dear, dataclasses.replace(make_post("b", "b1"), published_at=later),
        cfg, later)
    assert res["revived"] is False
    row = db.get_deal(key)
    assert row["status"] == "dead" and row["final_price"] == 50.0
    assert "price_moved" in row["flags"] and row["price_rechecked_at"]
    assert len(row["sightings"]) == 2
    near = make_savings(final=54.99, regular=200.0, pct=72.5, abs_s=145.01)
    res = dedupe.upsert_extracted(
        db, ext, near, dataclasses.replace(make_post("c", "c1"), published_at=later),
        cfg, later)
    assert res["revived"] is True
    row = db.get_deal(key)
    assert row["status"] == "alive" and row["final_price"] == 50.0
    assert row["price_since"] == util.iso(later)
    db.close()


UP = "https://blog.example/wp-content/uploads/2026/10/"
POST_URL = "https://blog.example/toy-roundup/"
_DBS = itertools.count()


def _ingest(tmp_path, html: str, deals, dropped=(), url: str = POST_URL) -> dict[str, str]:
    """An image-granted feed entry through the ingest path, as the pipeline
    runs it: the body's photos and first image (RssAdapter), every deal the
    parse read on post.parsed (parse_posts; ``dropped`` items failed the
    evidence check and are not upserted), shared links marked, each
    surviving deal upserted. A deal is (item, link as posted) or (item,
    link as posted, link resolved). Returns item -> its card photo (UP
    stripped)."""
    from dealradar import pipeline
    from dealradar.fetch.rss import _first_image, post_photos
    from dealradar.parse.extractor import _post_for_deals

    db = DB(tmp_path / f"state{next(_DBS)}.db")
    post = make_post(source="blog", uid="roundup", url=url)
    post.image_url = _first_image(html)
    post.photos = post_photos(html, url)
    post = _post_for_deals(post, len(deals))
    extracted = [make_extracted(item=d[0], store="Amazon", product_url=d[-1],
                                link_as_posted=d[1]) for d in deals]
    post.parsed = [(e.item, e.link_as_posted) for e in extracted]
    pairs = [(post, e) for e in extracted if e.item not in dropped]
    pipeline._mark_shared_links(pairs)
    keys = {e.item: dedupe.upsert_extracted(db, e, make_savings(), post, make_cfg(), NOW)
            ["dedupe_key"] for _p, e in pairs}
    out = {item: db.get_deal(k)["image_url"].removeprefix(UP) for item, k in keys.items()}
    db.close()
    return out


TOYS = [("Crayola Markers 40ct", "https://www.amazon.com/dp/B0000000A1"),
        ("LEGO Classic Bricks", "https://www.amazon.com/dp/B0000000B2"),
        ("Play-Doh 10-Pack Starter Set", "https://www.amazon.com/dp/B0000000C3")]
GENERIC = ("markers", "lego", "playdoh")          # file names that name no product
NAMED_FILES = ("Crayola-Markers-40ct", "LEGO-Classic-Bricks-300x300", "play-doh-starter")
ALTS = ("Crayola Markers 40ct", "LEGO Classic Bricks", "Play-Doh Starter Set")
FEATURED = (f'<figure class="wp-block-image"><img src="{UP}featured.jpg"></figure>'
            '<p>Great toy deals today!</p>')


def _img(name: str, alt: str) -> str:
    return f'<img src="{UP}{name}.jpg" alt="{alt}">'


def _button(link: str) -> str:
    return (f'<div class="wp-block-buttons"><div class="wp-block-button">'
            f'<a class="wp-block-button__link" href="{link}">Buy now</a></div></div>')


#: The reviewed layouts that shifted every photo to a neighbour's card
#: under layout pairing: (photo block, item, link) -> one product's HTML.
LAYOUTS = {
    "figure/h3/p-link": lambda img, item, link: (
        f'<figure class="wp-block-image">{img}</figure><h3>{item}</h3>'
        f'<p>Only $19.99 (reg $59.99) <a href="{link}">Buy now</a></p>'),
    "figure/p/buttons": lambda img, item, link: (
        f'<figure class="wp-block-image">{img}</figure><p>{item} only $19.99</p>{_button(link)}'),
    "columns": lambda img, item, link: (
        f'<div class="wp-block-column"><figure>{img}</figure><p>{item} $19.99</p>'
        f'{_button(link)}</div>'),
    "media-text": lambda img, item, link: (
        f'<div class="wp-block-media-text"><figure class="wp-block-media-text__media">{img}'
        f'</figure><div class="wp-block-media-text__content"><h3>{item}</h3>'
        f'<p>$19.99 <a href="{link}">Buy</a></p></div></div>'),
    "wp-caption": lambda img, item, link: (
        f'<div class="wp-caption">{img}<p class="wp-caption-text">{item}</p></div>'
        f'<p>Only $19.99 <a href="{link}">Buy now</a></p>'),
    "mirror h3-link/p/figure": lambda img, item, link: (
        f'<h3><a href="{link}">{item}</a></h3><p>Only $19.99</p><figure>{img}</figure>'),
}


def _listicle(layout: str, names, alts=("", "", "")) -> str:
    wrap = LAYOUTS[layout]
    body = "".join(wrap(_img(n, a), item, link) for n, a, (item, link) in zip(names, alts, TOYS))
    if layout == "columns":
        body = f'<div class="wp-block-columns">{body}</div>'
    return FEATURED + body


@pytest.mark.parametrize("layout", LAYOUTS)
def test_reviewed_layouts_never_give_a_neighbours_photo(tmp_path, layout) -> None:
    """Review finding (high): a heading, a price line or a button between a
    photo and its link shifted every photo of a Gutenberg listicle to the
    previous (or next) product's card. Where a photo sits proves nothing:
    photos that name no product give no card a photo; photos whose alt or
    file name names each product give each deal its own."""
    assert _ingest(tmp_path, _listicle(layout, GENERIC), TOYS) \
        == dict.fromkeys((i for i, _l in TOYS), "")
    # Names alone never place a photo (in-link evidence only): none.
    none = dict.fromkeys((i for i, _l in TOYS), "")
    assert _ingest(tmp_path, _listicle(layout, NAMED_FILES), TOYS) == none
    assert _ingest(tmp_path, _listicle(layout, ("a", "b", "c"), ALTS), TOYS) == none


def _heading_listicle(names, alts=("", "", ""), lead: str = FEATURED,
                      intro: str = '<p>Shop <a href="https://www.amazon.com/toys">toys</a></p>'):
    items = "".join(
        f'<h3><a href="{link}">{i}. {item} $19.99</a></h3><figure>{_img(n, a)}</figure>'
        for i, (n, a, (item, link)) in enumerate(zip(names, alts, TOYS), 1))
    return (lead + intro + items +
            '<p>Shop more toy deals <a href="https://www.amazon.com/b?node=12345">here</a></p>')


def test_heading_listicle_with_featured_image_and_shop_more_link(tmp_path) -> None:
    """Review finding (high): a featured image, an intro, a heading-linked
    listicle and a closing 'shop more' link read as photo-then-link and
    gave LEGO the markers. Classic-editor lead (<p><img><br>intro</p>) too;
    an intro 'shop the sale' link with a trailing 'pin for later' figure;
    a gallery over a linked list. Right photo by name, or none."""
    none = dict.fromkeys((i for i, _l in TOYS), "")
    classic = f'<p><img src="{UP}collage.jpg"><br>Big toy deals today!</p>'
    for lead in (FEATURED, classic):
        assert _ingest(tmp_path, _heading_listicle(GENERIC, lead=lead), TOYS) == none
        assert _ingest(tmp_path, _heading_listicle(("a", "b", "c"), ALTS, lead=lead), TOYS) \
            == none  # names never place a photo (in-link evidence only)
    sale = ('<p><a href="https://www.target.com/c/toy-sale/-/N-1">Shop the sale here</a></p>'
            + "".join(f'<figure>{_img(n, "")}</figure><p>{item} $19.99 <a href="{link}">Buy</a>'
                      '</p>' for n, (item, link) in zip(GENERIC, TOYS))
            + f'<figure><img src="{UP}pin-for-later.jpg"></figure>')
    assert _ingest(tmp_path, FEATURED + sale, TOYS) == none
    gallery = ('<figure class="wp-block-gallery">'
               + "".join(f'<figure>{_img(n, "")}</figure>' for n in GENERIC) + '</figure><ul>'
               + "".join(f'<li><a href="{link}">{item} $19.99</a></li>' for item, link in TOYS)
               + '</ul>')
    assert _ingest(tmp_path, FEATURED + gallery, TOYS) == none
    named_gallery = gallery.replace('alt=""', 'alt="{}"').format(*ALTS)
    assert _ingest(tmp_path, FEATURED + named_gallery, TOYS) == none  # names never place a photo (in-link evidence only)


def test_photo_inside_its_own_link_needs_no_name(tmp_path) -> None:
    """In-link rule: Hip2Save puts each product's photo inside that
    product's link — the photo is the deal's whatever its file is called
    (the link as posted, a short link, or the resolved URL's ASIN)."""
    html = FEATURED + "".join(
        f'<p><a href="{link}">{_img(n, "")}</a></p><p>{item} $19.99 <a href="{link}">Buy</a></p>'
        for n, (item, link) in zip(GENERIC, TOYS))
    assert _ingest(tmp_path, html, TOYS) \
        == {i: f"{n}.jpg" for (i, _l), n in zip(TOYS, GENERIC)}


def test_photo_inside_one_link_named_after_another_product_gives_none(tmp_path) -> None:
    """Conflict rule: the markers link wraps a photo the post names after
    the LEGO bricks, while another photo is named after the markers. The
    two rules disagree for the markers: no photo, not either guess."""
    html = FEATURED + (
        f'<p><a href="{TOYS[0][1]}"><img src="{UP}x.jpg" alt="LEGO Classic Bricks"></a></p>'
        f'<p><img src="{UP}y.jpg" alt="Crayola Markers 40ct"></p>')
    # Names only veto: the markers' in-link photo is named after LEGO, so
    # no one's; LEGO (its link not on the page) gets nothing from a name.
    assert _ingest(tmp_path, html, TOYS[:2]) == {TOYS[0][0]: "", TOYS[1][0]: ""}


def test_two_products_of_one_brand_need_names_that_separate_them(tmp_path) -> None:
    """Brand-only collision: two Crayola products whose photos say only
    'Crayola' get none, nor do photos naming only the start of them
    ('Crayola Markers' for the 40ct); photos whose words separate them go
    to each."""
    deals = [("Crayola Markers 40ct", "https://www.amazon.com/dp/B0000000A1"),
             ("Crayola Colored Pencils 50ct", "https://www.amazon.com/dp/B0000000A2")]
    html = FEATURED + "".join(
        f'<figure><img src="{UP}crayola-{i}.jpg" alt="{alt}"></figure>'
        f'<p>{item} $9.99 <a href="{link}">Buy</a></p>'
        for i, (alt, (item, link)) in enumerate(zip(("Crayola", "Crayola"), deals)))
    assert _ingest(tmp_path, html, deals) == {deals[0][0]: "", deals[1][0]: ""}
    start = html.replace('alt="Crayola"', 'alt="Crayola Markers"', 1) \
        .replace('alt="Crayola"', 'alt="Crayola Colored Pencils"', 1)
    assert _ingest(tmp_path, start, deals) == {deals[0][0]: "", deals[1][0]: ""}
    html = html.replace('alt="Crayola"', 'alt="Crayola Markers 40ct"', 1) \
        .replace('alt="Crayola"', 'alt="Crayola Colored Pencils, 50 Count"', 1)
    assert _ingest(tmp_path, html, deals) == {deals[0][0]: "", deals[1][0]: ""}  # names never place a photo (in-link evidence only)


def test_sale_page_collage_never_lands_on_a_card(tmp_path) -> None:
    """Review finding (medium): (a) the sale collage sits inside the
    Target /c/ sale link the LLM gave the link-less Hot Wheels item: a sale
    page names no product, so the collage is no one's; LEGO and Barbie keep
    the photos inside their own links. (b) Two items given one sale link
    (a collage beside it), one fails the evidence check: the survivor
    still holds a shared link (counted over everything the parse read) and
    gets neither the collage nor the post's image."""
    sale = "https://www.target.com/c/toy-sale/-/N-5xtnr"
    lego, barbie = "https://www.target.com/p/lego/-/A-2", "https://www.target.com/p/barbie/-/A-3"
    html = (f'<p><a href="{sale}"><img src="{UP}toy-sale-collage.jpg"></a></p>'
            '<p>Big toy sale! Hot Wheels 5-packs $4.99 (reg $7.99).</p>'
            f'<p><a href="{lego}"><img src="{UP}lego.jpg"></a> LEGO Classic $20</p>'
            f'<p><a href="{barbie}"><img src="{UP}barbie.jpg"></a> Barbie Dreamhouse $90</p>')
    deals = [("Hot Wheels 5-Pack", sale), ("LEGO Classic Bricks", lego),
             ("Barbie Dreamhouse", barbie)]
    assert _ingest(tmp_path, html, deals) == {
        "Hot Wheels 5-Pack": "", "LEGO Classic Bricks": "lego.jpg",
        "Barbie Dreamhouse": "barbie.jpg"}
    page = "https://www.kohls.com/catalog/home-sale.jsp"   # a sale page no rule knows
    html = (f'<p><img src="{UP}lead.jpg"></p><p>Home sale!</p>'
            f'<p><a href="{page}"><img src="{UP}sale-collage.jpg"></a></p>'
            '<ul><li>SONGMICS Shelf $39.99</li><li>Drying Rack $19.99</li></ul>')
    deals = [("SONGMICS Shelf", page), ("Drying Rack", page)]
    assert _ingest(tmp_path, html, deals, dropped=("Drying Rack",)) == {"SONGMICS Shelf": ""}


def test_single_product_post_lends_its_image_only_when_it_shows_one(tmp_path) -> None:
    """The post's own image is a deal's only on a post of one product: one
    deal parsed, at most one product link and one photo besides the
    featured one. A second product link or photo: none."""
    kettle = "https://www.amazon.com/dp/B0KETTLE01"
    one = (f'<p><img src="{UP}kettle-hero.jpg"></p><p>Kettle $12 <a href="{kettle}">Buy</a>'
           f' <a href="{kettle}?tag=x">again</a></p><p><img src="{UP}kettle-box.jpg"></p>')
    deal = [("Steel Kettle", kettle)]
    assert _ingest(tmp_path, one, deal) == {"Steel Kettle": "kettle-hero.jpg"}
    two_links = one + '<p>Also: <a href="https://www.amazon.com/dp/B0TOASTER1">a toaster</a></p>'
    assert _ingest(tmp_path, two_links, deal) == {"Steel Kettle": ""}
    two_photos = one + f'<p><img src="{UP}toaster.jpg"></p>'
    assert _ingest(tmp_path, two_photos, deal) == {"Steel Kettle": ""}
    # A post with no photos read (no grant: API adapters) lends its image.
    db = DB(tmp_path / "plain.db")
    post = make_post(uid="plain")
    post.image_url = "https://m.media-amazon.com/images/I/kettle.jpg"
    res = dedupe.upsert_extracted(db, make_extracted(item="Steel Kettle"), make_savings(),
                                  post, make_cfg(), NOW)
    assert db.get_deal(res["dedupe_key"])["image_url"] == post.image_url
    db.close()


def test_a_later_post_fills_a_photo_less_deal_but_never_replaces_a_photo(tmp_path) -> None:
    """A deal left photo-less by one roundup takes its photo from a later
    post that shows it; a photo once set is never replaced."""
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    shelf_link = "https://www.target.com/p/shelf/-/A-111"
    shelf = make_extracted(item="SONGMICS Shelf", store="Target", product_url=shelf_link)
    first = make_post(uid="r1")
    first.photos = PostPhotos(links={"a", "b"})
    key = dedupe.upsert_extracted(db, shelf, make_savings(), first, cfg, NOW)["dedupe_key"]
    assert db.get_deal(key)["image_url"] == ""
    later = make_post(uid="r2")
    later.photos = PostPhotos(in_link={shelf_link: UP + "shelf.jpg"}, links={"a", "b"})
    dedupe.upsert_extracted(db, shelf, make_savings(), later, cfg, NOW)
    assert db.get_deal(key)["image_url"] == UP + "shelf.jpg"
    again = make_post(uid="r3")
    again.photos = PostPhotos(in_link={shelf_link: UP + "other.jpg"})
    dedupe.upsert_extracted(db, shelf, make_savings(), again, cfg, NOW)
    assert db.get_deal(key)["image_url"] == UP + "shelf.jpg"
    db.close()


def test_link_as_posted_is_stored_and_filled_on_merge(tmp_path) -> None:
    """A post's photos are keyed by its links as posted, and the photo
    backfill matches on them later: a new deal stores its link as posted;
    a row stored without one takes it from a sighting of the same product
    (never from a variant merged by name), and keeps it."""
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    ip = "https://www.walmart.com/ip/Glue-Gun/123"
    goto = "https://goto.walmart.com/c/1/2/3?u=https%3A%2F%2Fwww.walmart.com%2Fip%2F123"
    ext = make_extracted(item="WORKPRO Cordless Glue Gun", store="Walmart",
                         product_url=ip, link_as_posted=goto)
    key = dedupe.upsert_extracted(db, ext, make_savings(), make_post(uid="a"),
                                  cfg, NOW)["dedupe_key"]
    assert db.get_deal(key)["link_as_posted"] == goto
    old = db.get_deal(key)
    del old["link_as_posted"]                    # a row stored before the field
    db.update_deal(key, payload=old)
    variant = make_extracted(item="WORKPRO Cordless Glue Gun", store="Walmart",
                             product_url="https://www.walmart.com/ip/Glue-Gun-Pink/456",
                             link_as_posted="https://goto.walmart.com/c/4/5/6")
    assert dedupe.upsert_extracted(db, variant, make_savings(), make_post(uid="b"),
                                   cfg, NOW)["dedupe_key"] == key
    assert not db.get_deal(key).get("link_as_posted")
    again = dataclasses.replace(ext, link_as_posted="https://goto.walmart.com/c/7/8/9")
    dedupe.upsert_extracted(db, again, make_savings(), make_post(uid="c"), cfg, NOW)
    assert db.get_deal(key)["link_as_posted"] == "https://goto.walmart.com/c/7/8/9"
    dedupe.upsert_extracted(db, ext, make_savings(), make_post(uid="d"), cfg, NOW)
    assert db.get_deal(key)["link_as_posted"] == "https://goto.walmart.com/c/7/8/9"
    db.close()


def test_store_link_shared_by_several_products_names_none(tmp_path) -> None:
    """Review finding: "home sale, shop it here" over a list of items the
    LLM all gives the one sale-page link. The photo inside that link is
    none of theirs, nor the post's image, and the row keeps the fact, so a
    later post's photo inside that link never lands either — only a photo
    named after the item. A product with its own link keeps its photo."""
    from dealradar import pipeline

    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    sale = "https://www.kohls.com/catalog/home-sale.jsp"
    lamp = "https://www.target.com/p/lamp/-/A-444"
    html = (f'<p><img src="{UP}lead.jpg"></p><p>Home sale!</p>'
            f'<p><a href="{sale}"><img src="{UP}home-sale-banner.jpg"></a> Shop the sale:</p>'
            '<ul><li>SONGMICS Shelf $39.99</li><li>Drying Rack $19.99</li></ul>'
            f'<p><a href="{lamp}"><img src="{UP}lamp.jpg"></a> Lamp $9</p>')
    post = make_post(uid="home-sale", url=POST_URL)
    post.photos = post_photos(html, POST_URL)
    # A known store's page without a product id is a listing: its banner is
    # never in a product link's keeping in the first place.
    assert sale not in post.photos.in_link
    pairs = [(post, make_extracted(item=item, store="Kohls", product_url=url,
                                   link_as_posted=url))
             for item, url in (("SONGMICS Shelf", sale), ("Drying Rack", sale),
                               ("Desk Lamp", lamp))]
    post.parsed = [(e.item, e.product_url) for _p, e in pairs]
    pipeline._mark_shared_links(pairs)
    assert [e.link_shared for _p, e in pairs] == [True, True, False]
    keys = [dedupe.upsert_extracted(db, e, make_savings(), post, cfg, NOW)["dedupe_key"]
            for _p, e in pairs]
    assert keys[0] == keys[1]   # one sale-page link is one row
    row = db.get_deal(keys[0])
    assert row["image_url"] == "" and row["link_shared"] is True
    assert db.get_deal(keys[2])["image_url"] == UP + "lamp.jpg"
    assert db.get_deal(keys[2])["link_shared"] is False
    # A later one-product post with a photo inside that link: still none...
    later = make_post(uid="shelf-only")
    later.photos = PostPhotos(in_link={sale: UP + "shelf.jpg"})
    later.image_url = UP + "shelf.jpg"
    shelf = make_extracted(item="SONGMICS Shelf", store="Kohls", product_url=sale)
    dedupe.upsert_extracted(db, shelf, make_savings(), later, cfg, NOW)
    assert db.get_deal(keys[0])["image_url"] == ""
    # ...nor a photo the post merely names after the item (names never
    # place a photo: in-link evidence only).
    named = make_post(uid="shelf-named")
    named.photos = PostPhotos(named={UP + "s.jpg": name_words("SONGMICS 5-Tier Shelf")})
    dedupe.upsert_extracted(db, shelf, make_savings(), named, cfg, NOW)
    assert db.get_deal(keys[0])["image_url"] == ""
    db.close()


def test_shared_link_counts_every_deal_the_parse_read() -> None:
    """Holders of a link are counted over the post's full parse: a second
    item on the link that failed evidence still makes it a shared (store)
    link. One short link as posted, resolved for one deal and not for the
    other, is one link; one product at two stores is one product."""
    from dealradar import pipeline

    sale, short = "https://www.kohls.com/catalog/sale.jsp", "https://howl.me/cSale1"
    post = make_post(uid="p")
    post.parsed = [("SONGMICS Shelf", sale), ("Drying Rack", sale)]
    survivor = make_extracted(item="SONGMICS Shelf", product_url=sale, link_as_posted=sale)
    pipeline._mark_shared_links([(post, survivor)])
    assert survivor.link_shared is True
    post = make_post(uid="t")
    timed = [(post, make_extracted(item=item, product_url=url, link_as_posted=short))
             for item, url in (("SONGMICS Shelf", sale), ("Drying Rack", short))]
    pipeline._mark_shared_links(timed)
    assert [e.link_shared for _p, e in timed] == [True, True]
    two_stores = make_post(uid="b")
    bluey = [(two_stores, make_extracted(item="Bluey Boo Book", store=s, product_url=sale))
             for s in ("Amazon", "Walmart")]
    pipeline._mark_shared_links(bluey)
    assert [e.link_shared for _p, e in bluey] == [False, False]


# ---- review round 5: a wrong photo is worse than none; when in doubt, none

NONE = dict.fromkeys((i for i, _l in TOYS), "")
SLUG = "crayola-markers-and-more-toy-deals"


@pytest.mark.parametrize("layout", LAYOUTS)
def test_featured_image_named_after_the_post_lands_on_no_card(tmp_path, layout) -> None:
    """Review finding (high): the featured image named the way WordPress
    names uploads after the post slug (crayola-markers-and-more-toy-
    deals.jpg) stayed a by-name candidate and landed on the lead product's
    card in every reviewed layout. On a roundup a featured photo (the
    first image, og:image, one named after the post) is no product's,
    neither in a link nor by name; named photos still go to each."""
    url = f"https://blog.example/2026/10/11/{SLUG}/"
    lead = _listicle(layout, GENERIC).replace(f"{UP}featured.jpg", f"{UP}{SLUG}.jpg")
    assert _ingest(tmp_path, lead, TOYS, url=url) == NONE
    # Not the first image, but named after the post: a featured collage too.
    second = lead.replace(f'<figure class="wp-block-image"><img src="{UP}{SLUG}.jpg">',
                          f'<p><img src="{UP}masthead.jpg"></p><figure><img src="{UP}{SLUG}-2.jpg">',
                          1)
    assert _ingest(tmp_path, second, TOYS, url=url) == NONE
    # Photos named after their products still go to each — but the lead
    # product's, whose words are the post's own name, is no different from
    # a collage named after the post: none (when in doubt, no photo).
    named = _listicle(layout, ("a", "b", "c"), ALTS).replace(f"{UP}featured.jpg",
                                                             f"{UP}{SLUG}.jpg")
    assert _ingest(tmp_path, named, TOYS, url=url) == NONE  # names never place a photo (in-link evidence only)


def test_lead_collage_with_the_title_as_alt_lands_on_no_card(tmp_path) -> None:
    """Review finding (high): a classic-editor lead <p><img><br>intro</p>
    whose alt an SEO plugin set to the post title named the markers."""
    classic = (f'<p><img src="{UP}collage.jpg" alt="Crayola Washable Markers + More Back to '
               'School Deals on Amazon"><br>Big toy deals today!</p>')
    deals = [("Crayola Washable Markers 40ct", TOYS[0][1])] + TOYS[1:]
    assert _ingest(tmp_path, _heading_listicle(GENERIC, lead=classic), deals) \
        == dict.fromkeys((i for i, _l in deals), "")


def test_brand_banner_lands_on_no_card(tmp_path) -> None:
    """Review finding (high): a Target category banner hot-wheels-sale.jpg
    ({hot, wheel}) and a loose melissa-and-doug-toys.jpg banner scored 100
    against their brand's deal: a two-word brand alone names no product."""
    deals = [("Hot Wheels 20-Car Gift Pack", "https://www.target.com/p/hw/-/A-7001"),
             ("LEGO Classic Bricks", "https://www.target.com/p/lego/-/A-7002"),
             ("Barbie Dreamhouse", "https://www.target.com/p/barbie/-/A-7003")]
    products = "".join(f'<p><img src="{UP}IMG_700{i}.jpg" alt=""></p>'
                       f'<p>{item} $19.99 <a href="{link}">Buy</a></p>'
                       for i, (item, link) in enumerate(deals, 1))
    html = (f'<p><img src="{UP}lead.jpg"></p><p>Toy deals at Target!</p>'
            f'<p><a href="https://www.target.com/c/hot-wheels/-/N-4y8b1">'
            f'<img src="{UP}hot-wheels-sale.jpg"></a> Hot Wheels toys are 30% off</p>' + products)
    assert _ingest(tmp_path, html, deals) == dict.fromkeys((i for i, _l in deals), "")
    deals[0] = ("Melissa & Doug Wooden Shape Sorting Puzzle", deals[0][1])
    html = (f'<p><img src="{UP}lead.jpg"></p><p>Toy deals at Target!</p>'
            f'<p><img src="{UP}melissa-and-doug-toys.jpg"></p>' + products)
    assert _ingest(tmp_path, html, deals) == dict.fromkeys((i for i, _l in deals), "")


def _post_photos_of(html: str, deals) -> dict[str, str]:
    """Each deal's photo from one post (dedupe._post_image), every deal of
    the parse known: the photo decision alone, before rows are merged."""
    from dealradar.fetch.rss import _first_image

    post = make_post(source="blog", uid="variants", url=POST_URL)
    post.image_url = _first_image(html)
    post.photos = post_photos(html, POST_URL)
    post.parsed = list(deals)
    return {item: dedupe._post_image(make_extracted(item=item, product_url=link,
                                                    link_as_posted=link), post)
            .removeprefix(UP) for item, link in deals}


def test_variant_photo_goes_to_its_own_variant_only() -> None:
    """Review finding (medium): 'Apple Watch Series 9 GPS' and 'Series 8'
    normalized alike (single digits dropped) and both cards showed the
    Series 9 photo; the 5-Pack card showed the 9-Pack photo. Numbers are
    kept: the Series 9 photo is Series 9's alone; a one-number difference
    (5-Pack vs 9-Pack) tells no photo apart, so neither gets it."""
    watches = [("Apple Watch Series 9 GPS", "https://www.amazon.com/dp/B0000WATC9"),
               ("Apple Watch Series 8 GPS", "https://www.amazon.com/dp/B0000WATC8")]
    html = (FEATURED + f'<figure><img src="{UP}s9.jpg" alt="Apple Watch Series 9 GPS"></figure>'
            f'<p>Series 9 $299 <a href="{watches[0][1]}">Buy</a></p>'
            f'<figure><img src="{UP}IMG_0042.jpg" alt=""></figure>'
            f'<p>Series 8 $199 <a href="{watches[1][1]}">Buy</a></p>')
    assert _post_photos_of(html, watches) == {watches[0][0]: "", watches[1][0]: ""}  # names never place a photo (in-link evidence only)
    packs = [("Hot Wheels 5-Pack", "https://www.amazon.com/dp/B0000HWPK5"),
             ("Hot Wheels 9-Pack", "https://www.amazon.com/dp/B0000HWPK9")]
    html = (FEATURED + f'<figure><img src="{UP}IMG_0050.jpg" alt=""></figure>'
            f'<p>5-Pack $5 <a href="{packs[0][1]}">Buy</a></p>'
            f'<figure><img src="{UP}hw9.jpg" alt="Hot Wheels 9-Pack"></figure>'
            f'<p>9-Pack $9 <a href="{packs[1][1]}">Buy</a></p>')
    assert _post_photos_of(html, packs) == {packs[0][0]: "", packs[1][0]: ""}


def test_short_link_resolving_to_a_store_page_names_no_photo(tmp_path) -> None:
    """Review finding (medium): the markers' amzn.to short link (the sale
    collage inside it) resolved to Amazon's Crayola store page, yet the
    link as posted still matched the collage. A link that resolves to a
    store, search or category page names no product: no photo inside it
    as posted, and no post image either; Elmer's keeps its own."""
    short = "https://amzn.to/3CrAyS"
    store = "https://www.amazon.com/stores/Crayola/page/1234-ABCD"
    glue = "https://www.amazon.com/dp/B0000GLUE1"
    html = (f'<p><img src="{UP}lead.jpg"></p><p>Back to school!</p>'
            f'<p><a href="{short}"><img src="{UP}crayola-sale-collage.jpg"></a></p>'
            '<p>Amazon has a big Crayola sale: Crayola Washable Markers 40ct $9.99</p>'
            f'<p><a href="{glue}"><img src="{UP}glue.jpg"></a> Elmer\'s Glue Sticks $4</p>')
    deals = [("Crayola Washable Markers 40ct", short, store), ("Elmer's Glue Sticks 30ct", glue)]
    assert _ingest(tmp_path, html, deals) == {deals[0][0]: "", deals[1][0]: "glue.jpg"}
    search = [(deals[0][0], short, "https://www.target.com/s?searchTerm=crayola"), deals[1]]
    assert _ingest(tmp_path, html, search) == {deals[0][0]: "", deals[1][0]: "glue.jpg"}
    one = (f'<p><img src="{UP}crayola-store.jpg"></p>'
           f'<p>Crayola sale on Amazon: markers $9.99 <a href="{short}">shop</a></p>')
    assert _ingest(tmp_path, one, deals[:1]) == {deals[0][0]: ""}
    # The same one-product post whose link resolves to a product lends it.
    item = [("Crayola Washable Markers 40ct", short, "https://www.amazon.com/dp/B0000000A1")]
    assert _ingest(tmp_path, one, item) == {item[0][0]: "crayola-store.jpg"}


def test_cloaked_same_host_links_are_product_links(tmp_path) -> None:
    """Review finding (medium): ThirstyAffiliates /recommends/ and Pretty
    Links /go/ links on the blog's own host were never counted, so a
    roundup read as one product and lent its collage. They are product
    links: each deal holding one gets the photo inside it, and a post with
    several is a roundup whose post image is no deal's."""
    url = "https://blog.example/2026/10/11/toy-deals/"
    links = [f"https://blog.example/recommends/{n}/" for n in ("markers", "lego")] \
        + ["https://blog.example/go/playdoh"]
    html = (f'<p><img src="{UP}toy-deals-collage.jpg"></p><p>Toy deals!</p>' + "".join(
        f'<p><a href="{link}"><img src="{UP}IMG_90{i}.jpg"></a></p>'
        f'<p><a href="{link}">{item}</a> $9.99</p>'
        for i, ((item, _l), link) in enumerate(zip(TOYS, links))))
    cloaked = [(item, link) for (item, _l), link in zip(TOYS, links)]
    assert _ingest(tmp_path, html, cloaked, url=url) \
        == {item: f"IMG_90{i}.jpg" for i, (item, _l) in enumerate(TOYS)}
    # One deal parsed, its link resolved past the cloak: no collage.
    assert _ingest(tmp_path, html, TOYS[:1], url=url) == {TOYS[0][0]: ""}
