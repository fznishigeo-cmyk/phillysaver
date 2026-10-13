"""Private useful-yield observations. No post text, model calls or rollout switch."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import rank
from .fetch.newsletter import registrable_domain
from .util import iso, parse_iso


def policy_key(cfg) -> str:
    # A rule/floor change needs fresh shadow evidence and a matching replay.
    rules = [1, min(cfg.min_pct, cfg.site_show_from_pct), cfg.used_min_pct,
             cfg.min_abs_savings, cfg.max_chars_per_post, cfg.critic_enabled,
             cfg.critic_model, cfg.exclude_suspect_msrp]
    return hashlib.sha256(json.dumps(rules).encode()).hexdigest()[:16]


def start(db, cfg, now: datetime, run_key: str) -> None:
    if db.meta_get("ai_yield_since") is None:
        # The old wall is a baseline, never a batch of newly supplied deals.
        for deal in db.all_deals():
            identity = hashlib.sha256(deal["dedupe_key"].encode()).hexdigest()
            db.conn.execute("INSERT OR IGNORE INTO ai_yield_events"
                            "(event_key,identity,kind,observed_at,run_key,source_name,category)"
                            " VALUES (?,?,'baseline',?,?,?,?)",
                            (identity, identity, iso(now), run_key, deal.get("source_name", ""),
                             deal.get("category") or "other"))
        for deal in rank.site_deals(db, cfg, now):
            if deal.get("status") == "alive":
                db.conn.execute("UPDATE deals SET ai_public_at=?, ai_public_price=?"
                                " WHERE dedupe_key=?", (iso(now), deal.get("final_price"),
                                                        deal["dedupe_key"]))
        db.conn.execute("UPDATE deals SET ai_public_at=? WHERE ai_public_at IS NULL", (iso(now),))
        db.meta_set("ai_yield_since", iso(now))
    db.conn.execute("INSERT INTO ai_yield_runs"
                    "(run_key,started_at,policy,mode) VALUES (?,?,?,?)"
                    " ON CONFLICT(run_key) DO UPDATE SET finished_at=NULL,"
                    " failed=MAX(failed,policy!=excluded.policy,mode!=excluded.mode)",
                    (run_key, iso(now), policy_key(cfg), cfg.prefilter_below_floor))
    db.conn.commit()


def observe_prefilter(db, cfg, posts, post_counts: dict, now: datetime, sources=()) -> None:
    sites = {s.name: s.site or s.url for s in sources}
    for post in posts:
        counts = post_counts.get(post.content_hash, {})
        if not counts.get("prefilter_would_skip"):
            continue
        site = registrable_domain(urlsplit(sites.get(post.source_name) or post.url).hostname or "")
        evaluated = bool(counts.get("evaluation_complete"))
        db.conn.execute(
            "INSERT INTO ai_prefilter_posts"
            "(policy,source_name,content_hash,site,text_fingerprint,first_seen_at,evaluated,miss,gap)"
            " VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(policy,source_name,content_hash)"
            " DO UPDATE SET evaluated=MAX(evaluated,excluded.evaluated),"
            " miss=MAX(miss,excluded.miss),"
            " gap=CASE WHEN evaluated OR excluded.evaluated THEN '' ELSE excluded.gap END",
            (policy_key(cfg), post.source_name, post.content_hash, site,
             counts.get("text_fingerprint", post.content_hash), iso(now),
             int(evaluated), int(bool(counts.get("prefilter_miss"))),
             "" if evaluated else counts.get("evaluation_gap", "not_parsed")))
        if counts.get("prefilter_miss"):
            db.meta_set("ai_yield_missed_policy", policy_key(cfg))
    db.conn.commit()


def record_public(db, cfg, now: datetime, run_key: str, public_keys) -> dict:
    """Count each first public identity, then changes to its backed public price.

    The two inventory columns survive observation retention. A price change
    is useful when the new price still passes the site's real gates, in either
    direction; repeats of the same price and old source statements add nothing.
    """
    counts = {"unique_new_public_deals": 0, "useful_price_updates": 0}
    since = db.meta_get("ai_yield_since")
    for key in set(public_keys):
        deal = db.get_deal(key)
        if deal is None or not rank.is_publishable(
                deal, cfg, min_pct=min(cfg.min_pct, cfg.site_show_from_pct))[0]:
            continue
        anchor = db.conn.execute("SELECT ai_public_at,ai_public_price FROM deals"
                                 " WHERE dedupe_key=?", (key,)).fetchone()
        price = deal.get("final_price")
        kind = ""
        if anchor["ai_public_at"] is None:
            if deal["first_seen_at"] >= since:
                kind = "new"
        elif price is not None and anchor["ai_public_price"] is not None \
                and price != anchor["ai_public_price"]:
            kind = "update"
        if kind:
            # Deal payloads omit source_name; the backed source facts live in
            # sightings. Credit the first supplier, or the latest sighting
            # supporting an updated price. API-only changes keep first credit.
            sightings = [s for s in db.sightings_for(deal["id"]) if s["seen_at"] <= iso(now)]
            matching = [s for s in sightings if s["final_price"] == price]
            credited = (matching[-1] if kind == "update" else matching[0]) if matching else (
                sightings[0] if sightings else {})
            source = deal.get("source_name") or credited.get("source_name", "")
            identity = hashlib.sha256(key.encode()).hexdigest()
            version = [identity, kind, anchor["ai_public_price"], price,
                       deal.get("price_published_at")]
            event = hashlib.sha256(json.dumps(version).encode()).hexdigest()
            result = db.conn.execute(
                "INSERT OR IGNORE INTO ai_yield_events"
                "(event_key,identity,kind,observed_at,run_key,source_name,category)"
                " VALUES (?,?,?,?,?,?,?)",
                (event, identity, kind, iso(now), run_key, source,
                 deal.get("category") or "other"))
            if result.rowcount:
                counts["unique_new_public_deals" if kind == "new" else "useful_price_updates"] += 1
        db.conn.execute("UPDATE deals SET ai_public_at=COALESCE(ai_public_at,?),"
                        " ai_public_price=? WHERE dedupe_key=?", (iso(now), price, key))
    db.conn.commit()
    return counts


def public_keys(site_result, cfg) -> list[str]:
    # Read the exact rendered public wall, including the paywall's real
    # unlock selection. Do not count locked stubs or confirmed-gone cards.
    path = Path(site_result.get("out_dir") or cfg.site_dir) / "deals.json"
    deals = json.loads(path.read_text(encoding="utf-8"))["deals"]
    return [d["dedupe_key"] for d in deals
            if d.get("alive", d.get("status") == "alive") and not d.get("locked")]


def finish(db, cfg, now: datetime, run_key: str, *, complete: bool) -> None:
    db.conn.execute("UPDATE ai_yield_runs SET finished_at=?, failed=MAX(failed,?) WHERE run_key=?",
                    (iso(now), int(not complete), run_key))
    db.conn.commit()


def metrics(counts: dict, spend: float, unevaluated: int) -> dict:
    return {**counts, "spend_usd": spend,
            "new_deals_per_usd": counts["unique_new_public_deals"] / spend if spend > 0 else None,
            "prefilter_unevaluated": unevaluated}


def aggregate(db, cfg, start_at: datetime, end_at: datetime) -> dict:
    if not db.conn.execute("SELECT 1 FROM sqlite_master WHERE name='ai_yield_events'").fetchone():
        return {"unavailable": "history before AI yield instrumentation is unavailable"}
    since = db.meta_get("ai_yield_since")
    if since is None:
        return {"unavailable": "history before AI yield instrumentation is unavailable"}
    retained = max(since, db.meta_get("ai_yield_retained_since") or since)
    if retained >= iso(end_at):
        return {"unavailable": "history before AI yield coverage is unavailable",
                "observed_since": since, "retained_since": retained}
    rows = db.conn.execute("SELECT kind,source_name,category FROM ai_yield_events"
                           " WHERE observed_at>=? AND observed_at<?",
                           (iso(start_at), iso(end_at))).fetchall()
    posts = db.conn.execute("SELECT source_name,site,text_fingerprint,evaluated,miss,gap FROM ai_prefilter_posts"
                            " WHERE first_seen_at>=? AND first_seen_at<? AND policy=?",
                            (iso(start_at), iso(end_at), policy_key(cfg))).fetchall()
    counts = {"unique_new_public_deals": sum(r["kind"] == "new" for r in rows),
              "useful_price_updates": sum(r["kind"] == "update" for r in rows)}
    spend = 0.0
    day = start_at
    while day < end_at:
        spend += db.spend_usd_for_day(day)
        day += timedelta(days=1)
    unevaluated = sum(not r["evaluated"] for r in posts)
    result = metrics(counts, spend, unevaluated)
    result["coverage"] = {"observed_since": since, "from": max(iso(start_at), retained),
                          "through": iso(end_at), "partial": retained > iso(start_at)}
    if result["coverage"]["partial"]:
        result["new_deals_per_usd"] = None
    result["prefilter"] = {"candidates": len(posts), "fully_evaluated": len(posts) - unevaluated,
                           "distinct_fully_evaluated": len({(r["site"], r["text_fingerprint"]) for r in posts if r["evaluated"]}),
                           "publishable_misses": sum(r["miss"] for r in posts),
                           "independent_sites": len({r["site"] for r in posts if r["evaluated"] and r["site"]}),
                           "gaps": dict(Counter(r["gap"] for r in posts if not r["evaluated"]))}
    result["prefilter"]["by_source"] = {
        source: {"candidates": sum(r["source_name"] == source for r in posts),
                 "publishable_misses": sum(r["miss"] for r in posts if r["source_name"] == source),
                 "unevaluated": sum(not r["evaluated"] for r in posts if r["source_name"] == source)}
        for source in sorted({r["source_name"] for r in posts})}
    result["new_by_source"] = dict(Counter(r["source_name"] for r in rows if r["kind"] == "new"))
    result["new_by_category"] = dict(Counter(r["category"] for r in rows if r["kind"] == "new"))
    return result


def readiness(db, cfg, now: datetime) -> dict:
    """Fail closed: seven whole UTC days and a matching offline replay, then review."""
    end = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start_at = end - timedelta(days=cfg.prefilter_complete_days)
    sample = aggregate(db, cfg, start_at, end)
    reasons = []
    if "unavailable" in sample:
        return {"ready_for_review": False, "reasons": [sample["unavailable"]], "complete_utc_days": 0}
    gap = timedelta(minutes=cfg.prefilter_max_gap_minutes)
    rows = db.conn.execute("SELECT * FROM ai_yield_runs WHERE started_at>=? AND started_at<?"
                           " ORDER BY started_at", (iso(start_at - gap), iso(end + gap))).fetchall()
    complete_days = 0
    day = start_at
    while day < end:
        stamps = [max(day, min(day + timedelta(days=1), parse_iso(r["started_at"])))
                  for r in rows if r["finished_at"] and not r["failed"]
                  and r["policy"] == policy_key(cfg) and r["mode"] == "shadow"
                  and day - gap <= parse_iso(r["started_at"]) <= day + timedelta(days=1) + gap]
        bounds = sorted([day, *stamps, day + timedelta(days=1)])
        failed = any(day <= parse_iso(r["started_at"]) < day + timedelta(days=1)
                     and (r["failed"] or not r["finished_at"] or r["policy"] != policy_key(cfg)
                          or r["mode"] != "shadow") for r in rows)
        covered = sample["coverage"]["from"] <= iso(day)
        if covered and not failed and max(b - a for a, b in zip(bounds, bounds[1:])) <= gap:
            complete_days += 1
        day += timedelta(days=1)
    pf = sample["prefilter"]
    if cfg.prefilter_below_floor != "shadow":
        reasons.append("shadow mode required")
    if complete_days < cfg.prefilter_complete_days:
        reasons.append(f"need {cfg.prefilter_complete_days} complete UTC days")
    if pf["distinct_fully_evaluated"] < cfg.prefilter_min_candidates:
        reasons.append(f"insufficient evidence: need {cfg.prefilter_min_candidates} fully evaluated candidates")
    if pf["independent_sites"] < cfg.prefilter_min_source_sites:
        reasons.append(f"need {cfg.prefilter_min_source_sites} independent source sites")
    if pf["publishable_misses"] or db.meta_get("ai_yield_missed_policy") == policy_key(cfg):
        reasons.append("publishable miss: keep shadow and narrow the rule")
    if sample["prefilter_unevaluated"]:
        reasons.append("incomplete candidate evaluations")
    # This is an explicit offline review attestation, never set by live code.
    # After replay proves source/category supply unchanged, store its policy
    # key in ai_yield_replay_policy. A changed rule invalidates that evidence.
    replay = db.meta_get("ai_yield_replay_policy") == policy_key(cfg)
    if not replay:
        reasons.append("matching offline source/category supply replay required")
    return {"ready_for_review": not reasons, "complete_utc_days": complete_days,
            "fully_evaluated_candidates": pf["distinct_fully_evaluated"],
            "independent_source_sites": pf["independent_sites"],
            "publishable_misses": pf["publishable_misses"],
            "unevaluated": sample["prefilter_unevaluated"], "offline_replay_preserved": replay,
            "reasons": reasons}


def report_data(db, cfg, now: datetime) -> dict:
    # AI costs and audit coverage use UTC, independently of traffic's local day.
    end = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if "unavailable" in (history := aggregate(db, cfg, end - timedelta(days=7), end)) \
            and "observed_since" not in history:
        return history
    return {"yesterday": aggregate(db, cfg, end - timedelta(days=1), end),
            "week": history,
            "observed_since": db.meta_get("ai_yield_since"),
            "readiness": readiness(db, cfg, now)}
