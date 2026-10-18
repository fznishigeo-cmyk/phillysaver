"""296c offline production-shaped write/size experiment; no production state.

Run: .venv/bin/python -m scripts.state_write_experiment
All synthetic databases/encrypted files live in owned /tmp/task296c-* scratch.
"""

import hashlib
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dealradar import accuracy, clicks
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.util import iso
from scripts.state_size_experiment import measure

START = datetime(2026, 10, 1, tzinfo=timezone.utc)
LIVE = 3300
RECHECKS = 2000
HOURS = 24


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def inventory(db):
    at = iso(START - timedelta(days=30))
    rows = []
    for i in range(LIVE):
        url = f"https://www.amazon.com/dp/B{i:09d}"
        key = "url|" + url
        payload = {"asin": f"B{i:09d}", "conditions": [], "regular_price": 100 + i % 200,
                   "category": "home", "evidence": {"final_price_quote": f"${25 + i % 100}.00"},
                   "price_published_at": at, "price_rechecked_at": at,
                   "critic": {"plausible": True, "reason": "The stated current price and reference agree."},
                   "flags": [], "codes": [], "stack_steps": []}
        db.conn.execute("INSERT INTO deals(dedupe_key,store,item,normalized_item,canonical_url,payload,"
                        "final_price,pct_at_checkout,abs_savings,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (key, "Amazon", f"Synthetic home widget {i}", f"synthetic home widget {i}", url,
                         json.dumps(payload), 25 + i % 100, 50, 25 + i % 100, at, at))
        deal_id = db.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for j in range(20):
            receipt = digest(f"sighting:{i}:{j}")
            db.conn.execute("INSERT INTO sightings(deal_id,source_name,source_url,content_hash,seen_at,final_price)"
                            " VALUES (?,?,?,?,?,?)", (deal_id, f"feed-{j % 7}",
                            f"https://feed.example/synthetic-widget-{i}-{j}", receipt, at, 25 + i % 100))
            db.conn.execute("INSERT INTO price_history(canonical_key,store,price,observed_at,content_hash)"
                            " VALUES (?,?,?,?,?)", (key, "Amazon", 25 + i % 100, at, receipt))
        rows.append({"key": digest(key)[:16], "dedupe_key": key, "status": "alive",
                     "final_price": 25 + i % 100, "store": "Amazon"})
    db.conn.commit()
    for deal in db.all_deals():
        deal["sightings"] = db.sightings_for(deal["id"])
        db.update_deal(deal["dedupe_key"], payload=deal)
    db._accuracy_published = {row["dedupe_key"]: {
        "at": at, "created_run": "seed", "final": row["final_price"], "store": "Amazon", "checkout": False,
        "bucket": "top30" if i < 30 else "top120" if i < 120 else "rest"} for i, row in enumerate(rows)}
    return rows


def rechecks(db, cfg, rows, at):
    key = iso(at) + "-hourly"
    payload_bytes = 0
    for i in range(RECHECKS):
        row = rows[(i + int(at.timestamp() // 3600) * 79) % LIVE]
        price = row["final_price"] + (1 if i % 19 == 0 else -1 if i % 29 == 0 else 0)
        observed = at - timedelta(seconds=i % 60)
        accuracy.record(db, cfg, row["dedupe_key"], at, source="keepa", price=price,
                        observed=observed, run_key=key)
        deal = db.get_deal(row["dedupe_key"])
        deal["price_rechecked_at"] = iso(at)
        db.update_deal(row["dedupe_key"], payload=deal)
        payload_bytes += len(db.conn.execute("SELECT payload FROM deals WHERE dedupe_key=?",
                                            (row["dedupe_key"],)).fetchone()[0].encode())
    return payload_bytes


def table_bytes(db, name):
    return db.conn.execute("SELECT SUM(s.pgsize) FROM dbstat s JOIN sqlite_schema m ON m.name=s.name"
                           " WHERE m.tbl_name=?", (name,)).fetchone()[0] or 0


def retained_receipts(db):
    # Match the 296b observed raw-rate x retained-window totals (including
    # indexes), using facts, URLs and deterministic, noncompressible hashes.
    targets = {"posts_seen": 159252480, "source_audit_posts": 37158912}
    counts = {}
    for table, target in targets.items():
        i = 0
        while table_bytes(db, table) < target:
            with db.conn:
                for _ in range(2000):
                    at = iso(START - timedelta(seconds=i % ((30 if table == "posts_seen" else 9) * 86400)))
                    receipt = digest(f"receipt-{i}")
                    source = f"feed-{i % 40}"
                    if table == "posts_seen":
                        db.conn.execute("INSERT INTO posts_seen(source_name,uid,url,content_hash,published_at,first_seen_at)"
                                        " VALUES (?,?,?,?,?,?)", (source, str(i),
                                        f"https://feed-{i % 40}.example/deals/synthetic-product-{i}", receipt, at, at))
                    else:
                        db.conn.execute("INSERT INTO source_audit_posts(source_name,content_hash,first_seen_at,parsed_at,"
                                        "extracted,evidence) VALUES (?,?,?,?,?,?)", (source, receipt, at, at, i % 5, i % 3))
                    i += 1
        counts[table] = {"rows": i, "raw_bytes": table_bytes(db, table)}
    return counts


def saturate(db, cfg, rows):
    # Add eight more days of hourly receipts and seven days of exposure. The
    # first day's samples/exposure were generated by the real public writers.
    for day in range(1, 9):
        with db.conn:
            for hour in range(24):
                at = START + timedelta(days=day, hours=hour)
                run = iso(at) + "-hourly"
                for i in range(RECHECKS):
                    row = rows[(i + int(at.timestamp() // 3600) * 79) % LIVE]
                    outcome = "higher" if i % 19 == 0 else "lower" if i % 29 == 0 else "match"
                    bucket = db._accuracy_published[row["dedupe_key"]]["bucket"]
                    observed = at - timedelta(seconds=i % 60)
                    if cfg.compact_accuracy_samples:
                        accuracy.store_compact(db, row["dedupe_key"], run, at, observed, bucket, "keepa", outcome)
                    else:
                        db.conn.execute("INSERT INTO accuracy_samples VALUES (?,?,?,?,?,?,?,?)",
                                        (row["dedupe_key"], run, iso(at), iso(observed), bucket, "Amazon", "keepa", outcome))
            if day < 8:
                day_number = int((START + timedelta(days=day)).timestamp() // 86400)
                for row in rows:
                    if cfg.compact_click_exposure:
                        db.conn.execute("INSERT INTO deal_click_days VALUES (?,?,?)",
                                        (row["key"], day_number, clicks.HOURS.pack(*([3600.] * 24))))
                    else:
                        db.conn.executemany("INSERT INTO deal_click_shown VALUES (?,?,3600,1)",
                                            [(row["key"], day_number * 24 + h) for h in range(24)])
    # A fixed live set's Keepa table saturates at 12 facts per deal, not the
    # unbounded extrapolation of the observed 20-minute growth burst.
    with db.conn:
        for i, row in enumerate(rows):
            samples = []
            for h in range(12):
                at = iso(START + timedelta(days=8, hours=h))
                samples.append(dict(requested_at=at, run_key=at + "-hourly", answered=True, observed_at=at,
                    shown_price=row["final_price"], live_price=row["final_price"], reading=True, changed=False,
                    death=False, tokens=4, request_tokens=4, estimated_tokens=0, live=True, used=False,
                    regular_price=100 + i % 200, last_change_at="", stable_since=iso(START), price_changed_at="",
                    observation=row["dedupe_key"] + "/" + at,
                    counted={"readings": 1, "changed": 0, "deaths": 0, "useful": 0}))
            db.conn.execute("INSERT INTO keepa_recheck_history VALUES (?,?)",
                            (row["dedupe_key"], json.dumps(samples, separators=(",", ":"))))


def main():
    output = {}
    with tempfile.TemporaryDirectory(prefix="task296c-model-", dir="/tmp") as scratch:
        directory = Path(scratch)
        for compact in (False, True):
            mode = "after" if compact else "before"
            cfg = load_config()
            cfg.compact_accuracy_samples = cfg.compact_click_exposure = compact
            db = DB(directory / (mode + ".db"))
            try:
                db.compact_deal_payloads = compact
                rows = inventory(db)
                clicks.observe_publication(db, cfg, START - timedelta(minutes=15), rows, run_key="seed")
                baseline = measure(db, directory)
                measurements = {"baseline": baseline}
                for hour in range(HOURS):
                    at = START + timedelta(hours=hour)
                    written = rechecks(db, cfg, rows, at)
                    clicks.observe_publication(db, cfg, at, rows, run_key=iso(at) + "-hourly")
                    if hour == 0:
                        measurements["hourly"] = measure(db, directory)
                        measurements["upsert_payload_bytes"] = written
                    for minute in (1, 16, 31, 46):
                        stamp = at + timedelta(minutes=minute)
                        clicks.observe_publication(db, cfg, stamp, rows, run_key=iso(stamp) + "-fast")
                        if hour == 0 and minute == 1:
                            measurements["fast"] = measure(db, directory)
                measurements["one_day"] = measure(db, directory)
                measurements["accuracy_rows"] = db.conn.execute("SELECT COUNT(*) FROM " + (
                    "accuracy_samples_compact" if compact else "accuracy_samples")).fetchone()[0]
                saturate(db, cfg, rows)
                measurements["retained_receipts"] = retained_receipts(db)
                db.conn.execute("VACUUM")
                measurements["steady"] = measure(db, directory)
                # Explicit allowance for the other production tables, dead
                # inventory and retained partial-day/budget-drain slack.
                measurements["steady_with_10mb_reserve"] = measurements["steady"]["enc_bytes"] + 10_000_000
                output[mode] = measurements
                print(json.dumps({mode: measurements}, sort_keys=True), flush=True)
            finally:
                db.close()
        delta = {mode: {name: {kind: values[name][kind] - values[base][kind]
                              for kind in ("db_bytes", "enc_bytes")}
                       for name, base in (("hourly", "baseline"), ("fast", "hourly"), ("one_day", "baseline"))}
                 for mode, values in output.items()}
        print(json.dumps({"growth_bytes": delta}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
