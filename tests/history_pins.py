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
