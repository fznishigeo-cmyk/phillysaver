"""Shipped price-history settings from before 2026-10-17, for recorded fixtures.

Amazon's email (operator 2026-10-17) turned price history off for every
store; tests recorded before that pin the old values instead of re-pinning.
"""

from dealradar.config import validated_checker


def pre_history_removal(cfg):
    cfg.amazon_history = "show"
    cfg.seo_product_pages_enabled = True
    cfg.store_policy = {store: "history" for store in cfg.store_policy}
    if cfg.raw:
        cfg.checker = validated_checker(dict(cfg.raw.get("checker", {})), "show")
    return cfg


def pre_history_removal_raw(config):
    """The same pins on a raw config.yaml mapping (for tests that write YAML)."""
    config["amazon_history"] = "show"
    config["seo"]["product_pages"]["enabled"] = True
    config["store_policy"] = {store: "history" for store in config.get("store_policy", {})}
    return config


# Task 279 (operator 2026-10-17): Google title, gadget/holiday lifts and adult-only
# exclusions. Fixtures recorded before it keep the earlier shipped values.
PRE_279_EXCLUDE = ["toy", "vibrator", "sex", "adult", "erotic", "intimate wellness",
                   "dildo", "masturbator", "bondage", "fetish", "sex lube", "sexual lubricant"]
PRE_279_SEASONS = ("thanksgiving", "gifts")
PRE_279_EVERYDAY = ["paper towels", "toilet paper", "detergent", "dish soap", "trash bags",
                    "toothpaste", "toothbrush", "deodorant", "razor", "vitamins"]


def _pre_task_279_appeal(appeal):
    if not isinstance(appeal, dict):
        return
    appeal.get("category_factors", {}).pop("electronics", None)
    want = appeal.get("want")
    if isinstance(want, dict):
        want.get("groups", {}).pop("gadgets", None)
        if "everyday" in want.get("groups", {}):
            want["groups"]["everyday"] = list(PRE_279_EVERYDAY)
        if "exclude_words" in want:
            want["exclude_words"] = list(PRE_279_EXCLUDE)
    if isinstance(appeal.get("season"), list):
        appeal["season"] = [w for w in appeal["season"] if w.get("name") in PRE_279_SEASONS]


def _pre_task_279_picks(picks):
    # First-screen mix knobs (same day): brand cap 3, no title brand, no clothing cap.
    if isinstance(picks, dict):
        picks["max_same_brand"] = 3
        for key in ("brand_from_title", "max_clothing_per_window", "mix_window", "mix_slots",
                    "clothing_categories", "clothing_words"):
            picks.pop(key, None)


def pre_task_279(cfg):
    pre_task_282(cfg)  # recorded before task 282
    cfg.home_title = cfg.home_description = ""
    _pre_task_279_appeal(cfg.front_page.get("appeal"))
    _pre_task_279_picks(cfg.front_page.get("top_picks"))
    return cfg


def pre_task_279_raw(config):
    """The same pins on a raw config.yaml mapping."""
    pre_task_282_raw(config)  # recorded before task 282
    for key in ("home_title", "home_description"):
        config.get("site", {}).pop(key, None)
    _pre_task_279_appeal(config.get("publish", {}).get("front_page", {}).get("appeal"))
    _pre_task_279_picks(config.get("publish", {}).get("front_page", {}).get("top_picks"))
    return config


def pre_first_screen_mix(cfg):
    """Only the first-screen mix knobs (2026-10-17), for fixtures recorded before them."""
    _pre_task_279_picks(cfg.front_page.get("top_picks"))
    pre_task_282(cfg)  # recorded before task 282
    return cfg


# Task 284 (operator 2026-10-17 19:20Z): no sweatshirt/jacket lift, bras and
# panties are allure, Christmas waits for the gifts window.
PRE_284_GROUPS = {
    "basics": ["bra", "bras", "bralette", "underwear", "panties", "boxer", "briefs", "socks",
               "leggings", "pajamas", "lingerie", "bodysuit", "bikini", "swimsuit", "slippers",
               "sneakers", "shoes", "hat", "beanie", "hoodie", "sweatshirt"],
    "wear & carry": ["sunglasses", "purse", "handbag", "tote", "backpack", "jewelry",
                     "earrings", "necklace", "bracelet", "jacket", "coat", "dress", "jeans", "pajama set"],
    "allure": ["lingerie", "babydoll", "bralette", "corset", "satin", "silk robe", "sleepwear",
               "nightgown", "cologne", "perfume", "boxer briefs", "swim trunks", "date night"],
}


def _pre_task_284(front_page):
    if not isinstance(front_page, dict):
        return
    if isinstance(front_page.get("demote_words"), list):
        front_page["demote_words"] = [w for w in front_page["demote_words"] if w not in ("christmas", "xmas", "tupperware")]
    ((front_page.get("appeal") or {}).get("want") or {}).pop("group_factors", None)
    groups = ((front_page.get("appeal") or {}).get("want") or {}).get("groups")
    if isinstance(groups, dict):
        groups.update({name: list(words) for name, words in PRE_284_GROUPS.items() if name in groups})


def pre_task_284(cfg):
    _pre_task_284(cfg.front_page)
    return cfg


def pre_task_283(cfg):
    """Recorded before photo-less cards left the public site after 3 hours."""
    cfg.max_photoless_h = None
    return cfg


def pre_task_283_raw(config):
    config.get("publish", {}).get("photos", {}).pop("max_photoless_h", None)
    return config


def pre_task_282(cfg):
    """Recorded before private ratings and configurable allure spacing; no re-pins."""
    pre_task_284(cfg)  # recorded before task 284
    pre_task_283(cfg)  # recorded before task 283
    cfg.home_link_resets = False  # recorded before task 286
    cfg.appeal_score = {}
    for key in ("allure_first", "allure_every"):
        cfg.front_page.get("appeal", {}).pop(key, None)
    # Bestseller boost switched on the same evening (task 255 shadow → on).
    if isinstance(cfg.front_page.get("popular_affordable"), dict):
        cfg.front_page["popular_affordable"]["enabled"] = False
    return cfg


def pre_task_282_raw(config):
    _pre_task_284(config.get("publish", {}).get("front_page"))  # recorded before task 284
    pre_task_283_raw(config)  # recorded before task 283
    config.get("site", {}).pop("home_link_resets", None)  # recorded before task 286
    config.pop("appeal_score", None)
    for key in ("allure_first", "allure_every"):
        config.get("publish", {}).get("front_page", {}).get("appeal", {}).pop(key, None)
    popular = config.get("publish", {}).get("front_page", {}).get("popular_affordable")
    if isinstance(popular, dict):
        popular["enabled"] = False
    return config


# Launch-night tuning (operator 2026-10-17 ~22:10Z, from the live first 24): Tupperware
# demoted, 120 rated candidates, wider clothing words, no bare "satin" allure, gadgets 1.3.
PRE_288_CLOTHING = {"blouse", "quarter zip", "polo", "flannel", "fleece", "romper", "jumpsuit",
                    "tank top", "crop top", "camisole", "bodysuit", "tunic", "kimono", "swimsuit", "bikini"}
PRE_288_ALLURE = ["lingerie", "babydoll", "bralette", "bra", "bras", "panties", "bodysuit", "bikini",
                  "swimsuit", "corset", "satin", "silk robe", "sleepwear", "nightgown", "cologne",
                  "perfume", "boxer briefs", "swim trunks", "date night"]


def pre_task_288(cfg):
    front_page = cfg.front_page
    if isinstance(front_page.get("demote_words"), list):
        front_page["demote_words"] = [w for w in front_page["demote_words"] if w != "tupperware"]
    picks = front_page.get("top_picks") or {}
    if isinstance(picks.get("clothing_words"), list):
        picks["clothing_words"] = [w for w in picks["clothing_words"] if w not in PRE_288_CLOTHING]
    want = (front_page.get("appeal") or {}).get("want") or {}
    if isinstance(want.get("groups"), dict) and "allure" in want["groups"]:
        want["groups"]["allure"] = list(PRE_288_ALLURE)
    if isinstance(want.get("group_factors"), dict):
        want["group_factors"].pop("gadgets", None)
    if cfg.appeal_score:
        cfg.appeal_score = dict(cfg.appeal_score, candidates=80)
    return cfg


def pre_task_290(cfg):
    """Before 2026-10-17 8pm: Amazon results were preview-only and search_boost absent."""
    cfg.search = dict(cfg.search, amazon_results="preview")
    cfg.search_boost = {"enabled": False, "exclude_words": []}
    return cfg
