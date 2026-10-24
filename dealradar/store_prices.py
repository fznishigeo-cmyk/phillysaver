"""Official Best Buy/eBay price checks; API bodies and OAuth stay in memory."""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx

from .config import get_secret, error_summary
from .errors import DeadlineExceeded
from .http_client import api_fetcher
from .models import RawPost
from .util import iso, parse_iso, now_utc

log = logging.getLogger("dealradar.store_prices")
BESTBUY_URL = "https://api.bestbuy.com/v1/products"
EBAY_URL = "https://api.ebay.com/buy/browse/v1/item/get_item_by_legacy_id"
EBAY_GROUP_URL = "https://api.ebay.com/buy/browse/v1/item/get_items_by_item_group"
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SECRETS = {"bestbuy": ("BESTBUY_API_KEY",),
           "ebay": ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET")}
NAMES = {"bestbuy": "Best Buy", "ebay": "eBay"}
COUNTERS = ("checked", "confirmed", "repriced", "suspect", "gone", "errors")
BESTBUY_PATHS = (r"/site/.*[/-](\d{1,16})\.p", r"/product/.+/sku/(\d{1,16})/?")
EBAY_PATH = r"/itm/(?:[^/]+/)?(\d{1,16})/?"


def store_offer(deal: dict) -> tuple[str, str]:
    """Exact US product identities, with tracking left untouched on the row."""
    try:
        url = urlsplit(deal.get("canonical_url") or "")
        host = (url.hostname or "").lower()
        if url.scheme not in {"http", "https"} or url.username or url.password:
            return "", ""
        query = parse_qs(url.query)
        if host == "bestbuy.com" or host.endswith(".bestbuy.com"):
            match = next((m for pattern in BESTBUY_PATHS if (m := re.fullmatch(pattern, url.path))), None)
            ids = query.get("skuId", []) + ([match[1]] if match else [])
            if ids and len(set(ids)) == 1 and re.fullmatch(r"\d{1,16}", ids[0]):
                return "bestbuy", ids[0]
        if host == "ebay.com" or host.endswith(".ebay.com"):
            # A legacy listing alone cannot verify a selected variation.
            match = re.fullmatch(EBAY_PATH, url.path)
            if match and not query.get("var"):
                return "ebay", match[1]
    except (ValueError, TypeError):
        pass
    return "", ""


def enabled(store: str, cfg) -> bool:
    return bool(store and cfg.feature_flags.get(store + "_live")
                and all(get_secret(name) for name in SECRETS[store]))


def _money(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, OverflowError):
        return None


def _usd(value) -> float | None:
    return _money(value.get("value")) if isinstance(value, dict) \
        and value.get("currency") == "USD" else None


@dataclass(frozen=True)
class Reading:
    available: bool
    price: float | None = None
    regular: float | None = None
    group: bool = False


def bestbuy_reading(product: dict) -> Reading | None:
    online, orderable = product.get("onlineAvailability"), product.get("orderable")
    if online is False or orderable is False or orderable in ("Not orderable", "Sold Out"):
        return Reading(False)
    if online is not True or not (orderable is True or orderable == "Available"):
        return None
    price = _money(product.get("salePrice"))
    if price is None:
        return None
    regular = _money(product.get("regularPrice"))
    return Reading(True, price, regular if regular is not None and regular > price else None)


def ebay_reading(item: dict, identity: str, now: datetime) -> Reading | None:
    if str(item.get("legacyItemId") or "") != identity:
        return None
    raw_end = item.get("itemEndDate")
    if raw_end is not None and not isinstance(raw_end, str):
        return None
    end = parse_iso(raw_end or "")
    if raw_end and end is None:
        return None
    if end is not None and end <= now:
        return Reading(False)
    availability = item.get("estimatedAvailabilities")
    if availability is not None and not isinstance(availability, list):
        return None
    if availability:
        if any(not isinstance(a, dict) for a in availability):
            return None
        if all(a.get("estimatedAvailabilityStatus") == "OUT_OF_STOCK"
               or (type(a.get("estimatedAvailableQuantity")) is int
                   and a["estimatedAvailableQuantity"] == 0) for a in availability):
            return Reading(False)
        if any(a.get("estimatedAvailabilityStatus") not in (None, "IN_STOCK", "LIMITED_STOCK", "OUT_OF_STOCK")
               or ("estimatedAvailableQuantity" in a
                   and (type(a["estimatedAvailableQuantity"]) is not int
                        or a["estimatedAvailableQuantity"] < 0)) for a in availability):
            return None
    # An auction's current bid is not a buyable deal price.
    buying = item.get("buyingOptions")
    if not isinstance(buying, list) or "FIXED_PRICE" not in buying:
        return None
    item_id = item.get("itemId")
    if item_id is not None and item_id != f"v1|{identity}|0":
        return None
    price = _usd(item.get("price"))
    if price is None:
        return None
    marketing = item.get("marketingPrice")
    regular = _usd(marketing.get("originalPrice")) if isinstance(marketing, dict) else None
    return Reading(True, price, regular if regular is not None and regular > price else None)


def ebay_group_reading(body: dict, identity: str, now: datetime) -> Reading | None:
    """A complete group proves its lowest buyable USD price, never a reference price."""
    items = body.get("items")
    if not isinstance(items, list):
        return None
    prices = []
    for item in items:
        if not isinstance(item, dict) or str(item.get("legacyItemId") or "") != identity:
            return None
        item_id = item.get("itemId")
        if not isinstance(item_id, str) or not re.fullmatch(r"v1\|" + re.escape(identity) + r"\|\d+", item_id):
            return None
        raw_end = item.get("itemEndDate")
        if raw_end is not None and (not isinstance(raw_end, str) or parse_iso(raw_end) is None):
            return None
        end = parse_iso(raw_end)
        buying, price = item.get("buyingOptions"), item.get("price")
        if not isinstance(buying, list) or not buying or any(not isinstance(b, str) for b in buying):
            return None
        if not isinstance(price, dict) or not isinstance(price.get("currency"), str) \
                or not price["currency"] or _money(price.get("value")) is None:
            return None
        availability = item.get("estimatedAvailabilities")
        if not isinstance(availability, list) or not availability:
            return None
        in_stock = False
        for available in availability:
            if not isinstance(available, dict):
                return None
            status = available.get("estimatedAvailabilityStatus")
            quantity = available.get("estimatedAvailableQuantity")
            if status not in (None, "IN_STOCK", "LIMITED_STOCK", "OUT_OF_STOCK") \
                    or ("estimatedAvailableQuantity" in available
                        and (type(quantity) is not int or quantity < 0)) \
                    or (status is None and quantity is None):
                return None
            if status != "OUT_OF_STOCK" and quantity != 0:
                in_stock = True
        if "FIXED_PRICE" in buying and price["currency"] == "USD" \
                and (end is None or end > now) and in_stock:
            prices.append(_usd(price))
    return Reading(True, min(prices), group=True) if prices else Reading(False)


class StoreAPI:
    """One run's hard request cap, including OAuth and all retry attempts."""

    def __init__(self, store, cfg, fetcher, deadline=None, run_key=None):
        self.store, self.cfg, self.fetcher, self.deadline = store, cfg, fetcher, deadline
        self.remaining = getattr(cfg, store + "_max_requests_per_run")
        self.stopped = False
        self.token = ""
        self.token_expires = 0.0
        self.diagnostics = {}
        self.now = now_utc()
        self.run_key = run_key
        self.run_requests = 0
        if run_key:
            receipt = json.loads(fetcher.db.meta_get("store_api_run:" + store) or "{}")
            self.run_requests = receipt.get("requests", 0) if receipt.get("run") == run_key else 0
            self.remaining = max(0, self.remaining - self.run_requests)

    def warning(self, phase, status, code=""):
        # Never log response bodies, URLs, auth headers, or arbitrary API text.
        reason = f"{phase}:" + (f"http_{status}" if isinstance(status, int) else str(status))
        reason += ":" + code if code else ""
        self.diagnostics[reason] = self.diagnostics.get(reason, 0) + 1
        log.warning("%s live check %s", self.store, reason)

    def reserve_request(self):
        key = "store_api_daily:" + self.store
        day = self.now.date().isoformat()
        try:
            usage = json.loads(self.fetcher.db.meta_get(key) or "{}")
        except (TypeError, ValueError):
            usage = {}
        count = usage.get("requests", 0) if usage.get("day") == day else 0
        if count >= getattr(self.cfg, self.store + "_max_requests_per_day"):
            self.stopped = True
            self.warning("quota", "daily_cap")
            return False
        self.fetcher.db.meta_set(key, json.dumps({"day": day, "requests": count + 1}))
        if self.run_key:
            self.run_requests += 1
            self.fetcher.db.meta_set("store_api_run:" + self.store,
                json.dumps({"run": self.run_key, "requests": self.run_requests}))
        return True

    def request(self, method, url, *, max_attempts=None, **kwargs):
        response = None
        attempts = max(1, self.cfg.max_retries if max_attempts is None else max_attempts)
        phase = "token" if url == TOKEN_URL else "group" if url == EBAY_GROUP_URL else "product"
        # httpx INFO includes the URL; httpcore DEBUG can include auth headers.
        loggers = [logging.getLogger(n) for n in ("httpx", "httpcore")]
        levels = [logger.level for logger in loggers]
        try:
            for logger in loggers:
                logger.setLevel(logging.WARNING)
            for attempt in range(attempts):
                if self.remaining <= 0 or self.stopped:
                    if self.remaining <= 0:
                        self.warning("quota", "run_cap")
                    break
                self.fetcher._check_deadline(self.deadline)
                if not self.reserve_request():
                    break
                self.remaining -= 1
                try:
                    options = dict(deadline=self.deadline, respect_robots=False, max_attempts=1)
                    if method == "GET":
                        response = self.fetcher.api_get(url, conditional=False,
                                                        follow_redirects=False, **options, **kwargs)
                    else:
                        response = self.fetcher.api_post(url, **options, **kwargs)
                except httpx.HTTPError as exc:
                    response = None
                    log.warning("%s live check: %s", self.store, error_summary(exc))
                    self.warning(phase, "transport")
                if response is not None and response.status_code >= 300:
                    code = ""
                    if url == TOKEN_URL:
                        error = self.body(response).get("error")
                        code = error if error in ("invalid_client", "invalid_scope", "invalid_request",
                                                  "unauthorized_client", "unsupported_grant_type") else "other"
                    if self.store == "ebay" and response.status_code >= 400:
                        errors = self.body(response).get("errors")
                        ids = [str(e["errorId"]) for e in errors
                               if isinstance(e, dict) and type(e.get("errorId")) is int
                               and e.get("domain") in ("API_BROWSE", "ACCESS")] \
                            if isinstance(errors, list) else []
                        code = ":".join(([code] if code else []) + ids[:3])
                    self.warning(phase, response.status_code, code)
                if response is not None and response.status_code != 429 \
                        and response.status_code < 500:
                    if response.status_code in (401, 403):
                        self.token = ""
                        self.stopped = True
                    return response
                if self.remaining and attempt + 1 < attempts:
                    self.fetcher._backoff(attempt, response.headers.get("Retry-After")
                                         if response is not None else None, self.deadline)
            self.stopped = True  # do not hammer an unhealthy/rate-limited API
            return response
        finally:
            for logger, level in zip(loggers, levels):
                logger.setLevel(level)

    @staticmethod
    def body(response) -> dict:
        try:
            value = response.json() if response is not None else None
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}

    def bestbuy(self, ids: list[str]) -> dict[str, Reading | None]:
        response = self.request("GET", BESTBUY_URL + "(sku in(" + ",".join(ids) + "))",
                                params={"apiKey": get_secret("BESTBUY_API_KEY"), "format": "json",
                                        "pageSize": 100,
                                        "show": "sku,salePrice,regularPrice,onSale,orderable,onlineAvailability,url"})
        body = self.body(response)
        products = body.get("products")
        if response is None or response.status_code != 200 or not isinstance(products, list):
            return {}
        result = {}
        for product in products:
            if not isinstance(product, dict):
                continue
            identity = str(product.get("sku") or "")
            if identity in ids:
                # Conflicting duplicate products cannot establish a price.
                result[identity] = bestbuy_reading(product) if identity not in result else None
        return result  # an omitted SKU is unknown, never proof of a sold item

    def ebay(self, identity: str, now: datetime) -> Reading | None:
        if not self.token or self.fetcher._clock() >= self.token_expires:
            self.token = ""
            started = self.fetcher._clock()
            response = self.request("POST", TOKEN_URL,
                                    data={"grant_type": "client_credentials",
                                          "scope": "https://api.ebay.com/oauth/api_scope"},
                                    auth=(get_secret("EBAY_CLIENT_ID"), get_secret("EBAY_CLIENT_SECRET")))
            body = self.body(response)
            expires = _money(body.get("expires_in"))
            token = body.get("access_token")
            if response is None or response.status_code != 200 or not isinstance(token, str) \
                    or not token or expires is None or expires <= 0:
                self.stopped = True
                if response is not None and response.status_code == 200:
                    self.warning("token", 200, "invalid_response")
                return None
            self.token, self.token_expires = token, started + expires
        if self.remaining <= 0:
            self.warning("quota", "run_cap")
            return None
        headers = {"Authorization": "Bearer " + self.token,
                   "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"}
        response = self.request("GET", EBAY_URL, params={"legacy_item_id": identity},
                                headers=headers)
        body = self.body(response)
        if response is None:
            return None
        if response.status_code == 400:
            response = self.request("GET", EBAY_GROUP_URL, params={"item_group_id": identity},
                                    headers=headers, max_attempts=1)
            if response is None or response.status_code != 200:
                return None
            reading = ebay_group_reading(self.body(response), identity, now)
            if reading is None:
                self.warning("group", 200, "invalid_response")
            return reading
        # Browse's specific unavailable-item error; arbitrary 404s prove nothing.
        errors = body.get("errors")
        if response.status_code == 404 and isinstance(errors, list) and any(
                isinstance(e, dict) and e.get("errorId") == 11001
                and e.get("domain") == "API_BROWSE" for e in errors):
            return Reading(False)
        reading = ebay_reading(body, identity, now) if response.status_code == 200 else None
        if reading is None and response.status_code == 200:
            self.warning("product", 200, "invalid_response")
        return reading


def plain_price(deal: dict) -> bool:
    """Only terms the listing price itself can prove, without checkout offers."""
    from .savings import parse_stack_step
    from .verify import _price_hidden_by_promo
    return not (_price_hidden_by_promo(deal) or deal.get("rebates") or deal.get("deferred_value")
                or any(parse_stack_step(step)["kind"] != "sale_price" for step in deal.get("stack_steps") or [])
                or set(deal.get("conditions") or []) - {"used", "open_box", "refurbished", "limited_qty"}
                or set(deal.get("flags") or []) & {"range", "per_unit", "rebate_dependent"})


def reprice_post(store: str, reading: Reading, deal: dict, now: datetime) -> RawPost | None:
    from .verify import is_used_deal
    if not reading.available or reading.price is None or not plain_price(deal) \
            or (store == "bestbuy" and is_used_deal(deal)):
        return None
    name = NAMES[store]
    final = f"Price at {name}: ${reading.price:,.2f}."
    regular = f"Regular price at {name}: ${reading.regular:,.2f}." if reading.regular is not None else ""
    shipping = deal.get("shipping_cost_or_threshold") or ""
    ship_quote = f"Previously stated shipping: {shipping}" if shipping else ""
    text = "\n".join(line for line in (final, regular, ship_quote) if line)
    return RawPost(
        source_name=store + "_live", uid="reprice:" + deal["dedupe_key"],
        url=deal["canonical_url"], title=deal["item"], text=text, published_at=now, fetched_at=now,
        hash_text=text + "\nReading: " + iso(now),
        structured={"item": deal["item"], "store": deal["store"],
                    "product_url": deal["canonical_url"], "online_only": "true",
                    "final_price": reading.price, "regular_price": reading.regular,
                    "regular_price_basis": "as stated by " + name if reading.regular is not None else "",
                    "shipping_cost_or_threshold": shipping, "confidence": 1.0,
                    "evidence": {"final_price_quote": final, "regular_price_quote": regular,
                                 "shipping_quote": ship_quote}})


def recheck_store_prices(db, cfg, now: datetime, *, fetcher=None, deadline=None, run_key=None) -> dict:
    """Suspects, then oldest checked; picks/wall break ties, bounded per store."""
    from . import rank
    from .dedupe import reprice_existing
    from .parse.extractor import verify_evidence
    from .parse.schema import LLMDeal, to_extracted
    from .publish.site import PICKS_META, WALL_META
    from .savings import compute_savings
    from .verify import CONFIRM_GONE_H, is_used_deal, live_price_reading
    from . import accuracy, price_checks

    stats = {store: dict.fromkeys(COUNTERS, 0) for store in SECRETS}

    def remembered(key):
        try:
            value = json.loads(db.meta_get(key) or "[]")
            return set(value) if isinstance(value, list) else set()
        except (TypeError, ValueError):
            return set()

    picks, wall = remembered(PICKS_META), remembered(WALL_META)
    for store in SECRETS:
        if not cfg.feature_flags.get(store + "_live"):
            continue
        if not enabled(store, cfg):
            log.info("%s live checks skipped: missing secrets", store)
            continue
        due = []
        for deal in db.all_deals(status="alive"):
            identity_store, identity = store_offer(deal)
            if identity_store != store or deal.get("final_price") is None \
                    or (store == "bestbuy" and is_used_deal(deal)):
                continue
            every = CONFIRM_GONE_H if deal.get("gone_suspect_at") else getattr(cfg, store + "_recheck_h")
            stamps = [parse_iso(deal.get(k) or "") for k in ("price_rechecked_at", "recheck_tried_at")]
            last = max((t for t in stamps if t is not None), default=None)
            if (run_key and deal.get("store_recheck_run") == run_key) or (last is not None and
                    (last >= now or now - last < timedelta(hours=every))):
                continue
            if not rank.is_publishable(deal, cfg, require_critic=False,
                                       min_pct=min(cfg.min_pct, cfg.site_show_from_pct))[0]:
                continue
            priority = (0 if deal.get("gone_suspect_at") else 1 if deal["dedupe_key"] in picks
                        else 2 if deal["dedupe_key"] in wall else 3)
            due.append((priority, iso(last) if last else "", deal["dedupe_key"], identity, deal))
        # When checks are due every run, rotate oldest first so a large top set
        # cannot permanently starve the rest of the store's inventory.
        due.sort(key=lambda d: (0 if d[0] == 0 else 1, d[1], d[0], d[2]))
        if not due or getattr(cfg, store + "_max_requests_per_run") <= 0:
            continue
        grouped = {}
        for *_, identity, deal in due:
            grouped.setdefault(identity, []).append(deal)
        ids = list(grouped)
        batch_size = 100 if store == "bestbuy" else 1
        counts = stats[store]
        with api_fetcher(fetcher, db, cfg) as client:
            api = StoreAPI(store, cfg, client, deadline, run_key)
            api.now = now
            for start in range(0, len(ids), batch_size):
                if api.remaining <= 0 or api.stopped:
                    break
                batch = ids[start:start + batch_size]
                for identity in batch:
                    for deal in grouped[identity]:
                        if run_key:
                            deal["store_recheck_run"] = run_key
                            db.update_deal(deal["dedupe_key"], payload=deal)
                try:
                    client._check_deadline(deadline)
                    diagnostic_count = sum(api.diagnostics.values())
                    readings = api.bestbuy(batch) if store == "bestbuy" else {batch[0]: api.ebay(batch[0], now)}
                except DeadlineExceeded:
                    break
                for identity in batch:
                    for deal in grouped[identity]:
                        reading = readings.get(identity)
                        payload = dict(deal)
                        if reading is None:
                            counts["errors"] += 1
                            if sum(api.diagnostics.values()) == diagnostic_count:
                                api.warning("product", "invalid_response")
                            diagnostic_count += 1
                            payload["recheck_tried_at"] = iso(now)
                            db.update_deal(deal["dedupe_key"], payload=payload)
                            continue
                        verdict = False if not reading.available else None
                        if not reading.available or not plain_price(deal):
                            accuracy.record(db, cfg, deal["dedupe_key"], now, source=store,
                                            price=reading.price, unavailable=not reading.available, run_key=run_key)
                        if reading.group and plain_price(deal) and reading.price <= deal["final_price"] + .01:
                            # Group prices prove the advertised price is still attainable;
                            # do not replace it with another size's price or add shipping twice.
                            verdict = True
                            accuracy.record(db, cfg, deal["dedupe_key"], now, source=store,
                                            price=reading.price, run_key=run_key)
                        elif reading.available and plain_price(deal):
                            post = reprice_post(store, reading, deal, now)
                            if post is not None:
                                # Shipping arithmetic stays in savings.py, as at ingest.
                                ext = to_extracted(LLMDeal.model_validate(post.structured), post, "UTC", now)
                                live = (compute_savings(ext, allow_price_only=True,
                                                        accuracy_lite=cfg.savings_accuracy_lite).final_price
                                        if verify_evidence(ext, post.text)[0] else None)
                                if live is not None:
                                    accuracy.record(db, cfg, deal["dedupe_key"], now, source=store,
                                                    price=live, run_key=run_key)
                                    verdict = live <= deal["final_price"] * (1 + getattr(cfg, store + "_tolerance_pct") / 100) + .01
                                if reprice_existing(db, deal, post, cfg, now):
                                    counts["repriced"] += 1
                                    verdict = True  # corrected prices, including rises, stay alive
                                payload = dict(deal)
                        if verdict is None:
                            payload["recheck_tried_at"] = iso(now)
                            db.update_deal(deal["dedupe_key"], payload=payload)
                            continue
                        counts["checked"] += 1
                        outcome = live_price_reading(payload, verdict, now)
                        counts[{"live": "confirmed", "suspect": "suspect", "waiting": "suspect", "dead": "gone"}[outcome]] += 1
                        db.update_deal(deal["dedupe_key"], payload=payload,
                                       status="dead" if outcome == "dead" else None)
                        if outcome == "live" and not reading.group:
                            price_checks.record(db, cfg, payload, reading.price, now, now, source=store)
            if api.diagnostics:
                counts["diagnostics"] = api.diagnostics
        log.info("%s live checks: %s", store, counts)
    return stats
