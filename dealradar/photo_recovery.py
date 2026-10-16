"""Bounded photo recovery from existing links and licensed Keepa facts only."""

import json
import math
import re
import time
from urllib.parse import urlsplit

from . import dedupe
from .config import error_summary, get_secret
from .errors import DeadlineExceeded
from .http_client import resolve_redirects
from .keepa_budget import KeepaBudget, MIN_FOR_IMAGE_BACKFILL, response_payload
from .product_images import _dice, _unwrap, name_words
from .verify import _current_price, _harvest_image, is_amazon_deal

SEARCH_META = "photo_keepa_search"


def _save(db, deal, **changes):
    deal.update(changes)
    db.update_deal(deal["dedupe_key"], payload={
        k: deal[k] for k in dedupe._DEAL_FIELDS if k in deal})


def short_targets(links, fetcher, cfg, deadline, cache, deferred=None):
    """Share one per-run cap/cache across posts and existing deals; HEAD only."""
    targets = []
    timed_out = False
    for link in links:
        for _ in range(5):
            try:
                inner = _unwrap(link)
            except ValueError:
                inner = ""
            if not inner or inner == link:
                break
            link = inner
        try:
            host = (urlsplit(link).hostname or "").lower()
        except ValueError:
            continue
        if host not in ("amzn.to", "a.co"):
            continue
        if link not in cache:
            if (len(cache) >= cfg.images_short_links_per_run or timed_out
                    or (deadline is not None and time.monotonic() >= deadline)):
                if deferred is not None:
                    deferred.add(link)
                continue
            cache[link] = ""  # even a timeout consumes this chain's allowance
            try:
                cache[link] = resolve_redirects(fetcher, link, cfg.redirect_max_hops,
                                                deadline=deadline, head_only=True)
            except DeadlineExceeded:
                timed_out = True
                cache[link] = None  # keep interrupted chains deferred for later posts too
        if cache[link] is None:
            if deferred is not None:
                deferred.add(link)
        elif cache[link]:
            targets.append(cache[link])
    return targets


def resolve_posts(pairs, fetcher, cfg, deadline):
    if fetcher is None:
        return
    cache = getattr(fetcher, "photo_short_links", None)
    if cache is None:
        cache = fetcher.photo_short_links = {}
    seen = set()
    for post, extracted in pairs:
        if post is None or post.via_email or id(post) in seen or not is_amazon_deal({"store": extracted.store}):
            continue
        seen.add(id(post))
        links = dedupe._URL_RE.findall(post.text or "") + list(post.photos.links)
        links += [extracted.product_url, extracted.link_as_posted]
        post.resolved_links = []
        deferred = set()
        targets = short_targets(links, fetcher, cfg, deadline, cache, deferred)
        try:
            if not deferred and all((urlsplit(target).hostname or "").lower()
                                    not in ("amzn.to", "a.co") for target in targets):
                post.resolved_links = targets
        except ValueError:
            continue


def backfill_asins(db, deals, fetcher, cfg, deadline):
    """One bounded attempt per existing live Amazon deal; keep click/credit links."""
    cache = getattr(fetcher, "photo_short_links", None)
    if cache is None:
        cache = fetcher.photo_short_links = {}
    tried = found = 0
    for deal in deals:
        if tried >= cfg.images_asin_backfill_per_run or (deadline is not None and time.monotonic() >= deadline):
            break
        if (deal.get("status") != "alive" or not is_amazon_deal(deal) or deal.get("asin")
                or deal.get("amazon_asin_tried") or "newsletter" in (deal.get("flags") or [])):
            continue
        links = [deal.get(k) or "" for k in ("product_url", "canonical_url", "link_as_posted")]
        asin = dedupe.asin_from_links(links)
        if not asin:
            deferred = set()
            targets = short_targets(links, fetcher, cfg, deadline, cache, deferred)
            if deferred:
                break  # quota/deadline-deferred chains have not had their one attempt yet
            asin = dedupe.asin_from_links(links + targets)
        changes = {"amazon_asin_tried": True}
        if asin:
            changes.update(asin=asin, asin_source="link", photo_asin="",
                           flags=[f for f in deal.get("flags") or [] if f not in
                                  ("image_backfill_tried", "product_image_tried")])
            if deal.get("photo_asin"):
                changes["image_url"] = ""  # independently found identity replaces the tentative photo
            found += 1
        _save(db, deal, **changes)
        tried += 1
    return {"tried": tried, "found": found}


def title_match(deal, products, minimum):
    """All candidates compete, including ones rejected by brand or price."""
    mine = name_words(deal.get("item") or "")
    ranked = sorted(((_dice(mine, name_words(p.get("title") or "")), asin, p)
                     for asin, p in products.items()), key=lambda x: (-x[0], x[1]))
    if not ranked or ranked[0][0] + 1e-9 < minimum:
        return ""
    score, asin, product = ranked[0]
    if len(ranked) > 1 and score - ranked[1][0] + 1e-9 < .10:
        return ""
    words = set(re.findall(r"[a-z0-9]+", (product.get("title") or "").lower()))
    brand = set(re.findall(r"[a-z0-9]+", (deal.get("brand") or "").lower()))
    if not brand <= words:
        return ""
    # Model/capacity/count numbers cannot be explained away by a high overlap.
    theirs = name_words(product.get("title") or "")
    if {w for w in mine if any(c.isdigit() for c in w)} != {w for w in theirs if any(c.isdigit() for c in w)}:
        return ""
    current = (product.get("stats") or {}).get("current")
    prices = [_current_price(current, i) for i in (0, 1, 4)]
    refs = [deal.get(k) for k in ("regular_price", "final_price")]
    if not any(isinstance(ref, (int, float)) and not isinstance(ref, bool)
               and math.isfinite(ref) and ref > 0 and price is not None
               and math.isfinite(price) and price > 0 and .6 * ref - 1e-9 <= price <= 1.4 * ref + 1e-9
               for ref in refs for price in prices):
        return ""
    return asin


def search_photos(db, deals, fetcher, cfg, now, deadline, run_key, *, rate_limited=False):
    """Lowest priority: reserve finder + all 50 product reads before searching.

    Uses the existing /query endpoint and fetch.keepa._finder_cost (11 tokens
    for 50 ASINs), then /product stats (one token/ASIN). Title/perPage/page:
    https://github.com/keepacom/api_backend/blob/master/src/main/java/com/keepa/api/backend/structs/ProductFinderRequest.java
    Photos are stored under photo_asin, outside every price/identity consumer.
    """
    from .fetch.keepa import QUERY_URL, _finder_cost
    from .verify import keepa_products
    import logging

    stats = {"searched": 0, "matched": 0, "tokens": 0, "rate_limited": rate_limited}
    key = get_secret("KEEPA_KEY")
    if not key or not cfg.feature_flags.get("keepa") or rate_limited or not cfg.images_keepa_search_per_run or not cfg.images_keepa_search_per_day:
        return stats
    day = now.strftime("%Y-%m-%d")
    try:
        ledger = json.loads(db.meta_get(SEARCH_META) or "{}")
        if not isinstance(ledger, dict):
            return stats
        if ledger.get("day") != day:
            ledger = {"day": day, "count": 0, "runs": {}}
        runs = ledger["runs"]
        if run_key not in runs and len(runs) >= cfg.images_keepa_search_per_day:
            return stats
        run = runs.get(run_key, {"count": 0, "rate_limited": False})
        if (type(ledger["count"]) is not int or type(run["count"]) is not int
                or ledger["count"] < 0 or run["count"] < 0):
            return stats
    except (ValueError, KeyError, TypeError, AttributeError):
        return stats  # unreadable quotas cannot authorize optional paid work
    if run["rate_limited"]:
        stats["rate_limited"] = True
        return stats
    budget = KeepaBudget.load(db)
    reserve = max(MIN_FOR_IMAGE_BACKFILL, cfg.keepa_reserve_tokens,
                  cfg.keepa_recheck_surplus_reserve)
    finder_cost = _finder_cost(50)
    try:
        for deal in deals:
            if ledger["count"] >= cfg.images_keepa_search_per_day or run["count"] >= cfg.images_keepa_search_per_run:
                break
            if deadline is not None and time.monotonic() + 15 >= deadline:
                break
            if (deal.get("status") != "alive" or deal.get("image_url") or deal.get("asin")
                    or not is_amazon_deal(deal) or deal.get("photo_search_tried")
                    or not {"post_link_tried", "newsletter"}.intersection(deal.get("flags") or [])
                    or len(name_words(deal.get("item") or "")) < 2):
                continue
            available = budget.available()
            if available is None or not math.isfinite(available) or available < reserve + finder_cost + 50:
                break  # unknown balance cannot authorize optional spending
            ledger["count"] += 1
            run["count"] += 1
            runs[run_key] = run
            db.meta_set(SEARCH_META, json.dumps(ledger))
            _save(db, deal, photo_search_tried=True)
            stats["searched"] += 1
            budget.reserve(finder_cost)
            budget.save(db)
            payload = resp = None
            try:
                resp = fetcher.api_get(QUERY_URL, params={"key": key, "domain": 1,
                    "selection": json.dumps({"title": " ".join(sorted(name_words(deal["item"]))),
                                             "perPage": 50, "page": 0})},
                    deadline=deadline, max_attempts=1, respect_robots=False, follow_redirects=False)
                payload = response_payload(resp)
            finally:
                budget.usage.note(payload, estimate=finder_cost,
                                  rate_limited=resp is not None and resp.status_code == 429)
                budget.note(payload)
                budget.save(db)
            if resp.status_code == 429:
                stats["rate_limited"] = True
                break
            resp.raise_for_status()
            if not isinstance(payload, dict) or not isinstance(payload.get("asinList"), list):
                continue
            asins = list(dict.fromkeys(a for a in payload["asinList"]
                                      if isinstance(a, str) and re.fullmatch(r"[A-Z0-9]{10}", a)))
            if not asins or len(asins) > 50 or payload.get("totalResults", len(asins)) > 50:
                continue  # unseen candidates could tie the winner
            if not budget.allows(reserve + len(asins)) or (deadline is not None and time.monotonic() + 15 >= deadline):
                break
            products = keepa_products(key, asins, 1, budget, fetcher=fetcher, deadline=deadline)
            budget.save(db)
            if products is None:
                stats["rate_limited"] = True
                break
            if set(products) != set(asins):
                continue  # missing runner-up data cannot establish separation
            asin = title_match(deal, products, cfg.images_keepa_match_min)
            if asin:
                photo = {}
                _harvest_image(photo, products[asin])
                if photo.get("image_url"):
                    _save(db, deal, photo_asin=asin, asin_source="title_match", image_url=photo["image_url"])
                    stats["matched"] += 1
    except Exception as exc:
        logging.getLogger(__name__).info("photo search stopped: %s", error_summary(exc))
    finally:
        run["rate_limited"] = stats["rate_limited"]
        db.meta_set(SEARCH_META, json.dumps(ledger))
        budget.save(db)
        stats["tokens"] = budget.usage.accounted
    return stats


def coverage(deals, cfg=None):
    causes = dict.fromkeys(("amazon_no_asin", "amazon_asin_no_lookup_yet",
                           "amazon_asin_lookup_found_none", "woot_no_photo",
                           "other_store_no_source", "other"), 0)
    live = [d for d in deals if d.get("status") == "alive"]
    with_photo = sum(bool(d.get("image_url")) for d in live)
    from .source_policy import _sources, image_granted
    granted = {s.name for s in _sources() if image_granted(s, cfg)}
    for deal in live:
        if deal.get("image_url"):
            continue
        if is_amazon_deal(deal):
            cause = ("amazon_no_asin" if not deal.get("asin") else
                     "amazon_asin_lookup_found_none" if deal.get("photo_lookup_found_none")
                     and deal.get("photo_lookup_asin") == deal.get("asin") else "amazon_asin_no_lookup_yet")
        elif str(deal.get("store") or "").lower().startswith("woot"):
            cause = "woot_no_photo"
        elif not any(s.get("source_name") in granted for s in deal.get("sightings") or []):
            cause = "other_store_no_source"
        else:
            cause = "other"
        causes[cause] += 1
    return {"live_public": len(live), "with_photo": with_photo,
            "without_photo": len(live) - with_photo, "by_cause": causes}
