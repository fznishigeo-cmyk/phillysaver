"""Optional deal-page comparisons, using only already-public live prices."""

import math
from html import escape

from ..products import condition


def blocks(live: list[dict], source: list[dict], paths: dict[str, str]) -> dict[str, str]:
    from .seo import store_slug

    raw = {d["dedupe_key"]: d for d in source}
    groups = {}
    for d in live:
        facts = raw.get(d["dedupe_key"], {})
        product_id = facts.get("product_id")
        price = d.get("final_price")
        if (not product_id or d.get("locked") or d.get("price_unconfirmed") or d.get("status") != "alive"
                or d["key"][:8] not in paths or not d.get("store")
                or str(facts.get("currency") or "USD").upper() != "USD"
                or {"range", "per_unit"} & set(d.get("flags") or [])
                or price is None or not math.isfinite(float(price)) or float(price) < 0):
            continue
        groups.setdefault((product_id, condition(facts)), []).append(d)
    result = {}
    for offers in groups.values():
        stores = {}
        for d in sorted(offers, key=lambda d: (float(d["final_price"]), d["key"])):
            stores.setdefault(store_slug(d["store"]), d)
        for d in offers:
            links = []
            for store, other in sorted(stores.items()):
                if not store or store == store_slug(d["store"]):
                    continue
                path = paths[other["key"][:8]]
                summary = str((other.get("checkout") or {}).get("summary") or "")
                qualifiers = f' — {escape(summary)}' if summary else ""
                links.append(f'<p>Also at <a href="{escape(path)}">{escape(other["store"])}</a>'
                             f' for ${float(other["final_price"]):.2f} USD{qualifiers}</p>')
            if links:
                result[d["dedupe_key"]] = '<aside aria-label="Other stores">' + ''.join(links) + '</aside>'
    return result
