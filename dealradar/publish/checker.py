"""Static Deal Check aliases, built only from public evidence-checked facts.

No lookup requests, new observations or persisted price copies. Policy and
checkout guards apply to history inputs as well as to the resulting entries.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from collections import Counter
from datetime import timedelta, timezone
from urllib.parse import urlsplit

from ..conditions import normalize_conditions
from ..http_client import _ASIN_RE, _TRACKING_EXACT, canonicalize_url, extract_asin
from ..products import WALMART_PATH, WOOT_PATH
from ..savings import DEAL_TYPE_PCT, product_price_facts
from ..store_prices import BESTBUY_PATHS, EBAY_PATH, plain_price, store_offer
from ..util import iso, parse_iso
from ..verify import is_amazon_deal, price_confirmed_at
from . import amazon_history, product_pages

HOSTS = {name + ".com": name for name in ("amazon", "bestbuy", "ebay", "walmart", "woot", "target")}
SHORT_HOSTS = ("amzn.to", "a.co", "link.amazon", "on.ltk.com", "goto.target.com", "zdcs.link",
               "creator.anchor.store", "go.magik.ly", "shoplowes.me", "go.dealmoon.com",
               "clicks.trx-hub.com", "bit.ly", "tinyurl.com", "t.co", "ebay.us", "walmrt.us",
               "sovrn.co", "redirect.viglink.com")
TARGET_PATH = r"/p/(?:[^/]+/)?(?:-/)?A-(\d+)/?"
HEADERS = "\n/check/data/*\n  X-Robots-Tag: noindex\n  Cache-Control: no-store\n"
MAX_SHARD_BYTES = 64 * 1024  # strict upper bound, including braces and UTF-8


def js_rules() -> dict:
    """JSON-safe URL rules for task 241; patterns use JS-compatible syntax.

    Path rules other than Amazon are full matches. Best Buy requires all
    skuId/path IDs to agree; eBay's selected var is deliberately unsupported.
    Unknown hosts use canonicalize_url's tracking rules, preserving other
    query pairs in order. URLs without a supported ID use the canonical URL;
    short links fail without resolving them.
    """
    return {"hosts": dict(HOSTS), "subdomains": True, "short_hosts": list(SHORT_HOSTS),
            "amazon": {"pattern": _ASIN_RE.pattern, "flags": "i", "uppercase": True},
            "bestbuy": {"paths": list(BESTBUY_PATHS), "query": "skuId",
                        "id": r"\d{1,16}", "require_agreement": True},
            "ebay": {"path": EBAY_PATH, "reject_query": "var"},
            "walmart": {"path": WALMART_PATH}, "woot": {"path": WOOT_PATH},
            "target": {"path": TARGET_PATH},
            "canonical": {"tracking_exact": sorted(_TRACKING_EXACT), "tracking_prefix": "utm_"},
            "fallback": "url:<canonical>", "schemes": ["http", "https"],
            "shard": {"hash": "SHA-256", "prefix_bits": True, "min_hex_width": 2}}


def _url(url):
    try:
        if not isinstance(url, str) or re.search(r'[\s\\<>"\x00-\x1f]', url):
            return None
        parts = urlsplit(url)
        if (parts.scheme not in {"https", "http"} or not parts.hostname
                or parts.username or parts.password or parts.port not in {None, 80, 443}):
            return None
        return parts
    except (ValueError, TypeError):
        return None


def store_key(url) -> str:
    parts = _url(url)
    host = (parts.hostname or "").lower() if parts else ""
    return next((store for domain, store in HOSTS.items()
                 if host == domain or host.endswith("." + domain)), "other")


def aliases(url) -> list[str]:
    """Exact URL identities only; never resolve a tracker or a short link."""
    parts = _url(url)
    if parts is None:
        return []
    host = parts.hostname.lower()
    if any(host == h or host.endswith("." + h) for h in SHORT_HOSTS):
        return []
    store = store_key(url)
    if store == "amazon":
        asin = extract_asin(url)
        if asin:
            return ["amazon:" + asin]
    if store in {"bestbuy", "ebay"}:
        found, identifier = store_offer({"canonical_url": url})
        if found:
            return [found + ":" + identifier]
    pattern = {"walmart": WALMART_PATH, "woot": WOOT_PATH, "target": TARGET_PATH}.get(store)
    if pattern:
        match = re.fullmatch(pattern, parts.path)
        if match:
            return [store + ":" + match[1]]
    return ["url:" + canonicalize_url(url)]


def _store(deal):
    # Amazon URL offers with an unrelated store name still have Amazon policy.
    if is_amazon_deal(deal):
        return "amazon"
    store = store_key(deal.get("canonical_url"))
    name = re.sub(r"[^a-z]", "", str(deal.get("store") or "").lower())
    return name if store == "other" and name in {"walmart", "ebay"} else store


def _clean(deal):
    return not (deal.get("locked") or deal.get("members_only") or deal.get("member_only")
                or deal.get("price_unconfirmed") or deal.get("flags") or deal.get("gone_suspect_at")
                or deal.get("suspect") or deal.get("flagged"))


def _price(price):
    return type(price) in {int, float} and math.isfinite(price) and price >= 0


def _plain(deal):
    conditions = set(normalize_conditions(deal.get("conditions"))) - {"new", "online_only", "online only"}
    return (plain_price(dict(deal, conditions=conditions)) and deal.get("basis") not in DEAL_TYPE_PCT
            and deal.get("deal_type") not in {*DEAL_TYPE_PCT, "free_after_rebate", "moneymaker"})


def _at(deal, now):
    from ..non_amazon_freshness import evidence_at

    # Preserve pre-death confirmations, never substitute the death/publish time.
    facts = dict(deal, death="") if deal.get("status") == "dead" else deal
    at = price_confirmed_at(facts) if is_amazon_deal(facts) else None
    if at is None or at > now:
        at = evidence_at(facts, now)
    return at if at and at <= now else None


def build(db, cfg, now, public, models, source=()) -> dict[str, dict]:
    """Alias → one live deal or last plain ended price; no DB writes.

    `public` is this render's unlocked wall/spotlight plus emitted ended deal
    pages. `models` is product_pages.prepare's already Amazon-filtered output.
    Unknown/deleted offer requirements cannot certify a plain history price.
    """
    now = now.astimezone(timezone.utc)
    cutoff = now - timedelta(days=cfg.checker["ended_days"])
    raw = {d["dedupe_key"]: d for d in db.all_deals() + list(source)}
    visible = {d["dedupe_key"]: d for d in public if not d.get("locked") and d.get("dedupe_key")}
    clean = {}
    for key, d in visible.items():
        facts = raw.get(key)
        if (facts is None or not _clean(d) or not _clean(facts)
                or not _price(d.get("final_price")) or d.get("final_price") != facts.get("final_price")):
            continue
        status = d.get("status")
        if status != facts.get("status"):
            continue
        if status == "alive" and not facts.get("death"):
            clean[key] = facts
        elif (status == "dead" and facts.get("death") in {"confirmed", "api_ended"}
              and (died := parse_iso(facts.get("died_at"))) and cutoff <= died <= now):
            clean[key] = facts

    # Include previously public ended offers even after they leave the grey wall.
    for page in models.values():
        for key in page["offers"]:
            facts = raw.get(key, {})
            died = parse_iso(facts.get("died_at"))
            if (facts.get("status") == "dead" and _clean(facts)
                    and facts.get("death") in {"confirmed", "api_ended"}
                    and died and cutoff <= died <= now and _price(facts.get("final_price"))):
                clean.setdefault(key, facts)

    history = {key for key, d in clean.items() if cfg.checker["stores"][_store(d)] == "history"
               and not amazon_history.hidden(cfg, d) and _plain(d)}
    page_keys = {product: {key for key in page["offers"] if key in history
                          and str(clean[key].get("product_id")) == product}
                 for product, page in models.items()}
    pages = {key: page for product, page in models.items() for key in page_keys[product]}
    paths = json.loads(db.meta_get("seo_deal_paths") or "{}")
    result = {}
    for key, facts in sorted(clean.items()):
        d = visible.get(key, {})
        store = _store(facts)
        policy = cfg.checker["stores"][store]
        live = facts.get("status") == "alive"
        if not live and (policy != "history" or key not in history):
            continue
        until = now if live else parse_iso(facts["died_at"])
        at = _at(facts, until)
        price = facts["final_price"]
        page = pages.get(key)
        if not live:
            checks = db.price_observations(key, cutoff, until)
            checks = [r for r in checks if _price(r.get("price"))]
            if checks:
                last = max(checks, key=lambda r: (parse_iso(r["observed_at"]), r["price"]))
                price, at = last["price"], parse_iso(last["observed_at"])
        if at is None or (not live and at < cutoff):
            continue
        entry = {"kind": "live" if live else "ended", "store": store, "item": facts["item"],
                 "at": iso(at), "deal_path": d.get("deal_path", "")}
        if not entry["deal_path"]:
            entry["deal_path"] = paths.get(hashlib.sha256(key.encode()).hexdigest()[:8], "")
        if policy != "link_only":
            entry.update(price=price, credit=d.get("credits") or (page or {}).get("offers", {}).get(key, {}).get("credits", []))
        if policy == "history":
            entry.update(p_path=page["path"] if page else "", verdict="", checks=0, since="")
            if page:
                allowed = page_keys[str(facts["product_id"])]
                checks = [r for r in page["checks"] if r["dedupe_key"] in allowed]
                offers = [o for k, o in page["offers"].items() if k in allowed]
                stats = product_price_facts(checks, offers, now)
                entry.update(checks=stats["checks"], since=iso(stats.get("first")),
                             verdict=product_pages.answer(price if live else None, stats, cfg))
        for alias in aliases(facts.get("canonical_url")):
            old = result.get(alias)
            order = lambda e: (e["kind"] == "live", e["at"], e["deal_path"], e["item"])
            if old is None or order(entry) > order(old):
                result[alias] = entry
    return dict(sorted(result.items()))


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def shard(alias, count):
    """Leading SHA256 bits; 256 buckets use exactly the first two hex digits."""
    bits = (count - 1).bit_length()
    digits = (bits + 3) // 4
    bucket = int(hashlib.sha256(alias.encode()).hexdigest()[:digits], 16) >> (digits * 4 - bits)
    return f'{bucket:0{max(2, digits)}x}'


def publish(out, cfg, now, entries):
    count = cfg.checker["shards"]
    width = max(2, len(f"{count - 1:x}"))
    buckets = {f'{i:0{width}x}': {} for i in range(count)}
    for alias, entry in entries.items():
        buckets[shard(alias, count)][alias] = entry
    dropped = 0
    payloads = {}
    for name, rows in buckets.items():
        size = 2 + sum(len(_encode(k)) + 1 + len(_encode(v)) + 1 for k, v in rows.items()) - bool(rows)
        for key in sorted((k for k in rows if rows[k]["kind"] == "ended"), key=lambda k: (rows[k]["at"], k)):
            if size < MAX_SHARD_BYTES:
                break
            size -= len(_encode(key)) + 1 + len(_encode(rows.pop(key))) + bool(rows)
            dropped += 1
        data = _encode(rows)
        if len(data) >= MAX_SHARD_BYTES:
            # Never silently discard a live offer or emit an oversized shard.
            raise ValueError("checker live entries exceed shard size; increase checker.shards")
        payloads[name + ".json"] = data
    kept = [entry for rows in buckets.values() for entry in rows.values()]
    payloads["meta.json"] = _encode({"generated_at": iso(now), "shards": count,
        "counts": {"store": dict(Counter(e["store"] for e in kept)),
                   "kind": dict(Counter(e["kind"] for e in kept))}, "stores": cfg.checker["stores"]})
    folder = out / "check" / "data"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    for name, data in sorted(payloads.items()):
        (folder / name).write_bytes(data)
    return {"aliases": len(kept), "live": sum(e["kind"] == "live" for e in kept),
            "ended": sum(e["kind"] == "ended" for e in kept),
            "link_only": sum(cfg.checker["stores"][e["store"]] == "link_only" for e in kept),
            "dropped_ended": dropped, "shards": count, "bytes": sum(map(len, payloads.values()))}
