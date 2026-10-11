"""Tests for dealradar.product_images: which photo of a post is this deal's."""

from __future__ import annotations

import pytest

from dealradar.models import PostPhotos
from dealradar.product_images import (
    file_words, link_identity, link_key, match_product_image, name_words, photo_by_name,
    photo_key, pick_photo, single_product)

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
           "&ascsubtag=a&afsrc=1&cjevent=c&clickid=k&irclickid=i&u1=u&subid=z#reviews")
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
    # host+path: www or not, and a param only one side carries, is no matter
    ("https://shop.example/p/kettle/9?color=red", "https://www.shop.example/p/kettle/9?th=1"),
])
def test_retailer_product_id(deal_link, post_link):
    photos = {post_link: PIC, "https://www.target.com/p/x/-/A-1": OTHER}
    assert match_product_image([deal_link], photos) == PIC


@pytest.mark.parametrize("deal_links,photos", [
    # params sorted: one product whatever order its query names them in
    (["https://shop.example/item?id=5&color=red"],
     {"https://shop.example/item?color=red&id=5": PIC}),
    # Best Buy's /NNNNNNN.p path names the SKU without ?skuId=
    (["https://www.bestbuy.com/site/lg-65-oled/6501234.p"],
     {"https://www.bestbuy.com/site/lg-tv/6501234.p?skuId=6501234": PIC}),
    # the deal's own link is unwrapped before its product id is read (its
    # redirect never resolved: the stored canonical is the wrapper)
    (["https://goto.target.com/c/1/2/3?u=https%3A%2F%2Fwww.target.com%2Fp%2Fslug%2F-%2FA-111"],
     {"https://www.target.com/p/other-slug/-/A-111": PIC}),
    # a two-segment host+path (a Shopify /products/<handle>) names a product
    (["https://brand.example/products/kettle"],
     {"https://www.brand.example/products/kettle?variant=4211": PIC}),
    # level first: the canonical's exact hit beats the posted link's id hit
    (["https://www.target.com/p/x/-/A-111", "https://www.target.com/p/c/-/A-999"],
     {"https://www.target.com/p/b-slug/-/A-111": OTHER,
      "https://www.target.com/p/c/-/A-999": PIC}),
])
def test_matching_rules(deal_links, photos):
    assert match_product_image(deal_links, photos) == PIC


def test_link_identity_ignores_tracking_and_redirects():
    """Two links that open one page (tracking params, a redirect wrapper)
    are one link: several products holding it is a shared store page."""
    sale = "https://www.target.com/c/home-sale/-/N-5xtnr"
    wrapped = "https://goto.target.com/c/1/2?u=" + sale.replace(":", "%3A").replace("/", "%2F")
    assert link_identity(sale + "?ref=h2s&utm_source=x") == link_identity(wrapped) \
        == link_identity(sale)
    assert link_identity("https://www.target.com/c/toy-sale/-/N-1") != link_identity(sale)
    assert link_identity("") == link_identity("N/A") == ""


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
    # One photo behind the path, but the shared param names another product.
    assert match_product_image(["https://shop.example/p/kettle/9?color=red"],
                               {"https://shop.example/p/kettle/9?color=blue": PIC}) == ""
    assert match_product_image(["https://share.example/r.cfm?b=2"],
                               {"https://share.example/r.cfm?b=1": PIC}) == ""


def test_old_navy_pid_names_the_product():
    """Gap-family product pages differ only in ?pid=: pid (also sid, cid)
    is no tracking param, and a bare host+path never pairs two different
    pids. Live risk: the jeans card showed the hoodie photo, and the clear
    step then took the photo off both."""
    hoodie = "https://oldnavy.gap.com/browse/product.do?pid=111002"
    jeans = "https://oldnavy.gap.com/browse/product.do?pid=222002"
    assert match_product_image([jeans], {hoodie: PIC}) == ""
    assert match_product_image([hoodie + "&utm_source=x"], {hoodie: PIC, jeans: OTHER}) == PIC
    # The same pid with a browse-path param on one side only is one product.
    assert match_product_image([jeans], {jeans + "&cid=5": PIC}) == PIC


def test_normalized_and_unwrapped_hits_must_agree():
    """Two post links that normalize (or unwrap) alike but show different
    photos: no single photo is the deal's."""
    photos = {"https://shop.example/kettle-9?tag=a": PIC,
              "https://shop.example/kettle-9?tag=b": OTHER}
    assert match_product_image(["https://shop.example/kettle-9"], photos) == ""
    wrapped = {f"https://go.example/c?u={ENC}": PIC, f"https://go.example/d?url={ENC}": OTHER}
    assert match_product_image([RETAIL], wrapped) == ""
    same = {f"https://go.example/c?u={ENC}": PIC, f"https://go.example/d?url={ENC}": PIC}
    assert match_product_image([RETAIL], same) == PIC


TRX = ("https://clicks.trx-hub.com/xid/hip2save_c4db4_hip2save?q=https%3A%2F%2Fwww.dpbolvw.net"
       "%2Fclick-3278587-17049705%3Furl%3Dhttps%253A%252F%252Fwww.nike.com%252Ft%252F{}"
       "&p=https%3A%2F%2Fhip2save.com%2Fdeals%2Fnike-sale%2F&event_type=click&devicetype=desktop")


def test_redirector_links_of_one_article_never_share_a_photo():
    """Every trx-hub link of one Hip2Save article has the same host+path;
    only its q param names the product. The one photo behind that path is
    still another product's."""
    assert match_product_image([TRX.format("revolution-8")],
                               {TRX.format("team-hustle"): PIC}) == ""
    assert match_product_image([TRX.format("team-hustle")],
                               {TRX.format("team-hustle"): PIC}) == PIC


@pytest.mark.parametrize("deal_link", [
    "https://www.target.com/c/toy-sale/-/N-5xtnr", "https://www.walmart.com/browse/toys/4171",
    "https://www.walmart.com/cp/rollbacks/123", "https://www.walmart.com/search?q=lego",
    "https://www.amazon.com/b?node=12345", "https://www.bestbuy.com/site/promo/toy-sale",
    "https://www.kohls.com/",
])
def test_listing_pages_match_nothing(deal_link):
    """A category, sale, search, promo or home page is every product: a
    deal holding one never takes the photo inside it."""
    assert match_product_image([deal_link], {deal_link: PIC}) == ""


def test_link_key_counts_products_not_spellings():
    """One ASIN or retailer id however linked is one product; links behind
    one redirector path differ by the product they carry."""
    assert link_key("https://www.amazon.com/dp/B0ABCDEFGH?tag=x") \
        == link_key("https://www.amazon.com/Kettle/dp/B0ABCDEFGH")
    assert link_key(TRX.format("revolution-8")) != link_key(TRX.format("team-hustle"))


def test_photo_key_ignores_size_and_query():
    assert photo_key("https://i0.wp.com/b.example/wp-content/uploads/2026/10/X-300x300.jpg?w=9") \
        == photo_key("https://b.example/wp-content/uploads/2026/10/x.jpg") == "2026/10/x.jpg"
    assert photo_key("https://b.example/up/2026/10/x-scaled.jpg") == "2026/10/x.jpg"


def test_name_words():
    assert name_words("Crayola Markers 40ct, Only $19.99 at Amazon!") \
        == {"crayola", "marker", "40ct", "19", "99"}
    assert name_words("Women's Fleece Jackets") == {"women", "fleece", "jacket"}
    # A file name: no extension, size suffix, digits-only upload counters.
    assert file_words("https://b.example/up/2026/10/LEGO-Classic-Bricks-2-300x300.jpg?w=9") \
        == {"lego", "classic", "brick"}
    assert file_words("https://b.example/up/IMG_2041.jpg") == set()


MARKERS, LEGO, PLAYDOH = (f"https://b.example/up/{n}.jpg" for n in ("m", "l", "p"))
ITEMS = ["Crayola Markers 40ct", "LEGO Classic Bricks", "Play-Doh Starter Set"]


def test_photo_by_name_gives_each_deal_the_photo_named_after_it():
    named = {MARKERS: name_words("Crayola Markers 40ct"),
             LEGO: file_words("https://b.example/up/LEGO-Classic-Bricks-300x300.jpg"),
             PLAYDOH: name_words("Play-Doh Starter Set 10 pack")}
    assert [photo_by_name(i, ITEMS, named) for i in ITEMS] == [MARKERS, LEGO, PLAYDOH]
    # A name the post shows no photo of gets none.
    assert photo_by_name("Hot Wheels 5-Pack", ITEMS + ["Hot Wheels 5-Pack"], named) == ""
    # Two shared words scoring under 70 are another product of the line.
    kitchen = {PLAYDOH: name_words("Play-Doh Kitchen Creations Bundle")}
    assert photo_by_name("Play-Doh Starter Set", ITEMS, kitchen) == ""


def test_brand_only_names_never_match():
    """Two Crayola products whose photos say only 'Crayola': one shared word
    is a brand, not a product — none, unless the words separate them."""
    items = ["Crayola Markers 40ct", "Crayola Colored Pencils 50ct"]
    brand = {MARKERS: name_words("Crayola") | file_words("https://b.example/crayola-1.jpg"),
             LEGO: name_words("Crayola") | file_words("https://b.example/crayola-2.jpg")}
    assert [photo_by_name(i, items, brand) for i in items] == ["", ""]
    named = {MARKERS: name_words("Crayola Markers"), LEGO: name_words("Crayola Colored Pencils")}
    assert [photo_by_name(i, items, named) for i in items] == [MARKERS, LEGO]


def test_photo_by_name_needs_a_clear_winner_owned_by_no_other_deal():
    """The best photo must clear the deal's next-best by 10 points, must
    not name another deal of the post as well or better (a variant's
    photo), nor name another deal besides (a collage naming several). Two
    products sharing words (an air fryer and its liners) each keep their
    own photo."""
    items = ["LEGO Classic Bricks 300pc", "LEGO Classic Bricks 1500pc"]
    named = {MARKERS: name_words("LEGO Classic Bricks 300pc"),
             LEGO: name_words("LEGO Classic Bricks 1500pc")}
    assert [photo_by_name(i, items, named) for i in items] == ["", ""]
    collage = {MARKERS: name_words("Crayola Markers, LEGO Classic Bricks and Play-Doh")}
    assert [photo_by_name(i, ITEMS, collage) for i in ITEMS] == ["", "", ""]
    # A variant's only photo stays with the variant it names best.
    one = {LEGO: name_words("LEGO Classic Bricks 1500pc")}
    assert [photo_by_name(i, items, one) for i in items] == ["", LEGO]
    fryer = ["Ninja Air Fryer 4qt", "Ninja Air Fryer Liners 100ct"]
    own = {MARKERS: name_words(fryer[0]), LEGO: name_words(fryer[1])}
    assert [photo_by_name(i, fryer, own) for i in fryer] == [MARKERS, LEGO]


def test_pick_photo_link_name_and_conflict():
    """In its link, or by name; both and they differ: none (None: no post
    image either); both and one photo (any size): that photo."""
    link = "https://www.amazon.com/dp/B0ABCDEFGH"
    named = {MARKERS: name_words("Crayola Markers 40ct")}
    assert pick_photo([link], ITEMS[0], ITEMS, PostPhotos(in_link={link: LEGO}, named=named)) \
        is None
    assert pick_photo([link], ITEMS[0], ITEMS,
                      PostPhotos(in_link={link: LEGO}, named={LEGO: frozenset()})) == LEGO
    assert pick_photo([], ITEMS[0], ITEMS, PostPhotos(in_link={link: LEGO}, named=named)) \
        == MARKERS
    sized = MARKERS.replace(".jpg", "-300x300.jpg")
    assert pick_photo([link], ITEMS[0], ITEMS,
                      PostPhotos(in_link={link: sized}, named=named)) == sized
    assert pick_photo([link], ITEMS[0], ITEMS, PostPhotos()) == ""


def test_single_product():
    assert single_product(PostPhotos(links={"a"}, images={"x"}))
    assert not single_product(PostPhotos(links={"a", "b"}))
    assert not single_product(PostPhotos(images={"x", "y"}))
