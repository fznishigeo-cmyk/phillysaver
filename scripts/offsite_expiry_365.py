"""Offline synthetic expiry measurement. Run: python -m scripts.offsite_expiry_365.

Uses only a TemporaryDirectory, SQLite and deterministic invented product facts.
No live snapshot, network, credentials, or state.db in the checkout is needed.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dealradar import offsite_expiry, state_retention
from dealradar.config import AppConfig
from dealradar.db import DB
from dealradar.util import iso

NOW = datetime(2026, 10, 21, 8, 58, tzinfo=timezone.utc)
ALIVE = 30_692
DEAD = 3_227
SIGHTINGS = 52_700


def seed(db):
    rng = random.Random(365)
    ages = ([rng.uniform(.01, .99) for _ in range(4736)]
            + [rng.uniform(1.001, 6.999) for _ in range(18699)]
            + [rng.uniform(7.001, 11.999) for _ in range(7257)])
    rng.shuffle(ages)
    public_ids = set(rng.sample(range(ALIVE), 1613))
    second_sightings = set(rng.sample(range(ALIVE + DEAD), SIGHTINGS - ALIVE - DEAD))
    public = []
    for i in range(ALIVE + DEAD):
        digest = hashlib.sha256(f"synthetic-365-{i}".encode()).hexdigest()
        asin = "B" + digest[:9].upper()
        store = ("Amazon", "Amazon", "Amazon", "Amazon", "Amazon", "Amazon",
                 "Walmart", "Target", "Woot", "Best Buy")[i % 10]
        key = f"url|https://{store.lower().replace(' ', '')}.example/product/{digest[:20]}"
        status = "alive" if i < ALIVE else "dead"
        age = ages[i] if i < ALIVE else rng.uniform(1, 11.999)
        at = iso(NOW - timedelta(days=age))
        title = f"Synthetic {('kitchen mixer', 'headphones', 'running shoes', 'cotton shirt')[i % 4]} {digest[:8]}"
        price = (999 + i % 49000) / 100
        payload = dict(asin=asin if store == "Amazon" else "", brand=f"Fixture {digest[8:12]}",
                       category=("kitchen", "electronics", "fashion", "fashion")[i % 4],
                       conditions=[], codes=[], stack_steps=[], rebates=[], flags=[],
                       currency="USD", region="US", regular_price=round(price * 4, 2),
                       basis="quoted_regular", image_url=f"https://images.example/{digest[:32]}.jpg",
                       price_rechecked_at=at, price_published_at=at, price_since=at,
                       link_as_posted=key[4:], confidence=.97,
                       critic={"pass": True, "reason": "Fixture quote matches the stated product and current price."},
                       _sightings_in_table=True)
        # Invented extracted facts only: tune average compact payload to the
        # reported 31.7 MB / 30,692 alive rows, without incompressible padding.
        payload["quantity_or_unit"] = "one item"
        payload["shipping_cost_or_threshold"] = "Free standard delivery on qualifying orders"
        payload["model"] = f"FIX-{digest[12:24]}"
        payload["color"] = ("black", "navy", "white", "green")[i % 4]
        payload["dimensions"] = "12 x 8 x 6 inches; manufacturer specifications"
        payload["pack_size"] = "1"
        payload["shipping_evidence"] = "Free standard delivery on orders meeting the retailer threshold; quoted delivery conditions apply."
        payload["quantity_evidence"] = "One retail package; accessories listed by the retailer are included."
        if status == "dead":
            payload.update(death="confirmed", died_at=at)
        packed = json.dumps(payload, separators=(",", ":"))
        db.conn.execute(
            "INSERT INTO deals(id,dedupe_key,store,item,normalized_item,canonical_url,payload,"
            "final_price,pct_at_checkout,abs_savings,online_only,verify_tier,status,first_seen_at,last_seen_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (i + 1, key, store, title, title.lower(), key[4:], packed, price,
             75, round(price * 3, 2), "true", "history-checked", status, at, at))
        # Durable offer links on 90%; exact identities on about 78% of those.
        if i % 10 != 9:
            product = i + 1 if i % 5 else None
            if product:
                db.conn.execute("INSERT INTO products VALUES (?,?,?,?)", (product, digest[:16], at, at))
                db.conn.execute("INSERT INTO product_keys VALUES (?,?,?)", ("asin" if store == "Amazon" else "sku:fixture", asin, product))
                db.conn.execute("UPDATE deals SET product_id=? WHERE id=?", (product, i + 1))
            db.conn.execute("INSERT INTO product_offers VALUES (?,?,?,?,?,?,?,?)",
                            (key, product, store, "new", "asin" if product else "unresolved",
                             bytes.fromhex(digest[:32]), at, at))
        for j in range(1 + (i in second_sightings)):
            content = hashlib.sha256(f"post-{i}-{j}".encode()).hexdigest()
            db.conn.execute("INSERT INTO sightings(deal_id,source_name,source_url,content_hash,seen_at,final_price)"
                            " VALUES (?,?,?,?,?,?)", (i + 1, ("feed_one", "feed_two")[j],
                            f"https://feed.example/deal/{digest[:16]}/{j}", content, at, price))
            db.conn.execute("INSERT INTO price_history(canonical_key,store,price,observed_at,content_hash)"
                            " VALUES (?,?,?,?,?)", (key, store, price, at, content))
        if store == "Amazon":
            samples = [{"at": iso(NOW - timedelta(days=age, hours=j)), "price": price,
                        "available": True, "run_key": f"fixture-{i}-{j}"} for j in range(12)]
            db.conn.execute("INSERT INTO keepa_recheck_history VALUES (?,?)",
                            (key, json.dumps(samples, separators=(",", ":"))))
            for j in range(3):
                db.conn.execute("INSERT INTO price_history(canonical_key,store,price,observed_at,content_hash)"
                                " VALUES (?,?,?,?,?)", (key, store, price,
                                iso(NOW - timedelta(days=age, hours=j)), f"check:{i}-{j}"))
        if i in public_ids:
            public.append({"dedupe_key": key})
    db.conn.commit()
    return offsite_expiry.protected_keys(db, public, {})


def measure(db):
    db.conn.commit()
    db.conn.execute("VACUUM")
    db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    data = Path(db.path).read_bytes()
    tables = ("deals", "sightings", "keepa_recheck_history", "price_history", "product_offers")
    return {"raw_bytes": len(data), "gzip_bytes": len(gzip.compress(data, compresslevel=9, mtime=0)),
            "rows": {t: db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables},
            "alive_payload_bytes": db.conn.execute("SELECT SUM(length(CAST(payload AS BLOB))) FROM deals WHERE status='alive'").fetchone()[0]}


def remaining(db, keys, days):
    return sum(r[0] not in keys for r in db.conn.execute(
        "SELECT dedupe_key FROM deals WHERE status!='dead' AND last_seen_at<?",
        (iso(NOW - timedelta(days=days)),)))


def experiment(directory, seed_db, keys, days, max_rows, budget):
    db = DB(directory / f"n{days}.db")
    seed_db.conn.backup(db.conn)
    cfg = AppConfig(state_retention_337=True, offsite_unseen_days=days,
                    state_maintenance_max_rows=max_rows, state_maintenance_budget_s=budget)
    backlog = remaining(db, keys, days)
    runs = []
    started = time.monotonic()
    while remaining(db, keys, days):
        run_key = f"expiry-{days}-{len(runs)}"
        db.start_run(run_key, "fast", NOW)
        result = state_retention.maintain(db, NOW, run_key=run_key, options=state_retention.options(cfg),
                                          posts_seen_days=30, deals_days=14, offsite_protected_keys=keys)
        counts = result["tables"].get("deals", {})
        runs.append({"examined": counts.get("examined", 0), "expired": counts.get("deleted", 0),
                     "elapsed_s": result["elapsed_s"]})
        if len(runs) > 1000:
            raise RuntimeError("synthetic backlog did not drain")
    report = {"days": days, "initial_backlog": backlog, "runs_to_drain": len(runs),
              "max_rows": max_rows, "budget_s": budget, "runs": runs,
              "elapsed_s": round(time.monotonic() - started, 3), "after": measure(db)}
    assert db.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not db.conn.execute("PRAGMA foreign_key_check").fetchall()
    db.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-rows", type=int, default=4096)
    parser.add_argument("--budget-s", type=float, default=1.0)
    args = parser.parse_args()
    state_retention.validate_limits(args.max_rows, args.budget_s)
    with tempfile.TemporaryDirectory(prefix="offsite-expiry-365-") as scratch:
        directory = Path(scratch)
        db = DB(directory / "synthetic.db")
        keys = seed(db)
        print(json.dumps({"before": measure(db), "protected": len(keys), "sqlite": sqlite3.sqlite_version}), flush=True)
        for days in (7, 3):
            print(json.dumps(experiment(directory, db, keys, days, args.max_rows, args.budget_s)), flush=True)
        db.close()


if __name__ == "__main__":
    main()
