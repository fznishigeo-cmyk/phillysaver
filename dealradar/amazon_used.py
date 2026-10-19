"""Amazon-only publication/spend policy; stored offers keep their history."""

import re


def excluded(deal, cfg):
    from .verify import is_amazon_deal, is_used_deal

    if (cfg.amazon_used_enabled or str(deal.get("store") or "").strip().casefold().startswith("woot")
            or not is_amazon_deal(deal)):
        return False
    # Older imports can name Resale in the store/title without a condition.
    words = " ".join(str(deal.get(k) or "") for k in ("store", "item"))
    return (is_used_deal(deal) or bool(re.search(r"\b(?:warehouse|resale)\b", str(deal.get("store") or ""), re.I))
            or bool(re.search(
        r"\bamazon\s+(?:warehouse|resale|renewed)\b|\bused\s*[-–—:]\s*"
        r"(?:like\s+new|very\s+good|good|acceptable)\b|\bopen[ -]box\b", words, re.I)))


def summary(db, cfg):
    from .fetch.keepa import DEAL_PAGE_COST, _finder_cost
    from .verify import USED_RECHECK_TOKENS

    rows = sum(excluded(d, cfg) for d in db.all_deals(status="alive"))
    pages = 0 if cfg.amazon_used_enabled else cfg.keepa_warehouse_pages_per_pull * DEAL_PAGE_COST
    size = max(50, min(100, cfg.keepa_sweep_asins_per_pull))
    sweep = (_finder_cost(size) + size
             if not cfg.amazon_used_enabled and cfg.keepa_sweep_enabled else 0)
    return {"enabled": cfg.amazon_used_enabled, "live_rows_suppressed": rows,
            "used_tokens_spent": None if cfg.amazon_used_enabled else 0,
            "discovery_tokens_avoided_ceiling_per_pull": pages + sweep,
            "recheck_tokens_avoided_ceiling_per_full_scan": rows * (USED_RECHECK_TOKENS + int(cfg.keepa_recheck_top_live)),
            "avoided_basis": "configured ceilings, not measured savings"}
