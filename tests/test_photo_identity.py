"""Exact product facts survive post routing; all source/paid reads are offline."""
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import pytest

from dealradar import dedupe, photo_recovery as photos, pipeline, util
from dealradar.config import AppConfig, load_config, load_sources
from dealradar.db import DB
from dealradar.fetch.pages import PageAdapter, parse_page
from dealradar.fetch.rss import RssAdapter, post_photos
from dealradar.http_client import PoliteFetcher
from dealradar.keepa_budget import KeepaBudget
from dealradar.models import FetchResult
from dealradar.parse.extractor import clean_for_llm, parse_posts, verify_evidence
from dealradar.parse.schema import LLMDeal
from dealradar.publish.site import _serialize_deal
from dealradar.savings import compute_savings
from tests.test_dedupe import NOW, make_extracted, make_post, make_savings
from tests.test_photos_every_deal import A, B, URL, PIC, seed

FIXTURES = Path(__file__).parent / "fixtures" / "photo_identity"
WALMART = "https://www.walmart.com/ip/123456789"
SOURCES = ["slickdeals_frontpage", "dealsofamerica", "hip2save"]


def source_post(name, db):
    primary, backups = load_sources()
    source = next(s for s in primary + backups if s.name == name)
    if source.method == "rss":
        source = replace(source, allowed_html=False)
        body = (FIXTURES / ("hip2save.xml" if name == "hip2save" else "slickdeals.xml")).read_bytes()
        calls = []
        def get(url, **kw):
            calls.append(url)
            assert url == source.url  # official feed only; no article or retailer fetch
            return FetchResult(url, url, 200, body)
        posts = RssAdapter().fetch_posts(source, SimpleNamespace(get=get), db, AppConfig(), NOW)
        assert calls == [source.url]
    else:
        posts = parse_page(source, (FIXTURES / f"{name}.html").read_bytes(), NOW)
    assert len(posts) == 1
    return posts[0]


def parsed(post, cfg):
    store = "Walmart" if post.source_name == "hip2save" else "Amazon"
    deal = LLMDeal(item=post.title, store=store, product_url=post.url,
                   final_price=20, regular_price=50, online_only="true",
                   conditions=["subscribe_save" if post.source_name == "dealmoon_us" else "in_cart"],
                   evidence={"final_price_quote": "for $20", "regular_price_quote": "regularly $50"})
    inputs = []
    def extract(text, meta, **kw):
        inputs.append(text)
        return [deal]
    pairs, stats = parse_posts([post], SimpleNamespace(extract=extract, spend_usd=0), cfg, NOW, {})
    assert stats["deals_dropped_evidence"] == 0 and len(pairs) == 1
    assert inputs == [clean_for_llm(post, cfg.max_chars_per_post)]
    post, extracted = pairs[0]
    extracted.link_as_posted = extracted.product_url
    return post, extracted


@pytest.mark.parametrize("name", SOURCES)
def test_source_body_identity_survives_parse_upsert_and_post_routing(name):
    with closing(DB(":memory:")) as db:
        cfg = AppConfig()
        cfg.affiliate_competitor_tracker_hosts = load_config().affiliate_competitor_tracker_hosts
        post, extracted = parsed(source_post(name, db), cfg)
        before = replace(extracted)
        assert "https://" in post.text and len(post.parsed) == 1
        result = dedupe.upsert_extracted(db, extracted, compute_savings(extracted), post, cfg, NOW)
        row = db.get_deal(result["dedupe_key"])
        expected = WALMART if name == "hip2save" else URL
        assert row["product_identity_url"] == expected
        assert row["asin"] == ("" if name == "hip2save" else A)
        assert row["canonical_url"] == row["link_as_posted"] == post.url
        assert extracted == before
        public = _serialize_deal(row, NOW, amazon_tag="wowpriced-20")
        assert public["url"] == post.url
        assert "product_identity_url" not in public and "product_identity_tried" not in public
        if name == "dealsofamerica":
            assert dedupe.asin_from_links(dedupe._URL_RE.findall(post.text)) == ""
            assert dedupe.asin_from_links(post.links) == A
        assert row["image_url"] == (post.image_url if name == "hip2save" else "")
        assert verify_evidence(replace(extracted, final_price=1.99), post.text)[0] is False
        counts = [db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("deals", "sightings", "price_history")]
        dedupe.upsert_extracted(db, extracted, compute_savings(extracted), post, cfg, NOW)
        assert [db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("deals", "sightings", "price_history")] == counts
        # Existing merge initializes the legacy expiry clock on its first merge.
        after = db.get_deal(row["dedupe_key"])
        assert {k: v for k, v in after.items() if k != "expires_published_at"} == {
            k: v for k, v in row.items() if k != "expires_published_at"}
        dump = "\n".join(db.conn.iterdump())
        assert post.text not in dump and "Buy at Amazon" not in dump
        assert "regularly $50" not in dump  # only numeric price facts survive


@pytest.mark.parametrize("own_link", ["empty", "post", "short"])
def test_single_product_amazon_post_keeps_legacy_dp_key_and_tagged_click(own_link):
    cfg = load_config()
    post = make_post(url="https://hip2save.com/deals/gloves", text=f"Gloves for $20 {URL}")
    link = {"empty": "", "post": post.url, "short": "https://amzn.to/gloves"}[own_link]
    if own_link == "short":
        post.text = f"Gloves for $20 {link}"
        post.resolved_links = [URL]
    extracted = make_extracted(store="Amazon", item="Rigwarl Winter Gloves",
                               product_url=link, link_as_posted=link, codes=["GLOVE20"])
    post.parsed = [(extracted.item, link)]
    before = replace(extracted)
    with closing(DB(":memory:")) as db:
        result = dedupe.upsert_extracted(db, extracted, make_savings(), post, cfg, NOW)
        row = db.get_deal(result["dedupe_key"])
        assert row["canonical_url"] == row["product_identity_url"] == URL
        assert row["dedupe_key"] == "url|" + URL
        assert row["link_as_posted"] == link and extracted == before
        public = _serialize_deal(row, NOW, amazon_tag=cfg.affiliate_amazon_tag,
                                 competitor_tracker_hosts=cfg.affiliate_competitor_tracker_hosts)
        assert public["url"] == URL + "?tag=wowpriced-20"
        assert public["earns"] is True and public["needs_steps"] is True
        assert public["codes"] == ["GLOVE20"]
        assert [credit["url"] for credit in public["credits"]] == [post.url]


def test_keepa_row_and_body_asin_post_stay_one_row():
    cfg = load_config()
    keepa = make_extracted(store="Amazon", item="Acme Thermometer", product_url=URL)
    post = make_post(url="https://hip2save.com/deals/thermometer", text=f"Wireless thermometer {URL}")
    extracted = make_extracted(store="Amazon", item="Dual-Probe Wireless Meat Thermometer",
                               product_url=post.url, link_as_posted=post.url)
    post.parsed = [(extracted.item, post.url)]
    with closing(DB(":memory:")) as db:
        first = dedupe.upsert_extracted(db, keepa, make_savings(),
                                       make_post(source="keepa_deals", uid="keepa"), cfg, NOW)
        second = dedupe.upsert_extracted(db, extracted, make_savings(), post, cfg, NOW)
        assert second["dedupe_key"] == first["dedupe_key"] == "url|" + URL
        assert second["deal_id"] == first["deal_id"] and not second["is_new_deal"]
        assert len(db.all_deals()) == 1
        assert {s["source_name"] for s in db.sightings_for(first["deal_id"])} == {"keepa_deals", "hip2save"}


@pytest.mark.parametrize("store,own_url,example", [
    ("Walmart", "https://www.walmart.com/browse/household-essentials/1115193", WALMART),
    ("Kroger", "https://www.kroger.com/", "https://www.kroger.com/p/brawny-towels/0004200044199"),
    ("Amazon", "https://www.amazon.com/s?k=brawny", URL),
])
def test_sale_store_and_search_deals_never_borrow_example_identity_or_photo(store, own_url, example):
    post = make_post(url="https://hip2save.com/deals/store-sale", text=f"Sale includes {example}")
    post.image_url = "https://hip2save.com/wp-content/uploads/2026/10/example.jpg"
    post.links = [example]
    extracted = make_extracted(store=store, item="Store sale", product_url=own_url,
                               link_as_posted=own_url)
    post.parsed = [(extracted.item, own_url)]
    assert photos.post_identity(extracted, post) == ""
    with closing(DB(":memory:")) as db:
        row = dedupe.upsert_extracted(db, extracted, make_savings(), post, load_config(), NOW)["payload"]
        assert not row.get("product_identity_url") and not row["asin"] and not row["image_url"]
        assert row["canonical_url"] == own_url and row["dedupe_key"] == "url|" + own_url


def test_dealmoon_opaque_buy_link_recovers_no_identity():
    with closing(DB(":memory:")) as db:
        cfg = load_config()
        post, extracted = parsed(source_post("dealmoon_us", db), cfg)
        assert post.links == ["https://go.dealmoon.com/exec/j?d=253001"]
        row = dedupe.upsert_extracted(db, extracted, compute_savings(extracted), post, cfg, NOW)["payload"]
        assert not row.get("product_identity_url") and not row["asin"]
        assert row["canonical_url"] == row["link_as_posted"] == post.url


@pytest.mark.parametrize("name", SOURCES)
@pytest.mark.parametrize("parsed_roundup", [False, True])
def test_source_roundups_never_borrow_identity(name, parsed_roundup):
    with closing(DB(":memory:")) as db:
        cfg = AppConfig()
        post, extracted = parsed(source_post(name, db), cfg)
        # A second product can exist even when only one clears evidence/the floor.
        rival = "https://www.walmart.com/ip/987654321" if name == "hip2save" else f"https://amazon.com/dp/{B}"
        post.text += "\nOther product: " + rival
        if parsed_roundup:
            post.parsed.append(("Different product", post.url))
        result = dedupe.upsert_extracted(db, extracted, compute_savings(extracted), post, cfg, NOW)
        row = db.get_deal(result["dedupe_key"])
        assert not row.get("product_identity_url") and not row["asin"]


@pytest.mark.parametrize("where", ["text", "links", "photos_key", "photos_url", "in_link", "resolved"])
@pytest.mark.parametrize("store,target,key", [("Amazon", URL, f"amazon:{A}"),
                                              ("Walmart", WALMART, "walmart.com:123456789")])
def test_transient_fact_locations(store, target, key, where):
    post = make_post()
    post.text = "The price facts are $20 and $50."
    extracted = make_extracted(store=store, product_url=post.url)
    if where == "text":
        post.text += " " + target
    elif where == "links":
        post.links = [target]
    elif where == "photos_key":
        post.photos.links.add(key)
    elif where == "photos_url":
        post.photos.links.add(target)
    elif where == "in_link":
        post.photos.in_link[target] = "https://images.example/exact.jpg"
    else:
        post.resolved_links = [target]
    assert photos.post_identity(extracted, post) == target


@pytest.mark.parametrize("links,store,expected", [
    ([URL, f"https://amazon.com/gp/product/{A}?tag=source"], "Amazon", URL),
    ([URL + "?url=" + quote(f"https://amazon.com/dp/{B}", safe="")], "Amazon", URL),
    ([URL, WALMART], "Amazon", ""),
    ([WALMART], "Amazon", ""),
    ([URL], "Walmart", ""),
    (["https://walmart.com/search?q=skirt"], "Walmart", ""),
    (["https://walmart.com/ip/123junk"], "Walmart", ""),
    (["https://www.walmart.com/blocked"], "Walmart", ""),
    (["https://go.dealmoon.com/exec/j?d=123456"], "Amazon", ""),
    (["https://www.bradsdeals.com/go/d/blt123-shop"], "Amazon", ""),
    (["https://shokz.com/products/openrun?utm_source=blog"], "Shokz.com", "https://shokz.com/products/openrun"),
    (["https://shokz.com/collections/headphones"], "Shokz.com", ""),
    (["https://www.woot.com/offers/exact-kettle"], "Woot", "https://www.woot.com/offers/exact-kettle"),
    (["https://[bad", None, "javascript:alert(1)"], "Amazon", ""),
    (["https://evil.example/products/skirt"], "Walmart", ""),
])
def test_exact_product_and_store_guards(links, store, expected):
    assert photos.product_identity(links, store) == expected


def test_source_image_is_matched_to_recovered_product_and_grant_is_enforced():
    # A featured collage cannot lend its image. The exact linked product can.
    link = "https://redirect.viglink.com?u=" + quote(WALMART, safe="")
    image = "https://hip2save.com/wp-content/uploads/2026/10/exact-skirt.jpg"
    markup = f'<img src="https://hip2save.com/collage.jpg"><a href="{link}"><img src="{image}"></a>'
    post = make_post(url="https://hip2save.com/deals/skirt", text=util.html_to_text(markup))
    post.photos = post_photos(markup, post.url)
    extracted = make_extracted(store="Walmart", product_url=post.url)
    with closing(DB(":memory:")) as db:
        row = dedupe.upsert_extracted(db, extracted, make_savings(), post, AppConfig(), NOW)["payload"]
        assert row["product_identity_url"] == WALMART and row["image_url"] == image
    post.photos.in_link[link] = "https://www.dealmoon.com/no-image-grant.jpg"
    with closing(DB(":memory:")) as db:
        row = dedupe.upsert_extracted(db, extracted, make_savings(), post, AppConfig(), NOW)["payload"]
        assert row["image_url"] == ""


def test_brads_opaque_go_link_is_available_but_cannot_establish_identity():
    primary, backups = load_sources()
    source = next(s for s in primary + backups if s.name == "bradsdeals")
    calls = []
    def get(url, **kw):
        calls.append(url)
        body = ((Path(__file__).parent / "fixtures/pages/bradsdeals.html").read_bytes()
                if url == source.url else b'<a href="/go/d/blt123-shop">Shop this deal</a>')
        return FetchResult(url, url, 200, body)
    with closing(DB(":memory:")) as db:
        post = PageAdapter().fetch_posts(source, SimpleNamespace(get=get), db, AppConfig(), NOW)[0]
        link = "https://www.bradsdeals.com/go/d/blt123-shop"
        assert link in post.text and link in post.links
        assert photos.post_identity(make_extracted(store="Amazon", product_url=post.url), post) == ""
        assert calls == [source.url, post.url]  # only the reader's existing enrichment


def test_rss_enrichment_retains_product_facts_beyond_its_text_cap():
    post = make_post(url="https://hip2save.com/deals/kettle", text="Price $20, regularly $50")
    markup = '<p>' + 'padding ' * 3000 + f'</p><a href="{URL}">Buy kettle</a>'
    fetcher = SimpleNamespace(get=lambda url, **kw: FetchResult(url, url, 200, markup.encode()))
    enriched = RssAdapter._enrich(post, fetcher, None, images=True)
    assert URL not in enriched.text
    assert enriched.content_hash == post.content_hash
    assert f"amazon:{A}" in enriched.photos.links
    assert URL in enriched.links
    extracted = make_extracted(store="Amazon", product_url=post.url)
    assert photos.post_identity(extracted, enriched) == URL


@pytest.mark.parametrize("store,identity,canonical", [
    ("Amazon", URL, "https://hip2save.com/deals/kettle"),
    ("Walmart", WALMART, "https://hip2save.com/deals/kettle"),
    ("Walmart", WALMART, ""),
])
def test_identity_avoids_repeating_existing_post_and_redirect_photo_fetches(store, identity, canonical):
    with closing(DB(":memory:")) as db:
        seed(db, store=store, canonical_url=canonical, product_identity_url=identity, flags=[])
        fetcher = SimpleNamespace(get=lambda *a, **kw: pytest.fail("identity needs no source fetch"),
                                  head=lambda *a, **kw: pytest.fail("identity needs no redirect fetch"))
        assert pipeline._resolve_short_links(db, fetcher, None, AppConfig()) == 0
        assert pipeline._links_from_posts(db, fetcher, None, AppConfig(), deals=db.all_deals()) == 0
        assert db.get_deal("a")["canonical_url"] == canonical


def test_recovered_identity_keeps_short_click_repair(monkeypatch):
    cfg = load_config()
    short = "https://shoplowes.me/3xYz9"
    target = "https://www.lowes.com/pd/dewalt-20v-drill/1000123456"
    post = make_post(url="https://hip2save.com/deals/drill", text=f"DeWalt drill {target}")
    extracted = make_extracted(store="Lowes", product_url=short, link_as_posted=short)
    calls = []
    def resolve(fetcher, link, *args, **kwargs):
        calls.append(link)
        return target
    monkeypatch.setattr("dealradar.http_client.resolve_redirects", resolve)
    with closing(DB(":memory:")) as db:
        result = dedupe.upsert_extracted(db, extracted, make_savings(), post, cfg, NOW)
        assert result["payload"]["product_identity_url"] == target
        assert pipeline._resolve_short_links(db, SimpleNamespace(), None, cfg) == 1
        row = db.get_deal(result["dedupe_key"])
        assert row["canonical_url"] == row["product_identity_url"] == target
        assert _serialize_deal(row, NOW)["url"] == target
        assert row["dedupe_key"] == "url|" + short
        assert pipeline._resolve_short_links(db, SimpleNamespace(), None, cfg) == 0
        assert calls == [short]


def test_recovered_identity_keeps_source_redirect_click_repair(monkeypatch):
    redirect = "https://slickdeals.net/click?lno=1&u2=walmart"
    calls = []
    def resolve(fetcher, link, *args, **kwargs):
        calls.append(link)
        return WALMART
    monkeypatch.setattr("dealradar.http_client.resolve_redirects", resolve)
    with closing(DB(":memory:")) as db:
        row = seed(db, store="Walmart", canonical_url=redirect, product_identity_url=WALMART, flags=[])
        db.add_sighting(row["id"], source_name="slickdeals", source_url="https://slickdeals.net/f/123",
                        content_hash="post", seen_at=NOW, final_price=20)
        assert pipeline._resolve_short_links(db, SimpleNamespace(), None, AppConfig()) == 1
        row = db.get_deal("a")
        assert row["canonical_url"] == row["product_identity_url"] == WALMART
        assert pipeline._resolve_short_links(db, SimpleNamespace(), None, AppConfig()) == 0
        assert calls == [redirect]


def test_amazon_identity_without_canonical_keeps_post_click_repair():
    post = "https://hip2save.com/deals/kettle"
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        assert url == post
        return FetchResult(url, url, 200, f'<a href="{URL}">Buy kettle</a>'.encode())
    fetcher = SimpleNamespace(get=get)
    with closing(DB(":memory:")) as db:
        row = seed(db, canonical_url="", asin=A, product_identity_url=URL, flags=[])
        db.add_sighting(row["id"], source_name="hip2save", source_url=post,
                        content_hash="post", seen_at=NOW, final_price=20)
        assert pipeline._links_from_posts(db, fetcher, None, AppConfig(), deals=db.all_deals()) == 1
        row = db.get_deal("a")
        assert row["canonical_url"] == row["product_identity_url"] == URL
        assert _serialize_deal(row, NOW, amazon_tag="wowpriced-20")["url"] == URL + "?tag=wowpriced-20"
        assert pipeline._links_from_posts(db, fetcher, None, AppConfig(), deals=db.all_deals()) == 0
        assert calls == [post]


@pytest.mark.parametrize("store,first,second", [("Amazon", URL, f"https://amazon.com/dp/{B}"),
                                               ("Walmart", WALMART, "https://walmart.com/ip/987654321")])
def test_later_body_cannot_lend_a_different_products_identity_or_photo(store, first, second):
    with closing(DB(":memory:")) as db:
        post = make_post(text=first, url="https://hip2save.com/deals/updated")
        extracted = make_extracted(store=store, product_url=post.url)
        key = dedupe.upsert_extracted(db, extracted, make_savings(), post, AppConfig(), NOW)["dedupe_key"]
        changed = replace(post, text=second, image_url="https://hip2save.com/wrong-product.jpg")
        dedupe.upsert_extracted(db, extracted, make_savings(), changed, AppConfig(), NOW)
        row = db.get_deal(key)
        assert row["product_identity_url"] == first and row["image_url"] == ""
        assert row["asin"] == (A if store == "Amazon" else "")


def test_offline_backfill_uses_stored_links_and_sightings_once_with_cap():
    with closing(DB(":memory:")) as db:
        cfg = AppConfig(images_asin_backfill_per_run=2)
        amazon = seed(db, "amazon", canonical_url="https://slickdeals.net/f/253", link_as_posted=
                      "https://redirect.viglink.com?u=" + quote(URL, safe=""), amazon_asin_tried=True,
                      photo_asin=B, asin_source="title_match", image_url=PIC)
        walmart = seed(db, "walmart", store="Walmart", canonical_url="https://hip2save.com/deals/skirt")
        db.add_sighting(walmart["id"], source_name="hip2save", source_url=WALMART,
                        content_hash="fact", seen_at=NOW, final_price=20)
        seed(db, "missing", canonical_url="https://dealsofamerica.com/gloves/253.htm")
        before = [tuple(r) for r in db.conn.execute("SELECT * FROM sightings")]
        assert photos.backfill_identities(db, db.all_deals(), cfg, None) == {"tried": 2, "found": 2}
        row = db.get_deal("amazon")
        assert row["asin"] == A and not row["photo_asin"] and not row["image_url"]
        assert row["asin_source"] == "link"
        assert row["canonical_url"] == amazon["canonical_url"]
        assert db.get_deal("walmart")["product_identity_url"] == WALMART
        assert photos.backfill_identities(db, db.all_deals(), cfg, None) == {"tried": 1, "found": 0}
        after = list(db.conn.iterdump())
        assert photos.backfill_identities(db, db.all_deals(), cfg, None) == {"tried": 0, "found": 0}
        assert list(db.conn.iterdump()) == after
        assert [tuple(r) for r in db.conn.execute("SELECT * FROM sightings")] == before


def test_backfill_deadline_ambiguity_and_dead_rows():
    with closing(DB(":memory:")) as db:
        seed(db, "ambiguous", canonical_url=URL, link_as_posted=f"https://amazon.com/dp/{B}")
        seed(db, "dead", canonical_url=URL, status="dead")
        db.update_deal("dead", status="dead")
        seed(db, "shared", canonical_url=URL, link_shared=True)
        assert photos.backfill_identities(db, db.all_deals(), AppConfig(), 0) == {"tried": 0, "found": 0}
        assert photos.backfill_identities(db, db.all_deals(), AppConfig(), None) == {"tried": 1, "found": 0}
        assert not any(d.get("product_identity_url") for d in db.all_deals())


def test_existing_capped_retailer_path_uses_identity_without_changing_click_link(monkeypatch):
    with closing(DB(":memory:")) as db:
        cfg = AppConfig()
        for n in range(3):
            seed(db, str(n), store="Walmart", canonical_url=f"https://hip2save.com/deals/skirt-{n}",
                 product_identity_url=f"https://www.walmart.com/ip/{n + 100}")
        seed(db, "unrelated", store="Walmart", canonical_url="https://walmart.com/ip/999")
        calls = []
        def handler(request):
            calls.append(str(request.url))
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /\n")
            return httpx.Response(200, text='<meta property="og:image" content="https://i5.walmartimages.com/skirt.jpg">')
        fetcher = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handler), sleep_fn=lambda _: None)
        monkeypatch.setattr(pipeline, "_PRODUCT_IMAGE_PER_RUN", 2)
        try:
            assert pipeline._backfill_product_images(db, fetcher, None, cfg, identity_only=True) == 2
            assert calls == ["https://www.walmart.com/robots.txt", "https://www.walmart.com/ip/100", "https://www.walmart.com/ip/101"]
            assert db.get_deal("0")["canonical_url"] == "https://hip2save.com/deals/skirt-0"
            assert pipeline._backfill_product_images(db, fetcher, None, cfg, identity_only=True) == 1
            count = len(calls)
            assert pipeline._backfill_product_images(db, fetcher, None, cfg, identity_only=True) == 0
            assert len(calls) == count
        finally:
            fetcher.close()


def test_identity_reuses_capped_keepa_photo_pass_and_rerun_makes_no_extra_call(monkeypatch):
    monkeypatch.setenv("KEEPA_KEY", "offline-test-key")
    with closing(DB(":memory:")) as db:
        cfg = AppConfig(feature_flags={"keepa": True}, min_pct=50, site_show_from_pct=50)
        post, extracted = parsed(source_post("slickdeals_frontpage", db), cfg)
        row = dedupe.upsert_extracted(db, extracted, compute_savings(extracted), post, cfg, NOW)["payload"]
        db.update_deal(row["dedupe_key"], payload=dict(row, price_rechecked_at=util.iso(NOW)))
        KeepaBudget(1000, 0, NOW, db).save(db)
        calls = []
        def handler(request):
            calls.append(request.url.path)
            assert request.url.path == "/product" and request.url.params["asin"] == A
            return httpx.Response(200, json={"products": [{"asin": A, "imagesCSV": "product.jpg"}],
                                            "tokensConsumed": 1, "tokensLeft": 999})
        fetcher = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handler), sleep_fn=lambda _: None)
        monkeypatch.setattr(pipeline, "_IMAGE_BACKFILL_PER_RUN", 1)
        try:
            pipeline._recheck_prices(db, cfg, NOW, fetcher=fetcher)
            assert db.get_deal(row["dedupe_key"])["image_url"] == PIC
            assert calls == ["/product"]
            counts = [db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("deals", "sightings", "price_history")]
            dedupe.upsert_extracted(db, extracted, compute_savings(extracted), post, cfg, NOW)
            pipeline._recheck_prices(db, cfg, NOW, fetcher=fetcher)
            assert calls == ["/product"]
            assert [db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("deals", "sightings", "price_history")] == counts
        finally:
            fetcher.close()


def test_private_summary_counts_recovered_sources_once_and_missing_photos():
    rows = [dict(dedupe_key="a", status="alive", asin=A,
                 sightings=[{"source_name": "slickdeals_frontpage"}]),
            dict(dedupe_key="b", status="alive", product_identity_url=WALMART,
                 image_url="https://hip2save.com/skirt.jpg", sightings=[{"source_name": "hip2save"}]),
            dict(dedupe_key="c", status="alive"), dict(dedupe_key="d", status="dead", asin=B)]
    assert photos.identity_summary({"a": False, "b": False}, rows) == {"recovered_identity": 2,
        "by_source": {"hip2save": 1, "slickdeals_frontpage": 1}, "still_missing": 2}
    assert photos.identity_summary({}, rows)["by_source"] == {"hip2save": 1}
    assert photos.identity_summary({"a": True, "b": True}, rows)["recovered_identity"] == 0


@pytest.mark.parametrize("own_field", ["canonical_url", "link_as_posted"])
def test_new_direct_keepa_identity_is_not_counted_as_recovered(own_field):
    row = dict(dedupe_key="keepa", status="alive", asin=A, product_identity_url=URL,
               sightings=[{"source_name": "keepa_deals"}])
    row[own_field] = f"https://amazon.com/gp/product/{A}?tag=source-20"
    assert photos.identity_summary({}, [row]) == {
        "recovered_identity": 0, "by_source": {}, "still_missing": 1}
    assert photos.identity_summary({"keepa": False}, [row])["recovered_identity"] == 1
