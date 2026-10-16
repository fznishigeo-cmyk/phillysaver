"""Shared social copy: deterministic title cleanup and network formats."""

import pytest

from dealradar.config import load_config
from dealradar.publish import meta, pinterest, telegram_pub
from dealradar.publish.social import social_title


EXAMPLES = [
    ("KOBE Range Hoods RAX2130SQB-WM-2 Wall Mount Range Hood, Stainless Steel",
     "KOBE Range Hoods", "KOBE Wall Mount Range Hood", 82, "Amazon"),
    ("Kenneth Cole REACTION Women's Card Wedge, Soft Gold PU, 8",
     "Kenneth Cole REACTION", "Kenneth Cole REACTION Women's Card Wedge", 85, "Amazon"),
    ("HIKVISION DS-2DP3236ZIXS-D/440 32MP 360 Pan Camera",
     "HIKVISION", "Hikvision 32MP 360 Pan Camera", 76, "Woot"),
    ("Vantage Apparel Heather Jersey Men's Team 1/4 Zip, Tcu Horned Frogs, XL",
     "Vantage Apparel", "Vantage Apparel Heather Jersey Men's Team 1/4 Zip", 86, "Amazon"),
]


@pytest.mark.parametrize("item,brand,title,pct,store", EXAMPLES)
def test_real_draft_titles_and_network_copy(item, brand, title, pct, store):
    assert social_title(item, brand) == title
    assert social_title(item, "") == title
    deal = {"item": item, "brand": brand, "store": store, "pct_at_checkout": pct,
            "image_url": "https://images.example/product.jpg"}
    cfg = load_config()
    cfg.meta["instagram"]["hashtags"] = "#deals #wowpriced"
    path = "/deal/product-abcd1234/"
    def linked(network):
        return (f"🔥 {pct}% OFF: {title}\n"
                f"At {store}, verified by wowpriced: https://wowpriced.com{path}"
                f"?utm_source={network}&utm_medium=social&utm_campaign=deal\n#ad")
    assert meta.caption(deal, path, cfg, "facebook") == linked("facebook")
    assert telegram_pub.format_message(deal, path) == linked("telegram")
    assert meta.caption(deal, path, cfg, "instagram") == (
        linked("instagram").removesuffix("\n#ad") +
        "\nTap the link in our bio for today's deals.\n#deals #wowpriced\n#ad")
    pin = pinterest.pin_payload(deal, path, "board", "#ad")
    assert pin["title"] == f"{title} | Deal"
    assert pin["description"] == f"{title}. Deal on wowpriced. #ad"
    assert pin["alt_text"] == item


@pytest.mark.parametrize("model", ["B0C1234XYZ", "ABC12345", "1234567890", "12345-67890",
                                   "(RAX2130SQB-WM-2)", "DS-2DP3236ZIXS-D/440"])
def test_model_and_sku_tokens_removed(model):
    assert social_title(f"LEGO {model} Building Set, Blue", "LEGO") == "LEGO Building Set"


def test_shopper_specs_and_short_model_tokens_stay():
    item = 'HP X200 32MP 360 1/4 4K 65" 2-Pack 8-piece 128GB 1080p 10-inch'
    assert social_title(item, "HP") == item


def test_brand_case_acronyms_and_repeated_brand_words():
    assert social_title("HIKVISION HIKVISION Camera", "HIKVISION") == "Hikvision Camera"
    assert social_title("KOBE LEGO HP SDRAM Set", "KOBE LEGO HP SDRAM") == "KOBE LEGO HP SDRAM Set"
    assert social_title("HIKVISION Camera", "") == "Hikvision Camera"
    assert social_title(EXAMPLES[0][0], "KOBE") == "KOBE Wall Mount Range Hood"


def test_word_limit_and_fallbacks():
    item = "Long product name " * 30
    title = social_title(item, "")
    assert 0 < len(title) <= 70 and item.startswith(title)
    assert title.split()[-1] in item.split()
    assert social_title("B0C1234XYZ", "") == "B0C1234XYZ"
    assert social_title(", Original name", "") == ", Original name"
    assert social_title("", "") == social_title("   ", "") == "Deal"
    assert social_title("$19.99 USD 50", "") == "Deal"
    assert social_title("x" * 100, "") == "x" * 70


def test_all_networks_remove_prices_even_from_fallbacks_and_disclosures():
    cfg = load_config()
    cfg.meta["disclosure"] = "#ad $8"
    cfg.meta["instagram"]["hashtags"] = "#deals USD 60"
    for item in ('LEGO B0C1234XYZ 2-Pack $19.99 £30 €40 USD 50 100 USD, Blue', '$19.99'):
        deal = {"item": item, "brand": "LEGO", "store": "Amazon $9.99",
                "pct_at_checkout": 80, "final_price": 12.34, "regular_price": 56.78,
                "image_url": "https://images.example/product.jpg"}
        path = "/deal/product-abcd1234/"
        pin = pinterest.pin_payload(deal, path, "board", "#ad $8")
        texts = [meta.caption(deal, path, cfg, "facebook"),
                 meta.caption(deal, path, cfg, "instagram"),
                 telegram_pub.format_message(deal, path, "#ad $8"),
                 pin["title"], pin["description"]]
        for text in texts:
            assert all(value not in text for value in
                       ("$", "£", "€", "USD", "19.99", "30", "40", "50", "100", "60",
                        "9.99", "12.34", "56.78", "B0C1234XYZ"))
        assert all(text.endswith("#ad") for text in texts[:3] + [pin["description"]])
        assert pin["alt_text"] == (pinterest._without_prices(item) or "Product photo")
