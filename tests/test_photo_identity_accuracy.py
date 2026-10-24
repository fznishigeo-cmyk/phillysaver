"""A matching title must establish the exact product before borrowing a photo."""

from contextlib import closing

import pytest
import httpx

from dealradar import dedupe, photo_recovery as photos, util
from dealradar.db import DB
from tests.test_dedupe import NOW, make_cfg, make_extracted, make_post, make_savings
from tests.test_identity_accuracy import DISTINCT_ITEMS
from tests.test_photos_every_deal import A, URL, product, search, search_run, seed


@pytest.mark.parametrize("first,second", [
    ("AA Batteries 4 Pack", "AA Batteries 8 Pack"),
    ("Acme Disposable Gloves 100 Count", "Acme Disposable Gloves 200 Count"),
    ("Crayola Washable Markers 40ct", "Crayola Washable Markers 64ct"),
    ("Acme Paper Towels 4pk", "Acme Paper Towels 8pk"),
])
def test_different_quantity_roundup_cannot_borrow_its_one_product_link(first, second):
    post = make_post(text=f"Two pack sizes: {first} and {second}. Buy one: {URL}")
    post.links = [URL]
    post.parsed = [(first, ""), (second, "")]
    extracted = make_extracted(store="Amazon", item=first)
    assert photos.post_identity(extracted, post) == ""
    with closing(DB(":memory:")) as db:
        result = dedupe.upsert_extracted(db, extracted, make_savings(), post, make_cfg(), NOW)
        assert not result["payload"]["asin"]
        assert not result["payload"].get("product_identity_url")
        replay = dedupe.upsert_extracted(db, extracted, make_savings(), post, make_cfg(), NOW)
        assert replay["deal_id"] == result["deal_id"]
        assert len(db.all_deals()) == 1
        assert len(db.sightings_for(result["deal_id"])) == 1


def test_pack_identity_changes_preserve_legacy_key_and_replay():
    assert util.normalize_item("AA Batteries 4 Pack") == "aa batteries"
    assert util.normalize_item("AA Batteries 8 Pack") == "aa batteries"
    key = "si|kohls|aa batteries"
    post = make_post(text="AA Batteries 4 Pack for $25")
    extracted = make_extracted(item="AA Batteries 4 Pack")
    with closing(DB(":memory:")) as db:
        first = dedupe.upsert_extracted(db, extracted, make_savings(), post, make_cfg(), NOW)
        assert first["dedupe_key"] == key
        for _ in range(2):
            replay = dedupe.upsert_extracted(db, extracted, make_savings(), post, make_cfg(), NOW)
            assert replay["dedupe_key"] == key and not replay["is_new_deal"]
        assert len(db.all_deals()) == 1
        assert db.price_stats(key)[0] == len(db.sightings_for(first["deal_id"])) == 1


@pytest.mark.parametrize("qualifier", "pro max plus ultra mini lite air se".split())
@pytest.mark.parametrize("reverse", [False, True])
def test_photo_title_cannot_borrow_base_or_qualified_sibling(qualifier, reverse):
    names = ["Acme Wireless Bluetooth Headphones with Active Noise Cancellation",
             f"Acme {qualifier} Wireless Bluetooth Headphones with Active Noise Cancellation"]
    if reverse:
        names.reverse()
    deal = {"item": names[0], "brand": "Acme", "final_price": 100}
    assert photos.title_match(deal, {A: product(names[1])}, .85) == ""


@pytest.mark.parametrize("first,second", DISTINCT_ITEMS)
def test_photo_title_keeps_model_generation_and_quantity_veto(first, second):
    deal = {"item": first, "final_price": 100}
    assert photos.title_match(deal, {A: product(second)}, .85) == ""


@pytest.mark.parametrize("qualifier", "pro max plus ultra mini lite air se".split())
def test_photo_title_still_matches_qualified_product_wording(qualifier):
    title = f"Acme {qualifier} Wireless Bluetooth Headphones Active Noise Cancellation"
    deal = {"item": title + " Black", "brand": "Acme", "final_price": 100}
    assert photos.title_match(deal, {A: product(title)}, .85) == A


def test_rejected_photo_variant_keeps_facts_and_makes_no_rerun_requests(search):
    db, _, _, calls, answers = search
    title = "Acme Wireless Bluetooth Headphones with Active Noise Cancellation"
    seed(db, item=title, final_price=100)
    before = db.get_deal("a")
    answers["/product"] = httpx.Response(200, json={
        "products": [product(title + " Plus")], "tokensConsumed": 1, "tokensLeft": 988})
    assert search_run(search)["matched"] == 0
    after = db.get_deal("a")
    # The seed's derived ranking score is not a stored deal fact.
    changed = {"photo_search_tried", "score"}
    assert {k: v for k, v in after.items() if k not in changed} == {
        k: v for k, v in before.items() if k not in changed}
    assert not after["image_url"] and not after.get("photo_asin") and not after["asin"]
    assert calls == ["/query", "/product"]
    assert search_run(search)["searched"] == 0
    assert search_run(search, "next")["searched"] == 0
    assert calls == ["/query", "/product"]


@pytest.mark.parametrize("detail", ["8 Pack", "30-hour battery", "2024", "6 piece"])
@pytest.mark.parametrize("reverse", [False, True])
def test_photo_extra_numbers_are_not_identity_conflicts(detail, reverse):
    names = ["Acme Durable Wireless Bluetooth Stereo Headphones with Active Noise Cancellation",
             "Acme Durable Wireless Bluetooth Stereo Headphones with Active Noise Cancellation " + detail]
    if reverse:
        names.reverse()
    assert photos.title_match({"item": names[0], "brand": "Acme", "final_price": 100},
                              {A: product(names[1])}, .85) == A


@pytest.mark.parametrize("first,second", [
    ("Duracell AA Batteries", "Duracell AA Batteries 8 Pack"),
    ("Crest 3D White Toothpaste 4 Pack", "Crest 3D White Toothpaste 4 Count"),
])
def test_single_product_post_allows_compatible_count_wording(first, second):
    post = make_post(text=f"{first}: {URL}")
    post.links = [URL]
    post.parsed = [(first, ""), (second, "")]
    assert photos.post_identity(make_extracted(store="Amazon", item=first), post) == URL
