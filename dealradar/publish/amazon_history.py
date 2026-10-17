"""Publication-only store history and saved-watch policy; stored checks stay intact."""

import re

from ..verify import is_amazon_deal

BLOCK = re.compile(r"/\*amazon-history\*/(.*?)/\*/amazon-history\*/", re.S)
#: Copy that explains our price-history badge; dropped when no store shows history.
SHOWN = re.compile(r"<!--history-shown-->(.*?)<!--/history-shown-->", re.S)


def store_of(deal):
    from .checker import store_key

    # Amazon URL offers with an unrelated store name still have Amazon policy.
    if is_amazon_deal(deal):
        return "amazon"
    store = store_key(deal.get("canonical_url"))
    name = re.sub(r"[^a-z]", "", str(deal.get("store") or "").lower())
    return name if store == "other" and name in {"walmart", "ebay"} else store


def live_only(cfg, deal):
    from ..amazon_reference import enabled
    if enabled(cfg):
        return True
    store = store_of(deal)
    return store != "amazon" and getattr(cfg, "store_policy", {}).get(store, "history") == "live_only"


def hidden(cfg, deal):
    from ..amazon_reference import enabled
    if enabled(cfg):
        return True
    return (getattr(cfg, "amazon_history", "show") == "hide"
            and (deal.get("amazon") is True or is_amazon_deal(deal))) or live_only(cfg, deal)


def watches_hidden(cfg):
    """Amazon price watches (alerts) follow amazon_history unless set on their own."""
    from ..amazon_reference import enabled
    if enabled(cfg):
        return True
    watches = getattr(cfg, "amazon_watches", "")
    return watches == "off" or (not watches and getattr(cfg, "amazon_history", "show") == "hide")


def any_shown(cfg):
    return (getattr(cfg, "amazon_history", "show") != "hide"
            or any(v != "live_only" for v in getattr(cfg, "store_policy", {}).values()))


def template(text, cfg):
    from ..amazon_reference import enabled
    text = SHOWN.sub(lambda m: m[1] if any_shown(cfg) else "", text)
    if enabled(cfg) or (cfg.keepa_click_price_rule == "on" and (
            cfg.amazon_history == "hide" or "live_only" in cfg.store_policy.values())):
        # A withheld comparison is unavailable, never a zero-percent discount.
        # Keep recorded history-mode bytes when the new price switches are OFF.
        text = text.replace('stamp: "-" + pct + "%",',
                            'stamp: d.pct_at_checkout == null ? "" : "-" + pct + "%",')
        text = text.replace('plain: "-" + pct + "% " + plain,',
                            'plain: (d.pct_at_checkout == null ? "" : "-" + pct + "% ") + plain,')
        text = text.replace('"under " + Math.round(alertFloor) + "% off"',
                            '(strong.some(function (d) { return d.pct_at_checkout == null; }) ? "more verified offers" : "under " + Math.round(alertFloor) + "% off")')
    return BLOCK.sub(lambda m: m[1] if cfg.amazon_history == "hide" or enabled(cfg) else "", text)


def watches_template(text, cfg):
    """The legacy Amazon blocks gate all stores' saved price watches."""
    keep = watches_hidden(cfg) or "live_only" in getattr(cfg, "store_policy", {}).values()
    return BLOCK.sub(lambda m: m[1] if keep else "", text)
