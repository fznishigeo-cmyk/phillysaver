"""Publication-only Amazon history control; stored checks remain intact."""

import re

from ..verify import is_amazon_deal

BLOCK = re.compile(r"/\*amazon-history\*/(.*?)/\*/amazon-history\*/", re.S)


def hidden(cfg, deal):
    return getattr(cfg, "amazon_history", "show") == "hide" and (deal.get("amazon") is True or is_amazon_deal(deal))


def watches_hidden(cfg):
    """Amazon price watches (alerts) follow amazon_history unless set on their own."""
    watches = getattr(cfg, "amazon_watches", "")
    return watches == "off" or (not watches and getattr(cfg, "amazon_history", "show") == "hide")


def template(text, cfg):
    return BLOCK.sub(lambda m: m[1] if cfg.amazon_history == "hide" else "", text)


def watches_template(text, cfg):
    """The engagement script's Amazon blocks only gate saved price watches."""
    return BLOCK.sub(lambda m: m[1] if watches_hidden(cfg) else "", text)
