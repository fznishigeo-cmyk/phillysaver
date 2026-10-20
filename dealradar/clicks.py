"""Private, anonymous hourly clicks and publication-time proxies; never live ranking.

The wire key is the existing public SHA256[:16], never the internal dedupe key.
See docs/first-party-clicks.md for coverage, exposure and shadow definitions.
"""
from __future__ import annotations

import json
import logging
import math
import re
import struct
from datetime import timedelta
from heapq import merge
from itertools import groupby
from urllib.parse import urlsplit

from .config import get_secret
from .util import parse_iso

log = logging.getLogger(__name__)
KEY = re.compile(r"[0-9a-f]{16}")
HOURS = struct.Struct("<24d")


def compact_exists(db):
    return db.conn.execute("SELECT 1 FROM sqlite_master WHERE name='deal_click_days'").fetchone() is not None


def compact_schema(db):
    db.conn.execute("CREATE TABLE IF NOT EXISTS deal_click_days (deal_key TEXT NOT NULL,"
                    " day INTEGER NOT NULL, seconds BLOB NOT NULL, PRIMARY KEY(deal_key,day))")
    db.conn.execute("CREATE INDEX IF NOT EXISTS idx_click_days_day ON deal_click_days(day)")


def add_exposure(db, key, hour, seconds):
    """24 binary64 slots preserve fractional seconds and hourly coverage exactly.

    Position is unused by every reader; features uses the current wall order.
    Caller creates the schema and owns the transaction.
    """
    day, slot = divmod(hour, 24)
    row = db.conn.execute("SELECT seconds FROM deal_click_days WHERE deal_key=? AND day=?", (key, day)).fetchone()
    values = list(HOURS.unpack(row[0])) if row else [-1.] * 24
    values[slot] = min(3600, max(0, values[slot]) + seconds)
    db.conn.execute("INSERT INTO deal_click_days VALUES (?,?,?) ON CONFLICT(deal_key,day)"
                    " DO UPDATE SET seconds=excluded.seconds", (key, day, HOURS.pack(*values)))


def exposure(db, start, end):
    """Merge both encodings, including partial days during rollout/rollback."""
    hourly = db.conn.execute("SELECT deal_key,hour,seconds FROM deal_click_shown"
                             " WHERE hour>=? AND hour<? ORDER BY deal_key,hour", (start, end))
    def daily():
        if not compact_exists(db):
            return
        for key, day, packed in db.conn.execute(
                "SELECT deal_key,day,seconds FROM deal_click_days WHERE day>=? AND day<=? ORDER BY deal_key,day",
                (start // 24, (end - 1) // 24)):
            for slot, seconds in enumerate(HOURS.unpack(packed)):
                hour = day * 24 + slot
                if seconds >= 0 and start <= hour < end:
                    yield key, hour, seconds
    identity = lambda row: (row[0], row[1])
    for (key, hour), rows in groupby(merge(hourly, daily(), key=identity), key=identity):
        yield key, hour, min(3600, sum(row[2] for row in rows))


def settings(cfg):
    raw = cfg.raw.get("first_party_clicks", {})
    raw = raw if isinstance(raw, dict) else {}
    def number(name, default, low, high):
        try:
            value = float(raw.get(name, default))
            return max(low, min(high, value)) if math.isfinite(value) else default
        except (TypeError, ValueError):
            return default
    return {"enabled": raw.get("enabled") is True,
            "endpoint": str(raw.get("endpoint") or ""),
            "hourly_cap": int(number("hourly_cap", 1000, 1, 3000)),
            "isolate_per_minute": int(number("isolate_per_minute", 120, 1, 600)),
            "deal_per_minute": int(number("deal_per_minute", 12, 1, 60)),
            "retention_days": int(number("retention_days", 30, 7, 90)),
            "lookback_hours": int(number("lookback_hours", 168, 1, 720)),
            "max_publish_gap_minutes": number("max_publish_gap_minutes", 30, 1, 60),
            "prior_hours": number("prior_hours", 2, 1, 24),
            "shadow_weight": number("shadow_weight", 10, 0, 100)}


def enabled(cfg):
    return settings(cfg)["enabled"]


def endpoint(cfg):
    """Our existing worker or a dedicated site subdomain, never arbitrary hosts."""
    value = settings(cfg)["endpoint"]
    try:
        url = urlsplit(value)
        domain = cfg.custom_domain.removeprefix("www.")
        worker_origins = {str(getattr(cfg, name, "")).rstrip("/") for name in
                          ("newsletter_worker_url", "paywall_api_url")}
        owned = (url.hostname and domain and url.hostname.endswith("." + domain)
                 and url.hostname not in {domain, "www." + domain})
        owned_worker = (url.hostname and url.hostname.endswith(".workers.dev")
                        and value.rstrip("/") in worker_origins)
        if ((owned or owned_worker) and url.scheme == "https" and url.hostname
                and re.fullmatch(r"[a-z0-9.-]+", url.hostname)
                and value.rstrip("/") == "https://" + url.hostname
                and url.netloc == url.hostname and url.path in {"", "/"}
                and not url.query and not url.fragment):
            return value.rstrip("/")
    except ValueError:
        pass
    return ""


def ingest(db, cfg, now, fetcher, run_key, *, deadline=None):
    """One bounded authenticated request/run, atomic absolute counts + cursor.

    Our own administrative API is not a crawl. The shared client still applies
    its descriptive UA and host pacing; no redirects, cache, or in-run retry.
    """
    if not enabled(cfg):
        return {"status": "off"}
    base, token = endpoint(cfg), get_secret("CLICK_SIGNAL_TOKEN")
    if not base or not token:
        return {"status": "needs_config"}
    try:
        hour = int(now.timestamp() // 3600)
        with db.conn:
            claimed = db.conn.execute("INSERT INTO deal_click_runs(run_key,hour,ingested) VALUES (?,?,1) "
                "ON CONFLICT(run_key) DO UPDATE SET ingested=1 WHERE ingested=0", (run_key, hour)).rowcount
        if not claimed:
            return {"status": "already_read"}
        since = max(hour - 168, int(db.meta_get("click_signal_cursor") or hour - 23) - 1)
        response = fetcher.api_get(base + "/c/counts", params={"since": since},
            headers={"Authorization": "Bearer " + token, "Cache-Control": "no-store"},
            deadline=deadline, max_attempts=1, respect_robots=False,
            conditional=False, follow_redirects=False)
        response.raise_for_status()
        body = response.json()
        end, rows = body["until"], body["hours"]
        if (body.get("since") != since or type(end) is not int or not since <= end <= min(hour, since + 6)
                or not isinstance(rows, list) or len(rows) != end - since):
            raise ValueError("invalid interval")
        parsed = []
        for expected, row in zip(range(since, end), rows):
            counts = row["counts"]
            if (row.get("hour") != expected or not isinstance(counts, dict) or len(counts) > 3000
                    or type(row.get("capped")) is not bool or type(row.get("available")) is not bool
                    or (not row["available"] and (counts or row["capped"]))):
                raise ValueError("invalid bucket")
            if any(not KEY.fullmatch(k) or type(v) is not int or not 1 <= v <= 3000
                   for k, v in counts.items()) or sum(counts.values()) > 3000:
                raise ValueError("invalid counts")
            parsed.extend((k, expected, v) for k, v in counts.items())
        with db.conn:
            db.conn.executemany("INSERT INTO deal_clicks VALUES (?,?,?) ON CONFLICT(deal_key,hour) "
                                "DO UPDATE SET count=MAX(count,excluded.count)", parsed)
            db.conn.executemany("INSERT INTO deal_click_coverage VALUES (?,?) ON CONFLICT(hour) "
                                "DO UPDATE SET capped=MAX(capped,excluded.capped)",
                                [(r["hour"], int(r["capped"])) for r in rows if r["available"]])
            db.conn.execute("INSERT INTO meta VALUES ('click_signal_cursor',?) ON CONFLICT(k) "
                            "DO UPDATE SET v=excluded.v", (str(end),))
        return {"status": "ok", "hours": len(rows), "buckets": len(parsed),
                "unavailable_hours": sum(not r["available"] for r in rows),
                "capped_hours": sum(r["capped"] for r in rows), "lag_hours": hour - end}
    except Exception as exc:
        # Never echo the endpoint, response or bearer token into public logs.
        log.warning("click signal ingestion unavailable: %s", type(exc).__name__)
        return {"status": "unavailable"}


def observe_publication(db, cfg, now, rows, *, run_key=None):
    """Accumulate time between generated public snapshots, not impressions.

    No assumed time before the first observation or across a long outage.
    The previous snapshot owns the interval; repeated/out-of-order renders add
    nothing. The signal never changes the publisher's rows or their order.
    """
    if not enabled(cfg):
        if db.meta_get("click_signal_publication") is not None:
            with db.conn:
                db.conn.execute("DELETE FROM meta WHERE k='click_signal_publication'")
        return
    opt = settings(cfg)
    stamp = now.timestamp()
    previous = json.loads(db.meta_get("click_signal_publication") or "{}")
    if run_key and db.conn.execute("SELECT 1 FROM deal_click_runs WHERE run_key=? AND published=1",
                                  (run_key,)).fetchone():
        return
    start = previous.get("at", stamp)
    if stamp <= start and previous:
        return
    with db.conn:
        if cfg.compact_click_exposure:
            if not db.conn.in_transaction:
                db.conn.execute("BEGIN IMMEDIATE")
            compact_schema(db)
        if run_key:
            db.conn.execute("INSERT INTO deal_click_runs(run_key,hour,published) VALUES (?,?,1) "
                "ON CONFLICT(run_key) DO UPDATE SET published=1", (run_key, int(stamp // 3600)))
        if 0 < stamp - start <= opt["max_publish_gap_minutes"] * 60:
            for key, position in previous.get("keys", []):
                left = start
                while left < stamp:
                    hour = int(left // 3600)
                    right = min(stamp, (hour + 1) * 3600)
                    if cfg.compact_click_exposure:
                        add_exposure(db, key, hour, right - left)
                    else:
                        db.conn.execute("INSERT INTO deal_click_shown VALUES (?,?,?,?) "
                            "ON CONFLICT(deal_key,hour) DO UPDATE SET "
                            "seconds=MIN(3600,seconds+excluded.seconds), position=excluded.position",
                            (key, hour, right - left, position))
                    left = right
        snapshot = {"at": stamp, "run_key": run_key,
                    "keys": [(d["key"], i) for i, d in enumerate(rows, 1)]}
        db.conn.execute("INSERT INTO meta VALUES ('click_signal_publication',?) ON CONFLICT(k) "
                        "DO UPDATE SET v=excluded.v", (json.dumps(snapshot),))
        cutoff = int(stamp // 3600) - opt["retention_days"] * 24
        for table in ("deal_clicks", "deal_click_shown", "deal_click_coverage", "deal_click_runs"):
            if cfg.state_retention_337 and table in ("deal_clicks", "deal_click_coverage", "deal_click_runs"):
                continue  # atomic archival in bounded maintenance; run guards never expire
            if table == "deal_click_shown" and cfg.deal_click_shown_days is not None:
                continue  # bounded cooperative cleanup in DB.prune
            db.conn.execute(f"DELETE FROM {table} WHERE hour < ?", (cutoff,))
        if cfg.deal_click_shown_days is None and compact_exists(db):
            db.conn.execute("DELETE FROM deal_click_days WHERE day < ?", (cutoff // 24,))


def features(db, cfg, now, published):
    """One internal feature row per public live deal, in published wall order.

    Prices/savings are copied from verified publication facts. Stock is the
    existing social policy result, not an assertion about inventory quantity.
    Callers must pass this publish's public rows, never the members payload.
    """
    if not enabled(cfg):
        return []
    from .conditions import normalize_conditions
    from .publish.social import plenty_of_stock
    opt = settings(cfg)
    end = int(now.timestamp() // 3600)
    start = end - opt["lookback_hours"]
    from .state_growers import click_rows, click_coverage
    covered = click_coverage(db, start, end)
    coverage = (len(covered), sum(covered.values()))
    by_hour = {(key, hour): n for key, hour, n in click_rows(db, start, end)}
    counts = {}
    for (key, hour), n in by_hour.items():
        counts[key] = counts.get(key, 0) + n
    totals = {}
    for key, hour, seconds in exposure(db, start, end):
        if hour in covered:
            value = totals.setdefault(key, [0., 0])
            value[0] += seconds
            value[1] += by_hour.get((key, hour), 0)
    shown = {key: (value[0] / 3600, value[1]) for key, value in totals.items()}
    def age(value):
        at = parse_iso(value or "")
        return max(0, (now - at).total_seconds() / 3600) if at and at <= now else None
    result = []
    for position, deal in enumerate(published, 1):
        if deal.get("locked") or deal.get("status") != "alive":
            continue
        key = deal["key"]
        price = deal.get("final_price")
        band = ("unknown" if price is None else "under_10" if price < 10 else
                "10_to_25" if price < 25 else "25_to_50" if price < 50 else
                "50_to_100" if price < 100 else "100_plus")
        hours, comparable = shown.get(key, (0, 0))
        source = db.get_deal(deal["dedupe_key"])
        corroboration = (db.distinct_sources_within(source["id"],
            now - timedelta(hours=cfg.corroborate_window_h),
            deal.get("final_price"), now) if source else None)
        result.append({"deal_key": key, "discount_pct": deal.get("pct_at_checkout"),
            "savings_usd": deal.get("abs_savings"), "price_band": band,
            "category": deal.get("category"), "store": deal.get("store"),
            "condition": normalize_conditions(deal.get("conditions") or []),
            "stock_evidence": plenty_of_stock(db, deal, cfg),
            "corroboration_count": corroboration, "age_hours": age(deal.get("first_seen_at")),
            "confirmation_age_hours": age(deal.get("checked_at")),
            "image_present": bool(deal.get("image_url")), "position": position,
            "clicks": counts.get(key, 0) if coverage[0] else None,
            "shown_hours": hours or None,
            "clicks_per_hour_shown": comparable / hours if hours else None,
            "covered_hours": coverage[0], "capped_hours": coverage[1] or 0,
            "engagement_score": math.log1p(comparable / (hours + opt["prior_hours"])) if hours else 0})
    return result


def shadow(rows, cfg):
    """Position prior + bounded log click-rate boost; shadow only, stable ties."""
    weight = settings(cfg)["shadow_weight"]
    order = sorted(rows, key=lambda r: (r["position"] - weight * r["engagement_score"], r["position"]))[:120]
    live = [r["deal_key"] for r in rows[:120]]
    proposed = [r["deal_key"] for r in order]
    return {"live_top120": live, "shadow_top120": proposed,
            "changed_positions": sum(a != b for a, b in zip(live, proposed)),
            "entered": [k for k in proposed if k not in set(live)],
            "moves": [{"deal_key": r["deal_key"], "from": r["position"], "to": i}
                      for i, r in enumerate(order, 1) if r["position"] != i]}


def record_shadow(db, cfg, now, published, *, run_key=None):
    if not enabled(cfg):
        return
    observe_publication(db, cfg, now, published, run_key=run_key)
    rows = features(db, cfg, now, published)
    result = shadow(rows, cfg)
    db.meta_set("click_signal_shadow", json.dumps({"at": now.isoformat(), "features": rows, **result}))
    log.info("click signal SHADOW: %d of top-120 positions would change; %d would enter",
             result["changed_positions"], len(result["entered"]))


def report_data(db, cfg):
    if not enabled(cfg):
        return {"status": "off"}
    data = json.loads(db.meta_get("click_signal_shadow") or "{}")
    return {"status": "ok", **data} if data else {"status": "unavailable"}


def report_lines(data):
    if not data or data.get("status") != "ok":
        return ["First-party clicks: " + (data or {}).get("status", "unavailable")]
    measured = [r for r in data["features"] if r["clicks"] is not None]
    return [f"First-party clicks (shadow, snapshot {data['at']}): "
            + (f"{sum(r['clicks'] for r in measured)} accepted clicks on current public deals" if measured else "unavailable"),
            f"Observed collection hours: {max((r['covered_hours'] for r in measured), default=0)}; "
            f"capped hours: {max((r['capped_hours'] for r in measured), default=0)} (window may be partial).",
            f"Top-120 would change {data['changed_positions']} positions; {len(data['entered'])} would enter.",
            "Clicks/hour shown is generated publication time, not impressions or people; "
            "privacy opt-outs, blockers, caps and outages undercount. Live ranking is unchanged.",
            "Shadow moves (key: old→new): " + (", ".join(
                f"{r['deal_key']}: {r['from']}→{r['to']}" for r in data['moves'][:20]) or "none")]
