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


def _pre_task_279_appeal(appeal):
    if not isinstance(appeal, dict):
        return
    appeal.get("category_factors", {}).pop("electronics", None)
    want = appeal.get("want")
    if isinstance(want, dict):
        want.get("groups", {}).pop("gadgets", None)
        if "exclude_words" in want:
            want["exclude_words"] = list(PRE_279_EXCLUDE)
    if isinstance(appeal.get("season"), list):
        appeal["season"] = [w for w in appeal["season"] if w.get("name") in PRE_279_SEASONS]


def pre_task_279(cfg):
    cfg.home_title = cfg.home_description = ""
    _pre_task_279_appeal(cfg.front_page.get("appeal"))
    return cfg


def pre_task_279_raw(config):
    """The same pins on a raw config.yaml mapping."""
    for key in ("home_title", "home_description"):
        config.get("site", {}).pop(key, None)
    _pre_task_279_appeal(config.get("publish", {}).get("front_page", {}).get("appeal"))
    return config
