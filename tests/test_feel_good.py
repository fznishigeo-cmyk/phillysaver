"""Operator 2026-10-23 ~00:05Z: "women buy beauty products, home and kitchen ...
items that will make them feel good ... not very pricey stuff"."""
import copy

import pytest

from dealradar import appeal, rank
from dealradar.config import load_config
from tests.history_pins import pre_feel_good, pre_feel_good_raw
from tests.test_what_people_buy import NOW, deal

SHIPPED = load_config()


def test_shipped_values():
    options = SHIPPED.front_page["appeal"]
    factors = options["category_factors"]
    assert (factors["beauty"], factors["home"], factors["kitchen"], factors["fashion"], factors["electronics"]) \
        == (1.35, 1.3, 1.3, 1.15, 1.0)
    assert options["want"]["group_factors"] == {"allure": 1.35, "gadgets": 1.3, "feel good": 1.25}
    everyday = SHIPPED.front_page["everyday_price"]
    assert (everyday["full_until"], everyday["floor_from"], everyday["floor_factor"]) == (30, 75, .45)
    # One home per word: the moved words left their old groups.
    groups = options["want"]["groups"]
    words = [w for name, ws in groups.items() if name != "allure" for w in ws]
    assert len(words) == len(set(words))
    assert "perfume" not in groups["feel good"] and "perfume" in groups["allure"]


def test_feel_good_lifts_and_adult_titles_never_do():
    cfg = copy.deepcopy(SHIPPED)
    cfg.front_page["popular_affordable"]["enabled"] = False
    cfg.appeal_score = {}
    for title in ("Yankee Candle Large Jar", "Fuzzy Socks 5 Pack", "Claw Clip Set", "Satin Pillowcase 2 Pack",
                  "Heatless Curls Headband", "Essential Oil Diffuser"):
        found, _, factor = appeal.signals(deal(item=title), cfg, NOW)
        assert "feel good" in found and factor == 1.25, title
    # Allure keeps its stronger lift; a robe that is also allure lifts at 1.35.
    assert appeal.signals(deal(item="Women's Satin Robe"), cfg, NOW)[2] == 1.35
    for title in ("Adult candle massage oil", "Fuzzy socks sex toy set", "Vibrator bath bombs"):
        assert appeal.signals(deal(item=title), cfg, NOW)[2] == 1.0, title


def test_everyday_price_is_cheaper_now():
    options = SHIPPED.front_page["everyday_price"]
    assert rank.everyday_price_factor({"final_price": 30}, options) == 1.0
    assert rank.everyday_price_factor({"final_price": 52.5}, options) == pytest.approx(.725)
    assert rank.everyday_price_factor({"final_price": 75}, options) == .45


def test_pins_restore_the_previous_values():
    cfg = pre_feel_good(load_config())
    options = cfg.front_page["appeal"]
    assert options["category_factors"]["beauty"] == options["category_factors"]["electronics"] == 1.15
    assert "feel good" not in options["want"]["groups"] and "feel good" not in options["want"]["group_factors"]
    assert "candle" in options["want"]["groups"]["home"]
    assert cfg.front_page["everyday_price"]["full_until"] == 40
    raw = pre_feel_good_raw(copy.deepcopy(load_config().raw))
    assert raw["publish"]["front_page"]["everyday_price"]["floor_factor"] == .55
