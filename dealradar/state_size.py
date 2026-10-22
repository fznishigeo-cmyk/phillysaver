"""Read-only, content-free snapshot sizes and the pre-push size guard.

Also executable directly by commit_state.sh, before its first remote access.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

MB = 1_000_000
# Deal breakdown only; the table sizes above it are never cut short by it.
INSPECTION_BUDGET_S = 3.0
STATUSES = ("alive", "dead", "other")
AGES = ("<1d", "1-7d", "7-14d", ">14d")


def deal_sizes(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    """Deal rows/payload bytes by status and age, plus sightings by deal status.

    Exact UTF-8 payload lengths; dead age uses died_at (else last_seen_at),
    other statuses last_seen_at. Sighting bytes are logical stored values
    (8 bytes per integer/real), not pages/indexes. Only the five largest
    payloads are parsed, for their top-level key sizes; never values.
    """
    now = now or datetime.now(timezone.utc)
    cutoffs = [(now.astimezone(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
               for days in (1, 7, 14)]
    status = "CASE WHEN status IN ('alive','dead') THEN status ELSE 'other' END"
    stamp = ("CASE WHEN status='dead' THEN COALESCE(NULLIF(json_extract(payload,'$.died_at'),''),"
             "last_seen_at) ELSE last_seen_at END")
    buckets = {s: {a: {"rows": 0, "payload_bytes": 0} for a in AGES} for s in STATUSES}
    top = []
    for rowid, s, at, size in conn.execute(
            f"SELECT id,{status},{stamp},length(CAST(payload AS BLOB)) FROM deals"):
        at = at or ""
        age = 0 if at > cutoffs[0] else 1 if at > cutoffs[1] else 2 if at >= cutoffs[2] else 3
        bucket = buckets[s][AGES[age]]
        bucket["rows"] += 1
        bucket["payload_bytes"] += size or 0
        item = (size or 0, -rowid)
        if len(top) < 5:
            heapq.heappush(top, item)
        elif item > top[0]:
            heapq.heapreplace(top, item)
    totals = {s: {k: sum(b[k] for b in ages.values()) for k in ("rows", "payload_bytes")}
              for s, ages in buckets.items()}
    largest = []
    for size, negative_id in sorted(top, reverse=True):
        keys = dict(conn.execute(
            "SELECT key,CASE type WHEN 'null' THEN 4 WHEN 'true' THEN 4 WHEN 'false' THEN 5"
            " WHEN 'text' THEN length(CAST(json_quote(value) AS BLOB))"
            " ELSE length(CAST(value AS BLOB)) END"
            " FROM json_each((SELECT payload FROM deals WHERE id=?))", (-negative_id,)))
        largest.append({"payload_bytes": size, "key_json_bytes": keys})
    sightings = {s: {"rows": 0, "value_bytes": 0} for s in STATUSES}
    for s, rows, size in conn.execute(
            "SELECT CASE WHEN d.status IN ('alive','dead') THEN d.status ELSE 'other' END,"
            " COUNT(*),SUM(16+length(CAST(s.source_name AS BLOB))+length(CAST(s.source_url AS BLOB))"
            "+length(CAST(s.content_hash AS BLOB))+length(CAST(s.seen_at AS BLOB))"
            "+CASE WHEN s.final_price IS NULL THEN 0 ELSE 8 END)"
            " FROM sightings s LEFT JOIN deals d ON d.id=s.deal_id GROUP BY 1"):
        sightings[s] = {"rows": rows, "value_bytes": size or 0}
    return {"deals": {"age_clock": "dead:died_at_or_last_seen_at;else:last_seen_at",
                      "by_status": totals, "by_status_age": buckets, "largest_payloads": largest},
            "sightings": {"method": "logical_value_bytes", "by_deal_status": sightings}}


def validate_limits(settings) -> tuple[float | None, float | None]:
    if not isinstance(settings, dict):
        raise ValueError("state must be a mapping")
    limits = []
    for name in ("warn_enc_mb", "max_enc_mb"):
        value = settings.get(name)
        if value is not None and (type(value) not in (int, float)
                                  or not math.isfinite(value) or value <= 0):
            raise ValueError(f"state.{name} must be finite and positive or null")
        limits.append(value)
    warn, maximum = limits
    if warn is not None and maximum is not None and warn >= maximum:
        raise ValueError("state.warn_enc_mb must be below state.max_enc_mb")
    return warn, maximum


def table_sizes(conn: sqlite3.Connection) -> dict:
    sizes = {"method": "page_counts", "page_size": conn.execute("PRAGMA page_size").fetchone()[0],
             "pages": conn.execute("PRAGMA page_count").fetchone()[0],
             "free_pages": conn.execute("PRAGMA freelist_count").fetchone()[0], "tables": []}
    try:
        rows = conn.execute(
            "SELECT COALESCE(m.tbl_name,s.name) AS name, SUM(s.pgsize) AS bytes"
            " FROM dbstat s LEFT JOIN sqlite_schema m ON m.name=s.name"
            " GROUP BY COALESCE(m.tbl_name,s.name) ORDER BY bytes DESC,name LIMIT 40").fetchall()
    except sqlite3.OperationalError:
        return sizes  # dbstat is optional; do not invent per-table estimates.
    sizes.update(method="dbstat", tables=[{"name": name, "bytes": size} for name, size in rows])
    return sizes


def snapshot_sizes(db: Path, packed: Path, encrypted: Path) -> dict:
    sizes = {"db_bytes": db.stat().st_size, "gz_bytes": packed.stat().st_size,
             "enc_bytes": encrypted.stat().st_size}
    try:
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            sizes.update(table_sizes(conn))
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
            if {"deals", "sightings"} <= names:
                # Bounded: commit_state runs this every run; a slow scan reports
                # that it stopped instead of delaying the push.
                stop = time.monotonic() + INSPECTION_BUDGET_S
                conn.set_progress_handler(lambda: int(time.monotonic() >= stop), 1000)
                try:
                    sizes.update(deal_sizes(conn))
                    sizes["deal_breakdown"] = "complete"
                except sqlite3.DatabaseError:
                    sizes["deal_breakdown"] = "budget_or_unavailable"
                finally:
                    conn.set_progress_handler(None, 0)
    except sqlite3.DatabaseError:
        # Diagnostics must not expose SQLite errors or row contents. The byte
        # guard still applies even if a legacy snapshot cannot be inspected.
        sizes.update(method="unavailable", tables=[])
    return sizes


def check_size(enc_bytes: int, warn: float | None, maximum: float | None) -> int:
    if maximum is not None and enc_bytes >= maximum * MB:
        print(f"[commit_state] ERROR enc_bytes={enc_bytes} at/above state.max_enc_mb={maximum}; refusing push")
        return 1
    if warn is not None and enc_bytes > warn * MB:
        print(f"[commit_state] WARNING enc_bytes={enc_bytes} above state.warn_enc_mb={warn}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", type=Path)
    parser.add_argument("packed", type=Path)
    parser.add_argument("encrypted", type=Path)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    try:
        raw = yaml.safe_load(args.config.read_text()) if args.config.exists() else {}
        if raw is None:
            raw = {}  # Match load_config's default-only configuration.
        if not isinstance(raw, dict):
            raise ValueError("config must be a mapping")
        warn, maximum = validate_limits(raw.get("state", {}))
        sizes = snapshot_sizes(args.db, args.packed, args.encrypted)
    except (OSError, ValueError, yaml.YAMLError):
        print("[commit_state] ERROR invalid size configuration or unreadable snapshot; refusing push")
        return 1
    print("[commit_state] size " + json.dumps(sizes, separators=(",", ":"), sort_keys=True))
    return check_size(sizes["enc_bytes"], warn, maximum)


if __name__ == "__main__":
    raise SystemExit(main())
