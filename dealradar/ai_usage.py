"""Private UTC usage and second-opinion yield, derived from durable receipts."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta, timezone

from .util import iso


def model_calls_today(conn, kind, model, now):
    day = now.astimezone(timezone.utc).date().isoformat()
    return conn.execute(
        "SELECT COUNT(*) FROM llm_calls c WHERE kind=? AND model=?"
        " AND (substr(started_at,1,10)=? OR substr(billed_at,1,10)=?"
        " OR EXISTS (SELECT 1 FROM llm_reservations r WHERE r.call_key=c.call_key AND r.day=?))",
        (kind, model, day, day, day)).fetchone()[0]


def second_opinion_sources(conn, now, days=1):
    since = iso(now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                - timedelta(days=days - 1))
    until = iso(now.astimezone(timezone.utc))
    columns = {r[1] for r in conn.execute("PRAGMA table_info(llm_calls)")}
    if "kind" not in columns:
        return {}
    sources = {}
    for source, model, calls in conn.execute(
            "SELECT source_name, model, COUNT(*) FROM llm_calls WHERE kind='second_opinion'"
            " AND status!='released' AND started_at>=? AND started_at<=? GROUP BY source_name, model",
            (since, until)):
        row = sources.setdefault(source, {"calls": 0, "models": {}, "extracted": 0,
                                          "evidence": 0, "successful_posts": 0})
        row["calls"] += calls
        row["models"][model] = calls
    for source, extracted, evidence, successful in conn.execute(
            "SELECT source_name, SUM(extracted), SUM(evidence), SUM(evidence>0)"
            " FROM second_opinion_yield WHERE observed_at>=? AND observed_at<=? GROUP BY source_name",
            (since, until)):
        if source in sources:
            sources[source].update(extracted=extracted, evidence=evidence, successful_posts=successful)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='state_audit_counts'").fetchone():
        for dimensions, n in conn.execute(
                "SELECT dimensions,SUM(n) FROM state_audit_counts WHERE kind='second_opinion_yield'"
                " AND stamp>=? AND stamp<=? GROUP BY dimensions", (since[:10], until[:10])):
            source, field = json.loads(dimensions)
            if source in sources:
                sources[source][field] += n
    for row in sources.values():
        row["yield"] = min(1.0, row["successful_posts"] / row["calls"])
    return sources


def record_second_opinion(db, post, fingerprint, now, extracted, evidence):
    key = hashlib.sha256((post.source_name + "\n" + fingerprint).encode()).hexdigest()
    from .state_growers import exists
    # Test the archived guard in the INSERT's transaction, including rollback.
    guard = (" WHERE NOT EXISTS (SELECT 1 FROM second_opinion_retired_events WHERE event_key=?)"
             if exists(db, "second_opinion_retired_events") else "")
    db.conn.execute("INSERT OR IGNORE INTO second_opinion_yield SELECT ?,?,?,?,?" + guard,
                    (key, iso(now), post.source_name, extracted, evidence) + ((key,) if guard else ()))
    db.conn.commit()


def daily(db, now):
    """Known token totals plus explicit missing usage; old costs never become free.

    Uncertain reservations are shown on each UTC day whose hard cap they consume.
    The sums therefore reconcile with spend_usd_for_day, not hypothetical invoices.
    """
    day = now.astimezone(timezone.utc).date().isoformat()
    columns = {r[1] for r in db.conn.execute("PRAGMA table_info(llm_calls)")}
    detailed = "kind" in columns
    fields = ("kind, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens"
              if detailed else "'unknown', model, NULL, NULL, NULL, NULL")
    rows = []
    if columns:
        rows = db.conn.execute(
            f"SELECT {fields}, cost_usd FROM llm_calls WHERE status='settled'"
            " AND substr(billed_at,1,10)=? UNION ALL "
            f"SELECT {fields}, r.usd FROM llm_calls c JOIN llm_reservations r USING(call_key)"
            " WHERE r.day=?", (day, day)).fetchall()
    result = {"day": day, "by_kind": {}, "legacy_usd": 0.0,
              "second_opinion_sources": second_opinion_sources(db.conn, now)}
    for kind, model, inp, out, read, write, cost in rows:
        row = result["by_kind"].setdefault(kind, {
            "calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
            "cache_write_tokens": 0, "unknown_usage_calls": 0, "usd": 0.0, "models": {}})
        row["calls"] += 1
        row["usd"] += cost
        row["models"][model] = row["models"].get(model, 0.0) + cost
        if inp is None:
            row["unknown_usage_calls"] += 1
        for name, value in zip(("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"),
                               (inp, out, read, write)):
            row[name] += value or 0
    if columns:
        result["legacy_usd"] = db.conn.execute(
            "SELECT COALESCE(SUM(usd),0) FROM llm_legacy_spend WHERE substr(billed_at,1,10)=?",
            (day,)).fetchone()[0]
    else:
        result["legacy_usd"] = db.spend_usd_for_day(now)
    result["usd"] = sum(r["usd"] for r in result["by_kind"].values()) + result["legacy_usd"]
    return result


def lines(data):
    if data is None:
        return ["AI usage (UTC): unavailable"]
    parts = []
    for kind, row in sorted(data["by_kind"].items()):
        label = "parse" if kind == "extract" else kind
        tokens = sum(row[k] for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"))
        missing = f"; usage missing for {row['unknown_usage_calls']}" if row["unknown_usage_calls"] else ""
        parts.append(f"{label} {row['calls']} calls/{tokens} tokens/${row['usd']:.4f}{missing}")
    if data["legacy_usd"]:
        parts.append(f"legacy ${data['legacy_usd']:.4f} (kind/calls/tokens unavailable)")
    output = [f"AI usage {data['day']} UTC ${data['usd']:.4f}: " + ("; ".join(parts) or "no recorded calls")]
    for source, row in sorted(data["second_opinion_sources"].items()):
        output.append(f"Second opinion {source}: {row['calls']} calls, {row['successful_posts']} backed posts, "
                      f"{row['evidence']}/{row['extracted']} deals pass evidence, yield {row['yield']:.1%}")
    return output
