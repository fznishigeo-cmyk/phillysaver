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
from .product_images import _dice, _unwrap, link_key, name_words
from .util import item_identity_conflict
from .verify import _current_price, _harvest_image, is_amazon_deal

SEARCH_META = "photo_keepa_search"


def product_identity(links, store):
    """One exact retailer product, decoded offline; competing products veto.

    PostPhotos.links holds product *keys*, not necessarily URLs. Only keys
    that losslessly name a product URL can be expanded. Opaque redirectors,
    blogs, listings and malformed URLs cannot supply an identity.
    """
    from .http_client import canonicalize_url, extract_asin, is_challenge_url, tracker_target
    from .product_images import _names_no_product, _product_id
    from .source_policy import sources_for_url

    candidates = {}
    for link in links:
        if not isinstance(link, str) or not link:
            continue
        if re.fullmatch(r"amazon:[A-Z0-9]{10}", link, re.I):
            link = "https://www.amazon.com/dp/" + link.split(":", 1)[1].upper()
        elif re.fullmatch(r"walmart\.com:\d+", link):
            link = "https://www.walmart.com/ip/" + link.split(":", 1)[1]
        try:
            for _ in range(5):
                pid = _product_id(link)
                if pid and not pid.startswith("path:"):
                    break  # a product's own query can contain an unrelated URL
                inner = tracker_target(link) or _unwrap(link)
                if not inner or inner == link:
                    break
                link = inner
            parts = urlsplit(link)
            host = (parts.hostname or "").lower().removeprefix("www.")
            if (parts.scheme != "https" or not host or parts.username or parts.password
                    or "\\" in link or any(ord(c) <= 32 or ord(c) == 127 for c in link)
                    or is_challenge_url(link)
                    or (sources_for_url(link) and _product_id(link).startswith("path:"))
                    or _names_no_product(link)):
                continue
            pid = _product_id(link)
            if not pid or (pid.startswith("path:") and not re.match(
                    r"^/(?:products?|p|pd|ip|item)/[^/]+", parts.path, re.I)):
                continue
            asin = extract_asin(link)
            if asin:
                url = "https://www.amazon.com/dp/" + asin
            elif pid.startswith("walmart.com:"):
                if not re.fullmatch(r"/ip/(?:[^/]+/)?\d+/?", parts.path):
                    continue
                url = "https://www.walmart.com/ip/" + pid.split(":", 1)[1]
            else:
                url = canonicalize_url(link)
            candidates[link_key(url)] = url
        except (ValueError, TypeError):
            continue
    if len(candidates) != 1:
        return ""
    url = next(iter(candidates.values()))
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    merchant = re.sub(r"[^a-z0-9]", "", str(store or "").lower())
    if extract_asin(url):
        return url if is_amazon_deal({"store": store}) else ""
    return url if merchant in {re.sub(r"[^a-z0-9]", "", host),
                              re.sub(r"[^a-z0-9]", "", host.split(".")[0])} else ""


def post_identity(extracted, post):
    """A deal's own exact link, else a single-product post's one product."""
    own = product_identity([extracted.product_url, extracted.link_as_posted], extracted.store)
    if own:
        return own
    from .util import normalize_item
    items = [item for item, _ in post.parsed]
    if (extracted.link_shared or len({normalize_item(item) for item in items}) > 1
            or any(item_identity_conflict(item, other)
                   for i, item in enumerate(items) for other in items[i + 1:])):
        return ""
    from .product_images import _names_no_product
    own_url = extracted.product_url or ""
    if own_url.lower().startswith(("http://", "https://")) and _names_no_product(own_url):
        return ""  # a sale/store/search deal: the body's one product is an example
    links = dedupe._URL_RE.findall(post.text or "") + post.links + sorted(post.photos.links)
    links += list(post.photos.in_link) + post.resolved_links
    links += [extracted.product_url, extracted.link_as_posted]
    # A partly resolved post may hide a second product. resolve_posts keeps
    # targets only after every short chain completed within its existing cap.
    if not post.resolved_links and any(_short_amazon_link(u) for u in links):
        return ""
    return product_identity(links, extracted.store)


def _short_amazon_link(link):
    try:
        for _ in range(5):
            target = _unwrap(link)
            if not target or target == link:
                break
            link = target
        return (urlsplit(link).hostname or "").lower() in ("amzn.to", "a.co")
    except ValueError:
        return False


def identity_changes(deal, url):
    """Attach a fact without changing click links, prices or evidence."""
    from .http_client import extract_asin

    changes = {"product_identity_url": url}
    asin = extract_asin(url)
    if asin and not deal.get("asin"):
        changes.update(asin=asin, asin_source="link", photo_asin="")
        if deal.get("photo_asin"):
            changes["image_url"] = ""
    if not deal.get("product_identity_url"):
        changes["flags"] = [f for f in deal.get("flags") or []
                            if f not in ("image_backfill_tried", "product_image_tried")]
    return changes


def backfill_identities(db, deals, cfg, deadline):
    """One bounded offline attempt per live legacy row, using stored facts only."""
    tried = found = 0
    for deal in deals:
        if tried >= cfg.images_asin_backfill_per_run or (deadline is not None and time.monotonic() >= deadline):
            break
        if (deal.get("status") != "alive" or deal.get("asin") or deal.get("product_identity_url")
                or deal.get("product_identity_tried") or deal.get("link_shared")):
            continue
        links = [deal.get(k) or "" for k in ("product_url", "canonical_url", "link_as_posted")]
        sightings = db.sightings_for(deal["id"])
        links += [s.get("source_url") or "" for s in sightings]
        url = product_identity(links, deal.get("store"))
        changes = identity_changes(deal, url) if url else {}
        _save(db, deal, product_identity_tried=True, **changes)
        tried += 1
        found += bool(url)
    return {"tried": tried, "found": found}


def identity_summary(before, deals):
    """Private counts only; still_missing measures photos, not just identity."""
    from collections import Counter

    live = [d for d in deals if d.get("status") == "alive"]

    def borrowed(d):
        own = {link_key(u) for u in (d.get("canonical_url"), d.get("link_as_posted")) if u}
        return bool(d.get("product_identity_url")) and link_key(d["product_identity_url"]) not in own

    recovered = [d for d in live if (d.get("asin") or d.get("product_identity_url")) and (
        before.get(d["dedupe_key"]) is False or (d["dedupe_key"] not in before and borrowed(d)))]
    by_source = Counter(next((s.get("source_name") for s in d.get("sightings") or []
                              if s.get("source_name")), "unknown") for d in recovered)
    return {"recovered_identity": len(recovered), "by_source": dict(sorted(by_source.items())),
            "still_missing": sum(not d.get("image_url") for d in live)}


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
        links = dedupe._URL_RE.findall(post.text or "") + post.links + list(post.photos.links)
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


def backfill_keepa_images(db, deals, fetcher, cfg, deadline, *, limit, rate_limited=False):
    """Catch identities learned after the fetch-time recheck, photos only.

    Use the existing identity cap and remaining photo slots. A known
    empty lookup waits for ordinary price rechecks; transport misses retry.
    Identities requested by this run's price recheck wait for the next run.
    """
    from .amazon_used import excluded
    from .verify import KEEPA_BATCH, keepa_products
    import logging

    stats = {"requested": 0, "found": 0, "tokens": 0, "rate_limited": rate_limited}
    if not cfg.images_post_ingest_keepa or not cfg.feature_flags.get("keepa") or rate_limited:
        return stats
    key = get_secret("KEEPA_KEY")
    limit = min(limit, cfg.images_asin_backfill_per_run)
    if not key or limit <= 0:
        return stats
    by_asin = {}
    for deal in sorted(deals, key=lambda d: str(d.get("first_seen_at") or ""), reverse=True):
        asin = deal.get("asin") or ""
        if (deal.get("status") != "alive" or deal.get("image_url") or excluded(deal, cfg)
                or not is_amazon_deal(deal) or not re.fullmatch(r"[A-Z0-9]{10}", asin)
                or asin in getattr(fetcher, "keepa_recheck_asins", ())
                or (deal.get("photo_lookup_asin") == asin and deal.get("photo_lookup_found_none"))):
            continue
        by_asin.setdefault(asin, []).append(deal)
    pending = list(by_asin)[:limit]
    budget = KeepaBudget.load(db, cfg)
    reserve = max(MIN_FOR_IMAGE_BACKFILL, cfg.keepa_reserve_tokens,
                  cfg.keepa_recheck_surplus_reserve)
    try:
        while pending:
            if deadline is not None and time.monotonic() + 15 >= deadline:
                break
            available = budget.spendable()
            if available is None or not math.isfinite(available) or not budget.allows(reserve + 1):
                break
            size = min(KEEPA_BATCH, len(pending), int(available - reserve))
            batch, pending = pending[:size], pending[size:]
            stats["requested"] += len(batch)
            products = keepa_products(key, batch, 1, budget, fetcher=fetcher, deadline=deadline,
                                      record_shared_facts=False)
            if products is None:
                stats["rate_limited"] = True
                break
            for asin in batch:
                for deal in by_asin[asin]:
                    photo = {"asin": asin}
                    _harvest_image(photo, products.get(asin, {}))
                    _save(db, deal, **{k: v for k, v in photo.items() if k != "asin"})
                    stats["found"] += bool(photo.get("image_url"))
    except Exception as exc:
        logging.getLogger(__name__).info("post-ingest photos stopped: %s", error_summary(exc))
    finally:
        if stats["requested"]:
            budget.save(db)
        stats["tokens"] = budget.usage.accounted
    return stats


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
    # Product variants cannot be explained away by a high word overlap.
    if item_identity_conflict(deal.get("item") or "", product.get("title") or ""):
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
    from .amazon_used import excluded
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
    budget = KeepaBudget.load(db, cfg)
    reserve = max(MIN_FOR_IMAGE_BACKFILL, cfg.keepa_reserve_tokens,
                  cfg.keepa_recheck_surplus_reserve)
    finder_cost = _finder_cost(50)
    try:
        for deal in deals:
            if ledger["count"] >= cfg.images_keepa_search_per_day or run["count"] >= cfg.images_keepa_search_per_run:
                break
            if deadline is not None and time.monotonic() + 15 >= deadline:
                break
            if (excluded(deal, cfg) or deal.get("status") != "alive" or deal.get("image_url") or deal.get("asin")
                    or not is_amazon_deal(deal) or deal.get("photo_search_tried")
                    or not {"post_link_tried", "newsletter"}.intersection(deal.get("flags") or [])
                    or len(name_words(deal.get("item") or "")) < 2):
                continue
            available = budget.available()
            if (available is None or not math.isfinite(available)
                    or not budget.allows(reserve + finder_cost + 50)):
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
