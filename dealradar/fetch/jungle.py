"""Jungle's public top-deals API, used by its own frontend; no retailer GETs."""
from __future__ import annotations

import json
import math
import re
from urllib.parse import urlsplit

from ..models import RawPost
from .base import SourceAdapter
from .rss import MAX_ENTRIES, _permission_host


def parse_jungle(source, body, now):
    try:
        items = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("empty_or_changed_jungle_api") from exc
    if not isinstance(items, list) or not items:
        raise RuntimeError("empty_or_changed_jungle_api")
    posts, seen = [], set()
    for item in items:
        if (not isinstance(item, dict)
                or not re.fullmatch(r"[A-Z0-9]{10}", str(item.get("ASIN", "")))
                or not isinstance(item.get("title"), str) or not item["title"].strip()):
            raise RuntimeError("changed_jungle_deal")
        lines = [item["title"]]
        for key, label in [("finalPrice", "Final price"), ("currentPrice", "Current price"),
                           ("listPrice", "List price"), ("averagePrice", "Average price"),
                           ("couponFixed", "Coupon amount"), ("promoFixed", "Promo amount")]:
            value = item.get(key)
            if value is None and key != "finalPrice":
                continue
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise RuntimeError("changed_jungle_price")
            # Copy API numbers; no savings calculation or reconstructed final price.
            lines.append(f"{label}: ${value}")
        # Preserve source conditions and coupon fields verbatim as JSON evidence.
        conditions = {key: item[key] for key in
                      ("couponPercentage", "promoCode", "ss", "maxSs", "qtyLimit",
                       "extraDeal", "extraDealPrice", "extraDealPriceEach")
                      if item.get(key) is not None}
        lines.append(json.dumps(conditions, ensure_ascii=False, sort_keys=True))
        url = f"https://{urlsplit(source.url).netloc}/deal/{item['ASIN']}"
        if url in seen:
            continue
        posts.append(RawPost(source.name, url, url, item["title"],
                             "\n".join(lines), None, now))
        seen.add(url)
        if len(posts) >= MAX_ENTRIES:
            break
    return posts


class JungleAdapter(SourceAdapter):
    def fetch_posts(self, source, fetcher, db, cfg, now, deadline=None):
        return self.fetch_deals(source, fetcher, now, deadline)

    @staticmethod
    def fetch_deals(source, fetcher, now, deadline=None):
        kw = {}
        if override := _permission_host(source):
            kw["robots_override_host"] = override
        res = fetcher.get(source.url, conditional=True, deadline=deadline, **kw)
        if res.not_modified or res.status == 304:
            return []
        if res.blocked_by_robots:
            raise RuntimeError("blocked_by_robots")
        if res.status != 200 or not res.body:
            raise RuntimeError(f"http_{res.status} {res.error}".strip())
        return parse_jungle(source, res.body, now)
