"""SQLite state: posts_seen, deals, sightings, price_history, alerts_sent,
source_health, http_cache, run_stats.

All datetimes are stored as ISO-8601 UTC strings. Idempotency anchors:
- posts_seen.content_hash is UNIQUE -> a post body is never parsed twice.
- alerts_sent (channel, dedup_token) is UNIQUE -> an alert is never sent twice.
- price_history (canonical_key, content_hash) is UNIQUE -> re-runs add no rows.
- deals.dedupe_key is UNIQUE -> merges, never duplicates.

Full post text is NEVER stored here — only extracted facts, links, and credit.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .models import RawPost
from .util import iso, now_utc, parse_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts_seen (
    id INTEGER PRIMARY KEY,
    source_name TEXT NOT NULL,
    uid TEXT NOT NULL,
    url TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE,
    published_at TEXT,
    first_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_first_seen ON posts_seen(first_seen_at);

CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    store TEXT NOT NULL,
    item TEXT NOT NULL,
    normalized_item TEXT NOT NULL,
    canonical_url TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL,
    final_price REAL,
    pct_at_checkout REAL,
    abs_savings REAL,
    online_only TEXT NOT NULL DEFAULT 'unknown',
    verify_tier TEXT NOT NULL DEFAULT 'unverified',
    status TEXT NOT NULL DEFAULT 'alive',
    expires_at TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deals_store ON deals(store);
CREATE INDEX IF NOT EXISTS idx_deals_status ON deals(status);

CREATE TABLE IF NOT EXISTS sightings (
    id INTEGER PRIMARY KEY,
    deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    final_price REAL,
    UNIQUE(deal_id, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_sightings_deal ON sightings(deal_id);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY,
    canonical_key TEXT NOT NULL,
    store TEXT NOT NULL,
    price REAL NOT NULL,
    observed_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    UNIQUE(canonical_key, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_ph_key ON price_history(canonical_key);

CREATE TABLE IF NOT EXISTS alerts_sent (
    id INTEGER PRIMARY KEY,
    deal_key TEXT NOT NULL,
    channel TEXT NOT NULL,
    dedup_token TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    price_at_send REAL,
    tier_at_send TEXT,
    UNIQUE(channel, dedup_token)
);
CREATE INDEX IF NOT EXISTS idx_alerts_deal ON alerts_sent(deal_key, channel);

CREATE TABLE IF NOT EXISTS source_health (
    source_name TEXT PRIMARY KEY,
    last_success_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    tripped_until TEXT,
    items_last_run INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS http_cache (
    url TEXT PRIMARY KEY,
    etag TEXT NOT NULL DEFAULT '',
    last_modified TEXT NOT NULL DEFAULT '',
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS run_stats (
    run_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'hourly',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    items_fetched INTEGER NOT NULL DEFAULT 0,
    posts_new INTEGER NOT NULL DEFAULT 0,
    deals_parsed INTEGER NOT NULL DEFAULT 0,
    published INTEGER NOT NULL DEFAULT 0,
    spend_usd REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


class DB:
    def __init__(self, path: str | Path = "state.db") -> None:
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    # ---------------- posts_seen ----------------

    def post_is_known(self, content_hash: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM posts_seen WHERE content_hash=?", (content_hash,)
        ).fetchone()
        return row is not None

    def record_post(self, post: RawPost, now: datetime) -> bool:
        """Insert if unseen. Returns True when the post is new."""
        try:
            self.conn.execute(
                "INSERT INTO posts_seen(source_name, uid, url, content_hash, published_at, first_seen_at)"
                " VALUES (?,?,?,?,?,?)",
                (post.source_name, post.uid, post.url, post.content_hash,
                 iso(post.published_at), iso(now)),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def post_first_seen(self, content_hash: str) -> datetime | None:
        row = self.conn.execute(
            "SELECT first_seen_at FROM posts_seen WHERE content_hash=?", (content_hash,)
        ).fetchone()
        return parse_iso(row["first_seen_at"]) if row else None

    # ---------------- deals ----------------

    def get_deal(self, dedupe_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM deals WHERE dedupe_key=?", (dedupe_key,)
        ).fetchone()
        return self._deal_row_to_dict(row) if row else None

    def _deal_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = json.loads(row["payload"])
        d.update({
            "id": row["id"],
            "dedupe_key": row["dedupe_key"],
            "store": row["store"],
            "item": row["item"],
            "normalized_item": row["normalized_item"],
            "canonical_url": row["canonical_url"],
            "final_price": row["final_price"],
            "pct_at_checkout": row["pct_at_checkout"],
            "abs_savings": row["abs_savings"],
            "online_only": row["online_only"],
            "verify_tier": row["verify_tier"],
            "status": row["status"],
            "expires_at": row["expires_at"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
        })
        return d

    def insert_deal(self, *, dedupe_key: str, store: str, item: str,
                    normalized_item: str, canonical_url: str, payload: dict[str, Any],
                    final_price: float | None, pct_at_checkout: float | None,
                    abs_savings: float | None, online_only: str, expires_at: str,
                    now: datetime) -> int:
        cur = self.conn.execute(
            "INSERT INTO deals(dedupe_key, store, item, normalized_item, canonical_url,"
            " payload, final_price, pct_at_checkout, abs_savings, online_only,"
            " expires_at, first_seen_at, last_seen_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (dedupe_key, store, item, normalized_item, canonical_url,
             json.dumps(payload), final_price, pct_at_checkout, abs_savings,
             online_only, expires_at, iso(now), iso(now)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_deal(self, dedupe_key: str, *, payload: dict[str, Any] | None = None,
                    final_price: float | None = None,
                    pct_at_checkout: float | None = None,
                    abs_savings: float | None = None,
                    online_only: str | None = None,
                    verify_tier: str | None = None,
                    status: str | None = None,
                    expires_at: str | None = None,
                    last_seen_at: datetime | None = None) -> None:
        sets, vals = [], []
        for col, v in [("final_price", final_price), ("pct_at_checkout", pct_at_checkout),
                       ("abs_savings", abs_savings), ("online_only", online_only),
                       ("verify_tier", verify_tier), ("status", status),
                       ("expires_at", expires_at)]:
            if v is not None:
                sets.append(f"{col}=?")
                vals.append(v)
        if payload is not None:
            sets.append("payload=?")
            vals.append(json.dumps(payload))
        if last_seen_at is not None:
            sets.append("last_seen_at=?")
            vals.append(iso(last_seen_at))
        if not sets:
            return
        vals.append(dedupe_key)
        self.conn.execute(f"UPDATE deals SET {', '.join(sets)} WHERE dedupe_key=?", vals)
        self.conn.commit()

    def deals_by_store(self, store: str, status: str = "alive") -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM deals WHERE store=? AND status=?", (store, status)
        ).fetchall()
        return [self._deal_row_to_dict(r) for r in rows]

    def deal_by_canonical_url(self, canonical_url: str) -> dict[str, Any] | None:
        if not canonical_url:
            return None
        row = self.conn.execute(
            "SELECT * FROM deals WHERE canonical_url=? LIMIT 1", (canonical_url,)
        ).fetchone()
        return self._deal_row_to_dict(row) if row else None

    def all_deals(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.conn.execute("SELECT * FROM deals WHERE status=?", (status,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM deals").fetchall()
        return [self._deal_row_to_dict(r) for r in rows]

    # ---------------- sightings ----------------

    def add_sighting(self, deal_id: int, *, source_name: str, source_url: str,
                     content_hash: str, seen_at: datetime,
                     final_price: float | None) -> bool:
        try:
            self.conn.execute(
                "INSERT INTO sightings(deal_id, source_name, source_url, content_hash,"
                " seen_at, final_price) VALUES (?,?,?,?,?,?)",
                (deal_id, source_name, source_url, content_hash, iso(seen_at), final_price),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def sightings_for(self, deal_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT source_name, source_url, seen_at, final_price FROM sightings"
            " WHERE deal_id=? ORDER BY seen_at", (deal_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def distinct_sources_within(self, deal_id: int, since: datetime) -> int:
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT source_name) AS n FROM sightings"
            " WHERE deal_id=? AND seen_at>=?", (deal_id, iso(since)),
        ).fetchone()
        return int(row["n"])

    # ---------------- price_history ----------------

    def record_price(self, canonical_key: str, store: str, price: float,
                     observed_at: datetime, content_hash: str) -> bool:
        try:
            self.conn.execute(
                "INSERT INTO price_history(canonical_key, store, price, observed_at, content_hash)"
                " VALUES (?,?,?,?,?)",
                (canonical_key, store, price, iso(observed_at), content_hash),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def price_stats(self, canonical_key: str) -> tuple[int, float | None]:
        rows = self.conn.execute(
            "SELECT price FROM price_history WHERE canonical_key=?", (canonical_key,)
        ).fetchall()
        prices = [r["price"] for r in rows]
        if not prices:
            return 0, None
        return len(prices), float(statistics.median(prices))

    # ---------------- alerts_sent ----------------

    def alert_exists(self, channel: str, dedup_token: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM alerts_sent WHERE channel=? AND dedup_token=?",
            (channel, dedup_token),
        ).fetchone()
        return row is not None

    def record_alert(self, *, deal_key: str, channel: str, dedup_token: str,
                     now: datetime, price_at_send: float | None = None,
                     tier_at_send: str = "") -> bool:
        try:
            self.conn.execute(
                "INSERT INTO alerts_sent(deal_key, channel, dedup_token, sent_at,"
                " price_at_send, tier_at_send) VALUES (?,?,?,?,?,?)",
                (deal_key, channel, dedup_token, iso(now), price_at_send, tier_at_send),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def last_alert(self, deal_key: str, channel: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM alerts_sent WHERE deal_key=? AND channel=?"
            " ORDER BY sent_at DESC LIMIT 1", (deal_key, channel),
        ).fetchone()
        return dict(row) if row else None

    # ---------------- source_health ----------------

    def is_tripped(self, source_name: str, now: datetime) -> bool:
        row = self.conn.execute(
            "SELECT tripped_until FROM source_health WHERE source_name=?", (source_name,)
        ).fetchone()
        if not row or not row["tripped_until"]:
            return False
        until = parse_iso(row["tripped_until"])
        return until is not None and now < until

    def record_source_result(self, source_name: str, *, ok: bool, error: str,
                             items: int, now: datetime, trip_threshold: int = 3,
                             cooldown_h: float = 6.0) -> None:
        row = self.conn.execute(
            "SELECT consecutive_failures FROM source_health WHERE source_name=?",
            (source_name,),
        ).fetchone()
        failures = int(row["consecutive_failures"]) if row else 0
        if ok:
            self.conn.execute(
                "INSERT INTO source_health(source_name, last_success_at, last_error,"
                " consecutive_failures, tripped_until, items_last_run, updated_at)"
                " VALUES (?,?,?,0,NULL,?,?)"
                " ON CONFLICT(source_name) DO UPDATE SET last_success_at=excluded.last_success_at,"
                " last_error='', consecutive_failures=0, tripped_until=NULL,"
                " items_last_run=excluded.items_last_run, updated_at=excluded.updated_at",
                (source_name, iso(now), items, iso(now)),
            )
        else:
            failures += 1
            tripped_until = (
                iso(now + timedelta(hours=cooldown_h)) if failures >= trip_threshold else None
            )
            self.conn.execute(
                "INSERT INTO source_health(source_name, last_error, consecutive_failures,"
                " tripped_until, items_last_run, updated_at) VALUES (?,?,?,?,0,?)"
                " ON CONFLICT(source_name) DO UPDATE SET last_error=excluded.last_error,"
                " consecutive_failures=excluded.consecutive_failures,"
                " tripped_until=excluded.tripped_until, updated_at=excluded.updated_at",
                (source_name, error[:500], failures, tripped_until, iso(now)),
            )
        self.conn.commit()

    def all_source_health(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM source_health ORDER BY source_name"
        ).fetchall()
        return [dict(r) for r in rows]

    def tripped_count(self, now: datetime) -> int:
        rows = self.conn.execute(
            "SELECT tripped_until FROM source_health WHERE tripped_until IS NOT NULL"
        ).fetchall()
        n = 0
        for r in rows:
            until = parse_iso(r["tripped_until"])
            if until and now < until:
                n += 1
        return n

    # ---------------- http_cache ----------------

    def get_validators(self, url: str) -> tuple[str, str]:
        row = self.conn.execute(
            "SELECT etag, last_modified FROM http_cache WHERE url=?", (url,)
        ).fetchone()
        return (row["etag"], row["last_modified"]) if row else ("", "")

    def store_validators(self, url: str, etag: str, last_modified: str,
                         now: datetime) -> None:
        self.conn.execute(
            "INSERT INTO http_cache(url, etag, last_modified, fetched_at) VALUES (?,?,?,?)"
            " ON CONFLICT(url) DO UPDATE SET etag=excluded.etag,"
            " last_modified=excluded.last_modified, fetched_at=excluded.fetched_at",
            (url, etag or "", last_modified or "", iso(now)),
        )
        self.conn.commit()

    # ---------------- run_stats ----------------

    def start_run(self, run_key: str, kind: str, now: datetime) -> None:
        self.conn.execute(
            "INSERT INTO run_stats(run_key, kind, started_at) VALUES (?,?,?)"
            " ON CONFLICT(run_key) DO UPDATE SET started_at=excluded.started_at",
            (run_key, kind, iso(now)),
        )
        self.conn.commit()

    def finish_run(self, run_key: str, *, now: datetime, items_fetched: int,
                   posts_new: int, deals_parsed: int, published: int,
                   spend_usd: float) -> None:
        self.conn.execute(
            "UPDATE run_stats SET finished_at=?, items_fetched=?, posts_new=?,"
            " deals_parsed=?, published=?, spend_usd=? WHERE run_key=?",
            (iso(now), items_fetched, posts_new, deals_parsed, published,
             spend_usd, run_key),
        )
        self.conn.commit()

    def avg_items_7d(self, now: datetime, kind: str = "hourly") -> float | None:
        since = iso(now - timedelta(days=7))
        rows = self.conn.execute(
            "SELECT items_fetched FROM run_stats WHERE kind=? AND finished_at IS NOT NULL"
            " AND started_at>=? AND started_at<?",
            (kind, since, iso(now - timedelta(minutes=30))),
        ).fetchall()
        if not rows:
            return None
        vals = [r["items_fetched"] for r in rows]
        return sum(vals) / len(vals)

    # ---------------- meta / retention ----------------

    def meta_get(self, k: str) -> str | None:
        row = self.conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row["v"] if row else None

    def meta_set(self, k: str, v: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (k, v),
        )
        self.conn.commit()

    def prune(self, now: datetime, *, posts_seen_days: int = 30,
              deals_days: int = 14) -> None:
        self.conn.execute(
            "DELETE FROM posts_seen WHERE first_seen_at < ?",
            (iso(now - timedelta(days=posts_seen_days)),),
        )
        self.conn.execute(
            "DELETE FROM deals WHERE last_seen_at < ?",
            (iso(now - timedelta(days=deals_days)),),
        )
        self.conn.commit()
