"""Offline, read-only state inventory and snapshot deltas; never print row contents.

python -m scripts.state_growth state.db [--before previous.db --hours 1]
Bytes include table indexes with dbstat; fallback bytes are logical column
lengths only (no index/page overhead). Compare snapshots using the same method.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def inventory(conn):
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    try:
        sizes = dict(conn.execute(
            "SELECT m.tbl_name,SUM(s.pgsize) FROM dbstat s"
            " JOIN sqlite_schema m ON m.name=s.name GROUP BY m.tbl_name"))
        method = "dbstat"
    except sqlite3.OperationalError:
        sizes, method = {}, "length_sums"
    result = []
    for table in tables:
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({quoted(table)})")]
        # Casting to BLOB measures UTF-8 bytes, not Unicode characters. Scalar
        # numerics use their textual length in the explicitly logical fallback.
        expr = [f"COALESCE(SUM(length(CAST({quoted(c)} AS BLOB))),0)" for c in columns]
        row = conn.execute(f"SELECT COUNT(*),{','.join(expr)} FROM {quoted(table)}").fetchone()
        lengths = dict(zip(columns, row[1:]))
        result.append(dict(name=table, rows=row[0], bytes=(sizes.get(table, 0) if method == "dbstat"
                                                         else sum(lengths.values())),
                           column_bytes=lengths))
    return dict(method=method, page_bytes=conn.execute("PRAGMA page_size").fetchone()[0] *
                conn.execute("PRAGMA page_count").fetchone()[0],
                free_bytes=conn.execute("PRAGMA page_size").fetchone()[0] *
                conn.execute("PRAGMA freelist_count").fetchone()[0],
                tables=sorted(result, key=lambda t: (-t["bytes"], t["name"])))


def snapshot(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        # Keep dbstat, row counts and column lengths on one consistent snapshot.
        conn.execute("BEGIN")
        return inventory(conn)


def growers(before, after, hours):
    if type(hours) not in (int, float) or not math.isfinite(hours) or hours <= 0:
        raise ValueError("hours must be finite and positive")
    if before["method"] != after["method"]:
        raise ValueError("cannot compare dbstat bytes with logical length sums")
    old, new = ({r["name"]: r for r in s["tables"]} for s in (before, after))
    result = []
    for name in old.keys() | new.keys():
        a, b = old.get(name, {}), new.get(name, {})
        delta = b.get("bytes", 0) - a.get("bytes", 0)
        columns = a.get("column_bytes", {}).keys() | b.get("column_bytes", {}).keys()
        result.append(dict(name=name, rows=b.get("rows", 0) - a.get("rows", 0), bytes=delta,
                           mb_per_hour=delta / hours / 1_000_000,
                           column_bytes={c: b.get("column_bytes", {}).get(c, 0) -
                                         a.get("column_bytes", {}).get(c, 0) for c in sorted(columns)}))
    return sorted(result, key=lambda t: (-t["bytes"], t["name"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", type=Path)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--hours", type=float, default=1)
    args = parser.parse_args()
    report = snapshot(args.db)
    if args.before:
        report["growers"] = growers(snapshot(args.before), report, args.hours)
        report["hours"] = args.hours
    print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
