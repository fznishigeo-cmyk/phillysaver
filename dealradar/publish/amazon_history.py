"""Publication-only Amazon history control; stored checks remain intact."""

import re

from ..verify import is_amazon_deal

BLOCK = re.compile(r"/\*amazon-history\*/(.*?)/\*/amazon-history\*/", re.S)


def hidden(cfg, deal):
    return getattr(cfg, "amazon_history", "show") == "hide" and (deal.get("amazon") is True or is_amazon_deal(deal))


def template(text, cfg):
    return BLOCK.sub(lambda m: m[1] if cfg.amazon_history == "hide" else "", text)
