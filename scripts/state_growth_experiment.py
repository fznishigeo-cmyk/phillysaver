"""337 offline continuation of the largest 296c fixture; no production state.

python -m scripts.state_growth_experiment
All SQLite and encryption scratch is removed automatically. Prints content-free
JSON measurements, not facts, hashes, URLs, credentials or production estimates.
"""

import json
import tempfile
from datetime import timedelta
from pathlib import Path

from dealradar import accuracy, clicks, state_retention
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.util import iso
from scripts.state_growth import growers, snapshot
from scripts.state_size_experiment import measure
from scripts.state_write_experiment import (LIVE, RECHECKS, START, digest, inventory, rechecks,
                                            retained_receipts, saturate)

NOW = START + timedelta(days=9)
POSTS_PER_HOUR = 623  # 448,000 receipts / 30 days, rounded up (296c stress corpus).
AUDITS_PER_HOUR = 565  # 122,000 / 9 days, rounded up; not a production measurement.
API_REQUESTS_PER_HOUR = 40  # Explicit scenario input, not observed traffic.


def receipts(db, at, label, *, compact_posts=False):
    with db.conn:
        for i in range(POSTS_PER_HOUR):
            receipt = digest(f"{label}:{i}")
            if compact_posts:
                from dealradar.state_growers import cold_post
                high=max(int(db.meta_get("post_id_high_water") or 0),db.conn.execute("SELECT COALESCE(MAX(id),0) FROM posts_seen").fetchone()[0])
                cold_post(db,dict(id=high+1,source_name=f"feed-{i%40}",uid=f"{label}-{i}",url=f"https://feed-{i%40}.example/deals/synthetic-product-{label}-{i}",content_hash=receipt,published_at=iso(at),first_seen_at=iso(at)))
            else:
                db.conn.execute("INSERT INTO posts_seen(source_name,uid,url,content_hash,published_at,first_seen_at)"
                                " VALUES (?,?,?,?,?,?)", (f"feed-{i % 40}", f"{label}-{i}",
                                f"https://feed-{i % 40}.example/deals/synthetic-product-{label}-{i}",
                                receipt, iso(at), iso(at)))
            if i < AUDITS_PER_HOUR:
                db.conn.execute("INSERT INTO source_audit_posts(source_name,content_hash,first_seen_at,parsed_at,"
                                "extracted,evidence) VALUES (?,?,?,?,?,?)",
                                (f"feed-{i % 40}", receipt, iso(at), iso(at), i % 5, i % 3))
        for i in range(API_REQUESTS_PER_HOUR):
            # Opaque API request identity, exactly the persisted validator shape.
            db.conn.execute("INSERT INTO http_cache VALUES (?,'','',?)",
                            ("api:" + digest(f"{label}:request:{i}"), iso(at)))


def maintenance(db, cfg, now, key):
    db.start_run(key, "hourly", now)
    db.prune(now, run_key=key, state_retention=state_retention.options(cfg),
             indexnow_batch_versions_days=cfg.indexnow_batch_versions_days,
             fast_lane=True)  # Measure explicit VACUUM separately below.


def drain(db, cfg, now, prefix):
    # Repeated real bounded maintenance calls; same clock, distinct run keys.
    # Keep until a complete receipt-details sweep has finished, not a guessed
    # number of calls. Each call keeps the shipped per-table row/time limits.
    begun = False
    for n in range(1000):
        key = f"{prefix}:{n}"
        maintenance(db, cfg, now, key)
        state = json.loads(db.conn.execute("SELECT state_maintenance FROM run_stats WHERE run_key=?", (key,)).fetchone()[0])
        begun |= "posts_seen_details" in state
        cursor = json.loads(db.meta_get("state_sweep:posts_seen_details") or "null")
        if begun and cursor is None:
            return n + 1
    raise RuntimeError("fixture maintenance failed to finish its bounded sweep")


def main():
    cfg = load_config()
    with tempfile.TemporaryDirectory(prefix="task337-model-", dir="/tmp") as scratch:
        directory = Path(scratch)
        db = DB(directory / "state.db")
        try:
            db.compact_deal_payloads = True
            rows = inventory(db)
            clicks.observe_publication(db, cfg, START - timedelta(minutes=15), rows, run_key="seed")
            # Seed the first historical day in one transaction. Actual current
            # writers are exercised in the measured hourly/day continuations.
            for hour in range(24):
                at = START + timedelta(hours=hour)
                with db.conn:
                    for i in range(RECHECKS):
                        row = rows[(i + int(at.timestamp() // 3600) * 79) % LIVE]
                        accuracy.store_compact(db, row["dedupe_key"], iso(at) + "-hourly", at,
                            at - timedelta(seconds=i % 60), db._accuracy_published[row["dedupe_key"]]["bucket"],
                            "keepa", "higher" if i % 19 == 0 else "lower" if i % 29 == 0 else "match")
            saturate(db, cfg, rows)
            # saturate seeds exposure on days 1..7; add day 8 to fill exactly
            # the eight-day learner window immediately before NOW.
            with db.conn:
                db.conn.executemany("INSERT INTO deal_click_days VALUES (?,?,?)",
                    [(row["key"], int((START + timedelta(days=8)).timestamp() // 86400),
                      clicks.HOURS.pack(*([3600.] * 24))) for row in rows])
            cohort = retained_receipts(db)
            # Spread receipts across the FULL retained windows. The earlier
            # helper's i-seconds timestamps span only 5.2/1.4 days, despite
            # sizing its tables for 30/9 days. Do not mistake catch-up cleanup
            # of that young corpus for steady-state shrinkage.
            with db.conn:
                for table, days in (("posts_seen", 30), ("source_audit_posts", 9)):
                    db.conn.execute(f"UPDATE {table} SET first_seen_at="
                        "strftime('%Y-%m-%dT%H:%M:%S',?, '-' || CAST((rowid-1)*?/ ? AS INTEGER) || ' seconds')||'+00:00'",
                        (iso(NOW), days * 86400, cohort[table]["rows"]))
                db.conn.execute("UPDATE source_audit_posts SET parsed_at=first_seen_at")
            print(json.dumps({"phase": "built", "cohorts": cohort}), flush=True)
            db.conn.execute("VACUUM")
            before_sizes = measure(db, directory)
            before = snapshot(db.path)
            # Isolate one warm hourly run: repeated deal upserts/observations,
            # five publications, new receipts; fixed live inventory, no deaths.
            receipts(db, NOW, "measured-hour")
            rechecks(db, cfg, rows, NOW)
            for minute in (0, 1, 16, 31, 46):
                at = NOW + timedelta(minutes=minute)
                clicks.observe_publication(db, cfg, at, rows, run_key=iso(at))
            hour_sizes = measure(db, directory)
            hourly = growers(before, snapshot(db.path), 1)
            print(json.dumps({"phase": "hour", "top_growers": hourly[:5],
                              "enc_growth": hour_sizes["enc_bytes"] - before_sizes["enc_bytes"]}), flush=True)
            # Untouched protected facts are compared after every row sweep.
            protected = {t: db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                         for t in ("deals", "sightings", "price_history", "alerts_sent")}
            calls = drain(db, cfg, NOW, "initial")
            unvacuumed_sizes = measure(db, directory)
            db.conn.execute("VACUUM")
            after_sizes = measure(db, directory)
            after = snapshot(db.path)
            print(json.dumps({"phase": "pruned", "calls": calls,
                              "before": before_sizes, "after": after_sizes,
                              "before_vacuum": unvacuumed_sizes,
                              "table_changes": growers(before, after, 1)}), flush=True)
            # Twenty-four more hours rotate time-based retention through a
            # saturated corpus. Posts/audits and accuracy use the same arrival
            # rates as above; each day adds all LIVE one-per-day price checks.
            for hour in range(1, 25):
                at = NOW + timedelta(hours=hour)
                receipts(db, at, f"day-hour-{hour}")
                rechecks(db, cfg, rows, at)
                for minute in (0, 1, 16, 31, 46):
                    stamp = at + timedelta(minutes=minute)
                    clicks.observe_publication(db, cfg, stamp, rows, run_key=iso(stamp))
                    maintenance(db, cfg, stamp, "day:" + iso(stamp))
                with db.conn:
                    # Measured protected growth, never pruned by task 337.
                    for i in range((hour - 1) * LIVE // 24, hour * LIVE // 24):
                        db.conn.execute("INSERT INTO price_history(canonical_key,store,price,observed_at,content_hash)"
                                        " VALUES (?,?,?,?,?)", (rows[i]["dedupe_key"], "Amazon", rows[i]["final_price"],
                                        iso(at), "check:fixture:" + iso(at)[:10]))
            extra_calls = drain(db, cfg, NOW + timedelta(days=1), "final")
            db.conn.execute("VACUUM")
            final_sizes = measure(db, directory)
            assert {t: db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in protected if t != "price_history"} == {t: n for t, n in protected.items() if t != "price_history"}
            assert db.conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == protected["price_history"] + LIVE
            assert db.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            print(json.dumps({"phase": "day", "final_drain_calls": extra_calls, "final": final_sizes,
                              "enc_bytes_per_hour": (final_sizes["enc_bytes"] - after_sizes["enc_bytes"]) / 24,
                              "with_10mb_allowance": final_sizes["enc_bytes"] + 10_000_000,
                              "tables": after["tables"],
                              "day_growers": growers(after, snapshot(db.path), 24)}), flush=True)
        finally:
            db.close()


if __name__ == "__main__":
    main()
