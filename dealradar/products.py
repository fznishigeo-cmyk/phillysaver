"""Conservative, local-only product links. Existing deal dedupe is unchanged."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from urllib.parse import parse_qs, urlsplit

from .conditions import normalize_conditions, USED_CONDITIONS


def _exact(value) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return " ".join(unicodedata.normalize("NFC", str("" if value is None else value)).split()).casefold()


def condition(deal: dict) -> str:
    stated = normalize_conditions(deal.get("conditions")) + normalize_conditions(deal.get("condition"))
    labels = set(stated) & USED_CONDITIONS
    return "/".join(sorted(labels)) or "new"


def _gtin(value) -> str:
    value = str(value or "").strip()
    if not re.fullmatch(r"(?:\d{8}|\d{12}|\d{13}|\d{14})", value) or not int(value):
        return ""
    digits = list(map(int, value))
    if (sum(n * (3 if i % 2 == 0 else 1) for i, n in enumerate(reversed(digits[:-1])))
            + digits[-1]) % 10:
        return ""
    return value.zfill(14)


def identity(deal: dict) -> tuple[list[tuple[str, str]], str]:
    """Keys in strength order, plus exact variant guards (never title matching).

    Titles are a veto, not an identity: even identical titles cannot link
    without an identifier or explicit brand/model. Keeping the full title
    guards unstructured sizes/colors, including words we do not recognize.
    """
    keys = []
    asin = str(deal.get("asin") or "").strip().upper()
    asins = {asin} if re.fullmatch(r"[A-Z0-9]{10}", asin) else set()
    skus = []
    store = re.sub(r"[^a-z0-9]", "", _exact(deal.get("store")))
    for field, namespace in (("sku", store), ("bestbuy_sku", "bestbuy"),
                             ("ebay_legacy_id", "ebay"), ("woot_id", "woot")):
        value = _exact(deal.get(field))
        if value and namespace:
            skus.append(("sku:" + namespace, value))
    selection = {}
    try:
        url = urlsplit(deal.get("canonical_url") or "")
        host = (url.hostname or "").lower().removeprefix("www.")
        if url.scheme in {"http", "https"} and not (url.username or url.password):
            from .http_client import extract_asin
            from .store_prices import store_offer

            if host == "amazon.com" or host.endswith(".amazon.com"):
                linked_asin = extract_asin(deal.get("canonical_url") or "")
                if linked_asin:
                    asins.add(linked_asin.upper())
            namespace, sku = store_offer(deal)
            if namespace and sku:
                skus.append(("sku:" + namespace, sku))
            # These URLs identify a listing, never a title-derived model.
            if host == "woot.com" or host.endswith(".woot.com"):
                match = re.fullmatch(r"/offers/([^/]+)/?", url.path)
                if match:
                    skus.append(("sku:woot_url", match[1]))
            if host == "walmart.com" or host.endswith(".walmart.com"):
                match = re.fullmatch(r"/ip/(?:[^/]+/)?(\d+)/?", url.path)
                if match:
                    skus.append(("sku:walmart", match[1]))
            query = parse_qs(url.query)
            selection = {k.casefold(): sorted(v) for k, v in query.items()
                         if k.casefold() in {"var", "variation", "variationid", "size", "color", "quantity"}}
    except (ValueError, TypeError):
        pass
    gtins = {_gtin(deal.get(k)) for k in ("gtin", "upc", "ean")} - {""}
    if len(asins) == 1:
        keys.append(("asin", next(iter(asins))))
    if len(gtins) == 1:
        keys.append(("gtin", next(iter(gtins))))
    keys.extend(sorted(set(skus)))
    brand = _exact(deal.get("brand"))
    model = _exact(deal.get("model") or deal.get("model_number"))
    models = {_exact(deal[k]) for k in ("model", "model_number") if deal.get(k)}
    if (brand and re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", model)
            and len(re.sub(r"[^a-z0-9]", "", model)) >= 4
            and re.search(r"[a-z]", model) and re.search(r"\d", model)
            and not re.fullmatch(r"\d+(?:gb|tb|mb|mhz|ghz|hz|w|v|mah|mm|cm|ml|oz|inch|in|p)", model)):
        keys.append(("brand_model", json.dumps([brand, model], separators=(",", ":"))))
    contradictory_sku = any(len({v for k, v in skus if k == kind}) > 1 for kind, _ in skus)
    if (len(asins) > 1 or len(gtins) > 1 or len(models) > 1 or contradictory_sku or deal.get("link_shared")
            or not _exact(deal.get("item"))):
        keys = []  # contradictory identifiers / a sale page identify nothing
    guards = {k: _exact(deal.get(k)) for k in (
        "item", "brand", "quantity_or_unit", "quantity", "pack_size", "capacity", "size", "color",
        "variant", "condition", "condition_grade") if deal.get(k) not in (None, "")}
    if model:
        guards["model"] = model
    if deal.get("link_shared"):
        guards["link_shared"] = True
    guards.update(state=condition(deal), currency=_exact(deal.get("currency") or "USD"),
                  region=_exact(deal.get("region") or "US"))
    if selection:
        guards["selection"] = selection
    return keys, json.dumps(guards, sort_keys=True, separators=(",", ":"))


def _compatible(conn, product_id: int, keys: list[tuple[str, str]]) -> bool:
    old = {}
    for kind, value in conn.execute("SELECT kind, value FROM product_keys WHERE product_id=?", (product_id,)):
        old.setdefault(kind, set()).add(value)
    new = {}
    for kind, value in keys:
        new.setdefault(kind, set()).add(value)
    # eBay and Woot identifiers name listings: a re-listing may have a new
    # one while an exact ASIN/GTIN/brand-model still identifies the product.
    listings = {"sku:ebay", "sku:woot", "sku:woot_url"}
    return all(old[k] == new[k] for k in (old.keys() & new.keys()) - listings)


def attach(conn, deal: dict) -> None:
    """Attach within the caller's transaction; no product coalescing or deal edits.

    A changed variant on an already linked row is quarantined: its old
    observations must not be silently attributed to the replacement item.
    """
    keys, signature = identity(deal)
    fingerprint = hashlib.sha256(json.dumps([keys, signature]).encode()).digest()[:16]
    key = deal["dedupe_key"]
    first, last = deal["first_seen_at"], deal["last_seen_at"]
    previous = conn.execute("SELECT * FROM product_offers WHERE dedupe_key=?", (key,)).fetchone()
    product_id = previous["product_id"] if previous else None
    reason = previous["match"] if previous else "unresolved"
    if previous is None or previous["fingerprint"] != fingerprint:
        old = conn.execute("SELECT signature FROM products WHERE product_id=?", (product_id,)).fetchone()
        if previous and (reason == "conflict" or (old and (
                old[0] != signature or (not keys and reason != "unresolved")
                or not _compatible(conn, product_id, keys)))):
            product_id, reason = None, "conflict"
        else:
            hits = {}
            for kind, value in keys:
                for row in conn.execute(
                        "SELECT p.product_id FROM product_keys k JOIN products p USING(product_id)"
                        " WHERE k.kind=? AND k.value=? AND p.signature=?", (kind, value, signature)):
                    hits.setdefault(row[0], kind.split(":")[0])
            if product_id is not None:
                # Existing identities are durable: do not union two established
                # entities when a new bridge key appears on a later sighting.
                if set(hits) - {product_id}:
                    product_id, reason = None, "conflict"
            elif len(hits) == 1:
                candidate = next(iter(hits))
                if _compatible(conn, candidate, keys):
                    product_id, reason = candidate, hits[candidate]
            elif len(hits) > 1:
                reason = "conflict"
            if product_id is None and reason != "conflict":
                product_id = conn.execute(
                    "INSERT INTO products(signature, first_seen_at, last_seen_at) VALUES (?,?,?)",
                    (signature, first, last)).lastrowid
            if product_id is not None:
                if reason == "unresolved":
                    reason = next((kind.split(":")[0] for kind, _ in keys), "unresolved")
                conn.executemany("INSERT OR IGNORE INTO product_keys(kind,value,product_id) VALUES (?,?,?)",
                                 [(kind, value, product_id) for kind, value in keys])
    conn.execute(
        "INSERT INTO product_offers VALUES (?,?,?,?,?,?,?,?)"
        " ON CONFLICT(dedupe_key) DO UPDATE SET product_id=excluded.product_id, store=excluded.store,"
        " condition=excluded.condition, match=excluded.match, fingerprint=excluded.fingerprint,"
        " first_seen_at=min(product_offers.first_seen_at,excluded.first_seen_at),"
        " last_seen_at=max(product_offers.last_seen_at,excluded.last_seen_at)",
        (key, product_id, deal["store"], condition(deal), reason, fingerprint, first, last))
    if product_id is not None:
        conn.execute("UPDATE products SET first_seen_at=min(first_seen_at,?),"
                     " last_seen_at=max(last_seen_at,?) WHERE product_id=?", (first, last, product_id))
    conn.execute("UPDATE deals SET product_id=? WHERE dedupe_key=?", (product_id, key))
