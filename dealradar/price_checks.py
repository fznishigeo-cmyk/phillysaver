"""Bounded history of successful, exact current-price readings; no new reads."""

import math
from datetime import timezone

from .util import parse_iso
from .verify import _price_hidden_by_promo


def record(db, cfg, deal, price, observed_at, now, *, source):
    if not cfg.seo_product_pages_record_own_checks_enabled:
        return False
    if (deal.get("status") != "alive" or deal.get("gone_suspect_at") or deal.get("death")
            or deal.get("price_unconfirmed")
            or {"suspect_msrp", "unbacked_price", "price_moved", "range", "per_unit"}
            & set(deal.get("flags") or []) or _price_hidden_by_promo(deal)
            or isinstance(price, bool) or not isinstance(price, (int, float))
            or not math.isfinite(price) or price < 0 or price != deal.get("final_price")
            or observed_at is None or observed_at > now):
        return False
    since = [at for name in ("first_seen_at", "price_since")
             if (at := parse_iso(deal.get(name) or "")) is not None]
    if since and observed_at < max(since):
        return False
    at = observed_at.astimezone(timezone.utc)
    return db.record_price(deal["dedupe_key"], deal["store"], price, at,
                           f"check:{source}:{at:%Y-%m-%dT%H}",
                           max_check_rows_per_day=cfg.seo_product_pages_record_own_checks_max_per_offer_per_day)
