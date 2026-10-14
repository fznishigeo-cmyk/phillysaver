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
from .util import iso, parse_iso

log = logging.getLogger("dealradar.store_prices")
BESTBUY_URL = "https://api.bestbuy.com/v1/products"
EBAY_URL = "https://api.ebay.com/buy/browse/v1/item/get_item_by_legacy_id"
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SECRETS = {"bestbuy": ("BESTBUY_API_KEY",),
           "ebay": ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET")}
NAMES = {"bestbuy": "Best Buy", "ebay": "eBay"}
COUNTERS = ("checked", "confirmed", "repriced", "suspect", "gone", "errors")


def store_offer(deal: dict) -> tuple[str, str]:
    """Exact US product identities, with tracking left untouched on the row."""
    try:
        url = urlsplit(deal.get("canonical_url") or "")
        host = (url.hostname or "").lower()
        if url.scheme not in {"http", "https"} or url.username or url.password:
            return "", ""
        query = parse_qs(url.query)
        if host == "bestbuy.com" or host.endswith(".bestbuy.com"):
            match = re.fullmatch(r"/site/.*[/-](\d{1,16})\.p", url.path)
            ids = query.get("skuId", []) + ([match[1]] if match else [])
            if ids and len(set(ids)) == 1 and re.fullmatch(r"\d{1,16}", ids[0]):
                return "bestbuy", ids[0]
        if host == "ebay.com" or host.endswith(".ebay.com"):
            # A legacy listing alone cannot verify a selected variation.
            match = re.fullmatch(r"/itm/(?:[^/]+/)?(\d{1,16})/?", url.path)
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


class StoreAPI:
    """One run's hard request cap, including OAuth and all retry attempts."""

    def __init__(self, store, cfg, fetcher, deadline=None):
        self.store, self.cfg, self.fetcher, self.deadline = store, cfg, fetcher, deadline
        self.remaining = getattr(cfg, store + "_max_requests_per_run")
        self.stopped = False
        self.token = ""
        self.token_expires = 0.0

    def request(self, method, url, **kwargs):
        response = None
        # httpx INFO includes the URL; httpcore DEBUG can include auth headers.
        loggers = [logging.getLogger(n) for n in ("httpx", "httpcore")]
        levels = [logger.level for logger in loggers]
        try:
            for logger in loggers:
                logger.setLevel(logging.WARNING)
            for attempt in range(max(1, self.cfg.max_retries)):
                if self.remaining <= 0 or self.stopped:
                    break
                self.fetcher._check_deadline(self.deadline)
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
                if response is not None and response.status_code != 429 \
                        and response.status_code < 500:
                    if response.status_code in (401, 403):
                        self.token = ""
                        self.stopped = True
                    return response
                if self.remaining and attempt + 1 < max(1, self.cfg.max_retries):
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
                return None
            self.token, self.token_expires = token, started + expires
        if self.remaining <= 0:
            return None
        response = self.request("GET", EBAY_URL, params={"legacy_item_id": identity},
                                headers={"Authorization": "Bearer " + self.token,
                                         "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"})
        body = self.body(response)
        if response is None:
            return None
        # Browse's specific unavailable-item error; arbitrary 404s prove nothing.
        errors = body.get("errors")
        if response.status_code == 404 and isinstance(errors, list) and any(
                isinstance(e, dict) and e.get("errorId") == 11001
                and e.get("domain") == "API_BROWSE" for e in errors):
            return Reading(False)
        return ebay_reading(body, identity, now) if response.status_code == 200 else None


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


def recheck_store_prices(db, cfg, now: datetime, *, fetcher=None, deadline=None) -> dict:
    """Suspects, top candidates, wall, then oldest unchecked; bounded per store."""
    from . import rank
    from .dedupe import reprice_existing
    from .parse.extractor import verify_evidence
    from .parse.schema import LLMDeal, to_extracted
    from .publish.site import PICKS_META, WALL_META
    from .savings import compute_savings
    from .verify import CONFIRM_GONE_H, is_used_deal, live_price_reading

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
            if last is not None and timedelta() <= now - last < timedelta(hours=every):
                continue
            if not rank.is_publishable(deal, cfg, require_critic=False,
                                       min_pct=min(cfg.min_pct, cfg.site_show_from_pct))[0]:
                continue
            priority = (0 if deal.get("gone_suspect_at") else 1 if deal["dedupe_key"] in picks
                        else 2 if deal["dedupe_key"] in wall else 3)
            due.append((priority, iso(last) if last else "", deal["dedupe_key"], identity, deal))
        due.sort(key=lambda d: d[:3])
        if not due or getattr(cfg, store + "_max_requests_per_run") <= 0:
            continue
        grouped = {}
        for *_, identity, deal in due:
            grouped.setdefault(identity, []).append(deal)
        ids = list(grouped)
        batch_size = 100 if store == "bestbuy" else 1
        counts = stats[store]
        with api_fetcher(fetcher, db, cfg) as client:
            api = StoreAPI(store, cfg, client, deadline)
            for start in range(0, len(ids), batch_size):
                if api.remaining <= 0 or api.stopped:
                    break
                batch = ids[start:start + batch_size]
                try:
                    client._check_deadline(deadline)
                    readings = api.bestbuy(batch) if store == "bestbuy" else {batch[0]: api.ebay(batch[0], now)}
                except DeadlineExceeded:
                    break
                for identity in batch:
                    for deal in grouped[identity]:
                        reading = readings.get(identity)
                        payload = dict(deal)
                        if reading is None:
                            counts["errors"] += 1
                            payload["recheck_tried_at"] = iso(now)
                            db.update_deal(deal["dedupe_key"], payload=payload)
                            continue
                        verdict = False if not reading.available else None
                        if reading.available and plain_price(deal):
                            post = reprice_post(store, reading, deal, now)
                            if post is not None:
                                # Shipping arithmetic stays in savings.py, as at ingest.
                                ext = to_extracted(LLMDeal.model_validate(post.structured), post, "UTC", now)
                                live = (compute_savings(ext, allow_price_only=True).final_price
                                        if verify_evidence(ext, post.text)[0] else None)
                                if live is not None:
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
        log.info("%s live checks: %s", store, counts)
    return stats
