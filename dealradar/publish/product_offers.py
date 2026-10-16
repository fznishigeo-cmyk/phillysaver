"""Guarded deal-page comparisons using only already-public, confirmed prices."""

import hashlib
import math
import re
from html import escape

from ..products import condition, identity
from ..savings import comparable_prices, lowest_seen
from . import amazon_history

MATCH_TYPES = ("asin", "gtin", "sku", "brand_model")
_PACK = re.compile(r"\b(?:pack|set|case|box|lot|bundle|qty|quantity)\s*(?:of\s*)?(\d+)\b"
                   r"|\b(\d+)\s*[- ]?\s*(?:packs?|counts?|ct|pieces?|pcs|pairs?|units?)\b")
_CAPACITY = re.compile(r"\b\d+(?:\.\d+)?\s*(?:tb|gb|mb|mah|ah|ml|cl|liters?|litres?|l|fl\s*oz|oz|g|kg|lb)\b")
_SIZE = re.compile(r"\b\d+(?:\.\d+)?\s*[- ]?\s*(?:inch(?:es)?|in|cm|mm|feet|ft)\b"
                   r"|\b(?:size\s+\w+|xxs|xs|s|m|l|xl|xxl|xxxl|small|medium|large)\b")
_COLOR = re.compile(r"\b(?:black|white|red|blue|green|yellow|orange|purple|pink|brown|gray|grey|silver|gold|beige|navy|teal)\b")
_CONDITION = re.compile(r"\b(?:used|pre[- ]?owned|refurbished|renewed|reconditioned|open[- ]?box|like[- ]?new|new)\b")
_MODEL = re.compile(r"\b(?=[a-z0-9._/-]*[a-z])(?=[a-z0-9._/-]*\d)[a-z0-9]+(?:[._/-][a-z0-9]+)*\b")


def _text(value) -> str:
    return " ".join(str(value or "").casefold().split())


def _facets(facts: dict) -> dict:
    title = _text(facts.get("item"))
    # Titles can veto an identity, never establish one. Unknown variants are
    # caught by the full-title veto below, without guessing colors or models.
    quantities = {a or b for a, b in _PACK.findall(title)}
    capacity = set(_CAPACITY.findall(title))
    size = set(_SIZE.findall(title))
    model_text = _PACK.sub(" ", _SIZE.sub(" ", _CAPACITY.sub(" ", title)))
    return {
        "pack_size": (quantities, tuple(_text(facts.get(k)) for k in ("pack_size", "quantity", "quantity_or_unit"))),
        "capacity": (capacity, _text(facts.get("capacity"))),
        "variant": (size, set(_COLOR.findall(title)), tuple(_text(facts.get(k)) for k in ("size", "color", "variant"))),
        "condition": (condition(facts), set(_CONDITION.findall(title)),
                      _text(facts.get("condition")), _text(facts.get("condition_grade"))),
        "model": (set(_MODEL.findall(model_text)), _text(facts.get("model") or facts.get("model_number"))),
    }


def guard_reasons(a: dict, b: dict, price: float, other_price: float, cfg=None) -> list[str]:
    left, right = _facets(a), _facets(b)
    reasons = [name for name in left if left[name] != right[name]]
    if not reasons and _text(a.get("item")) != _text(b.get("item")):
        reasons.append("variant")  # Unexplained title disagreement fails closed.
    if not comparable_prices(price, other_price,
                             getattr(cfg, "product_entities_min_price_ratio", 0.4),
                             getattr(cfg, "product_entities_max_price_ratio", 2.5)):
        reasons.append("price_ratio")
    return reasons


def _eligible(live, source, paths):
    raw = {d["dedupe_key"]: d for d in source}
    result = {}
    for d in live:
        facts = raw.get(d["dedupe_key"], {})
        try:
            price = float(d["final_price"])
        except (KeyError, TypeError, ValueError):
            continue
        if (not facts.get("product_id") or d.get("locked") or d.get("price_unconfirmed")
                or d.get("status") != "alive" or d["key"][:8] not in paths or not d.get("store")
                or not d.get("item") or str(facts.get("currency") or "USD").upper() != "USD"
                or {"range", "per_unit"} & set(d.get("flags") or [])
                or not math.isfinite(price) or price < 0):
            continue
        result[d["dedupe_key"]] = (d, dict(facts, item=d["item"], conditions=d.get("conditions")))
    return result


def _match(a, b):
    shared = set(identity(a)[0]) & set(identity(b)[0])
    return next((kind for kind in MATCH_TYPES if any(k.split(":")[0] == kind for k, _ in shared)),
                "unresolved")


def comparisons(live, source, paths, cfg=None):
    """One candidate per other store; the same vetoes feed shadow and display."""
    from .seo import store_slug

    eligible = _eligible(live, source, paths)
    groups = {}
    for d, facts in eligible.values():
        groups.setdefault(facts["product_id"], []).append(d)
    selected, pairs = {}, []
    for offers in groups.values():
        stores = {}
        for d in sorted(offers, key=lambda d: (float(d["final_price"]), d["key"])):
            stores.setdefault(store_slug(d["store"]), d)
        for d in offers:
            for store, other in sorted(stores.items()):
                if not store or store == store_slug(d["store"]):
                    continue
                a, b = eligible[d["dedupe_key"]][1], eligible[other["dedupe_key"]][1]
                match = _match(a, b)
                flags = guard_reasons(a, b, float(d["final_price"]), float(other["final_price"]), cfg)
                if match == "unresolved":
                    flags.append("identity")
                pair = {"page_key": d["key"], "other_key": other["key"], "match": match,
                        "flags": flags, "shown": not flags}
                for label, offer, facts in (("page", d, a), ("other", other, b)):
                    pair[label] = {"title": offer["item"], "store": offer["store"],
                                   "condition": condition(facts), "price": offer["final_price"]}
                pairs.append(pair)
                if not flags:
                    selected.setdefault(d["dedupe_key"], []).append(other)
    return selected, pairs


def blocks(live: list[dict], source: list[dict], paths: dict[str, str], cfg=None,
           *, selected=None) -> dict[str, str]:
    if selected is None:
        selected, _ = comparisons(live, source, paths, cfg)
    result = {}
    for key, offers in selected.items():
        links = []
        for other in offers:
            path = paths[other["key"][:8]]
            summary = str((other.get("checkout") or {}).get("summary") or "")
            qualifiers = f' — {escape(summary)}' if summary else ""
            links.append(f'<p>Also at <a href="{escape(path)}">{escape(other["store"])}</a>'
                         f' for ${float(other["final_price"]):.2f} USD{qualifiers}</p>')
        if links:
            result[key] = '<aside aria-label="Other stores">' + ''.join(links) + '</aside>'
    return result


def evaluate(db, cfg, now, pages, source, paths, run_key):
    selected, pairs = comparisons(pages, source, paths, cfg)
    eligible = _eligible(pages, source, paths)
    histories, lowest = {}, {}
    for key, (d, facts) in eligible.items():
        product_id = facts["product_id"]
        if product_id not in histories:
            histories[product_id] = [row for row in db.product_price_history(product_id, 30, now=now)
                                    if not amazon_history.hidden(cfg, dict(row, canonical_url=
                                        row["dedupe_key"][4:] if row["dedupe_key"].startswith("url|") else ""))]
        fact = lowest_seen(histories[product_id], condition(facts), now)
        if fact:
            lowest[key] = fact
    counts = {kind: len({p["page_key"] for p in pairs if p["shown"] and p["match"] == kind})
              for kind in MATCH_TYPES}
    blocked = {}
    for pair in pairs:
        for reason in pair["flags"]:
            blocked[reason] = blocked.get(reason, 0) + 1
    # Reproducible within a run, rotates between runs; suspicious pairs first.
    samples = sorted(pairs, key=lambda p: (not bool(p["flags"]), hashlib.sha256(
        f'{run_key}|{p["page_key"]}|{p["other_key"]}'.encode()).digest()))[:30]
    report = {"published_pages": len(pages), "eligible_pages": len(eligible),
              "also_at_pages": len(selected), "also_at_pages_by_match": counts,
              "candidate_pairs": len(pairs), "blocked_pairs": sum(bool(p["flags"]) for p in pairs),
              "blocked_by_reason": blocked, "lowest_seen_pages": len(lowest), "samples": samples}
    also_at = blocks(pages, source, paths, cfg, selected=selected) if cfg.product_entities_also_at_enabled else {}
    return also_at, lowest, report


def lowest_html(fact):
    if not fact:
        return ""
    return (f'<p class="lowest-seen">Lowest we\'ve seen: ${fact["price"]:.2f} on '
            f'<time datetime="{fact["date"]}">{fact["date"]}</time> (UTC, last 30 days)</p>')
