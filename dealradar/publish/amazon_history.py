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
    store = store_of(deal)
    return store != "amazon" and getattr(cfg, "store_policy", {}).get(store, "history") == "live_only"


def hidden(cfg, deal):
    return (getattr(cfg, "amazon_history", "show") == "hide"
            and (deal.get("amazon") is True or is_amazon_deal(deal))) or live_only(cfg, deal)


def watches_hidden(cfg):
    """Amazon price watches (alerts) follow amazon_history unless set on their own."""
    watches = getattr(cfg, "amazon_watches", "")
    return watches == "off" or (not watches and getattr(cfg, "amazon_history", "show") == "hide")


def any_shown(cfg):
    return (getattr(cfg, "amazon_history", "show") != "hide"
            or any(v != "live_only" for v in getattr(cfg, "store_policy", {}).values()))


def template(text, cfg):
    text = SHOWN.sub(lambda m: m[1] if any_shown(cfg) else "", text)
    return BLOCK.sub(lambda m: m[1] if cfg.amazon_history == "hide" else "", text)


def watches_template(text, cfg):
    """The legacy Amazon blocks gate all stores' saved price watches."""
    keep = watches_hidden(cfg) or "live_only" in getattr(cfg, "store_policy", {}).values()
    return BLOCK.sub(lambda m: m[1] if keep else "", text)
