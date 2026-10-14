"""Stored code verdicts and idempotent, private audit measurements; no IO to APIs."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta, timezone

from .util import iso, parse_iso


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def fingerprint(deal, cfg) -> str:
    fields = ("dedupe_key", "item", "asin", "store", "canonical_url", "regular_price",
              "final_price", "pct_at_checkout", "abs_savings", "conditions", "codes",
              "stack_steps", "quantity_or_unit", "price_is_range", "regular_price_basis",
              "audit_reference", "shipping_cost_or_threshold", "basis")
    facts = {k: deal.get(k) for k in fields}
    facts["flags"] = sorted(set(deal.get("flags") or []) & {
        "unit_mismatch", "pack_mismatch", "per_unit", "range"})
    facts["has_keepa_observation"] = parse_iso(deal.get("keepa_price_seen_at") or "") is not None
    knobs = {k: v for k, v in vars(cfg).items() if k.startswith("audit_") and k != "audit_mode"}
    return _hash([1, facts, knobs, cfg.min_pct, cfg.history_min_observations])


def valid_code_basis(deal, cfg) -> bool:
    audit = deal.get("audit") or {}
    return (audit.get("outcome") == "verified"
            and audit.get("reason") in {"amazon_reference", "recorded_regular_price"}
            and audit.get("fingerprint") == fingerprint(deal, cfg))


def settled_critic(verdict) -> bool:
    return (isinstance(verdict, dict) and type(verdict.get("plausible")) is bool
            and (not verdict["plausible"] or verdict.get("rechecked")
                 or float(verdict.get("confidence") or 0) >= .7))


def empty_stats(cfg):
    return {"mode": cfg.audit_mode, "outcomes": {k: 0 for k in ("verified", "reject", "needs_critic")},
            "reasons": {}, "agreement_by_reason": {}, "critic_deals_avoided": 0,
            "critic_calls_avoided": 0, "estimated_usd_saved": 0.0,
            "would_avoid_deals": 0, "would_avoid_calls": 0,
            "estimated_usd_if_enforced": 0.0,
            "estimate_basis": "uncached prompt estimate and output allowance; not billed savings",
            "model": cfg.critic_model}


def evaluate(db, deal, cfg, now, stats):
    from .verify import audit_verdict

    decision = audit_verdict(db, deal, cfg, now)
    decision["fingerprint"] = fingerprint(deal, cfg)
    event = _hash([deal["dedupe_key"], decision, cfg.audit_mode])
    decision["event_key"] = event
    # Changing mode reverses only an audit-owned flag; paid critic rejections stay.
    previous = deal.get("audit") or {}
    if previous.get("event_key") == event and previous.get("critic_avoided"):
        decision["critic_avoided"] = True
    flags = list(deal.get("flags") or [])
    if previous.get("applied_reject") and (deal.get("critic") or {}).get("plausible") is not False:
        flags = [f for f in flags if f != "suspect_msrp"]
    if cfg.audit_mode == "enforce" and decision["outcome"] == "reject":
        flags = list(dict.fromkeys([*flags, "suspect_msrp"]))
        decision["applied_reject"] = True
    changed = previous != decision or flags != deal.get("flags")
    deal.update(audit=decision, flags=flags)
    if changed:
        db.update_deal(deal["dedupe_key"], payload={k: v for k, v in deal.items() if k != "id"})
    fresh = db.conn.execute(
        "INSERT OR IGNORE INTO legitimacy_audits(event_key, observed_at, outcome, reason, mode)"
        " VALUES (?,?,?,?,?)", (event, iso(now), decision["outcome"], decision["reason"], cfg.audit_mode)).rowcount
    db.conn.commit()
    if fresh:
        stats["outcomes"][decision["outcome"]] += 1
        reason = decision["reason"]
        stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
    return decision


def compare(db, deal, verdict, cfg, now, stats):
    audit = deal.get("audit") or {}
    if cfg.audit_mode != "shadow" or audit.get("outcome") == "needs_critic" or not settled_critic(verdict):
        return
    agreed = int((audit["outcome"] == "verified") == verdict["plausible"])
    fresh = db.conn.execute("UPDATE legitimacy_audits SET agreement=?, compared_at=?"
                            " WHERE event_key=? AND agreement IS NULL",
                            (agreed, iso(now), audit["event_key"])).rowcount
    db.conn.commit()
    if fresh:
        bucket = stats["agreement_by_reason"].setdefault(audit["reason"], {"compared": 0, "agreed": 0})
        bucket["compared"] += 1
        bucket["agreed"] += agreed
        bucket["rate"] = bucket["agreed"] / bucket["compared"]


def record_batch(db, original, remaining, cfg, parser, now, stats):
    """Full eliminated batches count as calls; smaller batches save tokens only.

    Reuse is resolved before this point. Shadow records only batches actually
    attempted. A partial/missing response never counts as an agreement.
    """
    from .parse.claude_client import estimate_critic_cost

    avoided = len(original) - len(remaining)
    if not avoided:
        return
    if cfg.audit_mode == "enforce":
        for deal in original:
            if deal["audit"]["outcome"] != "needs_critic":
                deal["audit"]["critic_avoided"] = True
                db.update_deal(deal["dedupe_key"], payload={k: v for k, v in deal.items() if k != "id"})
    batch_key = _hash([cfg.audit_mode, cfg.critic_model,
                       sorted(d["audit"]["event_key"] for d in original)])
    full_cost = estimate_critic_cost(parser, original)
    remaining_cost = estimate_critic_cost(parser, remaining)
    saved = None if full_cost is None or remaining_cost is None else max(0.0, full_cost - remaining_cost)
    calls = int(not remaining)
    fresh = db.conn.execute(
        "INSERT OR IGNORE INTO legitimacy_batches VALUES (?,?,?,?,?,?,?)",
        (batch_key, iso(now), cfg.audit_mode, cfg.critic_model, avoided, calls, saved)).rowcount
    db.conn.commit()
    if not fresh:
        return
    stats["would_avoid_deals"] += avoided
    stats["would_avoid_calls"] += calls
    stats["estimated_usd_if_enforced"] = (None if saved is None or stats["estimated_usd_if_enforced"] is None
                                           else stats["estimated_usd_if_enforced"] + saved)
    if cfg.audit_mode == "enforce":
        stats["critic_deals_avoided"] += avoided
        stats["critic_calls_avoided"] += calls
        stats["estimated_usd_saved"] = stats["estimated_usd_if_enforced"]


def daily_stats(db, cfg, now):
    """Previous complete UTC day; old snapshots have no audit measurements."""
    until = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    since = until - timedelta(days=1)
    stats = empty_stats(cfg)
    stats["day"] = since.date().isoformat()
    if not db.conn.execute("SELECT 1 FROM sqlite_master WHERE name='legitimacy_audits'").fetchone():
        return {"unavailable": "snapshot predates legitimacy audit"}
    rows = db.conn.execute("SELECT * FROM legitimacy_audits WHERE observed_at>=? AND observed_at<?",
                           (iso(since), iso(until))).fetchall()
    comparisons = db.conn.execute("SELECT reason, agreement FROM legitimacy_audits"
                                  " WHERE compared_at>=? AND compared_at<?",
                                  (iso(since), iso(until))).fetchall()
    batches = db.conn.execute("SELECT * FROM legitimacy_batches WHERE observed_at>=? AND observed_at<?",
                              (iso(since), iso(until))).fetchall()
    if not rows and not comparisons and not batches:
        return {"unavailable": "no audit samples yesterday UTC"}
    stats["modes"] = sorted({r["mode"] for r in rows})
    for row in rows:
        stats["outcomes"][row["outcome"]] += 1
        reason = row["reason"]
        stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
    for row in comparisons:
        bucket = stats["agreement_by_reason"].setdefault(row["reason"], {"compared": 0, "agreed": 0})
        bucket["compared"] += 1
        bucket["agreed"] += row["agreement"]
        bucket["rate"] = bucket["agreed"] / bucket["compared"]
    stats["models"] = sorted({r["model"] for r in batches})
    for row in batches:
        for field, column in (("would_avoid_deals", "deals_avoided"), ("would_avoid_calls", "calls_avoided")):
            stats[field] += row[column]
        cost = row["estimated_usd_saved"]
        stats["estimated_usd_if_enforced"] = (None if cost is None or stats["estimated_usd_if_enforced"] is None
                                               else stats["estimated_usd_if_enforced"] + cost)
        if row["mode"] == "enforce":
            stats["critic_deals_avoided"] += row["deals_avoided"]
            stats["critic_calls_avoided"] += row["calls_avoided"]
            stats["estimated_usd_saved"] = (None if cost is None or stats["estimated_usd_saved"] is None
                                            else stats["estimated_usd_saved"] + cost)
    return stats


def daily_line(stats):
    if not stats or "unavailable" in stats:
        return "Legitimacy audit: not available (" + (stats or {}).get("unavailable", "state snapshot unavailable") + ")"
    counts = ", ".join(f"{k} {v}" for k, v in stats["outcomes"].items())
    agreement = ", ".join(f"{r} {b['agreed']}/{b['compared']} ({b['rate']:.0%})"
                          for r, b in sorted(stats["agreement_by_reason"].items())) or "unavailable"
    def money(value):
        return "unavailable" if value is None else f"${value:.4f}"
    return (f"Legitimacy audit ({stats['day']} UTC): {counts}; calls avoided {stats['critic_calls_avoided']}, "
            f"estimated saved {money(stats['estimated_usd_saved'])}; potential calls {stats['would_avoid_calls']}, "
            f"estimated potential {money(stats['estimated_usd_if_enforced'])}; agreement by reason: {agreement}")
