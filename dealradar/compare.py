"""Private, bounded comparison facts from the approved Amazon API source only."""

from __future__ import annotations

import json
import logging
import math
import re
import time
from datetime import timedelta
from pathlib import Path

import yaml

from . import verify
from .config import ROOT, get_secret
from .errors import DeadlineExceeded
from .fetch.keepa import QUERY_URL, _regular_basis
from .keepa_budget import response_payload
from .savings import pack_quantity, unit_price
from .util import iso, parse_iso

log = logging.getLogger(__name__)
STATE = "compare_state"
LEDGER = "compare_tokens"
UNITS = {"count", "load", "fl_oz", "oz", "lb", "ft", "each"}
UNIT_TYPES = {"count": "count", "load": "load", "loads": "load",
              "fluid ounce": "fl_oz", "fluid ounces": "fl_oz", "fl oz": "fl_oz",
              "ounce": "oz", "ounces": "oz", "oz": "oz", "pound": "lb",
              "pounds": "lb", "lb": "lb", "foot": "ft", "feet": "ft", "ft": "ft",
              "each": "each"}
ASIN = re.compile(r"[A-Z0-9]{10}")
PRIVATE_COPY = re.compile(r"\b(?:Keepa|Claude|Codex|LLM|Python|Cloudflare|Worker|GitHub|stars?|reviews?)\b"
                          r"|bought\s+(?:in|last)|sales\s*rank|[★⭐]", re.I)
HIDDEN_PROMO = re.compile(r"\b(?:coupons?|clip|clipped|promo|promotional|code|checkout|cart)\b", re.I)
PACK_WORD = re.compile(r"\b(?:packs?|pk|multi[- ]?packs?|bundles?|sets?|pairs?|twin|double|triple)\b", re.I)


def positive(value):
    return type(value) in {int, float} and math.isfinite(value) and value > 0


def load_terms(path="data/compare_terms.yaml", *, strict=True):
    """Tests fail loudly; runtime drops malformed terms with a private warning."""
    path = Path(path)
    try:
        rows = yaml.safe_load((path if path.is_absolute() else ROOT / path).read_text())
        if not isinstance(rows, list):
            raise ValueError("compare terms must be a list")
    except (OSError, ValueError, yaml.YAMLError):
        if strict:
            raise
        log.warning("compare terms file unavailable or invalid")
        return None
    terms, seen = [], set()
    for index, row in enumerate(rows):
        try:
            if not isinstance(row, dict) or set(row) - {
                    "slug", "name", "keywords", "root_category", "unit", "unit_words", "display_unit", "exclude", "max_price"}:
                raise ValueError("unsupported fields")
            if not isinstance(row.get("slug"), str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", row["slug"]):
                raise ValueError("invalid slug")
            if row["slug"] in seen:
                raise ValueError("duplicate slug")
            if not isinstance(row.get("name"), str) or not row["name"].strip():
                raise ValueError("empty name")
            if row.get("unit") not in UNITS:
                raise ValueError("unknown unit")
            if "display_unit" in row and (not isinstance(row["display_unit"], str) or not row["display_unit"].strip()):
                raise ValueError("display_unit must be nonempty text")
            for key in ("keywords", "unit_words", "exclude"):
                values = row.get(key, [] if key == "exclude" else None)
                if (not isinstance(values, list) or (not values and key != "exclude")
                        or any(not isinstance(v, str) or not v.strip() for v in values)):
                    raise ValueError(f"{key} must contain nonempty words")
            if row.get("root_category") is not None and (
                    type(row["root_category"]) is not int or row["root_category"] <= 0):
                raise ValueError("root_category must be a positive integer")
            if "max_price" in row and not positive(row["max_price"]):
                raise ValueError("max_price must be positive")
            seen.add(row["slug"])
            terms.append(row)
        except (ValueError, TypeError) as exc:
            if strict:
                raise ValueError(f"compare term {index}: {exc}") from exc
            log.warning("compare term %d skipped: %s", index, exc)
    return terms


def _title_quantities(title, term):
    words = '|'.join(re.escape(w) for w in sorted(term["unit_words"], key=len, reverse=True))
    pattern = rf"(?<![\w.,+−/-])(?P<n>\d+(?:,\d{{3}})*(?:\.\d+)?)\s*[-–]?\s*(?:{words})(?!\w)"
    matches = list(re.finditer(pattern, title, re.I))
    # Do not read the ounce part of '100 fl oz' as a dry-weight quantity.
    if term["unit"] == "oz":
        matches = [m for m in matches if not re.search(r"(?:fl\.?|fluid)\s*$", title[:m.start()], re.I)]
    return matches


def _ambiguous_quantity(product, term, matches=None):
    """Check the whole title, without a vocabulary of item/container nouns."""
    title = product.get("title") or ""
    matches = _title_quantities(title, term) if matches is None else matches
    metadata = any(positive(product.get(k)) for k in ("numberOfItems", "packageQuantity"))
    if not metadata and len(matches) > 1:
        return True
    covered = matches + (list(re.finditer(r"\bpack\s+of\s+\d+\b", title, re.I)) if metadata else [])
    remainder = title
    for match in sorted(covered, key=lambda m: m.start(), reverse=True):
        remainder = remainder[:match.start()] + ' ' * (match.end() - match.start()) + remainder[match.end():]
    # Attached electrical/storage capacities describe a single each-item.
    remainder = re.sub(r"\b\d+(?:\.\d+)?(?:mAh|GB|W)\b", "", remainder, flags=re.I)
    # Unconsumed numbers are ambiguous even in reverse counts ('Case of 2').
    return bool(PACK_WORD.search(remainder)
        or re.search(r"(?<![\w.])\d"
                     r"|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen"
                     r"|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty"
                     r"|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|trillion|dozens?)\b", remainder, re.I)
        or re.search(r"\d\s*[x×]|[x×]\s*\d", title, re.I))


def quantity_with_evidence(product, term):
    """Accept an evidenced quantity only when the whole title is unambiguous."""
    title = product.get("title") or ""
    matches = _title_quantities(title, term)
    if (_ambiguous_quantity(product, term, matches)
            or any(re.search(r"(?:[-+−]|\d\s*(?:[/×x–-]|to))\s*$", title[:m.start()], re.I) for m in matches)):
        return None, ""
    metadata = [product.get(k) for k in ("numberOfItems", "packageQuantity")]
    multipacks = [v for v in metadata if positive(v) and v > 1]
    if not matches:
        if term["unit"] == "each" and title.strip() and not multipacks:
            return 1, title
        return None, ""
    numbers = {float(m["n"].replace(',', '')) for m in matches}
    if len(numbers) != 1:
        return None, ""
    quantity = numbers.pop()
    start, end = matches[0].span()
    packs = list(re.finditer(r"\bpack\s+of\s+(\d+)\b", title, re.I))
    if re.search(r"(?:\bper\s+|/\s*)(?:roll|bottle|pack|item|box|pad|bag|ream|tube)\b", title, re.I):
        return None, ""  # unsupported pack forms must not become a one-item total
    if not packs and re.match(r"\s+each\b", title[end:], re.I):
        return None, ""
    if packs:
        if len({int(m[1]) for m in packs}) != 1:
            return None, ""
        # 'Each' explicitly binds the unit quantity to each pack item; without
        # it, '60 count (pack of 3)' might already state the total.
        if not re.match(r"\s+each\b", title[end:], re.I):
            return None, ""
        end += re.match(r"\s+each\b", title[end:], re.I).end()
        multiplier = int(packs[0][1])
        if any(v != multiplier for v in multipacks):
            return None, ""
        quantity = pack_quantity(quantity, multiplier)
        start, end = min(start, packs[0].start()), max(end, packs[0].end())
    elif multipacks and not (term["unit"] == "each" and all(v == quantity for v in multipacks)):
        return None, ""
    if not positive(quantity):
        return None, ""
    unit = product.get("unitCount")
    if isinstance(unit, dict) and UNIT_TYPES.get(str(unit.get("unitType", "")).lower()) == term["unit"]:
        value = unit.get("unitValue")
        if positive(value) and not math.isclose(value, quantity):
            return None, ""
        if positive(value):
            quantity = value
    return (quantity, title[start:end]) if positive(quantity) else (None, "")


def state(db):
    try:
        data = json.loads(db.meta_get(STATE) or '{"terms":{},"products":{}}')
        if not isinstance(data["terms"], dict) or not isinstance(data["products"], dict):
            raise ValueError("invalid compare state")
        return data
    except (ValueError, TypeError, KeyError):
        log.warning("compare facts unreadable; awaiting a refresh")
        return {"terms": {}, "products": {}}


def _prune(data, terms, cfg):
    wanted = {t["slug"] for t in terms[:cfg.compare["max_terms"]]}
    data["terms"] = {s: row for s, row in data["terms"].items() if s in wanted}
    referenced = set()
    for row in data["terms"].values():
        row["asins"] = row.get("asins", [])[:cfg.compare["candidates_per_term"]]
        referenced.update(row["asins"])
    data["products"] = {a: p for a, p in data["products"].items() if a in referenced}


def product_facts(product, now):
    """An allowlist: popularity and rating fields never enter our state."""
    title = str(product.get("title") or "")[:2000]
    first = verify._keepa_image_name(product)
    price = verify._live_new_price(product)
    if (product.get("coupon") or product.get("promotions")
            or HIDDEN_PROMO.search(title) or verify.keepa_time(product.get("lastUpdate")) is None):
        price = None
    stats = product.get("stats")
    avg = stats.get("avg90") if isinstance(stats, dict) else None
    basis = _regular_basis(avg, 1)
    unit = product.get("unitCount")
    unit = ({k: unit[k] for k in ("unitValue", "unitType", "eachUnitCount") if k in unit}
            if isinstance(unit, dict) else None)
    return dict(title=title, image=first, price=price if positive(price) else None,
                avg90=basis[0] if basis else None, unitCount=unit,
                **{k: product.get(k) for k in ("numberOfItems", "packageQuantity", "size",
                                               "availabilityAmazon", "lastUpdate")},
                read_at=iso(now), observed_at=(iso(verify.keepa_observed_at(product, now))
                    if verify.keepa_time(product.get("lastUpdate")) is not None else None))


def selection(term, options):
    result = dict(title=" ".join(term["keywords"]), productType=[0], singleVariation=True,
                  current_RATING_gte=options["min_rating"], current_COUNT_REVIEWS_gte=options["min_reviews"],
                  current_NEW_gte=1, sort=[["current_SALES", "asc"]], perPage=50, page=0)
    if options["prime_only"]:
        result["buyBoxIsPrimeEligible"] = True
    else:
        result["availabilityAmazon"] = [0]
    if term.get("root_category"):
        result["rootCategory"] = term["root_category"]
    if term.get("max_price"):
        result["current_NEW_lte"] = round(term["max_price"] * 100)
    return result


def refresh(db, cfg, now, fetcher, deadline, run_key, *, budget, rate_limited=False):
    """Last paid step: durable reservations, receipts, near-full-bucket gates."""
    options = cfg.compare
    summary = dict(finders=0, products=0, tokens=0, estimated_tokens=0,
                   receipts=[], finder_costs=[], finder_results=[], rate_limited=rate_limited)
    if not options["refresh"]:
        return summary
    terms = load_terms(options["terms_file"], strict=False)
    if terms is None:
        return summary  # an unreadable file is not evidence that stored terms were removed
    terms = terms[:options["max_terms"]]
    data = state(db)
    _prune(data, terms, cfg)
    db.meta_set(STATE, json.dumps(data))
    key = get_secret("KEEPA_KEY")
    if rate_limited or not key or not cfg.feature_flags.get("keepa"):
        return summary
    day = now.date().isoformat()
    try:
        ledger = json.loads(db.meta_get(LEDGER) or '{"day":"","tokens":0,"runs":{}}')
        if not isinstance(ledger, dict) or not isinstance(ledger.get("runs"), dict):
            raise ValueError("invalid quota")
        if run_key in ledger["runs"]:
            return ledger["runs"][run_key]["summary"]
        if ledger["day"] != day:
            ledger.update(day=day, tokens=0)
            ledger.pop("halted_day", None)
            cutoff = (now - timedelta(days=7)).date().isoformat()
            ledger["runs"] = {k: v for k, v in ledger["runs"].items() if v["day"] >= cutoff}
        if (type(ledger["tokens"]) not in {int, float} or not math.isfinite(ledger["tokens"])
                or ledger["tokens"] < 0 or len(ledger["runs"]) >= 4096):
            return summary
    except (ValueError, TypeError, KeyError):
        log.warning("compare quota unreadable; paid refresh skipped")
        return summary
    if ledger.get("halted_day") == day:
        summary["halted"] = True
        return summary
    stop = min(deadline if deadline is not None else math.inf, time.monotonic() + options["max_seconds_per_run"])
    request_timeout = min(cfg.request_timeout_s, options["request_timeout_s"])
    run = dict(day=day, tokens=0, summary=summary)
    ledger["runs"][run_key] = run

    def save():
        summary["tokens"] = run["tokens"]
        db.meta_set(LEDGER, json.dumps(ledger))
        db.meta_set(STATE, json.dumps(data))
        budget.save(db)

    def affordable(cost):
        available, capacity = budget.available(), budget.capacity()
        return (not summary["rate_limited"] and ledger.get("halted_day") != day
                and time.monotonic() + request_timeout + cfg.min_host_interval_s < stop
                and run["tokens"] + cost <= options["tokens_per_run"]
                and ledger["tokens"] + cost <= options["tokens_per_day"]
                and available is not None and capacity is not None and math.isfinite(available)
                and available >= max(cfg.keepa_reserve_tokens, cfg.keepa_recheck_surplus_reserve) + cost
                and available - cost >= options["min_bucket_share"] * capacity)

    def reserve(cost):
        run["tokens"] += cost
        ledger["tokens"] += cost
        save()  # a lost response or interrupted run cannot buy this work twice

    def receipt(before, cost, finder=False, unsent=False):
        consumed = budget.usage.accounted - before[0]
        actual = consumed if budget.usage.missing == before[1] else None
        run["tokens"] += consumed - cost
        ledger["tokens"] += consumed - cost
        if unsent:
            save()
            return
        summary["receipts"].append(actual)
        if actual is None:
            summary["estimated_tokens"] += consumed
        if finder:
            summary["finder_costs"].append(actual)
            if actual is not None and actual > cost:
                ledger["halted_day"] = day
                summary["halted"] = True
                log.warning("compare refresh stopped (FinderReserveExceeded)")
        save()

    save()
    def due_at(term):
        row = data["terms"].get(term["slug"], {})
        at = parse_iso(row.get("read_at"))
        observed = [parse_iso(data["products"].get(a, {}).get("observed_at")) for a in row.get("asins", [])]
        # Leave a refresh interval of headroom before the oldest kept price expires.
        dates = [at + timedelta(hours=options["refresh_hours"])] if at else [now - timedelta(days=365)]
        dates += [o + timedelta(hours=options["max_price_age_h"] - options["refresh_hours"])
                  for o in observed if o is not None]
        return min(dates)

    due = [t for t in terms if due_at(t) <= now and not (
        (retry := parse_iso(data["terms"].get(t["slug"], {}).get("retry_at"))) and retry > now)]
    due.sort(key=lambda t: (due_at(t), t["slug"]))
    ready = []

    def failed(row, exc):
        row["retry_at"] = iso(now + timedelta(hours=options["refresh_hours"]))
        log.warning("compare term deferred (%s)", type(exc).__name__)
        save()

    try:
        for term in due:
            row = data["terms"].setdefault(term["slug"], {})
            at = parse_iso(row.get("finder_at"))
            if row.get("finder_failed") or not at or now - at >= timedelta(days=options["finder_days"]):
                cost = options["finder_reserve_tokens"]
                if not affordable(cost):
                    continue
                reserve(cost)
                before = budget.usage.accounted, budget.usage.missing
                budget.reserve(cost)
                budget.save(db)
                payload = resp = None
                unsent = False
                try:
                    try:
                        resp = fetcher.api_get(QUERY_URL, params={"key": key, "domain": 1,
                            "selection": json.dumps(selection(term, options))}, deadline=stop,
                            max_attempts=1, respect_robots=False, follow_redirects=False, conditional=False,
                            timeout_s=request_timeout)
                        payload = response_payload(resp)
                    except DeadlineExceeded as exc:
                        unsent = not exc.request_sent
                        if unsent:
                            budget.reserve(-cost)
                        raise
                    finally:
                        limited = resp is not None and resp.status_code == 429
                        if not unsent:
                            budget.usage.note(payload, estimate=cost, rate_limited=limited)
                            budget.reserve(budget.usage.accounted - before[0] - cost)
                            budget.note(payload)
                        summary["rate_limited"] = limited
                        receipt(before, cost, finder=True, unsent=unsent)
                    if limited or summary.get("halted"):
                        return summary
                    row["finder_at"] = iso(now)
                    resp.raise_for_status()
                    summary["finders"] += 1
                    total = payload.get("totalResults") if isinstance(payload, dict) else None
                    total = total if type(total) is int and total >= 0 else None
                    summary["finder_results"].append(total)
                    log.info("compare finder totalResults=%s", total)
                    if not isinstance(payload, dict) or not isinstance(payload.get("asinList"), list):
                        raise ValueError("missing asinList")
                    row.update(asins=list(dict.fromkeys(a for a in payload["asinList"]
                        if isinstance(a, str) and ASIN.fullmatch(a)))[:options["candidates_per_term"]])
                    row.pop("finder_failed", None)
                    row.pop("retry_at", None)
                    save()
                except DeadlineExceeded:
                    return summary
                except Exception as exc:
                    row.update(finder_at=iso(now), finder_failed=True)
                    failed(row, exc)
                    continue
            ready.append(term)
        pending = list(dict.fromkeys(a for t in ready for a in data["terms"][t["slug"]].get("asins", [])))
        fresh_since = now - timedelta(hours=options["refresh_hours"])
        read = {a for a in pending if (at := parse_iso(data["products"].get(a, {}).get("read_at")))
                and fresh_since < at <= now and (
                    at == now or not (o := parse_iso(data["products"][a].get("observed_at")))
                    or o > now - timedelta(hours=options["max_price_age_h"] - options["refresh_hours"]))}
        pending = [a for a in pending if a not in read]
        while pending:
            count = min(verify.KEEPA_BATCH, len(pending))
            while count and not affordable(count):
                count -= 1
            if not count:
                break
            batch, pending = pending[:count], pending[count:]
            reserve(count)
            before = budget.usage.accounted, budget.usage.missing
            unsent = False
            try:
                try:
                    products = verify.keepa_products(key, batch, 90, budget, fetcher=fetcher,
                        deadline=stop, follow_redirects=False, record_shared_facts=False, timeout_s=request_timeout)
                except DeadlineExceeded as exc:
                    unsent = not exc.request_sent
                    raise
                finally:
                    receipt(before, count, unsent=unsent)
            except DeadlineExceeded:
                break
            except Exception as exc:
                for term in ready:
                    row = data["terms"][term["slug"]]
                    if set(row.get("asins", [])) & set(batch):
                        failed(row, exc)
                continue
            if products is None:
                summary["rate_limited"] = True
                break
            for asin in batch:
                data["products"][asin] = product_facts(products.get(asin, {}), now)
            read.update(batch)
            summary["products"] += len(batch)
        for term in ready:
            row = data["terms"][term["slug"]]
            if set(row.get("asins", [])) <= read:
                row["read_at"] = min((data["products"][a]["read_at"] for a in row.get("asins", [])), default=iso(now))
                row.pop("retry_at", None)
                if due_at(term) <= now:
                    row["retry_at"] = iso(now + timedelta(hours=min(1, options["refresh_hours"])))
    except Exception as exc:
        log.warning("compare refresh stopped (%s)", type(exc).__name__)
    finally:
        _prune(data, terms, cfg)
        save()
    return summary


def ranked_rows(term, data, cfg, now):
    """Fresh rows only; prices without evidenced quantities follow unit prices."""
    rows = []
    for asin in data["terms"].get(term["slug"], {}).get("asins", []):
        product = data["products"].get(asin, {})
        at = parse_iso(product.get("observed_at"))
        if (not at or not 0 <= (now - at).total_seconds() <= cfg.compare["max_price_age_h"] * 3600
                or verify.keepa_time(product.get("lastUpdate")) is None
                or not positive(product.get("price")) or not product.get("title")
                or PRIVATE_COPY.search(product["title"])
                or (not cfg.compare["prime_only"] and product.get("availabilityAmazon") != 0)
                or any(re.search(rf"\b{re.escape(word)}\b", product["title"], re.I)
                       for word in term.get("exclude", []))):
            continue
        quantity, quote = quantity_with_evidence(product, term)
        if (quantity is None and not any(positive(product.get(k)) for k in ("numberOfItems", "packageQuantity"))
                and _ambiguous_quantity(product, term)):
            continue
        rows.append(dict(product, asin=asin, quote=quote, unit_price=unit_price(product["price"], quantity)))
    return sorted(rows, key=lambda row: (row["unit_price"] is None,
                  row["unit_price"] if row["unit_price"] is not None else row["price"], row["price"], row["asin"]))[:10]
