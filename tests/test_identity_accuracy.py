"""Product identity must outrank similar wording and a shared promo code."""

from contextlib import closing
from datetime import timedelta

import pytest

from dealradar import dedupe, util, verify
from dealradar.db import DB
from tests.test_dedupe import NOW, make_cfg, make_extracted, make_post, make_savings


DISTINCT_ITEMS = [
    ("Sony WH-1000XM5 Wireless Headphones", "Sony WH-1000XM4 Wireless Headphones"),
    ("Apple iPhone 15 Smartphone", "Apple iPhone 15 Pro Smartphone"),
    ("Apple iPhone 15 128GB Unlocked Black Smartphone",
     "Apple iPhone 16 128GB Unlocked Black Smartphone"),
    ("Apple AirPods Pro 2 Wireless Earbuds", "Apple AirPods Pro Wireless Earbuds"),
    ("Samsung Galaxy S24 Smartphone", "Samsung Galaxy S24 Ultra Smartphone"),
    ("Samsung Galaxy S24 5G Smartphone", "Samsung Galaxy S25 5G Smartphone"),
    ("Ninja AF101 Air Fryer", "Ninja AF161 Air Fryer"),
    ("LEGO Creator 31174 Building Set", "LEGO Creator 31147 Building Set"),
    ("AA Batteries 4 Pack", "AA Batteries 8 Pack"),
    ("SanDisk Ultra 64GB Memory Card", "SanDisk Ultra 128GB Memory Card"),
    ("Acme Disposable Gloves 100 Count", "Acme Disposable Gloves 200 Count"),
]

SAME_ITEMS = [
    ("Sony WH-1000XM5 Wireless Headphones",
     "Sony WH-1000XM5 Noise Canceling Headphones, 30-Hour Battery, Black"),
    ("Sony WH-1000XM5 Wireless Headphones",
     "Sony WH-1000XM5 Wireless Headphones 30-Hour Battery"),
    ("Apple AirPods Pro 2", "Apple AirPods Pro (2nd Generation)"),
    ("AirPods Pro (2nd Generation)", "AirPods Pro 2"),
    ("Apple AirPods Pro 2", "Apple AirPods Pro 2nd Gen with USB-C"),
    ("Apple AirPods Pro (2nd Generation)", "Apple AirPods Pro 2nd Gen with USB-C"),
    ("Crest 3D White Toothpaste 4 Pack", "Crest 3D White Toothpaste, 4 Count"),
    ("Crest 4 Pack", "Crest, 4 Count"),
    ("Ninja AF101 Air Fryer, 4 Qt", "Ninja AF101 4-Quart Air Fryer, Grey"),
    ("Duracell AA Batteries", "Duracell AA Batteries, 8 Pack"),
    ("LEGO Creator 31174 Retro Telephone", "LEGO Creator 3-in-1 Retro Telephone 31174"),
    ("LEGO 31174 Retro Telephone", "LEGO Creator 3-in-1 Retro Telephone 31174"),
    ("LEGO 31174 Retro Telephone", "LEGO Creator 3-in-1 31174"),
]


def test_reordered_identical_facts_merge_three_sources_and_replay():
    names = ("Ninja AF101 Air Fryer 4 Qt", "Ninja Air Fryer AF101 4 Qt",
             "Air Fryer Ninja AF101 4 Qt")
    with closing(DB(":memory:")) as db:
        results = []
        for i, item in enumerate(names):
            post = make_post(source=("hip2save", "slickdeals_frontpage", "krazy_coupon_lady")[i],
                             uid=str(i), text=f"{item} for $25")
            result = dedupe.upsert_extracted(db, make_extracted(item=item), make_savings(),
                                            post, make_cfg(), NOW)
            results.append(result)
        assert len({result["deal_id"] for result in results}) == 1
        key = "si|kohls|" + util.normalize_item(names[0])
        assert all(result["dedupe_key"] == key for result in results)
        assert verify.assign_tier(db, db.get_deal(key), make_cfg(), NOW) == "corroborated"
        for i, item in enumerate(names):
            post = make_post(source=("hip2save", "slickdeals_frontpage", "krazy_coupon_lady")[i],
                             uid=str(i), text=f"{item} for $25")
            replay = dedupe.upsert_extracted(db, make_extracted(item=item), make_savings(),
                                            post, make_cfg(), NOW)
            assert replay["deal_id"] == results[0]["deal_id"]
        assert len(db.all_deals()) == 1
        assert len(db.sightings_for(results[0]["deal_id"])) == db.price_stats(key)[0] == 3


@pytest.mark.parametrize("legacy_suffix", [False, True])
def test_reordered_facts_find_existing_legacy_keys(legacy_suffix):
    first = "Ninja AF101 Air Fryer 4 Qt"
    later = "Ninja Air Fryer AF101 4 Qt"
    key = dedupe.canonical_key("Kohls", "", first)
    if legacy_suffix:
        key = dedupe._identity_key(key, first)
    with closing(DB(":memory:")) as db:
        db.insert_deal(dedupe_key=key, store="Kohls", item=first,
                       normalized_item=util.normalize_item(first), canonical_url="",
                       payload={"item": first, "store": "Kohls", "conditions": []},
                       final_price=25, pct_at_checkout=75, abs_savings=75,
                       online_only="true", expires_at="", now=NOW)
        result = ingest(db, later, 1)
        assert result["dedupe_key"] == key
        assert not result["is_new_deal"]
        assert len(db.all_deals()) == 1


@pytest.mark.parametrize("first,second,old_facts", [
    ("HP 14 inch Laptop", "HP 14.0 inch Laptop", ("14in", "14.0in")),
    ("CeraVe Moisturizing Cream 16 oz", "CeraVe Moisturizing Cream 16.0 oz", ("16oz", "16.0oz")),
])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("legacy_suffix", [False, True])
def test_equivalent_decimal_units_reuse_stored_key(first, second, old_facts, reverse, legacy_suffix):
    if reverse:
        first, second = second, first
    key = dedupe.canonical_key("Kohls", "", first)
    if legacy_suffix:
        # These suffixes were written before decimal spellings were canonical.
        key += "|identity|" + old_facts[reverse]
    with closing(DB(":memory:")) as db:
        original_id = db.insert_deal(
            dedupe_key=key, store="Kohls", item=first,
            normalized_item=util.normalize_item(first), canonical_url="",
            payload={"item": first, "store": "Kohls", "conditions": []},
            final_price=25, pct_at_checkout=75, abs_savings=75,
            online_only="true", expires_at="", now=NOW)
        result = ingest(db, second, 1)
        assert result["deal_id"] == original_id
        assert result["dedupe_key"] == key
        assert not result["is_new_deal"]
        replay = ingest(db, second, 1)
        assert replay["deal_id"] == original_id
        assert len(db.all_deals()) == 1
        assert len(db.sightings_for(original_id)) == db.price_stats(key)[0] == 1


@pytest.mark.parametrize("first,second", [
    ("HP 14.0 inch Laptop", "HP 14.1 inch Laptop"),
    ("CeraVe Moisturizing Cream 16.0 oz", "CeraVe Moisturizing Cream 16.1 oz"),
])
def test_different_decimal_units_keep_separate_prices(first, second):
    with closing(DB(":memory:")) as db:
        original = ingest(db, first, 1, ("DEVICE20",))
        later = ingest(db, second, 2, ("DEVICE20",))
        assert original["deal_id"] != later["deal_id"]
        assert db.get_deal(original["dedupe_key"])["final_price"] == 21.0
        assert db.get_deal(later["dedupe_key"])["final_price"] == 22.0


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("codes", [(), ("DEVICE20",)], ids=["fuzzy", "shared-code"])
@pytest.mark.parametrize("separator", [", ", " "])
def test_connective_plus_battery_feature_merges_and_replays(reverse, codes, separator):
    names = ["Sony WH-1000XM5 Wireless Headphones, 30-Hour Battery",
             f"Sony WH-1000XM5 Wireless Headphones{separator}plus 30-Hour Battery"]
    if reverse:
        names.reverse()
    with closing(DB(":memory:")) as db:
        results = []
        for index, item in enumerate(names):
            post = make_post(source=("hip2save", "slickdeals_frontpage")[index],
                             uid=str(index), text=f"{item} for $25")
            result = dedupe.upsert_extracted(
                db, make_extracted(item=item, codes=list(codes)), make_savings(),
                post, make_cfg(), NOW)
            results.append(result)
        assert results[0]["deal_id"] == results[1]["deal_id"]
        key = results[0]["dedupe_key"]
        assert key == "si|kohls|" + util.normalize_item(names[0])
        assert verify.assign_tier(db, db.get_deal(key), make_cfg(), NOW) == "corroborated"
        for index, item in enumerate(names):
            post = make_post(source=("hip2save", "slickdeals_frontpage")[index],
                             uid=str(index), text=f"{item} for $25")
            replay = dedupe.upsert_extracted(
                db, make_extracted(item=item, codes=list(codes)), make_savings(),
                post, make_cfg(), NOW)
            assert replay["dedupe_key"] == key
        assert len(db.all_deals()) == 1
        assert len(db.sightings_for(results[0]["deal_id"])) == db.price_stats(key)[0] == 2


@pytest.mark.parametrize("first,second", [
    ("Sony WH-1000XM5 Wireless Headphones, plus 30-Hour Battery",
     "Sony WH-1000XM5 Plus Wireless Headphones, plus 30-Hour Battery"),
    ("Samsung Galaxy S24 Smartphone, 30-Hour Battery",
     "Samsung Galaxy S24 Plus Smartphone, plus 30-Hour Battery"),
])
@pytest.mark.parametrize("reverse", [False, True])
def test_connective_battery_feature_keeps_real_plus_model(first, second, reverse):
    if reverse:
        first, second = second, first
    with closing(DB(":memory:")) as db:
        original = ingest(db, first, 1, ("DEVICE20",))
        later = ingest(db, second, 2, ("DEVICE20",))
        assert original["deal_id"] != later["deal_id"]
        assert db.get_deal(original["dedupe_key"])["final_price"] == 21.0
        assert db.get_deal(later["dedupe_key"])["final_price"] == 22.0


def test_occupied_identity_suffix_gets_a_free_key_and_replays():
    """Persisted keys are opaque: a legacy row can already occupy a suffix."""
    with closing(DB(":memory:")) as db:
        first = ingest(db, "Duracell AA Batteries 4 Pack", 1)
        item = "Duracell AA Batteries 8 Pack"
        occupied = dedupe._identity_key(first["dedupe_key"], item)
        other = "Duracell AA Batteries 12 Pack"
        db.insert_deal(dedupe_key=occupied, store="Kohls", item=other,
                       normalized_item=util.normalize_item(other), canonical_url="",
                       payload={"item": other, "store": "Kohls", "conditions": []},
                       final_price=30, pct_at_checkout=70, abs_savings=70,
                       online_only="true", expires_at="", now=NOW)
        result = ingest(db, item, 2)
        assert result["is_new_deal"]
        assert result["dedupe_key"] not in {first["dedupe_key"], occupied}
        replay = ingest(db, item, 2)
        assert replay["deal_id"] == result["deal_id"] and not replay["is_new_deal"]
        assert len(db.all_deals()) == 3
        assert db.get_deal(occupied)["final_price"] == 30
        assert len(db.sightings_for(result["deal_id"])) == db.price_stats(result["dedupe_key"])[0] == 1


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("codes", [(), ("DEVICE20",)])
def test_shared_resolution_cannot_hide_conflicting_webcam_models(reverse, codes):
    offers = [("Logitech C920 1080p HD USB Desktop Webcam", 30),
              ("Logitech C922 Pro Stream 1080p Webcam", 50)]
    if reverse:
        offers.reverse()
    with closing(DB(":memory:")) as db:
        results = []
        for i, (item, price) in enumerate(offers):
            post = make_post(uid=str(i), text=f"{item} ${price}")
            post.published_at = NOW + timedelta(minutes=i)
            results.append(dedupe.upsert_extracted(
                db, make_extracted(item=item, codes=list(codes)),
                make_savings(final=price), post, make_cfg(), NOW))
        assert results[0]["deal_id"] != results[1]["deal_id"]
        assert len(db.all_deals()) == 2
        for result, (_, price) in zip(results, offers):
            assert db.get_deal(result["dedupe_key"])["final_price"] == price


@pytest.mark.parametrize("first,second", SAME_ITEMS)
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("codes", [(), ("DEVICE20",)], ids=["fuzzy", "shared-code"])
def test_extra_title_facts_still_merge_corroborate_and_replay(first, second, reverse, codes):
    if reverse:
        first, second = second, first
    with closing(DB(":memory:")) as db:
        results = []
        for index, item in enumerate((first, second)):
            post = make_post(source=("hip2save", "slickdeals_frontpage")[index],
                             uid=str(index), text=f"{item} for $25")
            extracted = make_extracted(item=item, codes=list(codes))
            result = dedupe.upsert_extracted(db, extracted, make_savings(), post, make_cfg(), NOW)
            results.append(result)
            replay = dedupe.upsert_extracted(db, extracted, make_savings(), post, make_cfg(), NOW)
            assert replay["deal_id"] == result["deal_id"]
        assert results[0]["deal_id"] == results[1]["deal_id"]
        row = db.get_deal(results[0]["dedupe_key"])
        assert verify.assign_tier(db, row, make_cfg(), NOW) == "corroborated"
        assert len(db.all_deals()) == 1
        assert len(db.sightings_for(row["id"])) == db.price_stats(row["dedupe_key"])[0] == 2


def test_identity_suffix_can_merge_missing_count_without_renaming_or_adding_rows():
    with closing(DB(":memory:")) as db:
        original = ingest(db, "Duracell AA Batteries 4 Pack", 1)
        separate = ingest(db, "Duracell AA Batteries 8 Pack", 2)
        assert "|identity|" in separate["dedupe_key"]
        # The original row may be gone; a less detailed title can still find
        # the existing suffixed row by name, without manufacturing a conflict.
        db.conn.execute("DELETE FROM deals WHERE id = ?", (original["deal_id"],))
        later = ingest(db, "Duracell AA Batteries", 3)
        assert later["deal_id"] == separate["deal_id"]
        assert len(db.all_deals()) == 1


@pytest.mark.parametrize("other", [
    "Apple AirPods Pro Generation 2", "Apple AirPods Pro Gen 2",
])
def test_generation_label_is_an_explicit_fact(other):
    assert not util.item_identity_conflict("Apple AirPods Pro 2", other)
    assert util.item_identity_conflict("Apple AirPods Pro 3", other)


def test_matching_one_unit_does_not_hide_another_conflicting_unit():
    assert util.item_identity_conflict("Acme Lotion 12 oz 2 Pack", "Acme Lotion 16 oz 2 Pack")
    assert util.item_identity_conflict("Acme Panel 10 inch 4 ft", "Acme Panel 20 inch 4 ft")


@pytest.mark.parametrize("item", [
    "Sony WH-1000XM5 Replacement Ear Pads for Headphones",
    "Sony WH-1000XM5 Protective Case for Headphones",
    "Sony WH-1000XM5 Charging Cable for Headphones",
    "Sony WH-1000XM5 Wall Mount for Headphones",
])
def test_shared_model_does_not_merge_product_accessories(item):
    with closing(DB(":memory:")) as db:
        first = ingest(db, "Sony WH-1000XM5 Wireless Headphones", 1)
        later = ingest(db, item, 2)
        assert later["deal_id"] != first["deal_id"]


@pytest.mark.parametrize("accessory", ["Display Stand", "Lighting Kit"])
def test_lego_set_number_does_not_merge_accessories(accessory):
    with closing(DB(":memory:")) as db:
        first = ingest(db, "LEGO 31174 Retro Telephone", 1)
        later = ingest(db, "LEGO 31174 " + accessory, 2)
        assert later["deal_id"] != first["deal_id"]


@pytest.mark.parametrize("accessory", ["Charging Cable", "Silicone Tips"])
def test_generation_alias_cannot_merge_an_accessory(accessory):
    with closing(DB(":memory:")) as db:
        first = ingest(db, "Apple AirPods Pro 2", 1)
        later = ingest(db, "Apple AirPods Pro 2 " + accessory, 2)
        assert later["deal_id"] != first["deal_id"]


def test_marketing_number_is_not_a_stated_dimension():
    assert not util.item_identity_conflict("LEGO Creator 3-in-1 Retro Telephone 31174",
                                          "LEGO Creator Retro Telephone 31174, 6 inch")


def ingest(db, item, number, codes=(), url=""):
    post = make_post(uid=str(number), text=f"Offer {number} for {item}")
    post.published_at = NOW + timedelta(minutes=number)
    return dedupe.upsert_extracted(
        db, make_extracted(item=item, codes=list(codes), product_url=url),
        make_savings(final=20.0 + number), post, make_cfg(), NOW)


@pytest.mark.parametrize("codes", [(), ("DEVICE20",)], ids=["fuzzy", "shared-code"])
@pytest.mark.parametrize("first,second", DISTINCT_ITEMS)
@pytest.mark.parametrize("urls", [("", ""), ("https://www.kohls.com/product/prd-12345", ""),
                                  ("", "https://www.kohls.com/product/prd-12345")],
                         ids=["no-urls", "old-url", "new-url"])
def test_different_models_counts_and_capacities_keep_their_prices(first, second, codes, urls):
    with closing(DB(":memory:")) as db:
        original = ingest(db, first, 1, codes, urls[0])
        later = ingest(db, second, 2, codes, urls[1])
        assert later["is_new_deal"] is True
        assert original["deal_id"] != later["deal_id"]
        assert db.get_deal(original["dedupe_key"])["final_price"] == 21.0
        assert db.get_deal(later["dedupe_key"])["final_price"] == 22.0
        for number, (item, result) in enumerate(((first, original), (second, later)), 1):
            replay = ingest(db, item, number, codes, urls[number - 1])
            assert replay["deal_id"] == result["deal_id"]
            assert replay["is_new_deal"] is False
            assert len(db.sightings_for(result["deal_id"])) == 1
            assert db.price_stats(result["dedupe_key"])[0] == 1
        assert len(db.all_deals()) == 2


@pytest.mark.parametrize("qualifier", "pro max plus ultra mini lite air se".split())
@pytest.mark.parametrize("reverse", [False, True])
def test_shared_code_cannot_merge_base_and_qualified_product(qualifier, reverse):
    items = ["Acme Wireless Bluetooth Headphones", f"Acme {qualifier} Wireless Bluetooth Headphones"]
    if reverse:
        items.reverse()
    with closing(DB(":memory:")) as db:
        first = ingest(db, items[0], 1, ("DEVICE20",))
        second = ingest(db, items[1], 2, ("DEVICE20",))
        assert second["is_new_deal"] is True
        assert db.get_deal(first["dedupe_key"])["final_price"] == 21.0


@pytest.mark.parametrize("first,second", [
    ("Ninja Foodi 8qt Air Fryer Black", "Ninja Foodi Air Fryer 8 qt"),
    ("AA Batteries 4 Pack", "Batteries AA 4-Pack"),
    ("SanDisk Ultra 64GB Memory Card", "SanDisk Ultra Memory Card 64 GB"),
    ("Sony WH-1000XM5 Wireless Headphones Black", "Wireless Sony WH-1000XM5 Headphones"),
    ("Acme Women's Cozy Fleece Jacket (S-XL, Black)",
     "Acme Cozy Fleece Jacket for Women (Black, various sizes)"),
])
def test_matching_identity_keeps_duplicate_merges_and_original_key(first, second):
    with closing(DB(":memory:")) as db:
        original = ingest(db, first, 1, ("DEVICE20",))
        legacy_key = "si|kohls|" + util.normalize_item(first)
        assert original["dedupe_key"] == legacy_key
        later = ingest(db, second, 2, ("DEVICE20",))
        assert later["dedupe_key"] == legacy_key
        assert later["is_new_deal"] is False
        assert len(db.all_deals()) == 1
        assert db.get_deal(legacy_key)["final_price"] == 22.0


@pytest.mark.parametrize("first,second,same", [
    ("Acme Pure Botanical Moisturizing Lotion 12 oz", "Acme Pure Botanical Moisturizing Lotion 12 ml", False),
    ("Acme Pure Botanical Moisturizing Lotion 12oz", "Acme Pure Botanical Moisturizing Lotion 12 oz", True),
    ("Acme Pure Botanical Moisturizing Lotion 500ml", "Acme Pure Botanical Moisturizing Lotion 500 ml", True),
    ("SanDisk Ultra Portable USB Storage 1TB", "SanDisk Ultra Portable USB Storage 1 TB", True),
    ("Acme Heavy Duty AA Batteries Pack of 4", "Acme Heavy Duty AA Batteries 4 Pack", True),
    ("Acme Heavy Duty AA Batteries Pack of 4", "Acme Heavy Duty AA Batteries Pack of 8", False),
])
def test_quantity_units_and_spelling_preserve_exact_product(first, second, same):
    with closing(DB(":memory:")) as db:
        original = ingest(db, first, 1, ("DEVICE20",))
        later = ingest(db, second, 2, ("DEVICE20",))
        assert (later["deal_id"] == original["deal_id"]) is same
        assert db.get_deal(original["dedupe_key"])["final_price"] == (22.0 if same else 21.0)


@pytest.mark.parametrize("first,second,same", [
    ("Acme Pure Botanical Moisturizing Lotion 12 l", "Acme Pure Botanical Moisturizing Lotion 12 g", False),
    ("Acme Pure Botanical Moisturizing Lotion 5L", "Acme Pure Botanical Moisturizing Lotion 5 L", True),
    ("Acme Pure Botanical Moisturizing Lotion 5lb", "Acme Pure Botanical Moisturizing Lotion 5 lbs", True),
    ("Acme Outdoor Garden Ceramic Flower Pot 10 inch", "Acme Outdoor Garden Ceramic Flower Pot 10in", True),
])
def test_other_quantity_units_preserve_exact_product(first, second, same):
    with closing(DB(":memory:")) as db:
        original = ingest(db, first, 1, ("DEVICE20",))
        later = ingest(db, second, 2, ("DEVICE20",))
        assert (later["deal_id"] == original["deal_id"]) is same
