"""Amazon-only publication/spend policy; stored offers keep their history."""

import re


def renewed_title(title):
    """Explicit condition labels, not product-name words such as Renewed Hope."""
    return bool(re.search(
        r"\(\s*(?:renewed(?:\s+premium)?|refurbished)\s*\)"
        r"|\[\s*(?:renewed(?:\s+premium)?|refurbished)\s*\]"
        r"|\b(?:renewed\s+premium|amazon\s+renewed)\b"
        r"|[-–—|,:]\s*(?:renewed|refurbished)\s*$", str(title or ""), re.I))


def excluded(deal, cfg):
    from .verify import is_amazon_deal

    if (cfg.amazon_used_enabled or str(deal.get("store") or "").strip().casefold().startswith("woot")
            or not is_amazon_deal(deal)):
        return False
    return used_offer(deal, renewed_title_lock=getattr(cfg, "amazon_renewed_title_lock", False))


def used_offer(deal, *, renewed_title_lock=False):
    """Used, open-box, refurbished, Warehouse, Resale or Renewed, by condition or by name."""
    from .verify import is_used_deal

    # Older imports can name Resale in the store/title without a condition.
    fields = [str(deal.get(k) or "") for k in ("store", "item")]
    words = " ".join(fields)
    # The Amazon store + "Renewed Skin Serum" is not an Amazon Renewed label.
    renewed_words = " | ".join(fields) if renewed_title_lock else words
    return (is_used_deal(deal) or bool(re.search(r"\b(?:warehouse|resale)\b", str(deal.get("store") or ""), re.I))
            or bool(re.search(
        r"\bamazon\s+(?:warehouse|resale)\b|\bused\s*[-–—:]\s*"
        r"(?:like\s+new|very\s+good|good|acceptable)\b|\bopen[ -]box\b", words, re.I))
            or bool(re.search(r"\bamazon\s+renewed\b", renewed_words, re.I))
            or (renewed_title_lock and renewed_title(deal.get("item"))))


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
