"""New sources.yaml entries must join the run set without waiting for the
daily revalidation of the stored effective set — and no source may starve
because of its position in the list (live incident: the back half of
sources.yaml fetched every hour yet never had one post parsed)."""

from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

from dealradar import pipeline
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.errors import DeadlineExceeded
from dealradar.fetch.validate import save_effective_sources
from dealradar.models import FetchResult, RawPost, SourceConfig
from dealradar.util import iso, now_utc

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def make_source(name: str, **kw) -> SourceConfig:
    return SourceConfig(name=name, method="rss",
                        url=f"https://{name}.example/feed/", **kw)


class TestBlogImageBackfill:
    """Existing photo-less deals from an image-granted source get one polite
    article fetch for their og:image; a page with no image is flagged so it
    is never fetched again."""

    class _Fetcher:
        def __init__(self, pages):
            self.pages = pages
            self.calls = []

        def get(self, url, *, conditional=True, headers=None, deadline=None,
                **kw):
            self.calls.append(url)
            body = self.pages.get(url)
            return FetchResult(url=url, final_url=url,
                               status=200 if body is not None else 404,
                               body=body)

    @staticmethod
    def _seed(db, key, article_url, image="", canonical="", flags=(), item=""):
        item = item or key
        payload = {"dedupe_key": key, "store": "HSN", "item": item,
                   "flags": list(flags), "image_url": image, "conditions": []}
        deal_id = db.insert_deal(
            dedupe_key=key, store="HSN", item=item, normalized_item=item,
            canonical_url=canonical, payload=payload, final_price=9.99,
            pct_at_checkout=60.0, abs_savings=15.0, online_only="true",
            expires_at="", now=NOW)
        db.add_sighting(deal_id, source_name="hip2save",
                        source_url=article_url, content_hash="h" + key,
                        seen_at=NOW, final_price=9.99)

    ROUNDUP = "https://hip2save.com/deals/roundup/"
    SHELF = "https://www.target.com/p/songmics-shelf/-/A-111"
    RACK = "https://www.walmart.com/ip/drying-rack/222"
    GOTO = "https://goto.target.com/c/1/2/3?u=https%3A%2F%2Fwww.target.com%2Fp%2F"
    # Two products: the shelf's photo loose above its affiliate link (lazy-
    # loaded, named in its alt), the rack's inside its own link; the post's
    # collage as og:image and linked to the post itself.
    ROUNDUP_PAGE = (
        "<html><head><meta property='og:image' "
        "content='https://hip2save.com/i/collage.jpg'></head><body>"
        "<p><a href='https://hip2save.com/deals/roundup/'>"
        "<img src='https://hip2save.com/i/collage.jpg'></a></p>"
        "<p><img src='data:image/gif;base64,R0l' alt='SONGMICS 5-Tier Shelf' "
        "data-src='https://hip2save.com/i/shelf.jpg'></p>"
        "<p>SONGMICS Shelf $39.99 <a href='" + GOTO + "songmics-shelf%2F-%2FA-111'>"
        "Buy</a></p>"
        "<p><a href='https://www.walmart.com/ip/drying-rack/222?athbdg=L1'>"
        "<img src='https://hip2save.com/i/rack.jpg'></a></p>"
        "<p>Drying Rack $19.99 <a href='https://www.walmart.com/ip/drying-rack/222'>"
        "Buy</a></p></body></html>").encode()

    def test_roundup_article_never_supplies_a_photo(self, tmp_path):
        """Live wart: one collage on four cards. A roundup whose page shows
        no per-product photo (only its og:image collage) is fetched ONCE for
        both of its deals, which stay photo-less and flagged; a photo
        already on another product's card is never copied onto a second."""
        db = DB(tmp_path / "state.db")
        self._seed(db, "pants", self.ROUNDUP, canonical=self.SHELF)
        self._seed(db, "surge", self.ROUNDUP, canonical=self.RACK)
        self._seed(db, "solo", "https://hip2save.com/deals/solo/")
        self._seed(db, "owner", "https://hip2save.com/deals/owner/",
                   image="https://hip2save.com/i/shared.jpg")
        src = make_source("hip2save", allowed_html=True, image_permission=True,
                          crawl_permission="written grant on file")
        fetcher = self._Fetcher({
            self.ROUNDUP:
                b"<meta property='og:image' content='https://hip2save.com/i/collage.jpg'>",
            "https://hip2save.com/deals/solo/":
                b"<meta property='og:image' content='https://hip2save.com/i/shared.jpg'>",
        })
        pipeline._backfill_blog_images(db, fetcher, [src], deadline=None)
        assert fetcher.calls == [self.ROUNDUP, "https://hip2save.com/deals/solo/"]
        for key in ("pants", "surge"):
            d = db.get_deal(key)
            assert d["image_url"] == "" and "image_backfill_tried" in d["flags"]
        solo = db.get_deal("solo")
        assert solo["image_url"] == "" and "image_backfill_tried" in solo["flags"]
        assert db.get_deal("owner")["image_url"] == "https://hip2save.com/i/shared.jpg"
        db.close()

    def test_roundup_page_gives_each_product_its_own_photo(self, tmp_path):
        """Live: 400 photo-less cards came from roundups (three Target cards
        from one Southern Savers post showed the store name). One fetch of
        the page gives each deal its own photo — the rack's inside its link
        (the Walmart item id), the shelf's by the name in its alt — never the
        collage; a deal neither linked nor named gets nothing."""
        db = DB(tmp_path / "state.db")
        self._seed(db, "shelf", self.ROUNDUP, canonical=self.SHELF, item="SONGMICS Shelf")
        self._seed(db, "rack", self.ROUNDUP, canonical=self.RACK)
        self._seed(db, "nolink", self.ROUNDUP)
        self._seed(db, "amazon_search", self.ROUNDUP,
                   canonical="https://www.amazon.com/s?k=towels")
        src = make_source("hip2save", allowed_html=True, image_permission=True,
                          crawl_permission="written grant on file")
        fetcher = self._Fetcher({self.ROUNDUP: self.ROUNDUP_PAGE})
        assert pipeline._backfill_blog_images(db, fetcher, [src], deadline=None) == 1
        assert fetcher.calls == [self.ROUNDUP]
        assert db.get_deal("shelf")["image_url"] == "https://hip2save.com/i/shelf.jpg"
        assert db.get_deal("rack")["image_url"] == "https://hip2save.com/i/rack.jpg"
        for key in ("nolink", "amazon_search"):
            d = db.get_deal(key)
            assert d["image_url"] == "" and "image_backfill_tried" in d["flags"]
        # Idempotent: every deal is settled, a second pass fetches nothing.
        fetcher.calls.clear()
        assert pipeline._backfill_blog_images(db, fetcher, [src], deadline=None) == 0
        assert fetcher.calls == []
        db.close()

    def test_roundup_page_with_one_stored_deal_never_lends_its_collage(self, tmp_path):
        """The page shows two products with their own photos, but only one
        deal is stored for it (the other was in-store, failed evidence, or
        comes later) and its link matches neither: it gets nothing — not
        the og:image collage. A single-product page whose only other photos
        are the blog's own related-post thumbnails still lends its og:image."""
        db = DB(tmp_path / "state.db")
        self._seed(db, "towels", self.ROUNDUP,
                   canonical="https://www.kohls.com/product/prd-9/towels.jsp")
        solo = "https://hip2save.com/deals/solo/"
        self._seed(db, "solo", solo)
        related = "".join(
            f"<p><a href='https://hip2save.com/deals/other-{i}/'>"
            f"<img src='https://hip2save.com/i/thumb-{i}.jpg'></a></p><p>Other {i}</p>"
            for i in range(3))
        page = ("<meta property='og:image' content='https://hip2save.com/i/solo.jpg'>"
                "<p>One great deal today.</p>" + related).encode()
        src = make_source("hip2save", allowed_html=True, image_permission=True,
                          crawl_permission="written grant on file")
        fetcher = self._Fetcher({self.ROUNDUP: self.ROUNDUP_PAGE, solo: page})
        pipeline._backfill_blog_images(db, fetcher, [src], deadline=None)
        towels = db.get_deal("towels")
        assert towels["image_url"] == "" and "image_backfill_tried" in towels["flags"]
        assert db.get_deal("solo")["image_url"] == "https://hip2save.com/i/solo.jpg"
        db.close()

    def test_real_affiliate_links_get_their_own_photo(self, tmp_path):
        """Today's photo-less roundup deals keep the affiliate link exactly as
        posted (on.ltk.com, clicks.trx-hub.com, shoplowes.me, creator.anchor
        .store, goto.target.com, go.shopmy.us, Walmart /ip/, Amazon /dp/):
        one fetch of the article gives each its own photo."""
        from tests.test_rss import REAL_LINKS, UP, real_roundup_html

        db = DB(tmp_path / "state.db")
        article = "https://hip2save.com/deals/nike-sale/"
        for i, link in enumerate(REAL_LINKS):
            self._seed(db, f"deal{i}", article, canonical=link)
        src = make_source("hip2save", allowed_html=True, image_permission=True,
                          crawl_permission="written grant on file")
        fetcher = self._Fetcher({article: real_roundup_html(article).encode()})
        assert pipeline._backfill_blog_images(db, fetcher, [src], deadline=None) == 1
        assert [db.get_deal(f"deal{i}")["image_url"] for i in range(len(REAL_LINKS))] == [
            f"{UP}p{i}.jpg" for i in range(len(REAL_LINKS))]
        db.close()

    def test_roundup_photo_never_goes_to_a_different_product(self, tmp_path):
        """The photo named after the shelf is already another product's card
        photo: the shelf deal does not get it. The same product listed at a
        second store may share it."""
        db = DB(tmp_path / "state.db")
        self._seed(db, "shelf", self.ROUNDUP, canonical=self.SHELF, item="SONGMICS Shelf")
        self._seed(db, "rack", self.ROUNDUP, canonical=self.RACK)
        self._seed(db, "rack_other_store", "https://hip2save.com/deals/b/",
                   image="https://hip2save.com/i/rack.jpg", item="rack")
        self._seed(db, "lamp", "https://hip2save.com/deals/c/",
                   image="https://hip2save.com/i/shelf.jpg")
        # Within one pass too: two articles about different products whose
        # og:image is one picture; the second never gets what the first took.
        same = b"<meta property='og:image' content='https://hip2save.com/i/same.jpg'>"
        self._seed(db, "mug", "https://hip2save.com/deals/d/")
        self._seed(db, "kettle", "https://hip2save.com/deals/e/")
        src = make_source("hip2save", allowed_html=True, image_permission=True)
        pipeline._backfill_blog_images(
            db, self._Fetcher({self.ROUNDUP: self.ROUNDUP_PAGE,
                               "https://hip2save.com/deals/d/": same,
                               "https://hip2save.com/deals/e/": same}), [src], deadline=None)
        assert db.get_deal("shelf")["image_url"] == ""
        assert db.get_deal("rack")["image_url"] == "https://hip2save.com/i/rack.jpg"
        assert [db.get_deal(k)["image_url"] for k in ("mug", "kettle")] == [
            "https://hip2save.com/i/same.jpg", ""]
        db.close()

    def test_one_time_lift_retries_flagged_roundup_deals(self, tmp_path):
        """The old rules flagged roundup deals 'tried' (first without a fetch,
        then under layout pairing). Once (meta-keyed anew, so a db where the
        layout-rule lift already ran gets it too), photo-less roundup deals
        on the page lose that flag and get the per-product pass; dead-and-
        gone deals, deals of a single-product article and later flags are
        left alone."""
        db = DB(tmp_path / "state.db")
        db.meta_set("image_repair_2026_10_11", "done")  # the earlier repair ran
        db.meta_set("roundup_images_2026_10_11", "done")  # the layout-rule lift ran
        tried = ("image_backfill_tried",)
        self._seed(db, "shelf", self.ROUNDUP, canonical=self.SHELF, flags=tried,
                   item="SONGMICS Shelf")
        self._seed(db, "rack", self.ROUNDUP, canonical=self.RACK, flags=tried)
        self._seed(db, "gone", self.ROUNDUP, canonical=self.SHELF + "9", flags=tried)
        db.update_deal("gone", status="dead")  # no died_at: off the page
        single = "https://hip2save.com/deals/single/"
        self._seed(db, "single", single, flags=tried)
        src = make_source("hip2save", allowed_html=True, image_permission=True,
                          crawl_permission="written grant on file")
        fetcher = self._Fetcher({self.ROUNDUP: self.ROUNDUP_PAGE, single:
                                 b"<meta property='og:image' content='https://hip2save.com/i/s.jpg'>"})
        pipeline._backfill_blog_images(db, fetcher, [src], deadline=None)
        assert fetcher.calls == [self.ROUNDUP]
        assert db.get_deal("shelf")["image_url"] == "https://hip2save.com/i/shelf.jpg"
        assert db.get_deal("rack")["image_url"] == "https://hip2save.com/i/rack.jpg"
        assert db.get_deal("gone")["flags"] == ["image_backfill_tried"]
        assert db.get_deal("single")["flags"] == ["image_backfill_tried"]
        assert db.meta_get("roundup_photos_by_name_2026_10_11")
        # Runs once: a roundup deal flagged after the lift stays flagged.
        self._seed(db, "later", self.ROUNDUP, canonical=self.SHELF + "8", flags=tried)
        fetcher.calls.clear()
        assert pipeline._backfill_blog_images(db, fetcher, [src], deadline=None) == 0
        assert fetcher.calls == []
        assert db.get_deal("later")["flags"] == ["image_backfill_tried"]
        db.close()

    def test_one_product_at_two_stores_in_a_roundup_keeps_its_photo(self, tmp_path):
        """A roundup listing the Bluey book at Amazon and Walmart next to a
        puzzle: the book's photo on both of its deals is one product's
        photo, not a collage, and stays."""
        db = DB(tmp_path / "state.db")
        pic = "https://hip2save.com/i/bluey.jpg"
        self._seed(db, "bluey_amazon", self.ROUNDUP, image=pic, item="bluey boo book")
        self._seed(db, "bluey_walmart", self.ROUNDUP, image=pic, item="bluey boo book")
        self._seed(db, "puzzle", self.ROUNDUP, image="https://hip2save.com/i/puzzle.jpg")
        src = make_source("hip2save", allowed_html=True, image_permission=True)
        fetcher = self._Fetcher({})
        pipeline._backfill_blog_images(db, fetcher, [src], deadline=None)
        assert db.get_deal("bluey_amazon")["image_url"] == pic
        assert db.get_deal("bluey_walmart")["image_url"] == pic
        assert db.get_deal("puzzle")["image_url"] == "https://hip2save.com/i/puzzle.jpg"
        assert fetcher.calls == []
        db.close()

    def test_one_product_at_two_stores_keeps_its_photo(self, tmp_path):
        """Live regression: a deal-count rule treated 'Bluey book at Amazon
        or Walmart' as a roundup and dropped 173 photos. Two deals of the
        SAME product from one article are not a collage."""
        db = DB(tmp_path / "state.db")
        pic = "https://hip2save.com/i/bluey.jpg"
        for key, store in (("bluey_amazon", "Amazon"), ("bluey_walmart", "Walmart")):
            payload = {"dedupe_key": key, "store": store, "item": "Bluey Boo Book",
                       "flags": [], "image_url": pic, "conditions": []}
            deal_id = db.insert_deal(
                dedupe_key=key, store=store, item="Bluey Boo Book",
                normalized_item="bluey boo book", canonical_url="", payload=payload,
                final_price=5.0, pct_at_checkout=50.0, abs_savings=5.0,
                online_only="true", expires_at="", now=NOW)
            db.add_sighting(deal_id, source_name="hip2save",
                            source_url="https://hip2save.com/deals/bluey/",
                            content_hash="h" + key, seen_at=NOW, final_price=5.0)
        src = make_source("hip2save", allowed_html=True, image_permission=True,
                          crawl_permission="written grant on file")
        pipeline._backfill_blog_images(db, self._Fetcher({}), [src], deadline=None)
        assert db.get_deal("bluey_amazon")["image_url"] == pic
        assert db.get_deal("bluey_walmart")["image_url"] == pic
        db.close()

    def test_one_time_repair_refetches_wrongly_emptied_photos(self, tmp_path):
        db = DB(tmp_path / "state.db")
        # Emptied by the old rule: flagged tried, no photo, single-product article.
        payload = {"dedupe_key": "lego", "store": "Amazon", "item": "LEGO Advent",
                   "flags": ["image_backfill_tried"], "image_url": "", "conditions": []}
        deal_id = db.insert_deal(dedupe_key="lego", store="Amazon", item="LEGO Advent",
                                 normalized_item="advent lego", canonical_url="",
                                 payload=payload, final_price=20.0, pct_at_checkout=50.0,
                                 abs_savings=20.0, online_only="true", expires_at="", now=NOW)
        db.add_sighting(deal_id, source_name="hip2save",
                        source_url="https://hip2save.com/deals/lego/",
                        content_hash="hlego", seen_at=NOW, final_price=20.0)
        src = make_source("hip2save", allowed_html=True, image_permission=True,
                          crawl_permission="written grant on file")
        fetcher = self._Fetcher({"https://hip2save.com/deals/lego/":
                                 b"<meta property='og:image' content='https://hip2save.com/i/lego.jpg'>"})
        pipeline._backfill_blog_images(db, fetcher, [src], deadline=None)
        assert db.get_deal("lego")["image_url"] == "https://hip2save.com/i/lego.jpg"
        assert fetcher.calls == ["https://hip2save.com/deals/lego/"]
        # The repair runs once: a later photo-less, tried deal is left alone.
        payload2 = dict(payload, dedupe_key="later", item="Later", image_url="")
        did = db.insert_deal(dedupe_key="later", store="Amazon", item="Later",
                             normalized_item="later", canonical_url="", payload=payload2,
                             final_price=1.0, pct_at_checkout=50.0, abs_savings=5.0,
                             online_only="true", expires_at="", now=NOW)
        db.add_sighting(did, source_name="hip2save", source_url="https://hip2save.com/deals/later/",
                        content_hash="hlater", seen_at=NOW, final_price=1.0)
        fetcher.calls.clear()
        pipeline._backfill_blog_images(db, fetcher, [src], deadline=None)
        assert fetcher.calls == []
        db.close()

    def test_collage_already_attached_is_cleared(self, tmp_path):
        """Three puzzles from one post carried one puzzle's photo: an image
        shared by deals of a roundup is dropped from all of them, once."""
        db = DB(tmp_path / "state.db")
        for key in ("puzzle_a", "puzzle_b", "puzzle_c"):
            self._seed(db, key, "https://hip2save.com/deals/puzzles/",
                       image="https://hip2save.com/i/tunnel.jpg")
        self._seed(db, "variant_x", "https://hip2save.com/deals/x/",
                   image="https://cdn.example/same.jpg")
        self._seed(db, "variant_y", "https://hip2save.com/deals/y/",
                   image="https://cdn.example/same.jpg")
        src = make_source("hip2save", allowed_html=True, image_permission=True,
                          crawl_permission="written grant on file")
        fetcher = self._Fetcher({})
        pipeline._backfill_blog_images(db, fetcher, [src], deadline=None)
        for key in ("puzzle_a", "puzzle_b", "puzzle_c"):
            d = db.get_deal(key)
            assert d["image_url"] == "" and "image_backfill_tried" in d["flags"]
        # Two single-deal articles sharing one retailer photo are left alone.
        assert db.get_deal("variant_x")["image_url"] == "https://cdn.example/same.jpg"
        assert fetcher.calls == []
        db.close()

    def test_fills_flags_and_never_refetches(self, tmp_path):
        db = DB(tmp_path / "state.db")
        self._seed(db, "with_pic", "https://hip2save.com/deals/a/")
        self._seed(db, "no_pic", "https://hip2save.com/deals/b/")
        self._seed(db, "already", "https://hip2save.com/deals/c/",
                   image="https://x.example/img.jpg")
        src = make_source("hip2save", allowed_html=True, image_permission=True,
                          crawl_permission="written grant on file")
        fetcher = self._Fetcher({
            "https://hip2save.com/deals/a/":
                b"<meta property='og:image' content='https://hip2save.com/i/a.jpg'>",
            "https://hip2save.com/deals/b/":
                b"<html><body>no pictures here</body></html>",
        })
        n = pipeline._backfill_blog_images(db, fetcher, [src], deadline=None)
        assert n == 2
        assert db.get_deal("with_pic")["image_url"] == "https://hip2save.com/i/a.jpg"
        assert "image_backfill_tried" in db.get_deal("no_pic")["flags"]
        assert sorted(fetcher.calls) == ["https://hip2save.com/deals/a/",
                                         "https://hip2save.com/deals/b/"]
        # Nothing left to try: a second pass makes no fetch at all.
        fetcher.calls.clear()
        assert pipeline._backfill_blog_images(db, fetcher, [src], None) == 0
        assert fetcher.calls == []
        # No granted source in the run set -> no fetches, ever.
        plain = make_source("hip2save", allowed_html=True)
        assert pipeline._backfill_blog_images(db, fetcher, [plain], None) == 0
        db.close()

    def test_link_shared_by_several_products_names_none(self, tmp_path):
        """Review finding: two deals of one article hold the store's sale
        page ("shop the sale here"), whose link wraps the sale collage.
        Neither gets it nor the og:image, in any pass, and neither asks the
        retailer's sale page for its share image; the shelf still gets the
        photo the page names after it. A row marked at ingest (deals merged
        on that link) gets one look at its article for a named photo. A
        deal with its own link on the page gets the photo inside it."""
        db = DB(tmp_path / "state.db")
        article = "https://hip2save.com/deals/home-sale/"
        sale = "https://www.kohls.com/catalog/home-sale.jsp"
        lamp = "https://www.target.com/p/desk-lamp/-/A-444"
        page = ("<meta property='og:image' content='https://hip2save.com/i/lead.jpg'>"
                "<p><img src='https://hip2save.com/i/lead.jpg'></p><p>Kohl's home sale!</p>"
                f"<p><a href='{sale}'><img src='https://hip2save.com/i/sale-collage.jpg'></a>"
                " Shop the sale:</p>"
                "<ul><li>SONGMICS Shelf $39.99</li><li>Drying Rack $19.99</li></ul>"
                "<figure><img src='https://hip2save.com/i/songmics.jpg' "
                "alt='SONGMICS 5-Tier Shelf'></figure>"
                f"<p><a href='{lamp}'><img src='https://hip2save.com/i/lamp.jpg'></a> Lamp $9</p>"
                ).encode()
        self._seed(db, "shelf", article, canonical=sale, item="SONGMICS Shelf")
        self._seed(db, "rack", article, canonical=sale + "?ref=h2s",  # same page
                   item="Drying Rack")
        self._seed(db, "lamp", article, canonical=lamp)
        marked = "https://hip2save.com/deals/rollbacks/"
        self._seed(db, "merged", marked, canonical="https://www.walmart.com/ip/x/123")
        db.update_deal("merged", payload=dict(db.get_deal("merged"), link_shared=True))
        src = make_source("hip2save", allowed_html=True, image_permission=True,
                          crawl_permission="written grant on file")
        # The marked row's article is a one-product page: still no og:image.
        fetcher = self._Fetcher({article: page, marked:
                                 b"<meta property='og:image' content='https://hip2save.com/i/rb.jpg'>"})
        pipeline._backfill_blog_images(db, fetcher, [src], deadline=None)
        assert fetcher.calls == [article, marked]
        assert db.get_deal("shelf")["image_url"] == "https://hip2save.com/i/songmics.jpg"
        for key in ("rack", "merged"):
            d = db.get_deal(key)
            assert d["image_url"] == "" and "image_backfill_tried" in d["flags"]
        for key in ("shelf", "rack", "merged"):
            assert db.get_deal(key)["link_shared"] is True
        assert db.get_deal("lamp")["image_url"] == "https://hip2save.com/i/lamp.jpg"
        fetcher.calls.clear()
        pipeline._backfill_blog_images(db, fetcher, [src], deadline=None)
        pipeline._backfill_product_images(db, fetcher, None)
        assert fetcher.calls == []
        assert [db.get_deal(k)["image_url"] for k in ("rack", "merged")] == [""] * 2
        db.close()

    def test_reviewed_listicle_page_gives_the_right_photo_or_none(self, tmp_path):
        """Review finding (high), backfill path: a Gutenberg listicle (photo
        figure / name heading / price-and-link paragraph) served as the
        article, og:image a collage, three deals seeded with its dp links.
        Layout pairing gave the markers deal the LEGO photo. One fetch: none
        while the photos name no product, each its own once they do."""
        from tests.test_dedupe import ALTS, GENERIC, TOYS, UP, _listicle

        og = "<meta property='og:image' content='https://hip2save.com/i/collage.jpg'>"
        src = make_source("hip2save", allowed_html=True, image_permission=True,
                          crawl_permission="written grant on file")
        for i, (names, alts, want) in enumerate((
                (GENERIC, ("", "", ""), ["", "", ""]),
                (("a", "b", "c"), ALTS, [UP + "a.jpg", UP + "b.jpg", UP + "c.jpg"]))):
            db = DB(tmp_path / f"state{i}.db")
            for key, (item, link) in zip("xyz", TOYS):
                self._seed(db, key, self.ROUNDUP, canonical=link, item=item)
            fetcher = self._Fetcher({self.ROUNDUP: (
                og + _listicle("figure/h3/p-link", names, alts)).encode()})
            assert pipeline._backfill_blog_images(db, fetcher, [src], deadline=None) == 1
            assert [db.get_deal(k)["image_url"] for k in "xyz"] == want
            db.close()

    def test_one_stored_deal_of_a_page_of_three_gets_no_collage(self, tmp_path):
        """Review finding (medium): the page shows three products, each with
        its own photo and dp link, opens with 'Shop it here' and closes with
        'More toy deals here'; its one stored deal (a kettle, not on the
        page) is no single product's: never the og:image collage."""
        db = DB(tmp_path / "state.db")
        self._seed(db, "kettle", self.ROUNDUP, canonical="https://www.amazon.com/dp/B0000000K9",
                   item="Steel Kettle")
        page = ("<meta property='og:image' content='https://hip2save.com/i/collage.jpg'>"
                "<p><a href='https://www.walmart.com/shop/deals/toys'>Shop it here</a></p>"
                + "".join(f"<figure><img src='https://hip2save.com/i/{n}.jpg'></figure>"
                          f"<p>{n} $9 <a href='https://www.amazon.com/dp/B00000000{i}'>Buy</a></p>"
                          for i, n in enumerate(("markers", "lego", "playdoh")))
                + "<p>More toy deals <a href='https://www.target.com/c/toys/-/N-5xtb0'>here</a>"
                "</p>").encode()
        src = make_source("hip2save", allowed_html=True, image_permission=True,
                          crawl_permission="written grant on file")
        fetcher = self._Fetcher({self.ROUNDUP: page})
        assert pipeline._backfill_blog_images(db, fetcher, [src], deadline=None) == 1
        kettle = db.get_deal("kettle")
        assert kettle["image_url"] == "" and "image_backfill_tried" in kettle["flags"]
        db.close()

    def test_per_run_cap_leaves_deals_unflagged(self, tmp_path, monkeypatch):
        """Out of fetches, a deal is left as it was (never flagged 'tried'
        without a fetch) for the next pass; a deal of an article already
        fetched this pass is still settled."""
        db = DB(tmp_path / "state.db")
        solo = "https://hip2save.com/deals/solo/"
        self._seed(db, "shelf", self.ROUNDUP, canonical=self.SHELF)
        self._seed(db, "solo", solo)
        self._seed(db, "rack", self.ROUNDUP, canonical=self.RACK)
        src = make_source("hip2save", allowed_html=True, image_permission=True,
                          crawl_permission="written grant on file")
        monkeypatch.setattr(pipeline, "_BLOG_IMAGE_BACKFILL_PER_RUN", 1)
        fetcher = self._Fetcher({self.ROUNDUP: self.ROUNDUP_PAGE, solo:
                                 b"<meta property='og:image' content='https://hip2save.com/i/solo.jpg'>"})
        assert pipeline._backfill_blog_images(db, fetcher, [src], deadline=None) == 1
        assert fetcher.calls == [self.ROUNDUP]
        assert db.get_deal("rack")["image_url"] == "https://hip2save.com/i/rack.jpg"
        d = db.get_deal("solo")
        assert d["image_url"] == "" and "image_backfill_tried" not in d["flags"]
        fetcher.calls.clear()
        assert pipeline._backfill_blog_images(db, fetcher, [src], deadline=None) == 1
        assert fetcher.calls == [solo]
        assert db.get_deal("solo")["image_url"] == "https://hip2save.com/i/solo.jpg"
        db.close()

    def test_deadline_leaves_deals_unflagged(self, tmp_path):
        """Out of time before a fetch, or mid-fetch (DeadlineExceeded): the
        pass ends and no deal is flagged 'tried', so the next pass tries."""
        class Late(self._Fetcher):
            def get(self, url, **kw):
                self.calls.append(url)
                raise DeadlineExceeded("run deadline")

        db = DB(tmp_path / "state.db")
        self._seed(db, "shelf", self.ROUNDUP, canonical=self.SHELF)
        self._seed(db, "solo", "https://hip2save.com/deals/solo/")
        src = make_source("hip2save", allowed_html=True, image_permission=True,
                          crawl_permission="written grant on file")
        fetcher = Late({})
        assert pipeline._backfill_blog_images(
            db, fetcher, [src], deadline=time.monotonic() - 1) == 0
        assert fetcher.calls == []
        assert pipeline._backfill_blog_images(db, fetcher, [src], deadline=None) == 0
        assert fetcher.calls == [self.ROUNDUP]
        for key in ("shelf", "solo"):
            d = db.get_deal(key)
            assert d["image_url"] == "" and "image_backfill_tried" not in d["flags"]
        db.close()


def test_resolving_the_product_url_keeps_the_link_as_posted(monkeypatch):
    """A roundup's photos are keyed by its links as posted; resolving a
    deal's affiliate link to the retailer URL keeps the posted one (in
    memory) on every path: resolved, out of time, resolver error."""
    from dealradar import http_client
    from dealradar.models import ExtractedDeal

    posted = ("https://goto.target.com/c/1/2/3?u=https%3A%2F%2Fwww.target.com"
              "%2Fp%2Fx%2F-%2FA-1&utm_source=blog")
    resolved = "https://www.target.com/p/x/-/A-1"

    def deal():
        return ExtractedDeal(item="Shelf", store="Target", source_url="s",
                             source_name="n", product_url=posted)

    monkeypatch.setattr(http_client, "resolve_redirects",
                        lambda fetcher, url, hops, deadline=None: resolved)
    cfg = AppConfig()
    ok, late = deal(), deal()
    pipeline._resolve_product_urls([(None, ok)], None, cfg, time.monotonic() + 600)
    pipeline._resolve_product_urls([(None, late)], None, cfg, time.monotonic())
    assert (ok.product_url, ok.link_as_posted) == (resolved, posted)
    assert late.link_as_posted == posted and "utm_source" not in late.product_url

    def boom(*a, **kw):
        raise RuntimeError("redirect loop")

    monkeypatch.setattr(http_client, "resolve_redirects", boom)
    failed = deal()
    pipeline._resolve_product_urls([(None, failed)], None, cfg, time.monotonic() + 600)
    assert failed.link_as_posted == posted and "utm_source" not in failed.product_url


def make_post(source: str, i: int) -> RawPost:
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    return RawPost(source_name=source, uid=f"{source}-{i}",
                   url=f"https://{source}.example/{i}", title=f"{source} {i}",
                   text=f"deal {source} {i} $9.99", published_at=None,
                   fetched_at=now)


class TestFairScheduling:
    def test_interleave_round_robins_across_sources(self):
        posts = ([make_post("a", i) for i in range(3)]
                 + [make_post("b", i) for i in range(2)]
                 + [make_post("c", i) for i in range(1)])
        out = pipeline._interleave_by_source(posts)
        assert [p.uid for p in out] == [
            "a-0", "b-0", "c-0", "a-1", "b-1", "a-2"]

    def test_interleave_keeps_within_source_order_and_loses_nothing(self):
        posts = [make_post("x", i) for i in range(5)] \
            + [make_post("y", i) for i in range(4)]
        out = pipeline._interleave_by_source(posts)
        assert len(out) == len(posts)
        assert [p.uid for p in out if p.source_name == "x"] == \
            [f"x-{i}" for i in range(5)]
        assert pipeline._interleave_by_source([]) == []

    def test_rotation_is_deterministic_and_complete(self):
        sources = [make_source(f"s{i}") for i in range(7)]
        a = pipeline._rotate_for_run(sources, "2026-10-10T19")
        b = pipeline._rotate_for_run(sources, "2026-10-10T19")
        assert [s.name for s in a] == [s.name for s in b]  # idempotent re-run
        assert sorted(s.name for s in a) == sorted(s.name for s in sources)
        # Across the run keys of a day, the head position actually moves.
        heads = {pipeline._rotate_for_run(sources, f"2026-10-10T{h:02d}")[0].name
                 for h in range(24)}
        assert len(heads) > 1
        assert pipeline._rotate_for_run([], "k") == []

    def test_hourly_puts_slow_sources_before_fast_lane_feeds(self):
        """The hourly must fetch the slow blogs before the big fast-lane
        feeds, or the feeds eat the budget and the blogs never parse."""
        srcs = [make_source("slick", fast_lane=True),
                make_source("blog_a"), make_source("keepa", fast_lane=True),
                make_source("blog_b"), make_source("blog_c")]
        order = pipeline._order_for_run(srcs, "2026-10-10T20", fast_lane=False)
        names = [s.name for s in order]
        last_slow = max(i for i, s in enumerate(order) if not s.fast_lane)
        first_fast = min(i for i, s in enumerate(order) if s.fast_lane)
        assert last_slow < first_fast          # every slow source precedes fast
        assert sorted(names) == sorted(s.name for s in srcs)  # nothing dropped

    def test_fast_run_order_is_a_plain_rotation(self):
        srcs = [make_source(f"s{i}", fast_lane=(i % 2 == 0)) for i in range(5)]
        order = pipeline._order_for_run(srcs, "k", fast_lane=True)
        assert sorted(s.name for s in order) == sorted(s.name for s in srcs)

    def test_fetch_stops_inside_the_parse_reserve(self):
        """_fetch_all must leave _RESERVE_FOR_PARSE_S on the clock: with the
        deadline already inside the reserve, no source is attempted."""
        deadline = time.monotonic() + pipeline._RESERVE_FOR_PARSE_S - 5.0
        posts, errors = pipeline._fetch_all(
            [make_source("never_reached")], fetcher=None, db=None,
            cfg=load_config(), now=now_utc(), deadline=deadline,
            fast_lane=False)
        assert posts == [] and errors == {}

    def test_parse_reserve_exceeds_tail_reserve(self):
        assert pipeline._RESERVE_FOR_PARSE_S > pipeline._RESERVE_FOR_TAIL_S


def test_new_configured_sources_merge_into_stored_set(tmp_path, monkeypatch):
    db = DB(tmp_path / "state.db")
    cfg = load_config()
    now = now_utc()
    stored = [make_source("a"), make_source("b")]
    save_effective_sources(db, stored, now)

    primary = [make_source("a"), make_source("b"),
               make_source("brand_new"),
               make_source("disabled_new", enabled=False)]
    monkeypatch.setattr(pipeline, "load_sources", lambda: (primary, []))

    result = pipeline._load_effective_sources(db, cfg, fetcher=None, now=now)
    names = [s.name for s in result]
    assert names == ["a", "b", "brand_new"]  # stored set + the new enabled one

    # A stale stored set still revalidates (returns None -> validation path),
    # so the merge only applies while the stored set is fresh.
    later = now + timedelta(hours=25)
    result2 = pipeline._load_effective_sources(
        db, cfg, fetcher=None, now=later, validate_if_missing=False)
    assert [s.name for s in result2] == ["a", "b", "brand_new"]
    db.close()


def test_failing_sources_take_config_edits_immediately(tmp_path, monkeypatch):
    """A healthy stored source keeps its (possibly autodiscovered) stored
    entry; a FAILING one swaps to the current config entry, so a corrected
    URL or new crawl_permission heals it next run, not next revalidation.
    A name disabled in config drops out of the run set at once."""
    db = DB(tmp_path / "state.db")
    cfg = load_config()
    now = now_utc()
    stored = [make_source("healthy"), make_source("broken"),
              make_source("nowdisabled")]
    save_effective_sources(db, stored, now)

    db.record_source_result("healthy", ok=True, error="", items=5, now=now)
    db.record_source_result("broken", ok=False, error="http_404", items=0,
                            now=now)

    fixed = SourceConfig(name="broken", method="rss",
                         url="https://broken.example/real-feed/",
                         crawl_permission="written permission on file")
    healthy_cfg = SourceConfig(name="healthy", method="rss",
                               url="https://healthy.example/other-url/")
    primary = [healthy_cfg, fixed,
               make_source("nowdisabled", enabled=False)]
    monkeypatch.setattr(pipeline, "load_sources", lambda: (primary, []))

    result = pipeline._load_effective_sources(db, cfg, fetcher=None, now=now)
    by_name = {s.name: s for s in result}
    assert sorted(by_name) == ["broken", "healthy"]  # disabled one dropped
    # Healthy keeps the stored entry (autodiscovered URLs survive).
    assert by_name["healthy"].url == "https://healthy.example/feed/"
    # ...but takes the config's current operator grants and lane at once
    # (live incident: Hip2Save's image grant was stuck behind the daily
    # revalidation because the healthy stored entry won wholesale).
    assert by_name["healthy"].image_permission is False
    granted_cfg = SourceConfig(name="healthy", method="rss",
                               url="https://healthy.example/other-url/",
                               image_permission=True, fast_lane=True,
                               crawl_permission="written grant on file")
    monkeypatch.setattr(pipeline, "load_sources",
                        lambda: ([granted_cfg, fixed], []))
    again = {s.name: s for s in
             pipeline._load_effective_sources(db, cfg, fetcher=None, now=now)}
    assert again["healthy"].url == "https://healthy.example/feed/"  # stored URL
    assert again["healthy"].image_permission is True                # new grant
    assert again["healthy"].fast_lane is True
    assert again["healthy"].crawl_permission == "written grant on file"
    # Failing takes the config fix, permission note included.
    assert by_name["broken"].url == "https://broken.example/real-feed/"
    assert by_name["broken"].crawl_permission
    db.close()


class TestProductImageBackfill:
    """Photo-less deals that link straight to a well-known retailer get ONE
    polite fetch of that product page for its og:image / twitter:image —
    the picture the retailer publishes for link previews. Amazon (Keepa's
    job), trackers, blogs and unknown hosts are never fetched; a robots
    block or HTTP error flags the deal tried and moves on."""

    DICKS = "https://www.dickssportinggoods.com/p/dsg-ultimate-backpack-5-0"
    WALMART = "https://www.walmart.com/ip/Silicone-Spatula/123"
    TARGET = "https://www.target.com/p/thing/-/A-1"
    BESTBUY = "https://www.bestbuy.com/site/x/6535208.p"
    NIKE = "https://www.nike.com/t/air-force-1-07-mens-shoes/CW2288-111"

    class _Fetcher:
        """Canned product pages: a bytes body is a 200, an int is that HTTP
        status, a URL in ``blocked`` is a robots.txt disallow (the real
        fetcher makes no request for those), anything else a 404."""

        def __init__(self, pages, blocked=()):
            self.pages = pages
            self.blocked = set(blocked)
            self.calls = []

        def get(self, url, *, conditional=True, headers=None, deadline=None,
                **kw):
            self.calls.append(url)
            assert conditional and not kw  # conditional GET, no robots override
            if url in self.blocked:
                return FetchResult(url=url, final_url=url, status=0, body=None,
                                   error="robots_disallowed",
                                   blocked_by_robots=True)
            body = self.pages.get(url)
            if isinstance(body, int):
                return FetchResult(url=url, final_url=url, status=body, body=b"")
            return FetchResult(url=url, final_url=url,
                               status=200 if body is not None else 404,
                               body=body)

    @staticmethod
    def _seed(db, key, link, image="", flags=(), died_at=""):
        payload = {"dedupe_key": key, "store": "Dick's", "item": key,
                   "flags": list(flags), "image_url": image,
                   "conditions": [], "died_at": died_at}
        db.insert_deal(
            dedupe_key=key, store="Dick's", item=key, normalized_item=key,
            canonical_url=link, payload=payload, final_price=9.99,
            pct_at_checkout=60.0, abs_savings=15.0, online_only="true",
            expires_at="", now=NOW)
        if died_at:
            db.update_deal(key, status="dead")

    def test_retailer_page_yields_its_share_image(self, tmp_path):
        db = DB(tmp_path / "state.db")
        self._seed(db, "dicks", self.DICKS)
        self._seed(db, "walmart", self.WALMART)
        self._seed(db, "target", self.TARGET)
        self._seed(db, "bestbuy", self.BESTBUY)
        self._seed(db, "nike", self.NIKE)
        fetcher = self._Fetcher({
            # Dick's-style page: og:image with an entity-escaped query; the
            # price on the page is never read.
            self.DICKS: (b"<html><head><title>Now $4.99!</title>"
                         b"<meta property=\"og:image\" content=\"https://dks."
                         b"scene7.com/is/image/GolfGalaxy/X?qlt=70&amp;wid=600\">"
                         b"</head></html>"),
            # twitter:image only, content attribute first, protocol-relative.
            self.WALMART: (b"<meta content='//i5.walmartimages.com/seo/"
                           b"Silicone-Spatula_1.jpeg' name='twitter:image'>"),
            # Logo and placeholder declared first; the product shot wins.
            self.TARGET: (
                b"<meta property='og:image' content='https://t.com/logo.svg'>"
                b"<meta property='og:image' content='https://t.com/placeholder.png'>"
                b"<meta name='twitter:image' content='https://t.scene7.com/G1'>"),
            # Nothing but the store's logo: tried, no picture.
            self.BESTBUY: (b"<meta property='og:image' "
                           b"content='https://bby.com/logo.png'>"),
            # A CDN transform named t_default is not a default image.
            self.NIKE: (b"<meta property='og:image' content='https://static."
                        b"nike.com/a/images/t_default/abc/air-force-1.png'>"),
        })
        n = pipeline._backfill_product_images(db, fetcher, deadline=None)
        assert n == 4
        assert db.get_deal("dicks")["image_url"] == \
            "https://dks.scene7.com/is/image/GolfGalaxy/X?qlt=70&wid=600"
        assert db.get_deal("dicks")["final_price"] == 9.99
        assert db.get_deal("walmart")["image_url"] == \
            "https://i5.walmartimages.com/seo/Silicone-Spatula_1.jpeg"
        assert db.get_deal("target")["image_url"] == "https://t.scene7.com/G1"
        assert db.get_deal("bestbuy")["image_url"] == ""
        assert db.get_deal("nike")["image_url"] == \
            "https://static.nike.com/a/images/t_default/abc/air-force-1.png"
        for key in ("dicks", "walmart", "target", "bestbuy", "nike"):
            assert "product_image_tried" in db.get_deal(key)["flags"]
        assert sorted(fetcher.calls) == sorted(
            [self.DICKS, self.WALMART, self.TARGET, self.BESTBUY, self.NIKE])
        # One attempt per deal ever: a second pass makes no fetch at all.
        fetcher.calls.clear()
        assert pipeline._backfill_product_images(db, fetcher, None) == 0
        assert fetcher.calls == []
        db.close()

    def test_only_the_sellers_own_page_is_fetched(self, tmp_path):
        """Amazon is Keepa's; trackers, blogs, affiliate redirectors and any
        host outside the retailer allowlist are left alone, never fetched."""
        db = DB(tmp_path / "state.db")
        links = {
            "amazon": "https://www.amazon.com/dp/B000000000",
            "slick": "https://slickdeals.net/f/123-deal",
            "shortener": "https://bit.ly/abc",
            "unknown": "https://shop.unknownstore.example/p/1",
            "not_com": "https://www.target.co.uk/p/1",
            "nolink": "",
        }
        for key, link in links.items():
            self._seed(db, key, link)
        self._seed(db, "has_pic", self.DICKS + "?v=1",
                   image="https://x.example/i.jpg")
        self._seed(db, "tried", self.DICKS + "?v=2",
                   flags=["product_image_tried"])
        fetcher = self._Fetcher({})
        assert pipeline._backfill_product_images(db, fetcher, None) == 0
        assert fetcher.calls == []
        for key in links:
            assert db.get_deal(key)["flags"] == []
        db.close()

    def test_blocks_and_errors_flag_tried_without_image(self, tmp_path):
        db = DB(tmp_path / "state.db")
        self._seed(db, "robots", self.DICKS + "?v=robots")
        self._seed(db, "forbidden", self.WALMART + "?v=403")
        self._seed(db, "down", self.TARGET + "?v=500")
        self._seed(db, "gone", self.BESTBUY + "?v=404")
        fetcher = self._Fetcher({self.WALMART + "?v=403": 403,
                                 self.TARGET + "?v=500": 500},
                                blocked={self.DICKS + "?v=robots"})
        assert pipeline._backfill_product_images(db, fetcher, None) == 0
        assert len(fetcher.calls) == 4
        for key in ("robots", "forbidden", "down", "gone"):
            deal = db.get_deal(key)
            assert deal["image_url"] == ""
            assert "product_image_tried" in deal["flags"]
        # Never fetched again, blocked or not.
        fetcher.calls.clear()
        assert pipeline._backfill_product_images(db, fetcher, None) == 0
        assert fetcher.calls == []
        db.close()

    def test_per_run_cap_and_deadline_hold(self, tmp_path):
        db = DB(tmp_path / "state.db")
        cap = pipeline._PRODUCT_IMAGE_PER_RUN
        pages = {}
        for i in range(cap + 5):
            link = f"{self.DICKS}-{i}"
            self._seed(db, f"d{i}", link)
            pages[link] = (b"<meta property='og:image' "
                           b"content='https://dks.scene7.com/is/image/G/%d'>" % i)
        fetcher = self._Fetcher(pages)
        # A deadline already behind us: nothing is fetched or flagged.
        assert pipeline._backfill_product_images(
            db, fetcher, deadline=time.monotonic() - 1.0) == 0
        assert fetcher.calls == []

        # The fetcher crossing the deadline mid-pass ends the pass; that
        # deal stays unflagged for the next run.
        class _OutOfTime(self._Fetcher):
            def get(self, url, **kw):
                raise DeadlineExceeded("test")

        assert pipeline._backfill_product_images(db, _OutOfTime({}), None) == 0
        assert all("product_image_tried" not in d["flags"]
                   for d in db.all_deals())
        # Per-run cap, the remainder next run, then nothing.
        assert pipeline._backfill_product_images(db, fetcher, None) == cap
        assert len(fetcher.calls) == cap
        assert pipeline._backfill_product_images(db, fetcher, None) == 5
        assert len(fetcher.calls) == cap + 5
        assert pipeline._backfill_product_images(db, fetcher, None) == 0
        assert len(fetcher.calls) == cap + 5
        db.close()

    def test_dead_deals_only_inside_the_just_missed_window(self, tmp_path):
        db = DB(tmp_path / "state.db")
        cfg = AppConfig()  # default show_dead_hours
        real_now = now_utc()
        self._seed(db, "fresh_dead", self.DICKS + "?v=1",
                   died_at=iso(real_now - timedelta(hours=1)))
        self._seed(db, "old_dead", self.DICKS + "?v=2",
                   died_at=iso(real_now - timedelta(hours=cfg.show_dead_hours + 1)))
        fetcher = self._Fetcher({
            self.DICKS + "?v=1": (b"<meta property='og:image' "
                                  b"content='https://dks.scene7.com/G/1'>")})
        assert pipeline._backfill_product_images(db, fetcher, None, cfg) == 1
        assert fetcher.calls == [self.DICKS + "?v=1"]
        assert db.get_deal("fresh_dead")["image_url"] == "https://dks.scene7.com/G/1"
        assert db.get_deal("old_dead")["flags"] == []
        db.close()

    def test_run_wires_it_on_the_fast_lane_only(self, tmp_path, monkeypatch):
        """run() calls the backfill only on the fast lane (through the real
        PoliteFetcher: robots.txt first) and reports the count as
        summary['product_images']; the hourly never touches retailer pages."""
        for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "SMTP_HOST",
                    "DEALS_CLAUDE_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        root_cfg = Path(__file__).resolve().parent.parent / "config.yaml"
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(root_cfg.read_text().replace(
            'site_dir: "site"', f'site_dir: "{tmp_path / "site"}"'))
        db_path = tmp_path / "state.db"
        db = DB(db_path)
        self._seed(db, "dicks", self.DICKS)
        db.close()
        requests = []

        def handler(request):
            requests.append(request.url.path)
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /\n")
            if str(request.url) == self.DICKS:
                return httpx.Response(200, text=(
                    "<meta property='og:image' "
                    "content='https://dks.scene7.com/is/image/GolfGalaxy/X'>"))
            return httpx.Response(404)

        def run(fast_lane):
            return pipeline.run(
                fast_lane=fast_lane, db_path=str(db_path),
                config_path=str(cfg_path), dry_run_telegram=True,
                transport=httpx.MockTransport(handler), sources_override=[],
                skip_validation=True, now=NOW)

        summary = run(fast_lane=False)
        assert summary["product_images"] == 0
        assert requests == []
        summary = run(fast_lane=True)
        assert summary["product_images"] == 1
        assert requests == ["/robots.txt", "/p/dsg-ultimate-backpack-5-0"]
        db = DB(db_path)
        assert db.get_deal("dicks")["image_url"] == \
            "https://dks.scene7.com/is/image/GolfGalaxy/X"
        db.close()


def test_link_resolution_stops_at_its_budget(monkeypatch):
    """Resolution is bounded per run: past the budget every remaining link
    is canonicalized as posted, without a request."""
    from dealradar import http_client
    from dealradar.models import ExtractedDeal

    calls = []
    monkeypatch.setattr(http_client, "resolve_redirects",
                        lambda fetcher, url, hops, deadline=None: calls.append(url) or url)
    monkeypatch.setattr(pipeline, "_RESOLVE_BUDGET_S", -1.0)
    deals = [ExtractedDeal(source_url="s", source_name="n", store="X", item=f"i{i}",
                           product_url=f"https://short.ly/{i}?utm_source=t")
             for i in range(3)]
    cfg = load_config()
    pipeline._resolve_product_urls([(None, d) for d in deals], None, cfg,
                                   time.monotonic() + 600)
    assert calls == []
    assert [d.product_url for d in deals] == [f"https://short.ly/{i}" for i in range(3)]
