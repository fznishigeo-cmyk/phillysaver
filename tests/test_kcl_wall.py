"""Offline tests for the KCL live deal-wall adapter.

Live incident: the user's two test deals (Ekouaer lounge set, Women's
Christmas tee) sat on KCL's deal wall but never entered their RSS feed, so
dealradar missed both. The wall adapter fetches the wall page itself under
the operator's documented written permission (sources.yaml
``crawl_permission``) and chunks the text for the evidence-verified parse.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from dealradar.fetch.kcl_wall import (
    MAX_CHUNK_CHARS,
    MAX_CHUNKS,
    KclWallAdapter,
    _chunk_lines,
    _devalue,
    _embedded_deals,
    _ld_offers,
    _match_offer,
    _parse_cards,
)
from dealradar.models import FetchResult, SourceConfig

NOW = datetime(2026, 10, 10, 21, 0, 0, tzinfo=timezone.utc)
WALL_URL = "https://thekrazycouponlady.com/coupons-for/amazon"

WALL_HTML = """<html><head><title>Amazon Deals</title>
<script>var tracking = "no prices in here";</script></head><body>
<div class="card">
  <a href="https://thekrazycouponlady.com/deals/tee">Women's Christmas Graphic Tee</a>
  <span>$3.60</span> <s>$19.99</s> <span>82% Off</span>
  <a href="https://www.amazon.com/dp/B0TEE">Shop on Amazon</a>
</div>
<div class="card">
  <a href="https://thekrazycouponlady.com/deals/lounge">Ekouaer Lounge Set</a>
  <span>$14.99</span> <s>$39.99</s> <span>Code 5HXNMK18</span>
</div>
<div class="card">
  <a href="https://thekrazycouponlady.com/deals/vac">Robot Vacuum</a>
  <span>$89.00</span> <s>$299.99</s>
</div>
</body></html>"""


def fr(url: str = WALL_URL, *, status: int = 200, body: bytes | None = b"",
       not_modified: bool = False, blocked: bool = False) -> FetchResult:
    return FetchResult(url=url, final_url=url, status=status, body=body,
                       not_modified=not_modified, blocked_by_robots=blocked)


class FakeFetcher:
    """PoliteFetcher stand-in that records the kwargs of every get()."""

    def __init__(self, result: FetchResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, *, conditional=True, headers=None, deadline=None,
            **kw) -> FetchResult:
        self.calls.append((url, kw))
        return self.result


class StrictFetcher(FakeFetcher):
    """Rejects the override kwarg — proves it is only sent with permission."""

    def get(self, url, *, conditional=True, headers=None,
            deadline=None) -> FetchResult:
        self.calls.append((url, {}))
        return self.result


def make_source(**kw) -> SourceConfig:
    defaults: dict = dict(
        name="kcl_wall_amazon", method="api", adapter="kcl_wall",
        url=WALL_URL, site="https://thekrazycouponlady.com/", allowed_html=True,
        crawl_permission="operator reports written email permission from KCL",
    )
    defaults.update(kw)
    return SourceConfig(**defaults)


class TestFetch:
    def test_wall_maps_to_price_bearing_posts_with_links(self) -> None:
        fetcher = FakeFetcher(fr(body=WALL_HTML.encode()))
        posts = KclWallAdapter().fetch_posts(
            make_source(), fetcher, None, None, NOW, None)

        assert len(posts) == 1  # small wall fits one chunk
        post = posts[0]
        assert post.source_name == "kcl_wall_amazon"
        assert post.uid.startswith("wall:0:") and len(post.uid) == 19
        assert post.url == WALL_URL             # credit: the wall itself
        assert post.title == "KCL deal wall (part 1)"
        assert post.published_at is None        # first_seen governs freshness
        assert post.fetched_at == NOW
        # Verbatim prices survive for evidence verification.
        assert "$3.60" in post.text and "$19.99" in post.text
        assert "$14.99" in post.text and "5HXNMK18" in post.text
        # Anchor hrefs survive the cleaner so deals keep product links.
        assert "(https://www.amazon.com/dp/B0TEE)" in post.text
        # Script content never leaks into the parsed text.
        assert "tracking" not in post.text

    def test_permission_host_forwarded_to_fetcher(self) -> None:
        fetcher = FakeFetcher(fr(body=WALL_HTML.encode()))
        KclWallAdapter().fetch_posts(
            make_source(), fetcher, None, None, NOW, None)
        [(url, kw)] = fetcher.calls
        assert url == WALL_URL
        assert kw == {"robots_override_host": "thekrazycouponlady.com"}

    def test_no_permission_means_no_override_kwarg(self) -> None:
        # StrictFetcher would raise TypeError if the kwarg were passed.
        fetcher = StrictFetcher(fr(body=WALL_HTML.encode()))
        posts = KclWallAdapter().fetch_posts(
            make_source(crawl_permission=""), fetcher, None, None, NOW, None)
        assert len(posts) == 1

    def test_not_modified_returns_empty(self) -> None:
        fetcher = FakeFetcher(fr(status=304, body=None, not_modified=True))
        assert KclWallAdapter().fetch_posts(
            make_source(), fetcher, None, None, NOW, None) == []

    def test_blocked_by_robots_raises(self) -> None:
        fetcher = FakeFetcher(fr(status=200, body=None, blocked=True))
        try:
            KclWallAdapter().fetch_posts(
                make_source(), fetcher, None, None, NOW, None)
        except RuntimeError as e:
            assert "blocked_by_robots" in str(e)
        else:
            raise AssertionError("expected RuntimeError")

    def test_http_error_raises(self) -> None:
        fetcher = FakeFetcher(fr(status=503, body=None))
        try:
            KclWallAdapter().fetch_posts(
                make_source(), fetcher, None, None, NOW, None)
        except RuntimeError as e:
            assert "http_503" in str(e)
        else:
            raise AssertionError("expected RuntimeError")

    def test_priceless_page_warns_and_maps_nothing(self, caplog) -> None:
        """JS-only render / layout change: the diagnostic must be loud."""
        body = b"<html><body><div id='root'>Loading deals...</div></body></html>"
        fetcher = FakeFetcher(fr(body=body))
        with caplog.at_level(logging.WARNING, logger="dealradar.fetch.kcl_wall"):
            posts = KclWallAdapter().fetch_posts(
                make_source(), fetcher, None, None, NOW, None)
        assert posts == []
        assert any("price tokens" in r.message for r in caplog.records)

    def test_unchanged_wall_hashes_identically(self) -> None:
        """Same wall text => same uids, so re-runs re-parse nothing."""
        a = KclWallAdapter().fetch_posts(
            make_source(), FakeFetcher(fr(body=WALL_HTML.encode())),
            None, None, NOW, None)
        b = KclWallAdapter().fetch_posts(
            make_source(), FakeFetcher(fr(body=WALL_HTML.encode())),
            None, None, NOW, None)
        assert [p.uid for p in a] == [p.uid for p in b]


class TestChunking:
    def test_price_less_chunks_are_dropped(self) -> None:
        text = "nav\nabout us\ncareers\n" * 50  # no prices anywhere
        assert _chunk_lines(text) == []

    def test_long_wall_splits_and_caps(self) -> None:
        card = "Deal item name here\n$9.99 was $29.99\nShop now\n"
        text = card * 2000  # far beyond MAX_CHUNKS * MAX_CHUNK_CHARS
        chunks = _chunk_lines(text)
        assert 1 <= len(chunks) <= MAX_CHUNKS
        assert all(len(c) <= MAX_CHUNK_CHARS for c in chunks)
        assert all("$9.99" in c for c in chunks)

    def test_small_priced_text_is_one_chunk(self) -> None:
        chunks = _chunk_lines("Widget\n$5.00 was $25.00")
        assert chunks == ["Widget\n$5.00 was $25.00"]


# ------------------------------------------------------------ live card shape

#: The wall as it renders live (2026-10-11): one field per line, no hrefs,
#: and a Schema.org offers block carrying the Amazon links.
CARD_WALL_HTML = """<html><head><title>Amazon Deals</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"Amazon Deals","offers":{"@type":"AggregateOffer","priceCurrency":"USD","offerCount":3,"offers":[{"@type":"Offer","price":"5.99","priceCurrency":"USD","url":"https://www.amazon.com/dp/B0TEE12345?tag=kclunicorn-20&th=1"},{"@type":"Offer","price":"3.00","url":"https://www.amazon.com/dp/B0STEAM123?tag=kclunicorn-20"},{"@type":"Offer","price":"3.00","url":"https://www.amazon.com/dp/B0DUPL1234?tag=kclunicorn-20"}]}}</script>
</head><body>
<nav>Home<br>Stores<br>Popular Pages</nav>
<div>Online Deal<br>Amazon<br>Scott ComfortPlus Toilet Paper<br><span>$5.40</span><s>$6.49</s><br>17% Off<br>8 hours ago</div>
<div>Online Deal<br>Amazon<br>Women's Christmas Tee<br><span>$5.99</span><s>$19.99</s><br>70% Off<br>6 hours ago</div>
<div>Get all the new deals and savings hacks straight to your inbox<br>Enter your email</div>
<div>Online Deal<br>Amazon<br>Shower Steamers<br><span>$3.00</span><s>$9.99</s><br>70% Off<br>16 hours ago</div>
<div>Online Deal<br>Amazon<br>No Strike Item<br><span>$12.00</span><br>Just now</div>
<div>Show More</div>
</body></html>"""


class TestCards:
    def test_cards_parse_from_live_text(self) -> None:
        from dealradar import util
        cards = _parse_cards(util.html_to_text(CARD_WALL_HTML))
        assert [c["name"] for c in cards] == [
            "Scott ComfortPlus Toilet Paper", "Women's Christmas Tee",
            "Shower Steamers", "No Strike Item"]
        tee = cards[1]
        assert (tee["final"], tee["regular"], tee["pct"], tee["age"]) == \
            (5.99, 19.99, "70", "6 hours ago")
        assert cards[3]["regular"] is None and cards[3]["age"] == "Just now"

    def test_offers_and_price_match(self) -> None:
        offers = _ld_offers(CARD_WALL_HTML)
        assert [o["asin"] for o in offers] == ["B0TEE12345", "B0STEAM123",
                                               "B0DUPL1234"]
        tee = {"final": 5.99}
        assert _match_offer(tee, offers)["asin"] == "B0TEE12345"
        # Two offers at $3.00: no guess.
        assert _match_offer({"final": 3.0}, offers) is None
        assert _match_offer({"final": 12.0}, offers) is None

    def test_wall_maps_one_post_per_card(self) -> None:
        fetcher = FakeFetcher(fr(body=CARD_WALL_HTML.encode()))
        posts = KclWallAdapter().fetch_posts(
            make_source(), fetcher, None, None, NOW, None)
        assert [p.title for p in posts] == [
            "Scott ComfortPlus Toilet Paper", "Women's Christmas Tee",
            "Shower Steamers", "No Strike Item"]
        tee = posts[1]
        assert tee.uid.startswith("card:") and len(tee.uid) == 17
        assert tee.url == WALL_URL                      # credit: the wall
        # Every number verbatim, for the evidence check.
        assert "$5.99" in tee.text and "$19.99" in tee.text and "70% Off" in tee.text
        assert "Buy: https://www.amazon.com/dp/B0TEE12345?tag=kclunicorn-20" in tee.text
        assert "6 hours ago" not in tee.text             # age never in the text
        assert tee.published_at == NOW - timedelta(hours=6)
        assert posts[3].published_at == NOW
        assert "Buy:" not in posts[2].text               # ambiguous price: no link

    def test_card_hashes_stably_as_the_age_ticks(self) -> None:
        later = CARD_WALL_HTML.replace("6 hours ago", "7 hours ago")
        a = KclWallAdapter().fetch_posts(
            make_source(), FakeFetcher(fr(body=CARD_WALL_HTML.encode())),
            None, None, NOW, None)[1]
        b = KclWallAdapter().fetch_posts(
            make_source(), FakeFetcher(fr(body=later.encode())),
            None, None, NOW, None)[1]
        assert (a.uid, a.text) == (b.uid, b.text)        # parsed once, not hourly

    def test_cardless_wall_falls_back_to_chunks(self) -> None:
        posts = KclWallAdapter().fetch_posts(
            make_source(), FakeFetcher(fr(body=WALL_HTML.encode())),
            None, None, NOW, None)
        assert len(posts) == 1 and posts[0].uid.startswith("wall:0:")


# -------------------------------------------------------- embedded deal data

#: One deal object exactly as the live page serializes it (2026-10-11):
#: ``[0, x]`` wraps a value, ``[1, [...]]`` an array, ``[0]`` is null.
EMBEDDED_BLOB = (
    '{"initialDeals":[1,[[0,{"_id":[0,"70tIei2FYwbeci5kdfRnRD"],'
    '"title":[0,"Scott ComfortPlus Toilet Paper"],"bannerInformation":[0],'
    '"imageUrl":[0,"https://content-images.example/x.jpg"],'
    '"store":[0,{"_id":[0,"4Ry"],"name":[0,"Amazon"],"slug":[0,"amazon"]}],'
    '"dealType":[1,[[0,"Online"]]],"products":[1,[[0,{"_id":[0,"684568923"],'
    '"name":[0,"Scott ComfortPlus Toilet Paper, 12 ct (291.6 sq ft)"],'
    '"quantity":[0,1],"regularPrice":[0,6.49],"discountPrice":[0,5.68],'
    '"discountType":[0,"SALE"],"multipleSKU":[0,false],'
    '"url":[0,"https://www.amazon.com/dp/B07BGLT25K?tag=kclunicorn-20&linkCode=ogi&th=1&psc=1"],'
    '"affiliateLink":[0],"TCB8112Coupons":[1,[]],"ClipClubCoupons":[1,[]],'
    '"coupons":[1,[]],"DirectCoupons":[1,[]],'
    '"promos":[1,[[0,{"_id":[0,"a61f13679"],"line":[0,"5% off with Subscribe & Save"],'
    '"url":[0,null],"code":[0,null]}]]]}]]],'
    '"expiredAt":[0,"2026-10-24T23:59:00-06:00"],"originalPrice":[0,6.49],'
    '"percentSavings":[0,17],"finalPrice":[0,5.4],"savings":[0,1.09],'
    '"displayAsLowAs":[0,true],"updatedAt":[0,"2026-10-10T16:42:03.954Z"],'
    '"createdAt":[0,"2026-10-10T16:42:03.954Z"]}],'
    '[0,{"_id":[0,"codeDeal1"],"title":[0,"Ekouaer Pajama Set"],'
    '"store":[0,{"name":[0,"Amazon"]}],"dealType":[1,[[0,"Online"]]],'
    '"products":[1,[[0,{"name":[0,"Ekouaer Women\'s Pajama Set"],'
    '"regularPrice":[0,40.99],"discountPrice":[0,25.99],'
    '"url":[0,"https://www.amazon.com/dp/B0PAJAMA12?tag=kclunicorn-20"],'
    '"promos":[1,[[0,{"line":[0,"50% off with code"],"code":[0,"5HXNMK18"]}]]]}]]],'
    '"originalPrice":[0,40.99],"percentSavings":[0,68],"finalPrice":[0,12.99],'
    '"createdAt":[0,"2026-10-10T20:00:00.000Z"]}]]],"other":[0,1]}'
)


def _page_with_blob(blob: str = EMBEDDED_BLOB) -> str:
    import html as html_lib
    return ('<html><head><title>Amazon Deals</title></head><body>'
            '<nav>Home</nav><astro-island props="' + html_lib.escape(blob, quote=True) +
            '"></astro-island>' + CARD_WALL_HTML[CARD_WALL_HTML.index("<div>"):])


class TestEmbeddedDeals:
    def test_devalue_decodes_the_tuple_encoding(self) -> None:
        enc = {"a": [0, 1.5], "b": [0], "c": [1, [[0, "x"], [0, {"d": [0, None]}]]],
               "e": [1, []], "f": "plain"}
        assert _devalue(enc) == {"a": 1.5, "b": None, "c": ["x", {"d": None}],
                                 "e": [], "f": "plain"}

    def test_embedded_deals_decoded_from_the_escaped_attribute(self) -> None:
        deals = _embedded_deals(_page_with_blob())
        assert [d["title"] for d in deals] == ["Scott ComfortPlus Toilet Paper",
                                               "Ekouaer Pajama Set"]
        prod = deals[0]["products"][0]
        assert prod["regularPrice"] == 6.49 and prod["discountPrice"] == 5.68
        assert prod["url"].startswith("https://www.amazon.com/dp/B07BGLT25K")
        assert prod["promos"][0]["line"] == "5% off with Subscribe & Save"
        assert deals[0]["finalPrice"] == 5.4 and deals[0]["bannerInformation"] is None
        assert _embedded_deals(CARD_WALL_HTML) == []   # no blob: nothing

    def test_wall_maps_one_post_per_embedded_deal(self) -> None:
        posts = KclWallAdapter().fetch_posts(
            make_source(), FakeFetcher(fr(body=_page_with_blob().encode())),
            None, None, NOW, None)
        assert [p.uid for p in posts] == ["deal:70tIei2FYwbeci5kdfRnRD", "deal:codeDeal1"]
        tp, pj = posts
        assert tp.title == "Scott ComfortPlus Toilet Paper, 12 ct (291.6 sq ft)"
        assert tp.url == WALL_URL                                   # credit: the wall
        # Every number as the page states it, plus the promo that makes it.
        assert "$5.40" in tp.text and "$6.49" in tp.text and "$5.68" in tp.text
        assert "17% Off" in tp.text
        assert "5% off with Subscribe & Save" in tp.text
        assert "Expires: 2026-10-24" in tp.text
        assert "Buy: https://www.amazon.com/dp/B07BGLT25K?tag=kclunicorn-20" in tp.text
        assert "updatedAt" not in tp.text and "content-images" not in tp.text
        assert tp.published_at == datetime(2026, 10, 10, 16, 42, 3, 954000,
                                           tzinfo=timezone.utc)
        # A promo code travels with its line.
        assert "50% off with code (code 5HXNMK18)" in pj.text
        assert "$12.99" in pj.text and "$40.99" in pj.text and "$25.99" in pj.text

    def test_embedded_path_wins_over_text_cards_and_hashes_stably(self) -> None:
        a = KclWallAdapter().fetch_posts(
            make_source(), FakeFetcher(fr(body=_page_with_blob().encode())),
            None, None, NOW, None)
        later = _page_with_blob(EMBEDDED_BLOB.replace(
            '"updatedAt":[0,"2026-10-10T16:42:03.954Z"]',
            '"updatedAt":[0,"2026-10-11T01:00:00.000Z"]'))
        b = KclWallAdapter().fetch_posts(
            make_source(), FakeFetcher(fr(body=later.encode())), None, None, NOW, None)
        assert len(a) == 2 and [(p.uid, p.text) for p in a] == [(p.uid, p.text) for p in b]

    def test_deal_without_price_or_name_is_skipped(self) -> None:
        blob = EMBEDDED_BLOB.replace('"finalPrice":[0,5.4]', '"finalPrice":[0]')
        posts = KclWallAdapter().fetch_posts(
            make_source(), FakeFetcher(fr(body=_page_with_blob(blob).encode())),
            None, None, NOW, None)
        assert [p.uid for p in posts] == ["deal:codeDeal1"]
