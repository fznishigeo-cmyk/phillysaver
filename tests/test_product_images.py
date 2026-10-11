"""Tests for dealradar.product_images: which photo of a post is this deal's."""

from __future__ import annotations

import pytest

from dealradar.product_images import match_product_image

PIC, OTHER = "https://blog.example/up/mine.jpg", "https://blog.example/up/other.jpg"


def test_exact_link_wins_and_empty_inputs_match_nothing():
    link = "https://shop.example/p/kettle/9?color=red"
    photos = {link: PIC, "https://shop.example/p/toaster/8": OTHER}
    assert match_product_image([link], photos) == PIC
    assert match_product_image(["", link], photos) == PIC
    assert match_product_image([], photos) == ""
    assert match_product_image([link], {}) == ""
    assert match_product_image(["https://shop.example/p/mixer/7"], photos) == ""


def test_posted_link_tried_before_canonical():
    posted, canonical = "https://shop.example/p/a/1", "https://shop.example/p/b/2"
    photos = {posted: PIC, canonical: OTHER}
    assert match_product_image([posted, canonical], photos) == PIC
    assert match_product_image(["", canonical], photos) == OTHER


# One-segment paths below: a bare host+path never matches those, so each
# test proves its own level.
RETAIL = "https://shop.example/kettle-9"
ENC = "https%3A%2F%2Fshop.example%2Fkettle-9"


def test_normalized_case_fragment_tracking_trailing_slash():
    key = ("https://Shop.Example/kettle-9/?utm_source=blog&tag=h2s-20&ref=x&ref_=y"
           "&ascsubtag=a&afsrc=1&cjevent=c&clickid=k&irclickid=i&sid=s&cid=c"
           "&pid=p&u1=u&subid=z#reviews")
    assert match_product_image(["HTTPS://shop.example/kettle-9"], {key: PIC}) == PIC
    # A param that names the variant is not tracking: no normalized match.
    assert match_product_image([RETAIL + "?size=xl"], {RETAIL + "?size=s": PIC}) == ""


@pytest.mark.parametrize("param", ["url", "u", "murl", "ued", "dest", "destination",
                                   "target", "redirect", "r", "link", "to"])
def test_affiliate_unwrapped_on_either_side(param):
    wrapped = f"https://go.redirect.example/c/123?{param}={ENC}%3Futm_source%3Dx"
    assert match_product_image([wrapped], {RETAIL: PIC}) == PIC   # posted is wrapped
    assert match_product_image([RETAIL], {wrapped: PIC}) == PIC   # post's link is wrapped
    assert match_product_image([wrapped], {RETAIL + "-pro": PIC}) == ""


def test_unwrap_two_levels_and_double_encoding():
    inner = f"https://click.linksynergy.example/deeplink?id=1&murl={ENC}"
    outer = "https://hip.example/visit?url=" + inner.replace("%", "%25") \
        .replace(":", "%3A").replace("/", "%2F").replace("?", "%3F") \
        .replace("&", "%26").replace("=", "%3D")
    assert match_product_image([outer], {RETAIL: PIC}) == PIC
    double = "https://go.example/r?u=" + ENC.replace("%", "%25")
    assert match_product_image([double], {RETAIL: PIC}) == PIC


@pytest.mark.parametrize("deal_link,post_link", [
    ("https://www.amazon.com/dp/B0ABCDEFGH",
     "https://www.amazon.com/Kettle-Steel/dp/b0abcdefgh/ref=sr_1?tag=h2s-20"),
    ("https://www.amazon.com/gp/product/B0ABCDEFGH?th=1",
     "https://amazon.com/gp/aw/d/B0ABCDEFGH"),
    ("https://www.target.com/p/songmics-shelf/-/A-87654321",
     "https://www.target.com/p/other-slug/-/A-87654321?preselect=1#lnk=sametab"),
    ("https://www.walmart.com/ip/Mainstays-Towel/123456789",
     "https://www.walmart.com/ip/123456789?athbdg=L1600"),
    ("https://www.bestbuy.com/site/tv/6501234.p?skuId=6501234",
     "https://api.bestbuy.example/x?u=https%3A%2F%2Fwww.bestbuy.com%2Fsite%2Fx%3FskuId%3D6501234"),
    ("https://www.homedepot.com/p/Drill-Kit/312345678",
     "https://www.homedepot.com/p/DEWALT-Drill/312345678?MERCH=REC"),
    ("https://www.lowes.com/pd/Shelf-Unit/1000123456",
     "https://www.lowes.com/pd/Other-Name/1000123456"),
    ("https://www.kohls.com/product/prd-5678901/blender.jsp",
     "https://www.kohls.com/product/prd-5678901/blender-red.jsp?skuId=1"),
    ("https://www.costco.com/blender.product.100123456.html",
     "https://www.costco.com/vitamix-blender.product.100123456.html"),
    ("https://www.wayfair.com/furniture/pdp/x-w001.html?piid=555",
     "https://www.wayfair.com/furniture/pdp/x-renamed.html?piid=555"),
    ("https://www.wayfair.com/a/pdp/y.html?sku=ABC123",
     "https://www.wayfair.com/b/pdp/z.html?sku=abc123"),
    ("https://home.woot.com/offers/kettle-2-pack",
     "https://www.woot.com/offers/kettle-2-pack?ref=w_cnt"),
    ("https://shop.example/p/kettle/9?color=red", "https://www.shop.example/p/kettle/9?color=blue"),
])
def test_retailer_product_id(deal_link, post_link):
    photos = {post_link: PIC, "https://www.target.com/p/x/-/A-1": OTHER}
    assert match_product_image([deal_link], photos) == PIC


def test_different_products_of_one_retailer_never_match():
    assert match_product_image(
        ["https://www.target.com/p/shelf/-/A-111"],
        {"https://www.target.com/p/shelf/-/A-222": PIC}) == ""
    assert match_product_image(
        ["https://www.amazon.com/dp/B0AAAAAAAA"],
        {"https://www.amazon.com/dp/B0BBBBBBBB": PIC}) == ""


def test_amazon_link_without_asin_matches_nothing():
    search = "https://www.amazon.com/s?k=towels"
    assert match_product_image([search], {search: PIC}) == ""
    assert match_product_image(["https://hip.example/go?url=https%3A%2F%2Fwww.amazon.com%2Fdeals"],
                               {"https://www.amazon.com/deals": PIC}) == ""


def test_host_path_only_when_one_photo_stands_behind_it():
    """A redirector's path names no product: two different photos behind
    one host+path is ambiguous, and a one-segment path is never used."""
    photos = {"https://share.example/r/click?b=1&x=kettle": PIC,
              "https://share.example/r/click?b=1&x=toaster": OTHER}
    assert match_product_image(["https://share.example/r/click?b=1&x=mixer"], photos) == ""
    assert match_product_image(["https://share.example/r.cfm?b=2"],
                               {"https://share.example/r.cfm?b=1": PIC}) == ""
