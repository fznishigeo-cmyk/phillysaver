"""Cooperative retention for the largest state tables; no network or paid work."""

from __future__ import annotations

import json
import math
import time
from datetime import timedelta

from .util import iso

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
    if not (cfg.compact_deal_payloads or any(v is not None for v in (
            cfg.accuracy_samples_days, cfg.deal_click_shown_days, cfg.keepa_recheck_history_samples,
            cfg.source_audit_posts_days))):
        return None
    from .clicks import settings

    return {
        "accuracy_days": 35 if cfg.accuracy_samples_days is None else max(9, cfg.accuracy_samples_days),
        "click_hours": (None if cfg.deal_click_shown_days is None else
                        max(168, cfg.deal_click_shown_days * 24, settings(cfg)["lookback_hours"])),
        "history_samples": (None if cfg.keepa_recheck_history_samples is None else
                            max(2, cfg.keepa_recheck_history_samples,
                                int(cfg.keepa_recheck_adaptive.get("history_samples", 12)))),
        "compact_deals": cfg.compact_deal_payloads,
        "audit_days": None if cfg.source_audit_posts_days is None else max(9, cfg.source_audit_posts_days),
        "max_rows": cfg.state_maintenance_max_rows,
        "budget_s": cfg.state_maintenance_budget_s,
    }


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
    start = time.monotonic()
    stop = min(start + options["budget_s"], deadline if deadline is not None else math.inf)
    accuracy_cutoff = iso(now - timedelta(days=options["accuracy_days"]))
    posts_cutoff = iso(now - timedelta(days=posts_seen_days))
    audit_cutoff = iso(now - timedelta(days=options["audit_days"] or posts_seen_days))
    deals_cutoff = iso(now - timedelta(days=deals_days))
    tables = ["accuracy_samples", "deals", "keepa_recheck_history", "posts_seen", "source_audit_posts"]
    if options["click_hours"] is not None:
        tables.insert(1, "deal_click_shown")
    next_table = db.meta_get("state_maintenance_next_table")
    if next_table in tables:
        index = tables.index(next_table)
        tables = tables[index:] + tables[:index]
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
                check_time()
                with db.conn:
                    db.conn.execute("BEGIN IMMEDIATE")
                    state = run_state()
                    counts = state.setdefault(table, dict(examined=0, deleted=0, updated=0, completed=False))
                    limit = min(CHUNK_ROWS, options["max_rows"] - counts["examined"])
                    if counts["completed"] or limit <= 0:
                        continue
                    check_time()
                    sweep_key = "state_sweep:" + table
                    cursor = None
                    if table in ("deals", "keepa_recheck_history", "source_audit_posts"):
                        cursor = json.loads(db.meta_get(sweep_key) or "null")
                        if "sweep_run" in counts and (cursor is None or
                                cursor.get("run") != counts["sweep_run"]):
                            # Another lane finished our sweep. Do not join its
                            # successor or overwrite that successor's cursor.
                            cursor, rows = None, []
                        else:
                            if cursor is None:
                                high = db.conn.execute(f"SELECT MAX(rowid) FROM {table}").fetchone()[0] or 0
                                cursor = {"after": 0, "high": high}
                            cursor.setdefault("run", run_key)
                            counts["sweep_run"] = cursor["run"]
                            rows = db.conn.execute(
                                f"SELECT rowid AS sweep_id,* FROM {table} WHERE rowid>? AND rowid<=?"
                                " ORDER BY rowid LIMIT ?", (cursor["after"], cursor["high"], limit)).fetchall()
                    else:
                        column, cutoff = {
                            "accuracy_samples": ("checked_at", accuracy_cutoff),
                            "deal_click_shown": ("hour", int(now.timestamp() // 3600) - (options["click_hours"] or 0)),
                            "posts_seen": ("first_seen_at", posts_cutoff),
                        }[table]
                        rows = db.conn.execute(
                            f"SELECT rowid AS sweep_id,* FROM {table} WHERE {column}<?"
                            f" ORDER BY {column} LIMIT ?", (cutoff, limit)).fetchall()
                    check_time()
                    for row in rows:
                        delete, replacement = False, None
                        if table == "deals":
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
                        else:
                            delete = True
                        if delete:
                            db.conn.execute(f"DELETE FROM {table} WHERE rowid=?", (row["sweep_id"],))
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
