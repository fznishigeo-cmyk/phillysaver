"""Lossless cold post receipts and counted audit/click history (337)."""
from __future__ import annotations

import json
import re
import struct

from .util import iso, parse_iso


def exists(db, table):
    return db.conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() is not None


def schema(db):
    # Lazy creation leaves untouched OFF database bytes unchanged.
    db.conn.execute("CREATE TABLE IF NOT EXISTS posts_seen_cold (hash BLOB PRIMARY KEY,"
                    " id INTEGER NOT NULL, source_name TEXT NOT NULL, first_seen INTEGER NOT NULL,"
                    " details TEXT NOT NULL) WITHOUT ROWID")
    db.conn.execute("CREATE TABLE IF NOT EXISTS source_audit_posts_cold (hash BLOB NOT NULL, source_name TEXT NOT NULL, first_seen INTEGER NOT NULL, parsed_delay INTEGER NOT NULL, extracted INTEGER NOT NULL, evidence INTEGER NOT NULL, duplicate INTEGER NOT NULL, below_floor INTEGER NOT NULL, critic INTEGER NOT NULL, dead INTEGER NOT NULL, other INTEGER NOT NULL, PRIMARY KEY(hash,source_name)) WITHOUT ROWID")
    db.conn.execute("CREATE TABLE IF NOT EXISTS state_audit_counts (kind TEXT NOT NULL,"
                    " stamp TEXT NOT NULL, dimensions TEXT NOT NULL, n INTEGER NOT NULL,"
                    " PRIMARY KEY(kind,stamp,dimensions)) WITHOUT ROWID")
    db.conn.execute("CREATE TABLE IF NOT EXISTS click_daily_counts (deal_key TEXT NOT NULL,"
                    " day INTEGER NOT NULL, count INTEGER NOT NULL, slots BLOB NOT NULL,"
                    " PRIMARY KEY(deal_key,day)) WITHOUT ROWID")
    db.conn.execute("CREATE TABLE IF NOT EXISTS click_daily_coverage (day INTEGER PRIMARY KEY,"
                    " hours INTEGER NOT NULL, capped INTEGER NOT NULL)")
    db.conn.execute("CREATE TABLE IF NOT EXISTS accuracy_retired_runs (run_key TEXT PRIMARY KEY) WITHOUT ROWID")
    db.conn.execute("CREATE TABLE IF NOT EXISTS second_opinion_retired_events (event_key TEXT PRIMARY KEY) WITHOUT ROWID")
    db.conn.execute("CREATE TABLE IF NOT EXISTS accuracy_archive_watermarks (deal_key TEXT NOT NULL, source TEXT NOT NULL, observed_at INTEGER NOT NULL, PRIMARY KEY(deal_key,source)) WITHOUT ROWID")


def posts_relation(db):
    if not exists(db, "posts_seen_cold"):
        return "posts_seen"
    return ("(SELECT id,source_name,content_hash,first_seen_at FROM posts_seen UNION ALL "
            f"SELECT id,source_name,lower(hex(hash)),{cold_seen_sql()} FROM posts_seen_cold)")


def known_sql(db, expression):
    sql = f"EXISTS (SELECT 1 FROM posts_seen WHERE content_hash={expression})"
    if exists(db, "posts_seen_cold"):
        sql += f" OR EXISTS (SELECT 1 FROM posts_seen_cold WHERE hash=post_hash({expression}))"
    return "(" + sql + ")"


def post_hash(value):
    return bytes.fromhex(value) if re.fullmatch(r"[0-9a-f]{64}", value or "") else None


def count(db, kind, stamp, dimensions, n=1):
    db.conn.execute("INSERT INTO state_audit_counts VALUES (?,?,?,?)"
                    " ON CONFLICT(kind,stamp,dimensions) DO UPDATE SET n=n+excluded.n",
                    (kind, stamp, json.dumps(dimensions, separators=(",", ":")), n))


def cold_post(db, row):
    key = post_hash(row["content_hash"])
    at = parse_iso(row["first_seen_at"])
    if key is None or at is None:
        return False
    # IDs, URLs and source clocks survive, including URLs no reader uses today.
    db.conn.execute("INSERT OR IGNORE INTO posts_seen_cold VALUES (?,?,?,?,?)",
                    (key, row["id"], row["source_name"], int(at.timestamp()),
                     json.dumps([row["uid"], row["url"], row["published_at"]], separators=(",", ":"))))
    high = max(int(db.meta_get("post_id_high_water") or 0), row["id"])
    db.conn.execute("INSERT INTO meta VALUES ('post_id_high_water',?) ON CONFLICT(k)"
                    " DO UPDATE SET v=excluded.v", (str(high),))
    return True


def archive_accuracy(db, row, compact):
    from datetime import datetime, timezone
    stamp = (iso(datetime.fromtimestamp(row["checked_at"], timezone.utc)) if compact else row["checked_at"])
    count(db, "accuracy", stamp, [row["bucket"], row["outcome"]])
    if compact:
        key, source, run = db.conn.execute("SELECT k.deal_key,k.source,r.run_key FROM accuracy_sample_keys k,accuracy_sample_runs r WHERE k.id=? AND r.id=?", (row["key_id"],row["run_id"])).fetchone()
        observed = row["observed_at"]
    else:
        from .util import parse_iso
        key, source, observed = row["deal_key"], row["source"], int(parse_iso(row["observed_at"]).timestamp())
        run = row["run_key"]
    db.conn.execute("INSERT OR IGNORE INTO accuracy_retired_runs VALUES (?)", (run,))
    db.conn.execute("INSERT INTO accuracy_archive_watermarks VALUES (?,?,?) ON CONFLICT(deal_key,source) DO UPDATE SET observed_at=MAX(observed_at,excluded.observed_at)", (key, source, observed))


def tasks(db, extra):
    schema(db)
    return {
        "posts_seen_cold": ("first_seen_at", None),
        "source_audit_posts_cold": ("first_seen_at", extra["audit_detail_days"]),
        "deal_clicks": ("hour", extra["click_raw_days"]),
        "deal_click_coverage": ("hour", extra["click_raw_days"]),
        "legitimacy_audits": ("observed_at", extra["audit_detail_days"]),
        "legitimacy_batches": ("observed_at", extra["audit_detail_days"]),
        "second_opinion_yield": ("observed_at", extra["audit_detail_days"]),
        "run_stats_details": ("started_at", extra["audit_detail_days"]),
        "efficiency_details": ("started_at", extra["audit_detail_days"]),
    }


def archive(db, task, row):
    if task == "deal_clicks":
        day, slot = divmod(row["hour"], 24)
        old = db.conn.execute("SELECT slots FROM click_daily_counts WHERE deal_key=? AND day=?", (row["deal_key"], day)).fetchone()
        values = list(struct.unpack("<24I", old[0])) if old else [0] * 24
        values[slot] += row["count"]
        db.conn.execute("INSERT INTO click_daily_counts VALUES (?,?,?,?) ON CONFLICT(deal_key,day)"
                        " DO UPDATE SET count=excluded.count,slots=excluded.slots",
                        (row["deal_key"], day, sum(values), struct.pack("<24I", *values)))
    elif task == "deal_click_coverage":
        bit = 1 << (row["hour"] % 24)
        db.conn.execute("INSERT INTO click_daily_coverage VALUES (?,?,?) ON CONFLICT(day)"
                        " DO UPDATE SET hours=hours|excluded.hours,capped=capped|excluded.capped",
                        (row["hour"] // 24, bit, bit if row["capped"] else 0))
    elif task == "legitimacy_audits":
        count(db, task, row["observed_at"][:10], [row["outcome"], row["reason"], row["mode"]])
        if row["compared_at"] and row["agreement"] is not None:
            count(db, "legitimacy_comparisons", row["compared_at"][:10], [row["reason"], row["agreement"]])
    elif task == "legitimacy_batches":
        for field in ("deals_avoided", "calls_avoided", "estimated_usd_saved"):
            count(db, task + ":" + field, row["observed_at"][:10], [row["mode"], row["model"]], row[field] or 0)
        count(db, task, row["observed_at"][:10], [row["mode"], row["model"]])
        if row["estimated_usd_saved"] is None:
            count(db, task + ":unknown_cost", row["observed_at"][:10], [row["mode"], row["model"]])
    elif task == "second_opinion_yield":
        for field in ("extracted", "evidence"):
            count(db, task, row["observed_at"][:10], [row["source_name"], field], row[field])
        count(db, task, row["observed_at"][:10], [row["source_name"], "successful_posts"], int(row["evidence"] > 0))
        db.conn.execute("INSERT OR IGNORE INTO second_opinion_retired_events VALUES (?)", (row["event_key"],))


def click_rows(db, start, end):
    yield from db.conn.execute("SELECT deal_key,hour,count FROM deal_clicks WHERE hour>=? AND hour<?", (start, end))
    if exists(db, "click_daily_counts"):
        for key, day, packed in db.conn.execute("SELECT deal_key,day,slots FROM click_daily_counts WHERE day>=? AND day<=?", (start // 24, (end - 1) // 24)):
            for slot, n in enumerate(struct.unpack("<24I", packed)):
                hour = day * 24 + slot
                if n and start <= hour < end:
                    yield key, hour, n


def click_coverage(db, start, end):
    values = dict(db.conn.execute("SELECT hour,capped FROM deal_click_coverage WHERE hour>=? AND hour<?", (start, end)))
    if exists(db, "click_daily_coverage"):
        for day, hours, capped in db.conn.execute("SELECT * FROM click_daily_coverage WHERE day>=? AND day<=?", (start // 24, (end - 1) // 24)):
            for slot in range(24):
                hour = day * 24 + slot
                if hours & (1 << slot) and start <= hour < end:
                    values[hour] = int(bool(capped & (1 << slot)))
    return values


AUDIT_FIELDS = ("extracted", "evidence", "duplicate", "below_floor", "critic", "dead", "other")


def audit_relation(db):
    if not exists(db, "source_audit_posts_cold"):
        return "source_audit_posts"
    fields = ",".join(AUDIT_FIELDS)
    return ("(SELECT * FROM source_audit_posts UNION ALL SELECT source_name,lower(hex(hash)),"
            f"{cold_seen_sql()},{cold_seen_sql('first_seen+parsed_delay')},{fields} FROM source_audit_posts_cold)")


def audit_known_sql(db, source, content_hash):
    sql = f"EXISTS (SELECT 1 FROM source_audit_posts WHERE source_name={source} AND content_hash={content_hash})"
    if exists(db, "source_audit_posts_cold"):
        sql += f" OR EXISTS (SELECT 1 FROM source_audit_posts_cold WHERE hash=post_hash({content_hash}) AND source_name={source})"
    return "(" + sql + ")"


def cold_audit(db, row):
    key = post_hash(row["content_hash"])
    seen, parsed = parse_iso(row["first_seen_at"]), parse_iso(row["parsed_at"])
    if key is None or seen is None or parsed is None:
        return False
    db.conn.execute("INSERT OR IGNORE INTO source_audit_posts_cold VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (key,row["source_name"],int(seen.timestamp()),int(parsed.timestamp()-seen.timestamp()),*[row[f] for f in AUDIT_FIELDS]))
    return True


def archive_source(db, row):
    for field in AUDIT_FIELDS:
        count(db, "source_audit_posts", row["parsed_at"][:10], [row["source_name"],field], row[field])
    count(db, "source_audit_posts", row["parsed_at"][:10], [row["source_name"],"parsed"])
    count(db, "source_audit_posts", row["first_seen_at"][:10], [row["source_name"],"seen"])


def cold_seen_sql(column="first_seen"):
    # Every writer uses util.iso: UTC, whole seconds. Decode exactly that form.
    return f"strftime('%Y-%m-%dT%H:%M:%S',{column},'unixepoch')||'+00:00'"
