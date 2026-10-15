"""Private audits of prices already published, reusing paid API readings.

The snapshot freezes the shopper's price before a run can correct it. Cached
observations count once; failures and ambiguous readings prove nothing.
"""

from __future__ import annotations

import functools
import json
import logging
import math
import random
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .savings import accuracy_price_outcome
from .util import iso, parse_iso

META = "accuracy_published"
BUCKETS = ("top30", "top120", "rest")
log = logging.getLogger("dealradar.accuracy")


def _quiet(fn):
    """A private audit never breaks fetching or publishing; it logs and skips."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            log.warning("accuracy: %s skipped (%s)", fn.__name__, type(exc).__name__)
            return None
    return wrapper


def published(db):
    if not hasattr(db, "_accuracy_published"):
        db._accuracy_published = json.loads(db.meta_get(META) or "{}")
    return db._accuracy_published


@_quiet
def publish(db, rows, now, run_key):
    """Remember only visible live offers, in the actual public wall order."""
    from .verify import _price_hidden_by_promo, is_amazon_deal, is_used_deal

    previous, snapshot = published(db), {}
    for position, row in enumerate(rows):
        if row.get("locked") or row.get("status") != "alive" or row.get("final_price") is None:
            continue
        key = row["dedupe_key"]
        deal = db.get_deal(key)
        if deal is None:
            continue
        facts = {"final": row["final_price"], "store": row["store"],
                 "checkout": _price_hidden_by_promo(deal), "used": is_used_deal(deal),
                 "asin": deal.get("asin") if is_amazon_deal(deal) else None}
        old = previous.get(key, {})
        same = all(old.get(k) == v for k, v in facts.items())
        snapshot[key] = {**facts, "at": old["at"] if same else iso(now),
                         "created_run": old.get("created_run", run_key),
                         "bucket": "top30" if position < 30 else "top120" if position < 120 else "rest"}
    db.meta_set(META, json.dumps(snapshot, separators=(",", ":")))
    db._accuracy_published = snapshot


@_quiet
def record(db, cfg, deal_key, now, *, source, price=None, unavailable=False, observed=None,
           run_key=None):
    if db is None:
        return
    shown = published(db).get(deal_key)
    run_key = run_key or getattr(db, "accuracy_run_key", None) or iso(now)
    observed = observed or now
    if (not shown or shown["created_run"] == run_key or observed > now
            or observed < parse_iso(shown["at"])):
        return
    if unavailable:
        outcome = "unavailable"
    elif price is None or type(price) not in (int, float) or not math.isfinite(price) or price < 0:
        return
    elif shown["checkout"]:
        outcome = "checkout_price"
    else:
        outcome = accuracy_price_outcome(price, shown["final"], source=source,
                                         min_pct=cfg.keepa_reprice_min_pct)
    db.conn.execute(
        "INSERT OR IGNORE INTO accuracy_samples"
        " (deal_key,run_key,checked_at,observed_at,bucket,store,source,outcome) VALUES (?,?,?,?,?,?,?,?)",
        (deal_key, run_key, iso(now), iso(observed), shown["bucket"], shown["store"], source, outcome))
    db.conn.commit()


@_quiet
def keepa(db, cfg, products, now):
    """Use each price's own clock; missing price data is never a stock failure."""
    from . import buybox
    from .verify import _USED_OFFER_CONDITIONS, _offer_total, keepa_time

    if db is None:
        return
    for key, shown in published(db).items():
        product = products.get(shown["asin"])
        if product is None:
            continue
        stats = product.get("stats") or {}
        if not isinstance(stats, dict):
            continue
        price, unavailable = None, False
        at = keepa_time(product.get("lastUpdate"))
        if shown["used"]:
            offers, order = product.get("offers"), product.get("liveOffersOrder")
            if isinstance(offers, list) and isinstance(order, list):
                at = keepa_time(stats.get("lastOffersUpdate"))
                if product.get("offersSuccessful") is False or any(
                        type(i) is not int or not 0 <= i < len(offers)
                        or not isinstance(offers[i], dict) for i in order):
                    continue
                totals = [_offer_total(offers[i]) for i in order
                          if offers[i].get("condition") in _USED_OFFER_CONDITIONS]
                if None in totals:
                    continue
                price = min(totals, default=None)
                unavailable = not totals
            else:
                current = stats.get("current")
                cents = [buybox.number(current[i]) for i in (9, 2)
                         if isinstance(current, list) and len(current) > i]
                price = min((p / 100 for p in cents if p is not None), default=None)
        else:
            values = buybox.prices(product)
            box_at = keepa_time(stats.get("lastBuyBoxUpdate"))
            if values["buybox"] is not None:
                price, at = values["buybox"], box_at
            else:
                price = values["amazon"] if values["amazon"] is not None else values["new"]
                current = stats.get("current")
                # Keepa's explicit -1 means no offer. Missing/truncated slots
                # and unknown negative sentinels do not establish availability.
                unavailable = (isinstance(current, list) and len(current) > 1
                               and current[0] == current[1] == -1)
        if at is None or not now - timedelta(minutes=cfg.keepa_buybox_interval_min) <= at <= now:
            continue
        record(db, cfg, key, now, source="keepa", price=price, unavailable=unavailable, observed=at)


@_quiet
def extra_samples(db, cfg, now, *, fetcher=None, deadline=None, run_key=None, rate_limited=False):
    """Optional random reads, after normal work, within the shared reserve."""
    from .config import get_secret
    from .keepa_budget import KeepaBudget, MIN_FOR_DEAL_QUERY
    from .verify import USED_RECHECK_TOKENS, is_used_deal, recheck_amazon_prices

    limit = cfg.accuracy_extra_samples_per_day
    if not limit or rate_limited or not cfg.feature_flags.get("keepa") or not get_secret("KEEPA_KEY"):
        return
    run_key = run_key or getattr(db, "accuracy_run_key", None) or iso(now)
    if not db.conn.execute("INSERT OR IGNORE INTO accuracy_extra_runs VALUES (?,?)",
                           (run_key, iso(now))).rowcount:
        return
    db.accuracy_run_key = run_key
    day = now.astimezone(timezone.utc).date().isoformat()
    left = max(0, limit - db.conn.execute(
        "SELECT COUNT(*) FROM accuracy_extra_reads WHERE day=?", (day,)).fetchone()[0])
    recent = {r[0] for r in db.conn.execute(
        "SELECT deal_key FROM accuracy_samples WHERE checked_at>=? UNION "
        "SELECT deal_key FROM accuracy_extra_reads WHERE checked_at>=?",
        (iso(now - timedelta(hours=6)),) * 2)}
    candidates = []
    for key, shown in published(db).items():
        if not shown["asin"] or key in recent or shown["created_run"] == run_key:
            continue
        deal = db.get_deal(key)
        if (deal is None or deal.get("status") != "alive" or deal.get("asin") != shown["asin"]
                or is_used_deal(deal) != shown["used"]):
            continue
        stamps = [parse_iso(deal.get(k) or "") for k in
                  ("price_rechecked_at", "keepa_price_seen_at", "recheck_tried_at", "buybox_requested_at")]
        if any(at is not None and at >= now - timedelta(hours=6) for at in stamps):
            continue
        candidates.append(deal)
    random.shuffle(candidates)
    budget = KeepaBudget.load(db)
    reserve = max(MIN_FOR_DEAL_QUERY, cfg.keepa_reserve_tokens, cfg.keepa_recheck_surplus_reserve)
    attempted_asins = set()
    db.conn.commit()
    for deal in candidates:
        if not left or (deadline is not None and time.monotonic() + cfg.request_timeout_s + 5 >= deadline):
            break
        if deal["asin"] in attempted_asins:
            continue
        used = published(db)[deal["dedupe_key"]]["used"]
        cost = USED_RECHECK_TOKENS + 1 if used else 4
        available = budget.available(now)
        if available is None or available < reserve + cost:
            break
        if not db.conn.execute("INSERT OR IGNORE INTO accuracy_extra_reads VALUES (?,?,?,?)",
                               (day, deal["dedupe_key"], iso(now), run_key)).rowcount:
            continue
        db.conn.commit()  # failed/lost requests consume the allowance, too
        attempted_asins.add(deal["asin"])
        left -= 1
        try:
            result = recheck_amazon_prices([deal], cfg, budget=budget, live=True,
                                           buybox=not used, fetcher=fetcher)
        finally:
            budget.save(db)  # preserve receipts and conservative debits for the next run
        if getattr(result, "rate_limited", False):
            break


def wilson(matches, n):
    """Two-sided 95% Wilson score interval; no observations means no rate."""
    if not n:
        return None
    z = 1.959963984540054
    rate, z2 = matches / n, z * z
    center = (rate + z2 / (2 * n)) / (1 + z2 / n)
    half = z * math.sqrt(rate * (1 - rate) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return max(0, center - half), min(1, center + half)


def score(rows):
    counts = Counter(row["outcome"] for row in rows)
    n = sum(counts[k] for k in ("match", "higher", "lower", "unavailable"))
    return {"n": n, "matches": counts["match"], "rate": counts["match"] / n if n else None,
            "interval": wilson(counts["match"], n), "checkout_price": counts["checkout_price"],
            "causes": sorted(((k, counts[k]) for k in ("higher", "lower", "unavailable") if counts[k]),
                             key=lambda pair: (-pair[1], pair[0]))[:3]}


def report_data(db, cfg, now):
    yesterday = now.astimezone(ZoneInfo(cfg.timezone)).date() - timedelta(days=1)
    tz = ZoneInfo(cfg.timezone)
    start = datetime.combine(yesterday - timedelta(days=6), datetime.min.time(), tz)
    end = datetime.combine(yesterday + timedelta(days=1), datetime.min.time(), tz)
    exists = db.conn.execute("SELECT 1 FROM sqlite_master WHERE name='accuracy_samples'").fetchone()
    rows = [dict(r) for r in db.conn.execute(
        "SELECT checked_at,bucket,outcome FROM accuracy_samples WHERE checked_at>=? AND checked_at<?",
        (iso(start), iso(end)))] if exists else []
    for row in rows:
        row["day"] = parse_iso(row["checked_at"]).astimezone(tz).date()
    daily = []
    for offset in range(7):
        day = yesterday - timedelta(days=6 - offset)
        samples = [r for r in rows if r["day"] == day]
        daily.append({"day": day.isoformat(), "overall": score(samples),
                      "buckets": {b: score([r for r in samples if r["bucket"] == b]) for b in BUCKETS}})
    return {"daily": daily, "week": score(rows)}


def format_score(value):
    if not value or not value["n"]:
        return "not measured (n=0)"
    low, high = value["interval"]
    return f"{value['rate']:.2%} (95% Wilson {low:.2%}–{high:.2%}; n={value['n']:,})"


def report_lines(data):
    if not data:
        return ["Price accuracy: not measured (n=0)"]
    day = data["daily"][-1]
    lines = ["Price accuracy — " + day["day"]]
    for bucket, value in [("Overall", day["overall"]), *day["buckets"].items()]:
        causes = ", ".join(f"{cause} {count:,}" for cause, count in value["causes"]) or "none observed"
        lines.append(f"  {bucket}: {format_score(value)}; checkout-price kind {value['checkout_price']:,} "
                     f"(separate); top causes: {causes}")
    lines += ["7-day price accuracy: " + format_score(data["week"]),
              "7-day line: " + " | ".join(f"{d['day']}: {format_score(d['overall'])}" for d in data["daily"]),
              "Source: paid live API readings of published offers; checkout-price kinds excluded from n. "
              "Observed samples, not a random-site accuracy estimate; repeated cached readings excluded. "
              "99.9% is the goal, not a measured claim."]
    return lines
