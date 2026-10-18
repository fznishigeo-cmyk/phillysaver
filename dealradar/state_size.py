"""Read-only, content-free snapshot sizes and the pre-push size guard.

Also executable directly by commit_state.sh, before its first remote access.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path

import yaml

MB = 1_000_000


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
            " GROUP BY COALESCE(m.tbl_name,s.name) ORDER BY bytes DESC,name LIMIT 8").fetchall()
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
