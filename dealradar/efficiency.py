"""Private efficiency observations from existing receipts and public-yield facts.

No network or model clients belong here. UTC days match the spend ledger.
Ratios always divide totals, never average per-run ratios.
"""

from __future__ import annotations

import json
import math
from datetime import timedelta, timezone

from .util import iso

COUNTERS = ("feed_tokens", "feed_new_deals", "recheck_tokens", "useful_rechecks",
            "llm_usd", "published_deals", "critic_calls_avoided")
AGE_FIELDS = ("oldest_h", "within_1h", "within_3h", "within_12h")


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(value) and value >= 0 else None


def ratio(numerator, denominator):
    a, b = number(numerator), number(denominator)
    return a / b if a is not None and b is not None and b > 0 else None


def with_ratios(values):
    return {**values,
            "tokens_per_new_verified_deal": ratio(values.get("feed_tokens"), values.get("feed_new_deals")),
            "new_verified_deals_per_token": ratio(values.get("feed_new_deals"), values.get("feed_tokens")),
            "tokens_per_useful_recheck": ratio(values.get("recheck_tokens"), values.get("useful_rechecks")),
            "useful_rechecks_per_token": ratio(values.get("useful_rechecks"), values.get("recheck_tokens")),
            "llm_usd_per_published_deal": ratio(values.get("llm_usd"), values.get("published_deals"))}


def freshness(coverage):
    result = {}
    for condition in ("new", "used"):
        sample = (coverage or {}).get(condition) or {}
        result[condition] = {k: number(sample.get(k)) for k in ("amazon", "unconfirmed", *AGE_FIELDS)}
        # The oldest shown price is unknown if any shown price has no clock.
        if sample.get("unconfirmed") != 0:
            result[condition]["oldest_h"] = None
    return result


def worst_freshness(samples):
    result = {}
    for condition in ("new", "used"):
        rows = [(s or {}).get(condition) or {} for s in samples]
        rows = [r for r in rows if r.get("amazon") != 0]
        result[condition] = {}
        for field in AGE_FIELDS:
            values = [number(r.get(field)) for r in rows]
            result[condition][field] = (None if not values or None in values else
                                       (max(values) if field == "oldest_h" else min(values)))
        # Retain emptiness vs missing clocks when merging another run.
        result[condition]["amazon"] = max((r.get("amazon") or 0 for r in rows), default=0) if rows else 0
        if any(r.get("amazon") is None for r in rows):
            result[condition]["amazon"] = None
    return result


def run_metrics(summary, feed_usage, keepa_sources):
    public = summary.get("ai_yield") or {}
    counts = (summary.get("intake") or {}).get("unique_public_by_source")
    measured = "unavailable" not in public and number(public.get("unique_new_public_deals")) is not None
    recheck = summary.get("price_recheck") or {}
    # Task 087's per-deal history owns useful readings. Its cost includes
    # explicitly estimated debits, which are not actual spend receipts.
    useful = (recheck.get("yield") or {}).get("overall") or {}
    latest = freshness((recheck.get("confirmed_age") or {}).get("wall"))
    values = {
        "feed_tokens": feed_usage.total,
        "feed_new_deals": sum(counts.get(s, 0) for s in keepa_sources)
        if measured and isinstance(counts, dict) else None,
        "recheck_tokens": number(useful.get("tokens"))
        if useful.get("estimated_tokens") == 0 else None,
        "useful_rechecks": number(useful.get("useful")),
        "llm_usd": number(public.get("spend_usd")),
        "published_deals": number(public.get("unique_new_public_deals")) if measured else None,
        "critic_calls_avoided": number((summary.get("audit") or {}).get("critic_calls_avoided")),
        "freshness": {"latest": latest, "worst": latest},
    }
    return with_ratios(values)


def start(db, now, run_key):
    if db.meta_get("efficiency_since") is None:
        db.meta_set("efficiency_since", iso(now))
    # One row per run key, including retries. Pending records expose crashes.
    db.conn.execute("INSERT INTO efficiency_runs(run_key,started_at,observed_at) VALUES (?,?,?)"
                    " ON CONFLICT(run_key) DO UPDATE SET pending=CASE WHEN pending=0 THEN 1 ELSE 2 END",
                    (run_key, iso(now), iso(now)))
    db.conn.commit()


def record(db, now, run_key, values):
    row = db.conn.execute("SELECT metrics,pending FROM efficiency_runs WHERE run_key=?", (run_key,)).fetchone()
    if not row or not row[1]:
        return  # repeated bookkeeping is idempotent; start opens a real attempt
    if row[1] == 2:
        # A prior attempt died without receipts. A retry cannot fill that gap.
        values = {**values, **dict.fromkeys(COUNTERS),
                  "freshness": {**values["freshness"], "worst": freshness(None)}}
    if row and row[0]:
        previous = json.loads(row[0])
        values = dict(values)
        for key in COUNTERS:
            a, b = number(previous.get(key)), number(values.get(key))
            values[key] = a + b if a is not None and b is not None else None
        if "focus_finder_tokens" in previous or "focus_finder_tokens" in values:
            a, b = number(previous.get("focus_finder_tokens", 0)), number(values.get("focus_finder_tokens", 0))
            values["focus_finder_tokens"] = a + b if a is not None and b is not None else None
        values["freshness"] = {"latest": values["freshness"]["latest"],
                               "worst": worst_freshness([previous["freshness"]["worst"],
                                                         values["freshness"]["worst"]])}
    db.conn.execute("UPDATE efficiency_runs SET observed_at=?, metrics=?, pending=0 WHERE run_key=?",
                    (iso(now), json.dumps(with_ratios(values), allow_nan=False), run_key))
    db.conn.commit()


def daily(db, cfg, now):
    from .ai_yield import aggregate
    from .legitimacy import daily_stats

    end = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start_at = end - timedelta(days=1)
    result = {"day": start_at.date().isoformat(), **dict.fromkeys(COUNTERS)}
    has_table = db.conn.execute("SELECT 1 FROM sqlite_master WHERE name='efficiency_runs'").fetchone()
    rows = db.conn.execute("SELECT * FROM efficiency_runs WHERE started_at>=? AND started_at<?"
                           " ORDER BY observed_at,run_key", (iso(start_at), iso(end))).fetchall() if has_table else []
    samples = [json.loads(r["metrics"]) if r["metrics"] and not r["pending"] else {} for r in rows]
    missing_runs = db.conn.execute("SELECT run_key FROM run_stats WHERE started_at>=? AND started_at<?",
                                   (iso(start_at), iso(end))).fetchall()
    missing = {r[0] for r in missing_runs} - {r["run_key"] for r in rows}
    if samples and not missing:
        for key in COUNTERS:
            values = [number(s.get(key)) for s in samples]
            result[key] = sum(values) if None not in values else None
    latest = (samples[-1].get("freshness") or {}).get("latest") if samples else None
    result["freshness"] = {
        "latest": latest or freshness(None),
        "latest_at": rows[-1]["observed_at"] if rows else None,
        "worst": worst_freshness([(s.get("freshness") or {}).get("worst") for s in samples]
                                  + ([None] if missing else [])),
    }
    since = max(db.meta_get("efficiency_since") or iso(end),
                db.meta_get("efficiency_retained_since") or "")
    result["coverage"] = {"samples": len(samples), "from": max(since, iso(start_at)),
                          "partial": since > iso(start_at) or bool(missing) or any(r["pending"] for r in rows)}
    # Use deduped first-public events and the complete UTC spend ledger,
    # including failed runs and calls outside the ordinary pipeline.
    public = aggregate(db, cfg, start_at, end)
    public_gap = any(number(s.get("published_deals")) is None for s in samples)
    if "unavailable" not in public and not public["coverage"]["partial"] and not public_gap:
        result["published_deals"] = public["unique_new_public_deals"]
        result["llm_usd"] = public["spend_usd"]
    else:
        result["published_deals"] = result["llm_usd"] = None
    if result["critic_calls_avoided"] is None:
        # Older snapshots may already have the audit's deduped daily facts.
        audit = daily_stats(db, cfg, now)
        if "unavailable" not in audit:
            result["critic_calls_avoided"] = number(audit.get("critic_calls_avoided"))
    return with_ratios(result)


def lines(values, cfg):
    values = values or with_ratios({})
    fresh = values.get("freshness") or {}
    latest, worst = fresh.get("latest") or freshness(None), fresh.get("worst") or freshness(None)

    def fmt(value, digits=2):
        return "unavailable" if number(value) is None else f"{value:.{digits}f}"

    def age(sample):
        return " / ".join(fmt((sample.get(c) or {}).get("oldest_h")) for c in ("new", "used"))

    found = values.get("new_verified_deals_per_token")
    verdict = (f"Each Keepa token found {fmt(found)} newly verified deals" if number(found) is not None
               else "Keepa deal-finding efficiency is unavailable")
    out = [verdict + f"; oldest shown Amazon price, new / used: {age(latest)} h.",
           "Keepa tokens per newly verified deal: " + fmt(values.get("tokens_per_new_verified_deal"))
           + f" ({fmt(values.get('feed_tokens'))} tokens / {fmt(values.get('feed_new_deals'), 0)} new public deals)",
           "Tokens per useful recheck: " + fmt(values.get("tokens_per_useful_recheck"))
           + "; useful rechecks per token: " + fmt(values.get("useful_rechecks_per_token"))
           + f" ({fmt(values.get('useful_rechecks'), 0)} changes/deaths / {fmt(values.get('recheck_tokens'))} tokens)",
           f"Expired-deal clicks (death within {cfg.growth_expired_click_hours:g} h after click): "
           "unavailable (existing GA4 queries lack deal-level click times)",
           f"Oldest shown Amazon price, new / used (h): latest {age(latest)}; day's worst {age(worst)}"]
    for condition in ("new", "used"):
        def shares(sample):
            row = sample.get(condition) or {}
            return " / ".join("unavailable" if number(row.get(f"within_{h}h")) is None
                              else f"{row[f'within_{h}h']:.1%}" for h in (1, 3, 12))
        out.append(f"Shown Amazon {condition}, confirmed within 1 / 3 / 12 h: "
                   f"latest {shares(latest)}; day's worst {shares(worst)}")
    out += ["LLM dollars per first published deal: " + ("unavailable" if values.get("llm_usd_per_published_deal") is None
                                                    else "$" + fmt(values["llm_usd_per_published_deal"], 4))
            + f" ({fmt(values.get('llm_usd'), 4)} USD / {fmt(values.get('published_deals'), 0)} deals)",
            "Critic calls avoided: " + fmt(values.get("critic_calls_avoided"), 0)]
    if coverage := values.get("coverage"):
        out.append(f"Observed runs: {coverage['samples']}; coverage from {coverage['from']}"
                   + (" (partial day; missing history unavailable)" if coverage["partial"] else ""))
    if fresh.get("latest_at"):
        out.append("Latest shown-price snapshot: " + fresh["latest_at"])
    return out
