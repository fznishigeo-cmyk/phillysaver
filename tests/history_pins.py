"""Shipped price-history settings from before 2026-10-17, for recorded fixtures.

Amazon's email (operator 2026-10-17) turned price history off for every
store; tests recorded before that pin the old values instead of re-pinning.
"""

from dealradar.config import validated_checker


def pre_history_removal(cfg):
    pre_task_319(cfg)
    cfg.amazon_history = "show"
    cfg.seo_product_pages_enabled = True
    cfg.store_policy = {store: "history" for store in cfg.store_policy}
    if cfg.raw:
        cfg.checker = validated_checker(dict(cfg.raw.get("checker", {})), "show")
    return cfg


def pre_history_removal_raw(config):
    """The same pins on a raw config.yaml mapping (for tests that write YAML)."""
    pre_task_319_raw(config)
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
    pre_task_290_install(cfg)
    pre_task_291(cfg)
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
    pre_task_290_install_raw(config)
    config.pop("search_analyze", None)  # recorded before task 291
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
    pre_task_290_install(cfg)
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
    pre_task_291(cfg)
    cfg.search = dict(cfg.search, amazon_results="preview")
    cfg.search_boost = {"enabled": False, "exclude_words": []}
    return cfg


def pre_task_290_install(cfg):
    """Recorded before the header install control (task 290); keep every existing byte pin."""
    cfg.pwa_install_button = False
    pre_task_294(cfg)
    return cfg


def pre_task_290_install_raw(config):
    config.pop("tool_buttons_outside", None)
    config.pop("timer_center", None)
    config.pop("search_focus_ring_keyboard_only", None)
    config.pop("checker_similar_typed_words", None)
    config.pop("price_age_minutes", None)
    config.get("affiliate", {}).pop("exact_variant_links", None)
    pre_keepa_focus_finder_raw(config)
    pre_hide_deal_site_links_raw(config)
    config.pop("tool_pages_1020", None)
    config.pop("tool_pages_1020_phone_links", None)
    config.pop("fresh_more_plain", None)
    config.pop("search_buttons_1019", None)
    config.pop("search_discounts_button", None)
    config.pop("card_click_1020", None)
    config.pop("buttons_to_tools", None)
    config.pop("amazon_price_cap", None)
    config.pop("amazon_price_cap_usd", None)
    config.pop("tools_new_tab", None)
    config.pop("checker_similar_heading", None)
    config.pop("tool_faq_wording", None)
    config.pop("tool_pages", None)
    config.pop("timer_pill_ring", None)
    config.pop("phone_timer_raise", None)
    config.pop("tool_pages_hero", None)
    config.pop("discounts_from_link", None)
    config.pop("phone_tagline_count", None)
    config.pop("search_polish_1019", None)
    config.pop("analyze_top_sellers", None)
    config.pop("all_discounts_button", None)
    config.get("site", {}).pop("drop_fresh", None)
    config.get("site", {}).pop("drop_shuffle", None)  # recorded before task 300
    config.get("pwa", {}).pop("install_button", None)
    config.get("site", {}).pop("credit_links", None)
    config.get("site", {}).pop("trending_auto_cards", None)
    config.get("site", {}).pop("click_price_home_fill", None)
    words = config.get("publish", {}).get("front_page", {}).get("demote_words")
    if isinstance(words, list):
        config["publish"]["front_page"]["demote_words"] = [w for w in words if w not in PRE_294_DEMOTE]
    config.pop("search_box_glow", None)
    config.pop("card_tidy", None)
    config.pop("page_polish", None)
    config.pop("same_product_spacing", None)
    config.pop("header_polish", None)
    config.pop("search_more", None)
    config["search_analyze"] = dict(config.get("search_analyze") or {}, lazy=False)
    config.get("search", {}).pop("tokens_per_day", None)  # default 3000, as recorded
    config.get("site", {}).pop("brand_pages", None)
    return config

def pre_task_291(cfg):
    """Before Analyze/pasted links; keep current-config captures without re-pins."""
    # Captured on the 4140a49 release: also before the circling border and the install control.
    cfg.search_circle = False
    pre_task_290_install(cfg)
    cfg.search_analyze = {"enabled": False}
    return cfg


def pre_task_293(cfg):
    """Before related Amazon pages and spelling; preserve recorded release bytes."""
    pre_task_295(cfg)
    cfg.search_more = {"enabled": False}
    # Shipped in the same release: brand pages (task 289) switched on after the operator's preview.
    cfg.brand_pages = False
    return cfg


def pre_task_295(cfg):
    """Before deferred Analyze; preserve every existing publication hash."""
    pre_task_300(cfg)
    pre_task_301(cfg)
    pre_card_tidy(cfg)
    pre_page_polish(cfg)
    pre_same_product_spacing(cfg)
    pre_drop_fresh(cfg)
    pre_header_polish(cfg)
    pre_search_5000(cfg)
    cfg.search_analyze = dict(cfg.search_analyze, lazy=False)
    return cfg


def pre_task_300(cfg):
    """Before clock-driven Trending picks; never re-pin recorded publications."""
    cfg.drop_shuffle = False
    return cfg


def pre_search_5000(cfg):
    """Recorded with 3,000 search tokens a day (raised to 5,000 on 2026-10-18); no re-pins."""
    cfg.search = dict(cfg.search, tokens_per_day=3000)
    return cfg


def pre_task_294(cfg):
    """Before plain source credits, the one-in-ten gone-card limit and the team-merch demote; no re-pins."""
    pre_task_293(cfg)
    cfg.credit_links = True
    cfg.trending_auto_cards = None
    cfg.click_price_home_fill = False
    words = cfg.front_page.get("demote_words")
    if isinstance(words, list):
        cfg.front_page["demote_words"] = [w for w in words if w not in PRE_294_DEMOTE]
    return cfg


# Launch night 2026-10-17: team merchandise demoted like niche items (cards 9+, at most 2 on top).
PRE_294_DEMOTE = {"officially licensed", "nba", "nhl", "nfl", "mlb", "wnba", "ncaa"}

def pre_task_301(cfg):
    """Before the rainbow search field and early Amazon exit; keep recorded hashes."""
    cfg.search_box_glow = False
    return cfg


def pre_card_tidy(cfg):
    """Before folded checkout steps, the cart chip wording and the AI Analyze colors; no re-pins."""
    cfg.card_tidy = False
    return cfg


def pre_page_polish(cfg):
    """Before event search and Support styling (task 304); never re-pin hashes."""
    pre_all_discounts_button(cfg)
    cfg.page_polish = False
    return cfg


def pre_same_product_spacing(cfg):
    """Before family spacing (task 302); preserve every recorded publication hash."""
    pre_page_polish(cfg)
    cfg.same_product_spacing = False
    return cfg


def pre_drop_fresh(cfg):
    """Before fresh selections and the clock moment (task 303); never re-pin."""
    cfg.drop_fresh = False
    return cfg


def pre_header_polish(cfg):
    """Before the aligned header (task 305); preserve every recorded hash."""
    pre_all_discounts_button(cfg)
    cfg.header_polish = False
    return cfg


def pre_all_discounts_button(cfg):
    """Before the Amazon discounts twin (task 317); never re-pin older hashes."""
    pre_task_319(cfg)
    cfg.all_discounts_button = False
    pre_search_polish_1019(cfg)
    return cfg


def pre_search_polish_1019(cfg):
    """Before task 318's search/header polish; preserve the task 317 release."""
    pre_task_320(cfg)
    cfg.search_polish_1019 = False
    return cfg


def pre_task_320(cfg):
    """Before Analyze's top-five sellers (task 320); keep all recorded hashes."""
    pre_discounts_from_link(cfg)
    cfg.analyze_top_sellers = False
    return cfg


def pre_discounts_from_link(cfg):
    """Before task 326's URL-aware discounts; never re-pin older hashes."""
    pre_phone_tagline_count(cfg)
    cfg.discounts_from_link = False
    return cfg


def pre_phone_tagline_count(cfg):
    """Before task 324's phone count; preserve every recorded hash."""
    pre_timer_pill_ring(cfg)
    cfg.phone_tagline_count = False
    pre_tool_pages(cfg)
    return cfg


def pre_tool_pages(cfg):
    """Before task 328's preview pages; preserve recorded publication hashes."""
    cfg.tool_pages = {"enabled": False, "public": False}
    return cfg


def pre_timer_pill_ring(cfg):
    """Before task 329's compact timer outline; never re-pin older hashes."""
    pre_phone_timer_raise(cfg)
    cfg.timer_pill_ring = False
    return cfg


def pre_phone_timer_raise(cfg):
    """Before task 330's phone timer alignment; preserve every recorded hash."""
    pre_tool_pages_hero(cfg)
    pre_search_buttons_1019(cfg)
    cfg.phone_timer_raise = False
    return cfg


def pre_tool_pages_hero(cfg):
    """Before task 335's tool heroes and header dialog; never re-pin hashes."""
    pre_fresh_more_plain(cfg)
    cfg.tool_pages_hero = False
    return cfg


def pre_search_buttons_1019(cfg):
    """Before task 332's calm search buttons; preserve the live 328973d bytes."""
    pre_tool_pages_1020(cfg)
    cfg.search_buttons_1019 = False
    return cfg


def pre_tool_pages_1020(cfg):
    """Before task 339's tool refresh and discovery; never re-pin older hashes."""
    pre_fresh_more_plain(cfg)
    cfg.tool_pages_1020 = False
    return cfg


def pre_fresh_more_plain(cfg):
    """Before task 340's See all copy; preserve every recorded publication hash."""
    pre_search_discounts_button(cfg)
    pre_card_click_1020(cfg)
    cfg.fresh_more_plain = False
    return cfg


def pre_search_discounts_button(cfg):
    """Before task 342's yellow results exit; keep task 332 bytes."""
    pre_buttons_to_tools(cfg)
    cfg.search_discounts_button = False
    return cfg


def pre_card_click_1020(cfg):
    """Before task 342's stretched Get deal link; preserve recorded hashes."""
    pre_buttons_to_tools(cfg)
    cfg.card_click_1020 = False
    return cfg


# Task 319: historical fixtures keep Amazon used and their original schedules.
def pre_task_319_raw(config):
    config.pop("hide_timer_ticker_confetti", None)
    config.pop("sticky_buy_bar", None)
    config.pop("tools_merge", None)
    config.pop("tool_words_analyzer", None)
    config.pop("soft_buttons", None)
    config.pop("keep_first_cards", None)
    config.pop("phone_first_screen", None)
    config.pop("price_shipping_note", None)
    config.pop("add_to_cart", None)
    pre_social_profiles_raw(config)
    pre_tool_grade_event_raw(config)
    config.pop("deal_spotter", None)
    pre_review_checker_raw(config)
    config["amazon_used"] = {"enabled": True}
    keepa = config.setdefault("keepa", {})
    # These token-allocation fixtures also predate task 323's visitor floor.
    keepa.update(visitor_reserve_tokens=0, genuine_drops_only=False, focus_pages_per_category=1,
                 amazon_pages_per_pull=6, amazon_max_new_posts_per_pull=10,
                 focus_categories=["Beauty & Personal Care", "Health & Household"],
                 buybox_interval_min=60, recheck_top_h=.75, recheck_wall_h=1.5,
                 recheck_fresh_h=4, recheck_new_h=8, recheck_wall_max_age_h=4, recheck_idle_min_age_h=.5,
                 pick_max_unconfirmed_h=3, show_max_unconfirmed_h=12)
    keepa.setdefault("volatility", {}).update(
        targets={"FAST": dict(top=.5, wall=.5, rest=6, hidden=48),
                 "NORMAL": dict(top=.75, wall=2, rest=8, hidden=72),
                 "SLOW": dict(top=2, wall=6, rest=12, hidden=72)},
        stretch_limits={"SLOW": 4, "NORMAL": 4 / 3})
    keepa.setdefault("recheck_adaptive", {}).update(max_top_h=1, max_wall_h=2, max_rest_h=12)
    return config


def pre_task_319(cfg):
    """Pin only the changed knobs; preserve each test's other feature switches."""
    from copy import deepcopy
    previous = pre_task_319_raw({})
    cfg.amazon_used_enabled = True
    for key, value in previous["keepa"].items():
        if isinstance(value, dict):
            getattr(cfg, "keepa_" + key).update(deepcopy(value))
        else:
            setattr(cfg, "keepa_" + key, deepcopy(value))
    return cfg


def pre_buttons_to_tools(cfg):
    """Before task 344's tool handoff; preserve every recorded publication hash."""
    pre_amazon_price_cap(cfg)
    cfg.buttons_to_tools = False
    return cfg


def pre_amazon_price_cap(cfg):
    """Before the operator's $50 cap on the Amazon discount exits (2026-10-20)."""
    pre_tools_new_tab(cfg)
    cfg.amazon_price_cap = False
    return cfg


def pre_tools_new_tab(cfg):
    """Before the homepage tool links opened in a new tab (operator 2026-10-20)."""
    pre_checker_similar_heading(cfg)
    cfg.tools_new_tab = False
    return cfg


def pre_checker_similar_heading(cfg):
    """Before the Deal Checker lead opened the product and the top-sellers heading became the similar-deals button."""
    pre_tool_faq_wording(cfg)
    cfg.checker_similar_heading = False
    return cfg


def pre_tool_faq_wording(cfg):
    """Before the operator's 2026-10-20 tool-page FAQ wording."""
    pre_hide_deal_site_links(cfg)
    cfg.tool_faq_wording = False
    return cfg


def pre_hide_deal_site_links(cfg):
    """Before task 351's credit-by-name routing; never re-pin recorded bytes."""
    pre_keepa_focus_finder(cfg)
    cfg.hide_deal_site_links = False
    cfg.affiliate_repair_competitor_trackers = False
    return cfg


def pre_hide_deal_site_links_raw(config):
    config.pop("hide_timer_ticker_confetti", None)
    config.pop("sticky_buy_bar", None)
    config.pop("tools_merge", None)
    config.pop("tool_words_analyzer", None)
    config.pop("soft_buttons", None)
    config.pop("keep_first_cards", None)
    config.pop("phone_first_screen", None)
    config.pop("price_shipping_note", None)
    config.pop("add_to_cart", None)
    pre_social_profiles_raw(config)
    pre_tool_grade_event_raw(config)
    config.pop("deal_spotter", None)
    pre_review_checker_raw(config)
    config.get("publish", {}).pop("hide_deal_site_links", None)
    config.setdefault("affiliate", {})["repair_competitor_trackers"] = False
    return config


def pre_keepa_focus_finder(cfg):
    """Before task 350's idle-token discovery; never re-pin historical bytes."""
    pre_search_tokens_40000(cfg)
    cfg.keepa_focus_finder = dict(cfg.keepa_focus_finder, enabled=False)
    return cfg


def pre_keepa_focus_finder_raw(config):
    config.pop("hide_timer_ticker_confetti", None)
    config.pop("sticky_buy_bar", None)
    config.pop("tools_merge", None)
    config.pop("tool_words_analyzer", None)
    config.pop("soft_buttons", None)
    config.pop("keep_first_cards", None)
    config.pop("phone_first_screen", None)
    config.pop("price_shipping_note", None)
    config.pop("add_to_cart", None)
    pre_social_profiles_raw(config)
    pre_tool_grade_event_raw(config)
    pre_review_checker_raw(config)
    config.pop("keepa_focus_finder", None)
    config.pop("deal_spotter", None)
    return config


def pre_search_tokens_40000(cfg):
    """Before the 2026-10-20 search allowance raise; search-config.json is published."""
    pre_tool_buttons_outside(cfg)
    if cfg.search.get("tokens_per_day") == 40000:
        cfg.search = dict(cfg.search, tokens_per_day=10000)
    return cfg


def pre_tool_buttons_outside(cfg):
    """Before Deal Checker / Discount Finder moved outside the search bar (operator 2026-10-20)."""
    pre_timer_center(cfg)
    cfg.tool_buttons_outside = False
    return cfg


def pre_search_focus_ring(cfg):
    """Before the search box ring was limited to keyboard (Tab) focus (operator 2026-10-21)."""
    pre_checker_fixes_1021(cfg)
    cfg.search_focus_ring_keyboard_only = False
    return cfg


def pre_checker_fixes_1021(cfg):
    """Before similar deals used the typed words and the price age read in minutes (operator 2026-10-21)."""
    pre_exact_variant_links(cfg)
    cfg.checker_similar_typed_words = False
    cfg.price_age_minutes = False
    return cfg


def pre_exact_variant_links(cfg):
    """Before Amazon size/colour children opened as themselves (operator 2026-10-21)."""
    pre_keepa_starter(pre_sovrn_denied(cfg))
    cfg.affiliate_exact_variant_links = False
    return cfg


def pre_sovrn_denied(cfg):
    """Before Sovrn denied wowpriced.com and its links were switched off (2026-10-21).

    Only configs read from the shipped config.yaml (which now says sovrn_enabled:
    false) move back; default-built configs never had Sovrn.
    """
    if cfg.raw.get("affiliate", {}).get("sovrn_enabled") is False:
        cfg.affiliate_sovrn_enabled = True
        cfg.affiliate_link_overrides = {"walmart.com/222918738": "https://sovrn.co/168n3ev"}
    return cfg


def pre_keepa_starter(cfg):
    """Before the Keepa plan went from 60 to 20 tokens/min (operator 2026-10-21).

    Only the shipped search caps (published in search-config.json) move back,
    and only when they hold the new shipped values; scheduler tests that size
    reads to the 60/min plan set keepa_tokens_per_minute themselves.
    """
    pre_deal_spotter(cfg)
    pre_review_checker(cfg)
    pre_zero_waste(cfg)
    old = {"tokens_per_day": (8000, 40000), "max_per_minute": (4, 20)}
    cfg.search = {key: old[key][1] if key in old and value == old[key][0] else value
                  for key, value in cfg.search.items()}
    return cfg


def pre_keepa_starter_raw(config):
    """The same pins on a raw config.yaml mapping (for tests that write YAML)."""
    config.pop("hide_timer_ticker_confetti", None)
    config.pop("sticky_buy_bar", None)
    config.pop("tools_merge", None)
    config.pop("tool_words_analyzer", None)
    config.pop("soft_buttons", None)
    config.pop("keep_first_cards", None)
    config.pop("phone_first_screen", None)
    config.pop("price_shipping_note", None)
    config.pop("add_to_cart", None)
    pre_social_profiles_raw(config)
    pre_tool_grade_event_raw(config)
    if config.get("keepa", {}).get("recheck_surplus_reserve") == 250:
        config["keepa"]["recheck_surplus_reserve"] = 650
    config.pop("deal_spotter", None)
    pre_review_checker_raw(config)
    config["keepa"]["tokens_per_minute"] = 60
    config["search"].update(tokens_per_day=40000, max_per_minute=20)
    return config


def pre_timer_center(cfg):
    """Before the countdown digits were centred in the pill (operator 2026-10-20)."""
    pre_search_focus_ring(cfg)
    cfg.timer_center = False
    return cfg


def pre_deal_spotter(cfg):
    """Before task 366's public tool and footer links; retain every recorded hash."""
    cfg.deal_spotter = {"enabled": False}
    pre_hide_timer_ticker(cfg)
    return cfg


def pre_review_checker(cfg):
    """Task 367 adds only opt-in pages/links; recorded fixtures retain OFF bytes."""
    cfg.review_checker = dict(cfg.review_checker, enabled=False)
    pre_hide_timer_ticker(cfg)
    return cfg


def pre_review_checker_raw(config):
    config.pop("review_checker", None)
    return config


def pre_zero_waste(cfg):
    """Before the next-run reserve was sized for the 20/min plan (operator 2026-10-22)."""
    if cfg.keepa_recheck_surplus_reserve == 250:
        cfg.keepa_recheck_surplus_reserve = 650.0
    return cfg


def pre_hide_timer_ticker(cfg):
    """Before the timer, "Live deals" banner and drop confetti were hidden (operator 2026-10-22)."""
    cfg.hide_timer_ticker_confetti = False
    pre_price_shipping_note(cfg)
    return cfg


def pre_price_shipping_note(cfg):
    """Before tool prices showed shipping on its own line (operator 2026-10-22)."""
    cfg.price_shipping_note = False
    pre_social_profiles(cfg)
    return cfg


def pre_social_profiles(cfg):
    """Before the real Facebook and Pinterest profiles were configured (operator 2026-10-22)."""
    cfg.social_profiles = {}
    pre_tool_grade_event(cfg)
    pre_sticky_buy_bar(cfg)
    pre_add_to_cart(cfg)
    pre_front_mix(cfg)
    pre_tools_merge(cfg)
    pre_tool_words_analyzer(cfg)
    pre_soft_buttons(cfg)
    pre_keep_first_cards(cfg)
    pre_phone_first_screen(cfg)
    return cfg


def pre_tools_merge(cfg):
    """Before Deal Checker and Discount Finder folded into the two tools (operator 2026-10-23)."""
    cfg.tools_merge = False
    return cfg


def pre_tool_words_analyzer(cfg):
    """Before typed words waited through the link's analyzer box (operator 2026-10-23 ~20:05Z)."""
    cfg.tool_words_analyzer = False
    return cfg


def pre_soft_buttons(cfg):
    """Before the two search buttons were made a little softer (operator 2026-10-23)."""
    cfg.soft_buttons = False
    return cfg


def pre_keep_first_cards(cfg):
    """Before the home wall kept the server's cards (quality loop 2026-10-23: phone LCP / taps)."""
    cfg.keep_first_cards = False
    return cfg


def pre_phone_first_screen(cfg):
    """Before phones showed the first deals and their buttons on the first screen (operator 2026-10-23)."""
    cfg.phone_first_screen = False
    return cfg


def pre_sticky_buy_bar(cfg):
    """Before the phone deal pages' sticky Get deal bar (operator 2026-10-22)."""
    cfg.sticky_buy_bar = False
    return cfg


def pre_add_to_cart(cfg):
    """Before the "Add to Amazon cart" button (operator 2026-10-22)."""
    cfg.add_to_cart = {"enabled": False}
    return cfg


def pre_social_profiles_raw(config):
    config.get("social", {}).pop("profiles", None)
    pre_front_mix_raw(config)
    return config


# Front mix (operator 2026-10-22): bucket shares of every 30 front cards and the
# women's allure words it added. Recorded fixtures keep the earlier front page.
PRE_FRONT_MIX_ALLURE = {"lace teddy", "chemise", "silk slip", "slip dress", "bustier", "garter", "thigh highs",
                        "fishnet stockings", "lace stockings", "silk pajamas", "nightie", "bodycon dress",
                        "cocktail dress"}


def _pre_front_mix(front_page):
    if not isinstance(front_page, dict):
        return
    front_page.pop("mix", None)
    groups = ((front_page.get("appeal") or {}).get("want") or {}).get("groups")
    if isinstance(groups, dict) and isinstance(groups.get("allure"), list):
        groups["allure"] = [w for w in groups["allure"] if w not in PRE_FRONT_MIX_ALLURE]


def pre_front_mix(cfg):
    """Before the front mix; never re-pin recorded publication bytes."""
    pre_feel_good(cfg)
    _pre_front_mix(cfg.front_page)
    return cfg


def pre_front_mix_raw(config):
    pre_feel_good_raw(config)
    _pre_front_mix(config.get("publish", {}).get("front_page"))
    return config


# Operator 2026-10-23 ~00:05Z: beauty/home/kitchen lifts, the "feel good" group and
# everyday prices from $30. Recorded fixtures keep the earlier factors and groups.
PRE_FEEL_GOOD_FACTORS = {"home": 1.15, "kitchen": 1.15, "beauty": 1.15, "electronics": 1.15}
PRE_FEEL_GOOD_GROUPS = {
    "basics": ["underwear", "boxer", "briefs", "socks", "leggings", "pajamas", "slippers",
               "sneakers", "shoes", "hat", "beanie"],
    "carry": ["owala", "stanley", "hydro flask", "yeti", "tumbler", "water bottle"],
    "beauty": ["perfume", "makeup", "lipstick", "mascara", "skincare", "serum", "moisturizer", "sunscreen",
               "shampoo", "conditioner", "hair dryer", "straightener", "curling iron", "nail", "lip gloss"],
    "wear & carry": ["sunglasses", "purse", "handbag", "tote", "backpack", "jewelry",
                     "earrings", "necklace", "bracelet", "dress", "pajama set"],
    "home": ["towels", "sheets", "pillow", "blanket", "throw", "candle", "storage bins",
             "organizer", "vacuum", "air purifier", "humidifier", "string lights"],
}
PRE_FEEL_GOOD_EVERYDAY = {"full_until": 40, "floor_from": 100, "floor_factor": 0.55}


def _pre_feel_good(front_page):
    if not isinstance(front_page, dict):
        return
    appeal = front_page.get("appeal") or {}
    factors = appeal.get("category_factors")
    if isinstance(factors, dict):
        factors.update({k: v for k, v in PRE_FEEL_GOOD_FACTORS.items() if k in factors})
    want = appeal.get("want") or {}
    if isinstance(want.get("groups"), dict) and "feel good" in want["groups"]:
        want["groups"].pop("feel good")
        want["groups"].update({name: list(words) for name, words in PRE_FEEL_GOOD_GROUPS.items()
                               if name in want["groups"]})
    if isinstance(want.get("group_factors"), dict):
        want["group_factors"].pop("feel good", None)
    everyday = front_page.get("everyday_price")
    if isinstance(everyday, dict):
        everyday.update({k: v for k, v in PRE_FEEL_GOOD_EVERYDAY.items() if k in everyday})


def pre_feel_good(cfg):
    """Before the 2026-10-23 feel-good lean; never re-pin recorded bytes."""
    _pre_feel_good(cfg.front_page)
    return cfg


def pre_feel_good_raw(config):
    _pre_feel_good(config.get("publish", {}).get("front_page"))
    return config


def pre_tool_grade_event(cfg):
    """Before every page's wpTrack knew the tools' tool_grade event (2026-10-22)."""
    cfg.tool_grade_event = False
    return cfg


def pre_tool_grade_event_raw(config):
    config.pop("tool_grade_event", None)
    return config


def pre_haiku_only(cfg):
    """Before a confident Haiku price check was final (operator 2026-10-22, "do both on haiku")."""
    cfg.critic_tiering["haiku_only"] = False
    return cfg


def pre_haiku_only_raw(config):
    config["parse"]["critic_tiering"]["haiku_only"] = False
    return config
