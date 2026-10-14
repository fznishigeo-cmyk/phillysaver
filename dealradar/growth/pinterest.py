"""Bounded, private Pinterest observations. Selection never reads these weights.

API shape: pinterest/api-description v5, GET /pins/{pin_id}/analytics.
Use READY daily counts from the unsplit all group, never lifetime totals.
The multi-pin endpoint is beta; the standard endpoint is production-only.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from datetime import date, timedelta, timezone

from .. import util
from ..persistence import checkpoint_state
from ..publish.pinterest import _APIError, _environment, _meta

log = logging.getLogger(__name__)
METRICS = {"IMPRESSION": "impressions", "SAVE": "saves",
           "PIN_CLICK": "pin_clicks", "OUTBOUND_CLICK": "outbound_clicks"}
WINDOW_DAYS = 30


def _recent(db, environment, now):
    start = now.date() - timedelta(days=WINDOW_DAYS)
    pins = {}
    for entry in _meta(db, "pinterest_posted").values():
        # Older delivery anchors have no environment. Keep their dedupe, but
        # never guess their account, board or category for analytics.
        if entry.get("environment") != environment:
            continue
        at = util.parse_iso(entry.get("at"))
        pin_id = str(entry.get("pin_id") or "")
        if at and start <= at.date() < now.date() and re.fullmatch(r"\d+", pin_id):
            pins[pin_id] = entry
    return pins


def _daily_rows(data, start, end):
    groups = [value for key, value in data.items() if key.casefold() == "all"]
    if len(groups) != 1:
        raise ValueError("missing or ambiguous unsplit metrics")
    rows = groups[0]["daily_metrics"]
    if not isinstance(rows, list) or len(rows) > WINDOW_DAYS:
        raise ValueError("invalid daily metrics")
    counts = {}
    for row in rows:
        day = date.fromisoformat(row["date"])
        if not start <= day <= end or row.get("data_status") != "READY":
            continue
        values = [row.get("metrics", {}).get(key) for key in METRICS]
        if not all(type(v) in (int, float) and math.isfinite(v)
                   and 0 <= v < 2**63 and int(v) == v for v in values):
            continue
        if day.isoformat() in counts:
            raise ValueError("duplicate metric date")
        counts[day.isoformat()] = tuple(int(v) for v in values)
    return counts


def collect_daily(db, cfg, api, headers, now):
    """Fail soft, claim before requesting, and never retry within a UTC day."""
    try:
        _collect_daily(db, cfg, api, headers, now.astimezone(timezone.utc))
    except Exception as error:
        # This is an optional tail of the poster. No exception messages,
        # response bodies, pin IDs or counts enter the public log.
        log.warning("pinterest analytics: error (%s)", type(error).__name__)


def _collect_daily(db, cfg, api, headers, now):
    settings = cfg.pinterest.get("analytics") or {}
    environment = _environment(cfg)
    if not settings.get("enabled", False) or environment != "production":
        return
    key = "pinterest_analytics_" + environment
    previous = _meta(db, key)
    day = now.date().isoformat()
    not_before = util.parse_iso(previous.get("not_before"))
    if previous.get("day", "") >= day or (not_before and now < not_before):
        return
    start = now.date() - timedelta(days=WINDOW_DAYS)
    end = now.date() - timedelta(days=1)
    pins = _recent(db, environment, now)
    checked = {k: v for k, v in previous.get("checked", {}).items() if k in pins}
    expected = sum((end - util.parse_iso(p["at"]).date()).days + 1 for p in pins.values())
    state = {"day": day, "status": "partial", "eligible": len(pins), "requested": 0,
             "received": 0, "ready_days": 0, "checked": checked}
    # Atomic daily claim also protects two local callers sharing this DB.
    claim = db.conn.execute(
        "INSERT INTO meta(k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v"
        " WHERE COALESCE(json_extract(meta.v,'$.day'),'') < ?",
        (key, json.dumps(state), day))
    db.conn.commit()
    if not claim.rowcount:
        return
    db.conn.execute("DELETE FROM pinterest_pin_daily WHERE day < ? OR posted_at < ?",
                    (start.isoformat(), start.isoformat()))
    db.conn.commit()
    maximum = min(600, max(0, int(settings.get("max_requests_per_day", 60))))
    seconds = min(300, max(0, float(settings.get("max_seconds", 90))))
    ordered = sorted(pins, key=lambda p: (checked.get(p, ""), pins[p]["at"], p))
    if ordered and maximum and seconds > api.interval:
        # The hourly workflow can be retried from encrypted state after a
        # runner loss. Persist the daily claim before any analytics request.
        checkpoint_state(db)
    deadline = time.monotonic() + seconds
    for pin_id in ordered[:maximum]:
        remaining = deadline - time.monotonic() - api.interval
        if remaining <= 0:
            break
        entry = pins[pin_id]
        first = max(start, util.parse_iso(entry["at"]).date())
        state["requested"] += 1
        checked[pin_id] = util.iso(now)
        db.meta_set(key, json.dumps(state))
        try:
            data = api.request("GET", f"/pins/{pin_id}/analytics", headers=headers,
                               params={"start_date": first.isoformat(), "end_date": end.isoformat(),
                                       "metric_types": ",".join(METRICS), "app_types": "ALL",
                                       "split_field": "NO_SPLIT"},
                               timeout=min(cfg.request_timeout_s, remaining))
            counts = _daily_rows(data, first, end)
            with db.conn:
                db.conn.executemany(
                    "INSERT INTO pinterest_pin_daily VALUES (?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(environment,pin_id,day) DO UPDATE SET"
                    " impressions=excluded.impressions,saves=excluded.saves,"
                    " pin_clicks=excluded.pin_clicks,outbound_clicks=excluded.outbound_clicks",
                    [(environment, pin_id, d, entry.get("board") or "Unknown board",
                      entry.get("category") or "other", entry["at"], *values)
                     for d, values in counts.items()])
            state["received"] += 1
            state["ready_days"] += len(counts)
        except _APIError as error:
            state["status"] = "rate_limited" if error.status == 429 else "error"
            failures = min(6, int(previous.get("failures", 0)) + 1)
            state["failures"] = failures
            delay = max(3600 * 2**(failures - 1), getattr(error, "retry_after", 0))
            state["not_before"] = util.iso(now + timedelta(seconds=delay))
            break  # no in-run retries, including timeouts and server errors
        except (ValueError, KeyError, TypeError, OverflowError):
            state["status"] = "invalid_response"
            break
        finally:
            db.meta_set(key, json.dumps(state))
    if (state["status"] == "partial" and state["received"] == len(pins)
            and state["ready_days"] == expected):
        state["status"] = "ok"
    db.meta_set(key, json.dumps(state))
    log.info("pinterest analytics: %s", state["status"])


def shadow_weights(categories):
    """Smoothed outbound/impression rate vs the pool, clamped to 0.5–2x.

    One pseudo-click per 100 pseudo-impressions tempers small samples.
    Unobserved categories stay neutral. These are diagnostics, never inputs.
    """
    total_i = sum(row["impressions"] for row in categories)
    total_c = sum(row["outbound_clicks"] for row in categories)
    baseline = (total_c + 1) / (total_i + 100)
    return {row["name"]: (round(max(.5, min(2.,
            ((row["outbound_clicks"] + 1) / (row["impressions"] + 100)) / baseline)), 3)
            if row["impressions"] else 1.0) for row in categories}


def report_data(db, cfg, now):
    environment = _environment(cfg)
    if environment != "production":
        return {"unavailable": "sandbox analytics are unavailable"}
    now = now.astimezone(timezone.utc)
    start, end = now.date() - timedelta(days=WINDOW_DAYS), now.date() - timedelta(days=1)
    rows = [dict(row) for row in db.conn.execute(
        "SELECT * FROM pinterest_pin_daily WHERE environment=? AND day BETWEEN ? AND ?"
        " AND posted_at>=? AND posted_at<?",
        (environment, start.isoformat(), end.isoformat(), start.isoformat(), now.date().isoformat()))]
    state = _meta(db, "pinterest_analytics_" + environment)
    pins = _recent(db, environment, now)
    expected = sum((end - util.parse_iso(p["at"]).date()).days + 1 for p in pins.values())

    def aggregate(field):
        groups = {}
        for row in rows:
            name = (f"{util.parse_iso(row['posted_at']).hour:02d}:00 UTC" if field == "hour"
                    else row[field])
            group = groups.setdefault(name, {"name": name, "pins": set(), "pin_days": 0,
                                            **dict.fromkeys(METRICS.values(), 0)})
            group["pins"].add(row["pin_id"])
            group["pin_days"] += 1
            for metric in METRICS.values():
                group[metric] += row[metric]
        for group in groups.values():
            group["pins"] = len(group["pins"])
            group["outbound_per_100"] = (round(100 * group["outbound_clicks"] / group["impressions"], 3)
                                          if group["impressions"] else None)
        return sorted(groups.values(), key=lambda g: (-g["outbound_clicks"], -g["impressions"], g["name"]))

    categories = aggregate("category")
    weights = shadow_weights(categories)
    for category in cfg.pinterest.get("category_boards", {}):
        weights.setdefault(category, 1.0)
    impressions = sum(row["impressions"] for row in rows)
    outbound = sum(row["outbound_clicks"] for row in rows)
    return {"start": start.isoformat(), "end": end.isoformat(),
            "collection": {k: state.get(k) for k in ("day", "status", "eligible", "requested", "received")},
            "eligible_pins": len(pins), "observed_pins": len({row["pin_id"] for row in rows}),
            "pin_days": len(rows), "expected_pin_days": expected,
            "totals": {metric: sum(row[metric] for row in rows) for metric in METRICS.values()},
            "outbound_per_100": round(100 * outbound / impressions, 3) if impressions else None,
            "boards": aggregate("board"), "categories": categories,
            "hours": sorted((h for h in aggregate("hour") if h["outbound_per_100"] is not None),
                            key=lambda h: (-h["outbound_per_100"], -h["impressions"], h["name"])),
            "shadow_weights": weights}


def report_lines(data):
    lines = ["== Pinterest (private; shadow only) =="]
    if not data or "unavailable" in data:
        return lines + [(data or {}).get("unavailable", "Analytics not available")]
    collection = data["collection"]
    lines.append(f"Window {data['start']}–{data['end']} UTC; collection "
                 f"{collection['day'] or 'not yet run'}: {collection['status'] or 'unavailable'}")
    if collection["day"]:
        lines.append(f"Latest daily sample: {collection['received']}/{collection['eligible']} pins read, "
                     f"{collection['requested']} requests; least recently checked pins go first.")
    lines.append(f"Coverage: {data['observed_pins']}/{data['eligible_pins']} recent pins, "
                 f"{data['pin_days']}/{data['expected_pin_days']} ready pin-days. "
                 "Missing/processing days are unavailable; totals may be partial.")
    if not data["pin_days"]:
        return lines + ["Top boards/categories, click rate, posting hours and weights: not available"]
    totals = data["totals"]
    lines.append("Observed: " + ", ".join(f"{totals[m]} {m.replace('_', ' ')}" for m in METRICS.values()))
    rate = data["outbound_per_100"]
    lines.append("Outbound clicks per 100 impressions: " + (f"{rate:.2f}" if rate is not None else "unavailable"))
    for label, key in (("Top boards", "boards"), ("Top categories", "categories"),
                       ("Best posting hours (UTC; descriptive, not causal)", "hours")):
        lines.append(label + ": " + ("; ".join(
            f"{r['name']}: {r['outbound_clicks']} out / {r['impressions']} impressions, "
            f"{r['pins']} pins" for r in data[key][:5]) or "unavailable"))
    lines.append("Shadow category weights (not applied; smoothed rate, 0.5–2x): " + ", ".join(
        f"{name} {weight:.3f}x" for name, weight in sorted(data["shadow_weights"].items())))
    return lines
