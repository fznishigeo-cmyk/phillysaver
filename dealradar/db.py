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
import logging
import sqlite3
import statistics
import time
from contextlib import closing
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .models import RawPost
from .conditions import normalize_conditions
from .util import iso, now_utc, parse_iso

log = logging.getLogger(__name__)

_CACHE_SWEEP_KEY = "http_cache_sweep"
_CACHE_CHUNK_ROWS = 64
# Scheduling headroom from offline fixtures, not a hard SQLite timeout.
_CACHE_CHUNK_HEADROOM_S = 0.01


class _CacheSweepDeferred(Exception):
    """Roll back an unfinished chunk when its cooperative time allowance ends."""


@lru_cache(maxsize=1)
def _publisher_sites() -> dict[str, str]:
    from .config import load_sources

    primary, backup = load_sources()
    return {s.name: (urlsplit(s.site).hostname or "").lower().removeprefix("www.")
            for s in primary + backup}

SCHEMA = """
CREATE TABLE IF NOT EXISTS appeal_scores (
    dedupe_key TEXT NOT NULL,
    final_price REAL NOT NULL,
    score INTEGER NOT NULL CHECK(score BETWEEN 1 AND 5),
    rated_at TEXT NOT NULL,
    PRIMARY KEY(dedupe_key, final_price)
);
CREATE TABLE IF NOT EXISTS accuracy_samples (
    deal_key TEXT NOT NULL,
    run_key TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    bucket TEXT NOT NULL,
    store TEXT NOT NULL,
    source TEXT NOT NULL,
    outcome TEXT NOT NULL,
    PRIMARY KEY(run_key, deal_key, source),
    UNIQUE(deal_key, observed_at, source)
);
CREATE INDEX IF NOT EXISTS idx_accuracy_checked ON accuracy_samples(checked_at);
CREATE TABLE IF NOT EXISTS accuracy_extra_runs (
    run_key TEXT PRIMARY KEY,
    checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accuracy_extra_reads (
    day TEXT NOT NULL,
    deal_key TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    run_key TEXT NOT NULL,
    PRIMARY KEY(run_key, deal_key)
);
CREATE TABLE IF NOT EXISTS pinterest_pin_daily (
    environment TEXT NOT NULL,
    pin_id TEXT NOT NULL,
    day TEXT NOT NULL,
    board TEXT NOT NULL,
    category TEXT NOT NULL,
    posted_at TEXT NOT NULL,
    impressions INTEGER NOT NULL CHECK(impressions >= 0),
    saves INTEGER NOT NULL CHECK(saves >= 0),
    pin_clicks INTEGER NOT NULL CHECK(pin_clicks >= 0),
    outbound_clicks INTEGER NOT NULL CHECK(outbound_clicks >= 0),
    PRIMARY KEY(environment, pin_id, day)
);
CREATE TABLE IF NOT EXISTS deal_clicks (
    deal_key TEXT NOT NULL,
    hour INTEGER NOT NULL,
    count INTEGER NOT NULL CHECK(count >= 0),
    PRIMARY KEY(deal_key, hour)
);
CREATE TABLE IF NOT EXISTS deal_click_shown (
    deal_key TEXT NOT NULL,
    hour INTEGER NOT NULL,
    seconds REAL NOT NULL CHECK(seconds BETWEEN 0 AND 3600),
    position INTEGER NOT NULL,
    PRIMARY KEY(deal_key, hour)
);
CREATE INDEX IF NOT EXISTS idx_click_shown_hour ON deal_click_shown(hour);
CREATE TABLE IF NOT EXISTS deal_click_coverage (
    hour INTEGER PRIMARY KEY,
    capped INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS deal_click_runs (
    run_key TEXT PRIMARY KEY,
    hour INTEGER NOT NULL,
    ingested INTEGER NOT NULL DEFAULT 0,
    published INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS delivery_batches (
    channel TEXT NOT NULL,
    run_key TEXT NOT NULL,
    deal_keys TEXT NOT NULL,
    quota INTEGER NOT NULL,
    PRIMARY KEY(channel, run_key)
);
CREATE TABLE IF NOT EXISTS outgoing (
    channel TEXT NOT NULL,
    dedup_token TEXT NOT NULL,
    run_key TEXT NOT NULL,
    started_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    finished_at TEXT,
    PRIMARY KEY(channel, dedup_token)
);
CREATE TABLE IF NOT EXISTS llm_calls (
    call_key TEXT PRIMARY KEY,
    run_key TEXT NOT NULL,
    model TEXT NOT NULL,
    started_at TEXT NOT NULL,
    billed_at TEXT,
    reserved_usd REAL NOT NULL,
    cost_usd REAL,
    status TEXT NOT NULL DEFAULT 'reserved'
);
CREATE TABLE IF NOT EXISTS llm_reservations (
    call_key TEXT NOT NULL REFERENCES llm_calls(call_key),
    day TEXT NOT NULL,
    usd REAL NOT NULL,
    PRIMARY KEY(call_key, day)
);
CREATE INDEX IF NOT EXISTS idx_llm_run ON llm_calls(run_key, status);
CREATE INDEX IF NOT EXISTS idx_llm_day ON llm_calls(substr(billed_at,1,10), status);
CREATE INDEX IF NOT EXISTS idx_llm_reserved_day ON llm_reservations(day);
CREATE TABLE IF NOT EXISTS llm_legacy_spend (
    id INTEGER PRIMARY KEY,
    run_key TEXT NOT NULL,
    billed_at TEXT NOT NULL,
    usd REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_legacy_day ON llm_legacy_spend(substr(billed_at,1,10));
CREATE TABLE IF NOT EXISTS source_audit_posts (
    source_name TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    parsed_at TEXT,
    extracted INTEGER NOT NULL DEFAULT 0,
    evidence INTEGER NOT NULL DEFAULT 0,
    duplicate INTEGER NOT NULL DEFAULT 0,
    below_floor INTEGER NOT NULL DEFAULT 0,
    critic INTEGER NOT NULL DEFAULT 0,
    dead INTEGER NOT NULL DEFAULT 0,
    other INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(source_name, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_source_audit_seen ON source_audit_posts(first_seen_at);
CREATE TABLE IF NOT EXISTS source_audit_breakers (
    source_name TEXT PRIMARY KEY,
    tripped_at TEXT NOT NULL,
    trips INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS legitimacy_audits (
    event_key TEXT PRIMARY KEY,
    observed_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reason TEXT NOT NULL,
    mode TEXT NOT NULL,
    agreement INTEGER,
    compared_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_legitimacy_seen ON legitimacy_audits(observed_at);
CREATE INDEX IF NOT EXISTS idx_legitimacy_compared ON legitimacy_audits(compared_at);
CREATE TABLE IF NOT EXISTS legitimacy_batches (
    batch_key TEXT PRIMARY KEY,
    observed_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    model TEXT NOT NULL,
    deals_avoided INTEGER NOT NULL,
    calls_avoided INTEGER NOT NULL,
    estimated_usd_saved REAL
);
CREATE INDEX IF NOT EXISTS idx_legitimacy_batches_seen ON legitimacy_batches(observed_at);

CREATE TABLE IF NOT EXISTS critic_shadow_daily (
    day TEXT PRIMARY KEY,
    batches INTEGER NOT NULL DEFAULT 0,
    deals_judged INTEGER NOT NULL DEFAULT 0,
    same_verdict INTEGER NOT NULL DEFAULT 0,
    shadow_kept_critic_killed INTEGER NOT NULL DEFAULT 0,
    shadow_killed_critic_kept INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS second_opinion_yield (
    event_key TEXT PRIMARY KEY,
    observed_at TEXT NOT NULL,
    source_name TEXT NOT NULL,
    extracted INTEGER NOT NULL,
    evidence INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS critic_tier_batches (
    batch_key TEXT PRIMARY KEY,
    day TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS critic_tier_samples (
    event_key TEXT PRIMARY KEY,
    observed_at TEXT NOT NULL,
    agrees INTEGER NOT NULL
);

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

CREATE TABLE IF NOT EXISTS post_parses (
    text_fingerprint TEXT PRIMARY KEY,
    deals TEXT NOT NULL,
    parsed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_post_parses_at ON post_parses(parsed_at);

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

CREATE TABLE IF NOT EXISTS products (
    product_id INTEGER PRIMARY KEY,
    signature TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS product_keys (
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    PRIMARY KEY(kind, value, product_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_product_keys_product ON product_keys(product_id);
CREATE TABLE IF NOT EXISTS product_offers (
    dedupe_key TEXT PRIMARY KEY,
    product_id INTEGER REFERENCES products(product_id),
    store TEXT NOT NULL,
    condition TEXT NOT NULL,
    match TEXT NOT NULL,
    fingerprint BLOB NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_product_offers_product ON product_offers(product_id);

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

CREATE TABLE IF NOT EXISTS keepa_recheck_history (
    deal_key TEXT PRIMARY KEY,
    samples TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS keepa_recheck_yield (
    day TEXT NOT NULL,
    class TEXT NOT NULL,
    tokens REAL NOT NULL,
    estimated_tokens REAL NOT NULL,
    readings INTEGER NOT NULL,
    changed INTEGER NOT NULL,
    deaths INTEGER NOT NULL,
    useful INTEGER NOT NULL,
    PRIMARY KEY(day, class)
);

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

CREATE TABLE IF NOT EXISTS efficiency_runs (
    run_key TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    pending INTEGER NOT NULL DEFAULT 1,
    metrics TEXT
);
CREATE INDEX IF NOT EXISTS idx_efficiency_at ON efficiency_runs(started_at);

CREATE TABLE IF NOT EXISTS ai_yield_events (
    event_key TEXT PRIMARY KEY,
    identity TEXT NOT NULL,
    kind TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    run_key TEXT NOT NULL,
    source_name TEXT NOT NULL,
    category TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_yield_at ON ai_yield_events(observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_yield_first_identity ON ai_yield_events(identity)
    WHERE kind IN ('new','baseline');
CREATE TABLE IF NOT EXISTS ai_yield_runs (
    run_key TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    policy TEXT NOT NULL,
    mode TEXT NOT NULL,
    failed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ai_prefilter_posts (
    policy TEXT NOT NULL,
    source_name TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    site TEXT NOT NULL,
    text_fingerprint TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    evaluated INTEGER NOT NULL DEFAULT 0,
    miss INTEGER NOT NULL DEFAULT 0,
    gap TEXT NOT NULL DEFAULT 'not_parsed',
    PRIMARY KEY(policy, source_name, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_ai_prefilter_at ON ai_prefilter_posts(first_seen_at);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS indexnow_pages (
    url TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    pending_since TEXT,
    received_hash TEXT,
    received_at TEXT,
    key_pending INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    retry_at TEXT,
    removed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS indexnow_batches (
    endpoint TEXT NOT NULL,
    run_key TEXT NOT NULL,
    versions TEXT NOT NULL,
    started_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'sending',
    http_status INTEGER,
    PRIMARY KEY(endpoint, run_key)
);
"""



# A timed-out or unreadable paid call can still be billed shortly after the
# client gives up. Its worst-case reservation stays on its own UTC day, and on
# the next day only when it ended this close to midnight (2026-10-16: 190
# uncertain calls from one day blocked all parsing the next morning).
UNCERTAIN_BILLING_MARGIN = timedelta(minutes=15)

class DB:
    def __init__(self, path: str | Path = "state.db", *, read_only: bool = False) -> None:
        self.path = str(path)
        self.compact_deal_payloads = False
        self.source_audit_posts_days = None
        if read_only:
            self.conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
            self.conn.row_factory = sqlite3.Row
            return
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(llm_calls)")}
        for name, kind in (("kind", "TEXT NOT NULL DEFAULT 'unknown'"),
                           ("source_name", "TEXT NOT NULL DEFAULT ''"),
                           ("input_tokens", "INTEGER"), ("output_tokens", "INTEGER"),
                           ("cache_read_tokens", "INTEGER"), ("cache_write_tokens", "INTEGER")):
            if name not in columns:
                self.conn.execute(f"ALTER TABLE llm_calls ADD COLUMN {name} {kind}")
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(deals)")}
        # Lifetime dedupe anchors live on the existing inventory rows; the
        # dated observations below expire with run_stats, never these anchors.
        for name, kind in (("ai_public_at", "TEXT"), ("ai_public_price", "REAL"),
                           ("product_id", "INTEGER REFERENCES products(product_id)")):
            if name not in columns:
                self.conn.execute(f"ALTER TABLE deals ADD COLUMN {name} {kind}")
        if "text_fingerprint" not in {r["name"] for r in self.conn.execute("PRAGMA table_info(ai_prefilter_posts)")}:
            self.conn.execute("ALTER TABLE ai_prefilter_posts ADD COLUMN text_fingerprint TEXT NOT NULL DEFAULT ''")
        if "products_backfilled" not in {r["name"] for r in self.conn.execute("PRAGMA table_info(run_stats)")}:
            self.conn.execute("ALTER TABLE run_stats ADD COLUMN products_backfilled INTEGER NOT NULL DEFAULT 0")
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(run_stats)")}
        for name in ("cache_examined", "cache_deleted", "cache_completed", "posts_left", "deadline_hit"):
            if name not in columns:
                self.conn.execute(f"ALTER TABLE run_stats ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")
        if "state_maintenance" not in columns:
            self.conn.execute("ALTER TABLE run_stats ADD COLUMN state_maintenance TEXT NOT NULL DEFAULT '{}'")
        # Uncertain calls, and calls a run never settled (cut off at its parse
        # deadline), kept tomorrow's reservation however early they ended or
        # started; release those (idempotent; near-midnight calls keep theirs).
        self.conn.execute(
            "DELETE FROM llm_reservations WHERE day > (SELECT date(COALESCE(c.billed_at, c.started_at), ?)"
            " FROM llm_calls c WHERE c.call_key=llm_reservations.call_key"
            " AND c.status IN ('uncertain','reserved'))",
            (f"+{int(UNCERTAIN_BILLING_MARGIN.total_seconds() // 60)} minutes",))
        if self.meta_get("llm_ledger_migrated") is None:
            self.conn.execute(
                "INSERT INTO llm_legacy_spend(run_key, billed_at, usd)"
                " SELECT run_key, started_at, spend_usd FROM run_stats WHERE spend_usd > 0")
            self.meta_set("llm_ledger_migrated", "1")
        for row in self.conn.execute("SELECT id, payload, final_price FROM deals").fetchall():
            payload = json.loads(row["payload"])
            conditions = normalize_conditions(payload.get("conditions"))
            repaired = dict(payload, conditions=conditions)
            if not payload.get("price_published_at"):
                latest = self.conn.execute(
                    "SELECT final_price FROM sightings WHERE deal_id=? ORDER BY seen_at DESC,id DESC LIMIT 1",
                    (row["id"],)).fetchone()
                if latest and latest[0] != row["final_price"]:
                    repaired["flags"] = list(dict.fromkeys([*(payload.get("flags") or []), "unbacked_price"]))
            if repaired != payload:
                self.conn.execute("UPDATE deals SET payload=? WHERE id=?",
                                  (json.dumps(repaired), row["id"]))
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    # ---------------- posts_seen ----------------

    def audit_seen(self, posts: list[RawPost], now: datetime) -> None:
        """Count discoveries, including deferred posts; never store their text."""
        if self.meta_get("source_audit_since") is None:
            self.meta_set("source_audit_since", iso(now))
        sql = (
            "INSERT OR IGNORE INTO source_audit_posts"
            "(source_name, content_hash, first_seen_at) SELECT ?,?,?")
        # A processed post whose shorter audit window ended is not a new
        # discovery. Its existing posts_seen receipt still prevents re-parsing.
        if self.source_audit_posts_days is not None:
            sql += " WHERE NOT EXISTS (SELECT 1 FROM posts_seen WHERE content_hash=?)"
        self.conn.executemany(sql, [
            (p.source_name, p.content_hash, iso(now)) +
            ((p.content_hash,) if self.source_audit_posts_days is not None else ()) for p in posts])
        self.conn.commit()

    def audit_parsed(self, post: RawPost, counts: dict[str, int], now: datetime) -> None:
        """A post version's parse counters are recorded once, like posts_seen."""
        self.conn.execute(
            "UPDATE source_audit_posts SET parsed_at=?, extracted=?, evidence=?,"
            " duplicate=?, below_floor=?, critic=?, dead=?, other=?"
            " WHERE source_name=? AND content_hash=?"
            " AND parsed_at IS NULL",
            (iso(now), counts["extracted"], counts["evidence"],
             counts.get("duplicate", 0), counts.get("below_floor", 0),
             counts.get("critic", 0), counts.get("dead", 0), counts.get("other", 0),
             post.source_name, post.content_hash))
        self.conn.commit()

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

    def cached_post_parse(self, fingerprint: str, now: datetime,
                          days: int) -> list[dict] | None:
        """Source-independent LLM facts only; never the cleaned post text."""
        row = self.conn.execute(
            "SELECT deals FROM post_parses WHERE text_fingerprint=? AND parsed_at >= ?",
            (fingerprint, iso(now - timedelta(days=days))),
        ).fetchone()
        return json.loads(row["deals"]) if row else None

    def cache_post_parse(self, fingerprint: str, deals: list[dict], now: datetime) -> None:
        self.conn.execute(
            "INSERT INTO post_parses(text_fingerprint, deals, parsed_at) VALUES (?,?,?)"
            " ON CONFLICT(text_fingerprint) DO UPDATE SET deals=excluded.deals,"
            " parsed_at=excluded.parsed_at",
            (fingerprint, json.dumps(deals), iso(now)),
        )
        self.conn.commit()

    # ---------------- deals ----------------

    def get_deal(self, dedupe_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM deals WHERE dedupe_key=?", (dedupe_key,)
        ).fetchone()
        return self._deal_row_to_dict(row) if row else None

    def _deal_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = json.loads(row["payload"])
        if d.pop("_sightings_in_table", False):
            d["sightings"] = self.sightings_for(row["id"])
        d["conditions"] = normalize_conditions(d.get("conditions"))
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
            "product_id": row["product_id"] if "product_id" in row.keys() else None,
        })
        return d

    def _deal_payload(self, payload: dict[str, Any], deal_id: int | None = None) -> str:
        saved = {k: v for k, v in payload.items() if k != "product_id"}
        if self.compact_deal_payloads:
            # Only remove fields reconstructed verbatim from authoritative SQL
            # columns. Legacy/synthetic sightings without matching rows survive.
            for key in ("id", "dedupe_key", "store", "item", "normalized_item", "canonical_url",
                        "final_price", "pct_at_checkout", "abs_savings", "online_only",
                        "verify_tier", "status", "expires_at", "first_seen_at", "last_seen_at"):
                saved.pop(key, None)
            if "sightings" in saved and deal_id is not None:
                if saved["sightings"] == self.sightings_for(deal_id):
                    del saved["sightings"]
                    saved["_sightings_in_table"] = True
                else:
                    saved.pop("_sightings_in_table", None)
            return json.dumps(saved, separators=(",", ":"))
        return json.dumps(saved)

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
             self._deal_payload(payload),
             final_price, pct_at_checkout, abs_savings,
             online_only, expires_at, iso(now), iso(now)),
        )
        self._attach_product(dedupe_key)
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
                    last_seen_at: datetime | None = None,
                    canonical_url: str | None = None,
                    replace_prices: bool = False) -> None:
        sets, vals = [], []
        for col, v in [("final_price", final_price), ("pct_at_checkout", pct_at_checkout),
                       ("abs_savings", abs_savings), ("online_only", online_only),
                       ("verify_tier", verify_tier), ("status", status),
                       ("expires_at", expires_at), ("canonical_url", canonical_url)]:
            if v is not None or (replace_prices and col in {"final_price", "pct_at_checkout", "abs_savings"}):
                sets.append(f"{col}=?")
                vals.append(v)
        if payload is not None:
            sets.append("payload=?")
            row = (self.conn.execute("SELECT id FROM deals WHERE dedupe_key=?", (dedupe_key,)).fetchone()
                   if self.compact_deal_payloads else None)
            vals.append(self._deal_payload(payload, row[0] if row else None))
        if last_seen_at is not None:
            sets.append("last_seen_at=?")
            vals.append(iso(last_seen_at))
        if not sets:
            return
        vals.append(dedupe_key)
        self.conn.execute(f"UPDATE deals SET {', '.join(sets)} WHERE dedupe_key=?", vals)
        # Ordinary maintenance of legacy rows must not turn the bounded
        # backfill into an unbounded one. A real upsert (last_seen_at) can
        # attach immediately; other updates maintain already-linked offers.
        if last_seen_at is not None or ((payload is not None or canonical_url is not None)
                and self.conn.execute("SELECT 1 FROM product_offers WHERE dedupe_key=?",
                                      (dedupe_key,)).fetchone()):
            self._attach_product(dedupe_key)
        self.conn.commit()

    def _attach_product(self, dedupe_key: str) -> None:
        from .products import attach

        deal = self.get_deal(dedupe_key)
        if deal is not None:
            attach(self.conn, deal)

    def backfill_products(self, limit: int, *, run_key: str | None = None) -> int:
        """Resume with the next unlinked rows; each durable offer is its checkpoint."""
        from .products import attach

        if limit <= 0:
            return 0
        with self.conn:
            if run_key is not None and self.conn.execute(
                    "UPDATE run_stats SET products_backfilled=1 WHERE run_key=? AND products_backfilled=0",
                    (run_key,)).rowcount != 1:
                return 0
            rows = self.conn.execute(
                "SELECT d.* FROM deals d LEFT JOIN product_offers p USING(dedupe_key)"
                " WHERE p.dedupe_key IS NULL ORDER BY d.id LIMIT ?", (limit,)).fetchall()
            for row in rows:
                attach(self.conn, self._deal_row_to_dict(row))
        return len(rows)

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

    def distinct_sources_within(self, deal_id: int, since: datetime,
                                final_price: float | None = None,
                                now: datetime | None = None, *,
                                tolerance_pct: float = 10.0) -> int:
        if final_price is None:
            row = self.conn.execute("SELECT final_price FROM deals WHERE id=?", (deal_id,)).fetchone()
            final_price = row[0] if row else None
            if final_price is None:
                return 0
        rows = self.conn.execute(
            "SELECT source_name, source_url, final_price FROM sightings"
            " WHERE deal_id=? AND seen_at>=? AND seen_at<=? ORDER BY seen_at DESC, id DESC",
            (deal_id, iso(since), iso(now or now_utc()))).fetchall()
        latest = {}
        sites = _publisher_sites()
        for row in rows:
            publisher = sites.get(row["source_name"]) or (
                urlsplit(row["source_url"]).hostname or "").lower().removeprefix("www.")
            if publisher:
                latest.setdefault(publisher, row["final_price"])
        # Use the live-price check's existing 10% plus one-cent tolerance.
        epsilon = .01 if tolerance_pct else .001
        return sum(price is not None and abs(price - final_price) <= abs(final_price) * tolerance_pct / 100 + epsilon
                   for price in latest.values())

    def deals_for_post(self, source_name: str, source_url: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT DISTINCT deals.* FROM deals JOIN sightings ON sightings.deal_id=deals.id"
            " WHERE sightings.source_name=? AND sightings.source_url=?",
            (source_name, source_url)).fetchall()
        return [self._deal_row_to_dict(row) for row in rows]

    # ---------------- price_history ----------------

    def record_price(self, canonical_key: str, store: str, price: float,
                     observed_at: datetime, content_hash: str, *, max_check_rows_per_day: int | None = None) -> bool:
        sql = ("INSERT INTO price_history(canonical_key, store, price, observed_at, content_hash)"
               " SELECT ?,?,?,?,?")
        params = (canonical_key, store, price, iso(observed_at), content_hash)
        if max_check_rows_per_day is not None:
            day = observed_at.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            sql += (" WHERE (SELECT COUNT(*) FROM price_history WHERE canonical_key=?"
                    " AND content_hash GLOB 'check:*' AND observed_at>=? AND observed_at<?) < ?")
            params += (canonical_key, iso(day), iso(day + timedelta(days=1)), max_check_rows_per_day)
        try:
            cursor = self.conn.execute(sql, params)
            self.conn.commit()
            return cursor.rowcount > 0
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

    def price_observations(self, canonical_key: str, since: datetime,
                           until: datetime) -> list[dict[str, Any]]:
        """Our recorded checks only, in time order within an inclusive window."""
        rows = self.conn.execute(
            "SELECT price, observed_at FROM price_history WHERE canonical_key=?"
            " AND observed_at>=? AND observed_at<=? ORDER BY observed_at, id",
            (canonical_key, iso(since), iso(until)),
        ).fetchall()
        return [dict(r) for r in rows]

    def product_price_history(self, product_id: int, days: int | None,
                              *, now: datetime | None = None) -> list[dict[str, Any]]:
        """Recorded observations across linked offers, retaining store and condition.

        Sightings fill gaps only: the same post already in price_history is
        one observation, not two. None selects all retained checks.
        No prices or savings are inferred.
        """
        import math

        if days is not None and days < 0:
            return []
        until = now or now_utc()
        since = until - timedelta(days=days) if days is not None else datetime.min.replace(tzinfo=timezone.utc)
        window = (product_id, iso(since), iso(until))
        rows = self.conn.execute(
            "SELECT h.price, h.observed_at, p.dedupe_key, h.store, p.condition"
            " FROM product_offers p JOIN price_history h ON h.canonical_key=p.dedupe_key"
            " WHERE p.product_id=? AND h.observed_at>=? AND h.observed_at<=?"
            " UNION ALL"
            " SELECT s.final_price, s.seen_at, p.dedupe_key, p.store, p.condition"
            " FROM product_offers p JOIN deals d USING(dedupe_key)"
            " JOIN sightings s ON s.deal_id=d.id"
            " WHERE p.product_id=? AND s.seen_at>=? AND s.seen_at<=?"
            " AND NOT EXISTS (SELECT 1 FROM price_history h"
            " WHERE h.canonical_key=p.dedupe_key AND h.content_hash=s.content_hash)"
            " ORDER BY observed_at, dedupe_key, price", window + window).fetchall()
        return [dict(r) for r in rows if r["price"] is not None
                and math.isfinite(r["price"]) and r["price"] >= 0]

    # ---------------- alerts_sent ----------------

    def delivery_batch(self, channel: str, run_key: str, keys: list[str],
                       quota: int | None = None) -> list[str]:
        self.conn.execute(
            "INSERT OR IGNORE INTO delivery_batches(channel, run_key, deal_keys, quota) VALUES (?,?,?,?)",
            (channel, run_key, json.dumps(keys), len(keys) if quota is None else quota))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT deal_keys FROM delivery_batches WHERE channel=? AND run_key=?",
            (channel, run_key)).fetchone()
        return json.loads(row[0])

    def delivery_budget(self, channel: str, run_key: str) -> int:
        row = self.conn.execute(
            "SELECT quota - (SELECT COUNT(*) FROM outgoing WHERE channel=? AND run_key=?"
            " AND status!='rejected') FROM delivery_batches WHERE channel=? AND run_key=?",
            (channel, run_key, channel, run_key)).fetchone()
        return max(0, row[0]) if row else 0

    def outgoing_exists(self, channel: str, token: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM outgoing WHERE channel=? AND dedup_token=? AND status!='rejected'",
            (channel, token)).fetchone() is not None

    def outgoing_status(self, channel: str, token: str) -> str | None:
        row = self.conn.execute("SELECT status FROM outgoing WHERE channel=? AND dedup_token=?",
                                (channel, token)).fetchone()
        return row[0] if row else None

    def claim_outgoing(self, channel: str, token: str, run_key: str, now: datetime) -> bool:
        # Check the run quota in the same write that claims the operation.
        result = self.conn.execute(
            "INSERT INTO outgoing(channel, dedup_token, run_key, started_at) SELECT ?,?,?,?"
            " WHERE NOT EXISTS (SELECT 1 FROM delivery_batches WHERE channel=? AND run_key=?"
            " AND quota <= (SELECT COUNT(*) FROM outgoing WHERE channel=? AND run_key=?"
            " AND status!='rejected'))"
            " ON CONFLICT(channel, dedup_token) DO UPDATE SET status='pending',"
            " started_at=excluded.started_at, run_key=excluded.run_key, finished_at=NULL"
            " WHERE outgoing.status='rejected'",
            (channel, token, run_key, iso(now), channel, run_key, channel, run_key))
        self.conn.commit()
        return result.rowcount == 1

    def finish_outgoing(self, channel: str, token: str, now: datetime, *,
                        rejected: bool = False) -> None:
        self.conn.execute(
            "UPDATE outgoing SET status=?, finished_at=? WHERE channel=? AND dedup_token=?",
            ("rejected" if rejected else "sent", iso(now), channel, token))
        self.conn.commit()

    def outgoing_for(self, channel: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM outgoing WHERE channel=? AND status!='rejected'", (channel,))]

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
            " ORDER BY sent_at DESC, id DESC LIMIT 1", (deal_key, channel),
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
                             cooldown_h: float = 6.0,
                             count_failure: bool = True) -> None:
        """Record a run result. ``count_failure=False`` records the error for
        the health page without advancing the circuit breaker — used for
        deterministic config states (``needs_key``) that aren't site failures."""
        row = self.conn.execute(
            "SELECT consecutive_failures, tripped_until FROM source_health WHERE source_name=?",
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
                (source_name, iso(now), "", items, iso(now)),
            )
        else:
            if count_failure:
                failures += 1
            else:
                failures = 0
            tripped_until = (
                iso(now + timedelta(hours=cooldown_h))
                if count_failure and failures >= trip_threshold else None
            )
            old_until = parse_iso(row["tripped_until"]) if row else None
            if tripped_until and (old_until is None or old_until <= now):
                self.conn.execute(
                    "INSERT INTO source_audit_breakers(source_name, tripped_at, trips)"
                    " VALUES (?,?,1) ON CONFLICT(source_name) DO UPDATE SET"
                    " tripped_at=excluded.tripped_at, trips=trips+1",
                    (source_name, iso(now)))
            self.conn.execute(
                "INSERT INTO source_health(source_name, last_error, consecutive_failures,"
                " tripped_until, items_last_run, updated_at) VALUES (?,?,?,?,0,?)"
                " ON CONFLICT(source_name) DO UPDATE SET last_error=excluded.last_error,"
                " consecutive_failures=excluded.consecutive_failures,"
                " tripped_until=excluded.tripped_until, updated_at=excluded.updated_at",
                (source_name, error[:500], failures, tripped_until, iso(now)),
            )
        self.conn.commit()

    def reset_breaker(self, source_name: str) -> None:
        """Clear a source's failure count and trip (its config changed)."""
        self.conn.execute(
            "UPDATE source_health SET consecutive_failures=0, tripped_until=NULL"
            " WHERE source_name=?", (source_name,))
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

    def prune_http_cache(self, now: datetime, *, run_key: str, days: int = 30,
                         max_examined_per_run: int = 256, budget_s: float = 0.25,
                         deadline: float | None = None) -> dict[str, Any]:
        """Checkpoint bounded keyset chunks; a run can complete at most one sweep.

        The high-water key freezes the sweep's end. Inserts behind the cursor
        or beyond that end wait for the next sweep. NULL fetched_at survives,
        exactly as in the legacy DELETE. Only the existing URL index is used.
        """
        from .config import validate_incremental_cache_limits

        validate_incremental_cache_limits(max_examined_per_run, budget_s)
        if not self.conn.execute("SELECT 1 FROM run_stats WHERE run_key=?", (run_key,)).fetchone():
            raise ValueError("cache maintenance requires an existing run")
        started = time.monotonic()
        stop_at = min(started + budget_s, deadline if deadline is not None else float("inf"))
        cutoff = iso(now - timedelta(days=days))

        def check_time():
            if time.monotonic() + _CACHE_CHUNK_HEADROOM_S > stop_at:
                raise _CacheSweepDeferred

        while True:
            try:
                check_time()
                with self.conn:
                    self.conn.execute("BEGIN IMMEDIATE")
                    check_time()
                    run = self.conn.execute(
                        "SELECT cache_examined,cache_completed FROM run_stats WHERE run_key=?",
                        (run_key,)).fetchone()
                    if run["cache_completed"] or run["cache_examined"] >= max_examined_per_run:
                        break
                    state = json.loads(self.meta_get(_CACHE_SWEEP_KEY) or "null")
                    after = state["after"] if state else None
                    high = state["high_water"] if state else None
                    if high is None:
                        check_time()
                        row = self.conn.execute(
                            "SELECT url FROM http_cache ORDER BY url DESC LIMIT 1").fetchone()
                        high = row[0] if row else None
                    limit = min(_CACHE_CHUNK_ROWS, max_examined_per_run - run["cache_examined"])
                    rows = []
                    if high is not None:
                        check_time()
                        if after is None:
                            rows = self.conn.execute(
                                "SELECT url FROM http_cache WHERE url <= ? ORDER BY url LIMIT ?",
                                (high, limit)).fetchall()
                        else:
                            rows = self.conn.execute(
                                "SELECT url FROM http_cache WHERE url > ? AND url <= ? ORDER BY url LIMIT ?",
                                (after, high, limit)).fetchall()
                    check_time()
                    # Recheck expiry by primary key at deletion time. Never
                    # use meta_set here: its commit would split this checkpoint.
                    deleted = self.conn.executemany(
                        "DELETE FROM http_cache WHERE url=? AND fetched_at < ?",
                        [(row[0], cutoff) for row in rows]).rowcount
                    check_time()
                    completed = len(rows) < limit or rows[-1][0] == high
                    state = {"after": None if completed else rows[-1][0],
                             "high_water": None if completed else high}
                    self.conn.execute(
                        "INSERT INTO meta(k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                        (_CACHE_SWEEP_KEY, json.dumps(state)))
                    check_time()
                    self.conn.execute(
                        "UPDATE run_stats SET cache_examined=cache_examined+?,"
                        " cache_deleted=cache_deleted+?,cache_completed=? WHERE run_key=?",
                        (len(rows), deleted, int(completed), run_key))
                    check_time()
                if completed or run["cache_examined"] + len(rows) >= max_examined_per_run:
                    break
            except _CacheSweepDeferred:
                break  # the transaction context rolls back this chunk only
        run = self.conn.execute(
            "SELECT cache_examined,cache_deleted,cache_completed FROM run_stats WHERE run_key=?",
            (run_key,)).fetchone()
        return {"examined": run["cache_examined"], "deleted": run["cache_deleted"],
                "completed": bool(run["cache_completed"]), "deferred": not bool(run["cache_completed"]),
                "elapsed_s": round(time.monotonic() - started, 6)}

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
                   spend_usd: float, accounted_spend_usd: float = 0.0,
                   untracked_at: datetime | None = None,
                   posts_left: int = 0, deadline_hit: int = 0) -> None:
        self.conn.execute(
            # spend ADDS: two runs can share a quarter-hour key (10-minute
            # cron), and real money spent must never vanish from the daily
            # cap's ledger (live review finding).
            # Preserve the first finish's backlog when this run key is replayed.
            "UPDATE run_stats SET posts_left=CASE WHEN finished_at IS NULL THEN ? ELSE posts_left END,"
            " deadline_hit=CASE WHEN finished_at IS NULL THEN ? ELSE deadline_hit END,"
            " finished_at=?, items_fetched=?, posts_new=?,"
            " deals_parsed=?, published=?,"
            " spend_usd=COALESCE(spend_usd, 0) + ? WHERE run_key=?",
            (posts_left, int(deadline_hit), iso(now), items_fetched, posts_new, deals_parsed, published,
             spend_usd, run_key),
        )
        untracked = max(0.0, spend_usd - accounted_spend_usd)
        if untracked:
            self.conn.execute(
                "INSERT INTO llm_legacy_spend(run_key, billed_at, usd) VALUES (?,?,?)",
                (run_key, iso(untracked_at or now), untracked))
        self.conn.commit()

    def spend_usd_for_day(self, now: datetime) -> float:
        """Total recorded LLM spend for the current UTC day, across all runs."""
        day = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='llm_calls'").fetchone():
            return float(self.conn.execute(
                "SELECT COALESCE(SUM(spend_usd),0) FROM run_stats WHERE substr(started_at,1,10)=?",
                (day,)).fetchone()[0])  # read-only snapshots before the migration
        return self._day_spend(self.conn, day)

    def spend_usd_for_run(self, run_key: str) -> float:
        """All calls sharing a run key, including unsettled worst-case bounds."""
        row = self.conn.execute(
            "SELECT (SELECT COALESCE(SUM(COALESCE(cost_usd,reserved_usd)),0)"
            " FROM llm_calls WHERE run_key=? AND status!='released')"
            " + (SELECT COALESCE(SUM(usd),0) FROM llm_legacy_spend WHERE run_key=?)",
            (run_key, run_key)).fetchone()
        return float(row[0])

    def kind_spend_usd_for_run(self, kind: str, run_key: str) -> float:
        return float(self.conn.execute(
            "SELECT COALESCE(SUM(COALESCE(cost_usd,reserved_usd)),0) FROM llm_calls"
            " WHERE run_key=? AND kind=? AND status!='released'", (run_key, kind)).fetchone()[0])

    def kind_spend_usd_for_day(self, kind: str, now: datetime) -> float:
        day = now.astimezone(timezone.utc).date().isoformat()
        return self._kind_day_spend(self.conn, kind, day)

    @staticmethod
    def _kind_day_spend(conn, kind: str, day: str) -> float:
        return float(conn.execute(
            "SELECT (SELECT COALESCE(SUM(cost_usd),0) FROM llm_calls"
            " WHERE kind=? AND status='settled' AND substr(billed_at,1,10)=?)"
            " + (SELECT COALESCE(SUM(r.usd),0) FROM llm_reservations r"
            " JOIN llm_calls c USING(call_key) WHERE c.kind=? AND r.day=?)",
            (kind, day, kind, day)).fetchone()[0])

    @staticmethod
    def _day_spend(conn, day: str) -> float:
        # Uncertain calls retain their full reservation, including the next
        # UTC day: a response lost at midnight must not escape either cap.
        row = conn.execute(
            "SELECT (SELECT COALESCE(SUM(usd),0) FROM llm_legacy_spend"
            " WHERE substr(billed_at,1,10)=?)"
            " + (SELECT COALESCE(SUM(cost_usd),0) FROM llm_calls"
            " WHERE status='settled' AND substr(billed_at,1,10)=?)"
            " + (SELECT COALESCE(SUM(usd),0) FROM llm_reservations WHERE day=?)",
            (day, day, day)).fetchone()
        return float(row[0])

    def reserve_llm_call(self, call_key: str, run_key: str, model: str,
                         cost: float, now: datetime, allowances: dict[str, float],
                         run_cap: float, *, kind: str = "unknown", source_name: str = "",
                         daily_call_cap: int | None = None,
                         kind_run_cap: float | None = None,
                         kind_day_cap: float | None = None) -> bool:
        from .errors import SpendCapReached

        # Separate connections allow parser workers and other parser
        # instances to reserve atomically without sharing a SQLite cursor.
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if daily_call_cap is not None:
                from .ai_usage import model_calls_today

                # Count actual attempts, including re-asks, concurrent reservations,
                # and an uncertain midnight call on either possible billing day.
                calls = model_calls_today(conn, kind, model, now)
                if calls >= daily_call_cap:
                    return False
            used = conn.execute(
                "SELECT (SELECT COALESCE(SUM(COALESCE(cost_usd,reserved_usd)),0)"
                " FROM llm_calls WHERE run_key=? AND status!='released')"
                " + (SELECT COALESCE(SUM(usd),0) FROM llm_legacy_spend WHERE run_key=?)",
                (run_key, run_key)).fetchone()[0]
            if used + cost > run_cap or any(
                    self._day_spend(conn, day) + cost > cap for day, cap in allowances.items()):
                raise SpendCapReached("insufficient run or daily allowance for LLM reservation")
            if kind_run_cap is not None:
                used_kind = conn.execute(
                    "SELECT COALESCE(SUM(COALESCE(cost_usd,reserved_usd)),0) FROM llm_calls"
                    " WHERE run_key=? AND kind=? AND status!='released'", (run_key, kind)).fetchone()[0]
                if used_kind + cost > kind_run_cap:
                    raise SpendCapReached("insufficient kind run allowance for LLM reservation")
            if kind_day_cap is not None:
                for day in allowances:
                    used_kind = self._kind_day_spend(conn, kind, day)
                    if used_kind + cost > kind_day_cap:
                        raise SpendCapReached("insufficient kind daily allowance for LLM reservation")
            conn.execute(
                "INSERT INTO llm_calls(call_key, run_key, model, started_at, reserved_usd, kind, source_name)"
                " VALUES (?,?,?,?,?,?,?)", (call_key, run_key, model, iso(now), cost, kind, source_name))
            conn.executemany("INSERT INTO llm_reservations(call_key, day, usd) VALUES (?,?,?)",
                             [(call_key, day, cost) for day in allowances])
            return True

    def settle_llm_call(self, call_key: str, cost: float, now: datetime, *,
                        uncertain: bool = False, usage=None) -> None:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("UPDATE llm_calls SET cost_usd=?, billed_at=?, status=? WHERE call_key=?",
                         (cost, iso(now), "uncertain" if uncertain else "settled", call_key))
            if uncertain:
                # Billing can only land on a later day if the call ended near midnight.
                last_day = (now + UNCERTAIN_BILLING_MARGIN).astimezone(timezone.utc).date().isoformat()
                conn.execute("DELETE FROM llm_reservations WHERE call_key=? AND day>?", (call_key, last_day))
            if usage is not None:
                conn.execute("UPDATE llm_calls SET input_tokens=?, output_tokens=?,"
                             " cache_read_tokens=?, cache_write_tokens=? WHERE call_key=?",
                             (usage.input_tokens, usage.output_tokens,
                              getattr(usage, "cache_read_input_tokens", 0) or 0,
                              getattr(usage, "cache_creation_input_tokens", 0) or 0, call_key))
            if not uncertain:
                conn.execute("DELETE FROM llm_reservations WHERE call_key=?", (call_key,))

    def release_llm_call(self, call_key: str) -> None:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("UPDATE llm_calls SET cost_usd=0, status='released' WHERE call_key=?",
                         (call_key,))
            conn.execute("DELETE FROM llm_reservations WHERE call_key=?", (call_key,))

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

    def critic_shadow_counts(self, now: datetime, days: int = 1) -> dict[str, int]:
        day = now.astimezone(timezone.utc).date()
        columns = ("batches", "deals_judged", "same_verdict",
                   "shadow_kept_critic_killed", "shadow_killed_critic_kept")
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                                 " AND name='critic_shadow_daily'").fetchone():
            return dict.fromkeys(columns, 0)  # read-only snapshot before this migration
        row = self.conn.execute(
            "SELECT " + ", ".join(f"COALESCE(SUM({c}), 0) AS {c}" for c in columns)
            + " FROM critic_shadow_daily WHERE day >= ? AND day <= ?",
            ((day - timedelta(days=days - 1)).isoformat(), day.isoformat()),
        ).fetchone()
        return dict(row)

    def start_critic_shadow_batch(self, now: datetime, limit: int) -> bool:
        """Reserve a daily slot before sending, including failed batches."""
        day = now.astimezone(timezone.utc).date().isoformat()
        self.conn.execute("INSERT OR IGNORE INTO critic_shadow_daily(day) VALUES (?)", (day,))
        result = self.conn.execute(
            "UPDATE critic_shadow_daily SET batches=batches+1 WHERE day=? AND batches < ?",
            (day, limit))
        self.conn.commit()
        return result.rowcount == 1

    def record_critic_shadow(self, now: datetime, critic: list, shadow: list) -> None:
        counts = [0, 0, 0, 0]
        for real, other in zip(critic, shadow):
            if real is None or other is None:
                continue  # missing verdicts are not agreements or disagreements
            counts[0] += 1
            if real["plausible"] == other["plausible"]:
                counts[1] += 1
            elif other["plausible"]:
                counts[2] += 1
            else:
                counts[3] += 1
        self.conn.execute(
            "UPDATE critic_shadow_daily SET deals_judged=deals_judged+?,"
            " same_verdict=same_verdict+?, shadow_kept_critic_killed=shadow_kept_critic_killed+?,"
            " shadow_killed_critic_kept=shadow_killed_critic_kept+? WHERE day=?",
            (*counts, now.astimezone(timezone.utc).date().isoformat()))
        self.conn.commit()

    # ---------------- IndexNow receipts ----------------

    def indexnow_pages(self) -> dict[str, dict[str, Any]]:
        return {r["url"]: dict(r) for r in self.conn.execute("SELECT * FROM indexnow_pages")}

    def indexnow_discover(self, versions: dict[str, str], now: datetime,
                         resubmit_days: float) -> None:
        """Discovery creates pending work, never a receipt. Old listed-at meta
        is deliberately not migrated into receipts: no POST proved it."""
        previous = self.indexnow_pages()
        stamp = iso(now)
        with self.conn:
            for url, version in versions.items():
                row = previous.get(url)
                removed = int(version == "removed")
                if row is None:
                    self.conn.execute(
                        "INSERT INTO indexnow_pages(url,content_hash,pending_since,removed) VALUES (?,?,?,?)",
                        (url, version, stamp, removed))
                    continue
                changed = row["content_hash"] != version
                received = parse_iso(row["received_at"] or "")
                relist = (resubmit_days > 0 and not removed and received is not None
                          and now - received >= timedelta(days=resubmit_days))
                if changed and version == row["received_hash"] and not relist:
                    self.conn.execute(
                        "UPDATE indexnow_pages SET content_hash=?,pending_since=NULL,"
                        " attempts=0,failed=0,retry_at=NULL,removed=? WHERE url=?",
                        (version, removed, url))
                elif changed or (relist and row["pending_since"] is None):
                    self.conn.execute(
                        "UPDATE indexnow_pages SET content_hash=?,pending_since=COALESCE(pending_since,?),"
                        " removed=? WHERE url=?", (version, stamp, removed, url))

    def indexnow_batch(self, endpoint: str, run_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM indexnow_batches WHERE endpoint=? AND run_key=?",
            (endpoint, run_key)).fetchone()
        return dict(row) if row else None

    def indexnow_claim(self, endpoint: str, run_key: str, versions: dict[str, str],
                       now: datetime, retries: dict[str, str]) -> bool:
        """Freeze one exact POST per endpoint/run, including a retry reservation
        in case the runner stops without recording the response."""
        with self.conn:
            result = self.conn.execute(
                "INSERT OR IGNORE INTO indexnow_batches(endpoint,run_key,versions,started_at) VALUES (?,?,?,?)",
                (endpoint, run_key, json.dumps(versions, sort_keys=True), iso(now)))
            if result.rowcount != 1:
                return False
            for url, version in versions.items():
                self.conn.execute(
                    "UPDATE indexnow_pages SET attempts=attempts+1,retry_at=?"
                    " WHERE url=? AND content_hash=? AND pending_since IS NOT NULL",
                    (retries[url], url, version))
        return True

    def indexnow_finish(self, endpoint: str, run_key: str, now: datetime,
                        status: int | None, retries: dict[str, str] | None = None) -> None:
        """Only the frozen batch's versions can acquire a receipt. Finishing
        twice is a no-op, including when newer content was discovered meanwhile."""
        batch = self.indexnow_batch(endpoint, run_key)
        if not batch or batch["status"] != "sending":
            return
        accepted = status in (200, 202)
        with self.conn:
            self.conn.execute(
                "UPDATE indexnow_batches SET status=?,http_status=? WHERE endpoint=? AND run_key=?",
                ("received" if accepted else "failed", status, endpoint, run_key))
            for url, version in json.loads(batch["versions"]).items():
                if accepted:
                    self.conn.execute(
                        "UPDATE indexnow_pages SET received_hash=?,received_at=?,key_pending=?,"
                        " pending_since=NULL,attempts=0,failed=0,retry_at=NULL WHERE url=? AND content_hash=?",
                        (version, iso(now), int(status == 202), url, version))
                else:
                    self.conn.execute(
                        "UPDATE indexnow_pages SET failed=1,retry_at=COALESCE(?,retry_at)"
                        " WHERE url=? AND content_hash=? AND pending_since IS NOT NULL",
                        ((retries or {}).get(url), url, version))

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
              deals_days: int = 14, post_parses_days: int = 2,
              llm_calls_days: int = 35, run_stats_days: int = 60,
              http_cache_days: int = 30, outgoing_days: int = 60,
              indexnow_batch_versions_days: int | None = None,
              appeal_scores_days: int | None = None, appeal_scores_ttl_h: float = 72,
              state_retention: dict | None = None,
              incremental_cache_enabled: bool = False, run_key: str | None = None,
              cache_max_examined_per_run: int = 256, cache_budget_s: float = 0.25,
              fast_lane: bool = False, deadline: float | None = None) -> dict[str, Any] | None:
        if incremental_cache_enabled and run_key is None:
            raise ValueError("incremental cache maintenance requires a run key")
        if state_retention is not None and (run_key is None or not self.conn.execute(
                "SELECT 1 FROM run_stats WHERE run_key=?", (run_key,)).fetchone()):
            raise ValueError("state maintenance requires an existing run")
        for days in (indexnow_batch_versions_days, appeal_scores_days):
            if days is not None and (type(days) is not int or not 1 <= days <= 3650):
                raise ValueError("optional state retention must be an integer in 1..3650 or None")
        if indexnow_batch_versions_days is not None:
            # Completed batches only need their permanent endpoint/run/status
            # anchor. Sending batches still need every version to finish safely.
            self.conn.execute(
                "UPDATE indexnow_batches SET versions='{}' WHERE status IN ('received','failed')"
                " AND started_at < ? AND versions!='{}'",
                (iso(now - timedelta(days=indexnow_batch_versions_days)),))
        if appeal_scores_days is not None:
            # The reader's configured TTL wins over a shorter retention request.
            # Run claims and the spend ledger are separate and remain untouched.
            self.conn.execute("DELETE FROM appeal_scores WHERE rated_at < ?",
                              (iso(now - max(timedelta(days=appeal_scores_days),
                                             timedelta(hours=appeal_scores_ttl_h))),))
        for table in (("accuracy_samples",) if state_retention is None else ()) + (
                "accuracy_extra_runs", "accuracy_extra_reads"):
            self.conn.execute(f"DELETE FROM {table} WHERE checked_at < ?",
                              (iso(now - timedelta(days=35)),))
        # Own checks serve the existing 90-day history window. Post observations
        # retain their lifetime facts; frequent API checks must not grow forever.
        self.conn.execute("DELETE FROM price_history WHERE content_hash GLOB 'check:*' AND observed_at < ?",
                          (iso(now - timedelta(days=90)),))
        if state_retention is None:
            self.conn.execute(
                "DELETE FROM posts_seen WHERE first_seen_at < ?",
                (iso(now - timedelta(days=posts_seen_days)),))
            self.conn.execute(
                "DELETE FROM source_audit_posts WHERE first_seen_at < ?",
                (iso(now - timedelta(days=posts_seen_days)),))
        self.conn.execute(
            "DELETE FROM post_parses WHERE parsed_at < ?",
            (iso(now - timedelta(days=post_parses_days)),))
        # Only DEAD deals are pruned, deals_days after they died. A live deal
        # is never removed for its age (operator call, 2026-10-11): Amazon
        # "used - like new" offers stay up until someone buys them, so a
        # deal leaves the wall only when it is genuinely over (its expiry
        # passed, its post says so, or a live price check found it gone).
        if state_retention is None:
            self.conn.execute(
                "DELETE FROM deals WHERE status = 'dead' AND"
                " COALESCE(NULLIF(json_extract(payload, '$.died_at'), ''), last_seen_at) < ?",
                (iso(now - timedelta(days=deals_days)),))
        # Both call timestamps and every per-day reservation must be old.
        # Even a zero/negative retention cannot erase today's or tomorrow's
        # cap; reserved calls and their reservations are never removed.
        today = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = min(iso(now - timedelta(days=llm_calls_days)), iso(today))
        old_calls = (
            "SELECT call_key FROM llm_calls WHERE status IN ('settled','released','uncertain')"
            " AND started_at < ? AND COALESCE(billed_at,started_at) < ?"
            " AND NOT EXISTS (SELECT 1 FROM llm_reservations r"
            " WHERE r.call_key=llm_calls.call_key AND r.day >= ?)")
        params = (cutoff, cutoff, cutoff[:10])
        self.conn.execute("DELETE FROM llm_reservations WHERE call_key IN (" + old_calls + ")", params)
        self.conn.execute("DELETE FROM llm_calls WHERE call_key IN (" + old_calls + ")", params)
        self.conn.execute("DELETE FROM second_opinion_yield WHERE observed_at < ?", (cutoff,))
        self.conn.execute("DELETE FROM critic_tier_batches WHERE day < ?", (cutoff[:10],))
        self.conn.execute("DELETE FROM run_stats WHERE started_at < ?",
                          (iso(now - timedelta(days=run_stats_days)),))
        self.conn.execute("DELETE FROM meta WHERE k GLOB 'ai_take_run:*' AND v < ?",
                          (iso(now - timedelta(days=min(30, run_stats_days))),))
        self.conn.execute("DELETE FROM meta WHERE k GLOB 'ai_take_publish:*'")
        # Retire the old per-facts hashed keys (prompt v1); current caches are
        # one row per page, with a bounded history inside that row.
        self.conn.execute("DELETE FROM meta WHERE k GLOB 'ai_take:*' AND CASE WHEN json_valid(v)"
                          " THEN k != 'ai_take:' || COALESCE(json_extract(v,'$.key'),'') ELSE 1 END")
        if state_retention is None:
            self.conn.execute("DELETE FROM keepa_recheck_history WHERE deal_key NOT IN"
                              " (SELECT dedupe_key FROM deals)")
        self.conn.execute("DELETE FROM keepa_recheck_yield WHERE day < ?",
                          (iso(now - timedelta(days=run_stats_days))[:10],))
        yield_cutoff = iso(now - timedelta(days=run_stats_days))
        self.conn.execute("DELETE FROM efficiency_runs WHERE started_at < ?", (yield_cutoff,))
        if self.meta_get("efficiency_since"):
            self.meta_set("efficiency_retained_since", max(
                self.meta_get("efficiency_retained_since") or "", yield_cutoff))
        for table, stamp in (("legitimacy_audits", "observed_at"),
                             ("legitimacy_batches", "observed_at"),
                             ("ai_yield_events", "observed_at"),
                             ("ai_yield_runs", "started_at"),
                             ("ai_prefilter_posts", "first_seen_at")):
            self.conn.execute(f"DELETE FROM {table} WHERE {stamp} < ?", (yield_cutoff,))
        previous = self.meta_get("ai_yield_retained_since") or ""
        if self.meta_get("ai_yield_since") and yield_cutoff > previous:
            self.meta_set("ai_yield_retained_since", yield_cutoff)
        if not incremental_cache_enabled:
            self.conn.execute("DELETE FROM http_cache WHERE fetched_at < ?",
                              (iso(now - timedelta(days=http_cache_days)),))
        # Only failed attempts expire. A `sent` row is the record that a live
        # deal (which can stay up for months) was already posted; dropping it
        # could let a channel post the same deal again.
        self.conn.execute(
            "DELETE FROM outgoing WHERE status = 'rejected'"
            " AND COALESCE(finished_at,started_at) < ?",
            (iso(now - timedelta(days=outgoing_days)),))
        self.conn.commit()

        maintenance = None
        if state_retention is not None:
            from .state_retention import maintain
            state_result = maintain(self, now, run_key=run_key, options=state_retention,
                                    posts_seen_days=posts_seen_days, deals_days=deals_days,
                                    deadline=deadline)
            log.info("state maintenance: %s", state_result)
        if incremental_cache_enabled:
            maintenance = self.prune_http_cache(
                now, run_key=run_key, days=http_cache_days,
                max_examined_per_run=cache_max_examined_per_run, budget_s=cache_budget_s,
                deadline=deadline)
            maintenance["compaction_deferred"] = fast_lane

        pages = self.conn.execute("PRAGMA page_count").fetchone()[0]
        free = self.conn.execute("PRAGMA freelist_count").fetchone()[0]
        if free * 10 > pages and not ((incremental_cache_enabled or state_retention is not None) and fast_lane):
            try:
                busy, _, _ = self.conn.execute("PRAGMA wal_checkpoint(FULL)").fetchone()
                if busy:
                    raise sqlite3.OperationalError("WAL checkpoint busy")
                self.conn.execute("VACUUM")
            except sqlite3.OperationalError as e:
                if maintenance is not None:
                    maintenance["compaction_deferred"] = True
                # Compaction is an optimisation: never fail the run for it.
                log.warning("state compaction deferred (%s): pages=%d, free=%d", e, pages, free)
            else:
                after = self.conn.execute("PRAGMA page_count").fetchone()[0]
                after_free = self.conn.execute("PRAGMA freelist_count").fetchone()[0]
                log.info("state compacted: pages=%d->%d, free=%d->%d", pages, after, free, after_free)
        counts = [self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("price_history", "alerts_sent")]
        log.info("state rows: price_history=%d alerts_sent=%d", *counts)
        if maintenance is not None:
            log.info("cache maintenance: %s", maintenance)
        return maintenance
