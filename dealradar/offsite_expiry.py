"""Optional off-site expiry: render facts, lifetime anchors and count-only logs."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import timedelta
from urllib.parse import urlsplit

from .util import iso

log = logging.getLogger(__name__)


def validate_days(days):
    if days is not None and (type(days) is not int or not 1 <= days <= 3650):
        raise ValueError("retention.offsite_unseen_days must be an integer in 1..3650 or null")


def protected_keys(db, rows, products):
    """Use the render's selected objects, never duplicate publication filters.

    The full wall also feeds saved deals, hubs, the static mirror, finder and
    tool pages; checker and comparisons use subsets of these rows/products.
    Keep remembered offers behind outstanding live IndexNow URLs too, even
    when this render has stopped writing that URL. Receipts are never erased.
    """
    keys = {d["dedupe_key"] for d in rows}
    keys.update(k for page in products.values() for k in page["offers"])
    live_paths = {urlsplit(r["url"]).path for r in db.conn.execute(
        "SELECT url FROM indexnow_pages WHERE removed=0")}
    hashes = {m[1] for path in live_paths
              if (m := re.fullmatch(r"/deal/[a-z0-9-]+-([0-9a-f]{8})/", path))}
    if hashes:
        keys.update(r[0] for r in db.conn.execute("SELECT dedupe_key FROM deals")
                    if hashlib.sha256(r[0].encode()).hexdigest()[:8] in hashes)
    from .publish.product_pages import META
    state = json.loads(db.meta_get(META) or '{"pages":{}}')
    known_products = {page["path"] for page in state["pages"].values()} | {
        page["path"] for page in products.values()}
    if any(path.startswith("/p/") and path != "/p/" and path not in known_products for path in live_paths):
        # A receipt without its offer mapping cannot establish which rows are
        # absent from that URL. Wait for the receipt to be retired/reconciled.
        return None
    keys.update(k for page in state["pages"].values() if page["path"] in live_paths
                for k in page["offers"])
    return keys


def retire(db, row):
    """In the caller's chunk transaction; sightings cascade with the deal.

    Product identities/offers, price evidence, click/accuracy history and
    send/run receipts have independent lifetimes, just as for dead pruning.
    Preserve the lifetime yield fields formerly anchored on the inventory row.
    """
    db.conn.execute("CREATE TABLE IF NOT EXISTS offsite_deal_anchors ("
                    "dedupe_key TEXT PRIMARY KEY, ai_public_at TEXT, ai_public_price REAL) WITHOUT ROWID")
    db.conn.execute("INSERT INTO offsite_deal_anchors VALUES (?,?,?) ON CONFLICT(dedupe_key)"
                    " DO UPDATE SET ai_public_at=COALESCE(excluded.ai_public_at,ai_public_at),"
                    " ai_public_price=COALESCE(excluded.ai_public_price,ai_public_price)",
                    (row["dedupe_key"], row["ai_public_at"], row["ai_public_price"]))
    db.conn.execute("DELETE FROM keepa_recheck_history WHERE deal_key=?", (row["dedupe_key"],))


def restore(db, key):
    # Lazy schema leaves the OFF database and writer bytes unchanged.
    if db.conn.execute("SELECT 1 FROM sqlite_master WHERE name='offsite_deal_anchors'").fetchone():
        db.conn.execute("UPDATE deals SET (ai_public_at,ai_public_price)="
                        " (SELECT ai_public_at,ai_public_price FROM offsite_deal_anchors WHERE dedupe_key=?)"
                        " WHERE dedupe_key=? AND EXISTS"
                        " (SELECT 1 FROM offsite_deal_anchors WHERE dedupe_key=?)", (key, key, key))


def status_group(status):
    return "alive" if status == "alive" else "other_non_dead"


def report(db, now, days, keys, run_key):
    """SQL counts only; the inventory sweep's row/time bounds govern mutations."""
    expired = {s: 0 for s in ("alive", "other_non_dead")}
    state = json.loads(db.conn.execute("SELECT state_maintenance FROM run_stats WHERE run_key=?",
                                       (run_key,)).fetchone()[0])
    expired.update(state.get("deals", {}).get("offsite_expired", {}))
    protected, left, unproven = (dict.fromkeys(expired, 0) for _ in range(3))
    # TEMP state never rides the encrypted DB, and also avoids SQL variable limits.
    db.conn.execute("CREATE TEMP TABLE IF NOT EXISTS offsite_protected (dedupe_key TEXT PRIMARY KEY) WITHOUT ROWID")
    db.conn.execute("DELETE FROM offsite_protected")
    db.conn.executemany("INSERT INTO offsite_protected VALUES (?)", ((k,) for k in keys or ()))
    for row in db.conn.execute(
            "SELECT CASE WHEN d.status='alive' THEN 'alive' ELSE 'other_non_dead' END AS status,"
            " p.dedupe_key IS NOT NULL AS protected,"
            " julianday(d.last_seen_at)<julianday(?) AS stale,COUNT(*) AS n"
            " FROM deals d LEFT JOIN offsite_protected p USING(dedupe_key) WHERE d.status!='dead'"
            " GROUP BY 1,2,3", (iso(now - timedelta(days=days)),)):
        if row["protected"]:
            protected[row["status"]] += row["n"]
        elif row["stale"]:
            (unproven if keys is None else left)[row["status"]] += row["n"]
    db.conn.commit()
    log.info("offsite expiry: expired=%s protected=%s left_by_bound=%s unproven=%s",
             expired, protected, left, unproven)
