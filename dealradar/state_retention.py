"""Cooperative retention for the largest state tables; no network or paid work."""

from __future__ import annotations

import json
import math
import time
from datetime import timedelta

from .util import iso, parse_iso

CHUNK_ROWS = 64
HEADROOM_S = .01


def validate_limits(rows, seconds):
    if type(rows) is not int or not 1 <= rows <= 16384:
        raise ValueError("retention.state_maintenance_max_rows must be an integer in 1..16384")
    if (type(seconds) not in (float, int) or not .05 <= seconds <= 5
            or not math.isfinite(seconds)):
        raise ValueError("retention.state_maintenance_budget_s must be finite and in 0.05..5")


def options(cfg):
    """Omitted knobs preserve bulk legacy expiry; reader windows are floors."""
    if not (cfg.state_retention_337 or cfg.compact_deal_payloads or cfg.compact_accuracy_samples or cfg.compact_click_exposure or any(v is not None for v in (
            cfg.accuracy_samples_days, cfg.deal_click_shown_days, cfg.keepa_recheck_history_samples,
            cfg.source_audit_posts_days))):
        return None
    from .clicks import settings

    result = {
        "accuracy_days": 35 if cfg.accuracy_samples_days is None else max(9, cfg.accuracy_samples_days),
        "click_hours": (None if cfg.deal_click_shown_days is None else
                        max(168, cfg.deal_click_shown_days * 24, settings(cfg)["lookback_hours"])),
        "click_legacy_hours": settings(cfg)["retention_days"] * 24,
        "history_samples": (None if cfg.keepa_recheck_history_samples is None else
                            max(2, cfg.keepa_recheck_history_samples,
                                int(cfg.keepa_recheck_adaptive.get("history_samples", 12)))),
        "compact_deals": cfg.compact_deal_payloads,
        "compact_accuracy": cfg.compact_accuracy_samples,
        "compact_clicks": cfg.compact_click_exposure,
        "audit_days": None if cfg.source_audit_posts_days is None else max(9, cfg.source_audit_posts_days),
        "max_rows": cfg.state_maintenance_max_rows,
        "budget_s": cfg.state_maintenance_budget_s,
    }
    if cfg.state_retention_337:
        result["retention_337"] = {name: getattr(cfg, name) for name in (
            "api_cache_days", "post_receipt_details_days", "terminal_indexnow_versions_days",
            "audit_detail_days", "audit_hot_days", "click_raw_days", "accuracy_detail_days", "accuracy_last_per_deal")}
        result["accuracy_days"] = cfg.accuracy_detail_days
        result["audit_days"] = max(9, cfg.audit_detail_days)
    return result


class _Deferred(Exception):
    pass


def maintain(db, now, *, run_key, options, posts_seen_days, deals_days, deadline=None):
    """Round-robin 64-row transactions, including durable cursor/run allowance.

    Expiry-index scans touch only expired samples/clicks/posts. Inventory and
    history use bounded rowid sweeps with a frozen high water, so fresh rows
    cannot starve old ones. Each table can finish at most one sweep per run.
    This is cooperative, not a hard SQLite wall-clock timeout.
    """
    validate_limits(options["max_rows"], options["budget_s"])
    if json.loads(db.conn.execute("SELECT state_maintenance FROM run_stats WHERE run_key=?", (run_key,)).fetchone()[0]).get("retired"):
        return {"tables": {}, "deferred": False, "elapsed_s": 0.0}
    start = time.monotonic()
    stop = min(start + options["budget_s"], deadline if deadline is not None else math.inf)
    accuracy_cutoff = iso(now - timedelta(days=options["accuracy_days"]))
    posts_cutoff = iso(now - timedelta(days=posts_seen_days))
    audit_cutoff = iso(now - timedelta(days=options["audit_days"] or posts_seen_days))
    deals_cutoff = iso(now - timedelta(days=deals_days))
    tables = ["accuracy_samples", "deals", "keepa_recheck_history", "posts_seen", "source_audit_posts"]
    if options["click_hours"] is not None or options["compact_clicks"]:
        tables.insert(1, "deal_click_shown")
    from . import accuracy, clicks, state_growers as growers
    if options["compact_accuracy"] or accuracy.compact_exists(db):
        tables += ["accuracy_samples_compact", "accuracy_sample_keys", "accuracy_sample_runs"]
    if options["compact_clicks"] or clicks.compact_exists(db):
        tables.append("deal_click_days")
    click_hours = options["click_hours"] or options["click_legacy_hours"]
    click_cutoff = int(now.timestamp() // 3600) - click_hours
    sweeps = {"deals", "keepa_recheck_history", "source_audit_posts", "accuracy_sample_keys", "accuracy_sample_runs"}
    extra = options.get("retention_337")
    sweep_tables = {}
    if extra is not None:
        sweep_tables = {"http_cache_api": "http_cache", "posts_seen_details": "posts_seen",
                        "indexnow_batches": "indexnow_batches", "source_audit_posts_details": "source_audit_posts"}
        with db.conn:
            grow_tasks = growers.tasks(db, extra)
            db.conn.execute("INSERT OR IGNORE INTO meta VALUES ('posts_cold_enabled','1')")
        sweep_tables.update({"run_stats_details": "run_stats", "efficiency_details": "efficiency_runs"})
        tables += list(grow_tasks)
        sweeps.update(grow_tasks)
        live_post_hashes = {r[0] for r in db.conn.execute("SELECT s.content_hash FROM sightings s JOIN deals d ON d.id=s.deal_id WHERE d.status!='dead'")}
        tables += [t for t in sweep_tables if t not in tables]
        sweeps.update(sweep_tables)
        api_cutoff = iso(now - timedelta(days=extra["api_cache_days"]))
        details_cutoff = iso(now - timedelta(days=extra["post_receipt_details_days"]))
        audit_hot_cutoff = iso(now - timedelta(days=extra["audit_hot_days"]))
        versions_cutoff = iso(now - timedelta(days=extra["terminal_indexnow_versions_days"]))
    if options["compact_accuracy"] or extra:
        sweeps.add("accuracy_samples")
    if extra and "accuracy_samples_compact" in tables:
        sweeps.add("accuracy_samples_compact")
    if options["compact_clicks"]:
        sweeps.add("deal_click_shown")
    next_table = db.meta_get("state_maintenance_next_table")
    if next_table in tables:
        index = tables.index(next_table)
        tables = tables[index:] + tables[:index]
    db.state_retention_337 = extra is not None
    db.compact_deal_payloads = options["compact_deals"]
    db.source_audit_posts_days = options["audit_days"]

    def check_time():
        if time.monotonic() + HEADROOM_S > stop:
            raise _Deferred

    def run_state():
        return json.loads(db.conn.execute(
            "SELECT state_maintenance FROM run_stats WHERE run_key=?", (run_key,)).fetchone()[0])

    try:
        while True:
            advanced = False
            for table in tables:
                source_table = sweep_tables.get(table, table)
                check_time()
                with db.conn:
                    db.conn.execute("BEGIN IMMEDIATE")
                    state = run_state()
                    counts = state.setdefault(table, dict(examined=0, deleted=0, updated=0, completed=False))
                    limit = min(CHUNK_ROWS, options["max_rows"] - counts["examined"])
                    if counts["completed"] or limit <= 0:
                        continue
                    check_time()
                    if table == "accuracy_samples_compact":
                        accuracy._compact_schema(db)
                    elif table == "deal_click_days":
                        clicks.compact_schema(db)
                    sweep_key = "state_sweep:" + table
                    cursor = None
                    if table in sweeps:
                        cursor = json.loads(db.meta_get(sweep_key) or "null")
                        if "sweep_run" in counts and (cursor is None or
                                cursor.get("run") != counts["sweep_run"]):
                            # Another lane finished our sweep. Do not join its
                            # successor or overwrite that successor's cursor.
                            cursor, rows = None, []
                        else:
                            if cursor is None:
                                if table == "source_audit_posts_cold":
                                    high_row = db.conn.execute("SELECT hash,source_name FROM source_audit_posts_cold ORDER BY hash DESC,source_name DESC LIMIT 1").fetchone()
                                    cursor = {"after": "", "high": high_row[0].hex()+":"+high_row[1] if high_row else ""}
                                elif table == "posts_seen_cold":
                                    high_row = db.conn.execute("SELECT hash FROM posts_seen_cold ORDER BY hash DESC LIMIT 1").fetchone()
                                    cursor = {"after": "", "high": high_row[0].hex() if high_row else ""}
                                else:
                                    high = db.conn.execute(f"SELECT MAX(rowid) FROM {source_table}").fetchone()[0] or 0
                                    cursor = {"after": 0, "high": high}
                            cursor.setdefault("run", run_key)
                            counts["sweep_run"] = cursor["run"]
                            if table == "source_audit_posts_cold":
                                def bound(value):
                                    key, _, source = value.partition(":")
                                    return bytes.fromhex(key), source
                                rows = db.conn.execute(f"SELECT lower(hex(hash))||':'||source_name AS sweep_id,lower(hex(hash)) AS content_hash,{growers.cold_seen_sql()} AS first_seen_at,{growers.cold_seen_sql('first_seen+parsed_delay')} AS parsed_at,* FROM source_audit_posts_cold WHERE (hash,source_name)>(?,?) AND (hash,source_name)<=(?,?) ORDER BY hash,source_name LIMIT ?", (*bound(cursor["after"]),*bound(cursor["high"]),limit)).fetchall()
                            elif table == "posts_seen_cold":
                                rows = db.conn.execute(f"SELECT lower(hex(hash)) AS sweep_id,{growers.cold_seen_sql()} AS first_seen_at,* FROM posts_seen_cold WHERE hash>? AND hash<=? ORDER BY hash LIMIT ?", (bytes.fromhex(cursor["after"]), bytes.fromhex(cursor["high"]), limit)).fetchall()
                            else:
                                rows = db.conn.execute(
                                    f"SELECT rowid AS sweep_id,* FROM {source_table} WHERE rowid>? AND rowid<=?"
                                    " ORDER BY rowid LIMIT ?", (cursor["after"], cursor["high"], limit)).fetchall()
                    else:
                        column, cutoff = {
                            "accuracy_samples": ("checked_at", accuracy_cutoff),
                            "accuracy_samples_compact": ("checked_at", int(parse_iso(accuracy_cutoff).timestamp())),
                            "deal_click_shown": ("hour", click_cutoff),
                            "deal_click_days": ("day", click_cutoff // 24),
                            "posts_seen": ("first_seen_at", posts_cutoff),
                            "posts_seen_cold": ("first_seen_at", posts_cutoff),
                        }[table]
                        rows = db.conn.execute(
                            f"SELECT {'hash' if table == 'posts_seen_cold' else 'rowid'} AS sweep_id,* FROM {table} WHERE {column}<?"
                            f" ORDER BY {column} LIMIT ?", (cutoff, limit)).fetchall()
                    check_time()
                    for row in rows:
                        delete, replacement = False, None
                        if table == "http_cache_api":
                            delete = (row["url"].startswith("api:") and row["fetched_at"] is not None
                                      and row["fetched_at"] < api_cutoff)
                        elif table == "posts_seen_details":
                            if (row["first_seen_at"] < details_cutoff and row["content_hash"] not in live_post_hashes):
                                if growers.cold_post(db, row):
                                    delete = True
                                    counts["updated"] += 1
                        elif table == "posts_seen_cold":
                            delete = row["first_seen_at"] < posts_cutoff
                        elif table == "source_audit_posts_details":
                            if row["first_seen_at"] < audit_hot_cutoff and row["parsed_at"] and row["parsed_at"] < audit_hot_cutoff:
                                delete = growers.cold_audit(db,row)
                                counts["updated"] += int(delete)
                        elif table == "source_audit_posts_cold":
                            delete = row["first_seen_at"] < audit_cutoff and row["parsed_at"] < audit_cutoff
                            if delete:
                                growers.archive_source(db,row)
                        elif extra and table in grow_tasks:
                            stamp, days = grow_tasks[table]
                            cutoff = (int(now.timestamp() // 86400) - max(days, 8)) * 24 if stamp == "hour" else iso(now - timedelta(days=days))
                            if row[stamp] < cutoff and not (table == "legitimacy_audits" and row["compared_at"] and row["compared_at"] >= cutoff):
                                if table in ("run_stats_details", "efficiency_details"):
                                    if table == "run_stats_details" and row["finished_at"] and row["state_maintenance"] != '{"retired":true}':
                                        growers.count(db, "run_stats", row[stamp][:10], [row["kind"], "runs"])
                                        for field in ("items_fetched", "posts_new", "deals_parsed", "published"):
                                            growers.count(db, "run_stats", row[stamp][:10], [row["kind"], field], row[field])
                                        db.conn.execute("UPDATE run_stats SET state_maintenance='{\"retired\":true}' WHERE run_key=?", (row["run_key"],))
                                        counts["updated"] += 1
                                    elif table == "efficiency_details" and not row["pending"] and row["metrics"]:
                                        metrics = json.loads(row["metrics"])
                                        from .efficiency import COUNTERS
                                        for field in COUNTERS:
                                            if type(metrics.get(field)) in (float, int):
                                                growers.count(db, "efficiency", row[stamp][:10], [field], metrics[field])
                                        db.conn.execute("UPDATE efficiency_runs SET metrics=NULL WHERE run_key=?", (row["run_key"],))
                                        counts["updated"] += 1
                                elif table in ("legitimacy_audits", "legitimacy_batches"):
                                    marker = "mode" if table == "legitimacy_audits" else "model"
                                    if row[marker]:
                                        growers.archive(db, table, row)
                                        if table == "legitimacy_audits":
                                            db.conn.execute("UPDATE legitimacy_audits SET outcome='',reason='',mode='',agreement=-1,compared_at=NULL WHERE event_key=?", (row["event_key"],))
                                        else:
                                            db.conn.execute("UPDATE legitimacy_batches SET mode='',model='',deals_avoided=0,calls_avoided=0,estimated_usd_saved=NULL WHERE batch_key=?", (row["batch_key"],))
                                        counts["updated"] += 1
                                else:
                                    growers.archive(db, table, row)
                                    delete = True
                        elif table == "accuracy_samples_compact" and extra:
                            newer = db.conn.execute("SELECT observed_at FROM accuracy_samples_compact WHERE key_id=? ORDER BY observed_at DESC LIMIT 1 OFFSET ?", (row["key_id"], extra["accuracy_last_per_deal"] - 1)).fetchone()
                            delete = row["checked_at"] < int(parse_iso(accuracy_cutoff).timestamp()) or bool(newer and row["observed_at"] < newer[0])
                            if delete:
                                growers.archive_accuracy(db, row, True)
                        elif table == "indexnow_batches":
                            if (row["status"] in ("received", "failed") and
                                    row["started_at"] < versions_cutoff and row["versions"] != "{}"):
                                db.conn.execute("UPDATE indexnow_batches SET versions='{}' WHERE rowid=?",
                                                (row["sweep_id"],))
                                counts["updated"] += 1
                        elif table == "accuracy_samples" and extra and not options["compact_accuracy"]:
                            newer = db.conn.execute("SELECT observed_at FROM accuracy_samples WHERE deal_key=? AND source=? ORDER BY observed_at DESC LIMIT 1 OFFSET ?", (row["deal_key"], row["source"], extra["accuracy_last_per_deal"] - 1)).fetchone()
                            delete = row["checked_at"] < accuracy_cutoff or bool(newer and row["observed_at"] < newer[0])
                            if delete:
                                growers.archive_accuracy(db, row, False)
                        elif table == "accuracy_samples" and options["compact_accuracy"]:
                            if row["checked_at"] >= accuracy_cutoff:
                                accuracy.store_compact(db, row["deal_key"], row["run_key"],
                                    parse_iso(row["checked_at"]), parse_iso(row["observed_at"]),
                                    row["bucket"], row["source"], row["outcome"])
                                counts["updated"] += 1
                            elif extra:
                                growers.archive_accuracy(db, row, False)
                            delete = True
                        elif table == "deal_click_shown" and options["compact_clicks"]:
                            if row["hour"] >= click_cutoff:
                                clicks.compact_schema(db)
                                clicks.add_exposure(db, row["deal_key"], row["hour"], row["seconds"])
                                counts["updated"] += 1
                            delete = True
                        elif table in ("accuracy_sample_keys", "accuracy_sample_runs"):
                            column = "key_id" if table == "accuracy_sample_keys" else "run_id"
                            delete = not (extra and table == "accuracy_sample_runs") and not db.conn.execute(
                                f"SELECT 1 FROM accuracy_samples_compact WHERE {column}=? LIMIT 1",
                                (row["id"],)).fetchone()
                        elif table == "deals":
                            payload = json.loads(row["payload"])
                            delete = (row["status"] == "dead" and
                                      (payload.get("died_at") or row["last_seen_at"]) < deals_cutoff)
                            if not delete and options["compact_deals"]:
                                replacement = db._deal_payload(payload, row["id"])
                                if replacement != row["payload"]:
                                    db.conn.execute("UPDATE deals SET payload=? WHERE id=?",
                                                    (replacement, row["id"]))
                                    counts["updated"] += 1
                        elif table == "keepa_recheck_history":
                            delete = not db.conn.execute("SELECT 1 FROM deals WHERE dedupe_key=?",
                                                         (row["deal_key"],)).fetchone()
                            if not delete and options["history_samples"] is not None:
                                samples = json.loads(row["samples"])
                                if len(samples) > options["history_samples"]:
                                    replacement = json.dumps(samples[-options["history_samples"]:], separators=(",", ":"))
                                    db.conn.execute("UPDATE keepa_recheck_history SET samples=? WHERE rowid=?",
                                                    (replacement, row["sweep_id"]))
                                    counts["updated"] += 1
                        elif table == "source_audit_posts":
                            # Deferred discoveries and recently completed parses
                            # still feed starvation/report readers, whatever their discovery age.
                            delete = (row["first_seen_at"] < audit_cutoff and
                                      (options["audit_days"] is None or
                                       (row["parsed_at"] is not None and row["parsed_at"] < audit_cutoff)))
                            if delete and extra:
                                growers.archive_source(db,row)
                        else:
                            delete = True
                        if delete:
                            if table == "source_audit_posts_cold":
                                db.conn.execute("DELETE FROM source_audit_posts_cold WHERE hash=? AND source_name=?", (row["hash"],row["source_name"]))
                            else:
                                db.conn.execute(f"DELETE FROM {source_table} WHERE {'hash' if table == 'posts_seen_cold' else 'rowid'}=?", (bytes.fromhex(row["sweep_id"]) if table == "posts_seen_cold" else row["sweep_id"],))
                            counts["deleted"] += 1
                        check_time()
                    counts["examined"] += len(rows)
                    counts["completed"] = len(rows) < limit or bool(
                        cursor and rows and rows[-1]["sweep_id"] == cursor["high"])
                    if cursor is not None:
                        cursor = None if counts["completed"] else dict(cursor, after=rows[-1]["sweep_id"])
                        db.conn.execute("INSERT INTO meta(k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                                        (sweep_key, json.dumps(cursor, separators=(",", ":"))))
                    db.conn.execute("UPDATE run_stats SET state_maintenance=? WHERE run_key=?",
                                    (json.dumps(state, separators=(",", ":")), run_key))
                    db.conn.execute("INSERT INTO meta(k,v) VALUES ('state_maintenance_next_table',?)"
                                    " ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                                    (tables[(tables.index(table) + 1) % len(tables)],))
                    check_time()
                    advanced = True
            if not advanced:
                break
    except _Deferred:
        pass  # the unfinished chunk, cursor and allowance all roll back
    state = run_state()
    return {"tables": state, "deferred": any(not state.get(t, {}).get("completed") for t in tables),
            "elapsed_s": round(time.monotonic() - start, 6)}
