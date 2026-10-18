"""Offline, reproducible state-size model; never opens production state.

Run from the repository root: python -m scripts.state_size_experiment
Temporary synthetic SQLite/gzip/encrypted files are removed on exit.
"""

import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dealradar.config import ROOT, load_config
from dealradar.db import DB
from dealradar.state_size import snapshot_sizes
from dealradar.util import iso

NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
DAYS = 14
BATCHES_PER_DAY = 101
URLS_PER_BATCH = 1000


def cohort(db, day):
    # Different hashes model changed page bytes; repeated URL prefixes/slugs
    # model the actual per-batch JSON. All facts are deliberately synthetic.
    for run in range(BATCHES_PER_DAY):
        key = f"day{day}-run{run}"
        versions = {
            f"https://wowpriced.com/deal/synthetic-kitchen-widget-with-accessories-for-the-home-{i:08d}/":
                hashlib.sha256(("shared-noindex-version" if i % 7 == 0 else f"{key}:{i}").encode()).hexdigest()
            for i in range(URLS_PER_BATCH)}
        db.conn.execute("INSERT INTO indexnow_batches VALUES (?,?,?,?,?,?)",
                        ("https://api.indexnow.org/indexnow", key, json.dumps(versions, sort_keys=True),
                         iso(NOW + timedelta(days=day, hours=-12)), "received", 200))
    db.conn.commit()


def protected_facts(db):
    at = iso(NOW - timedelta(days=30))
    for i in range(2000):
        key = f"widget-{i}"
        db.conn.execute(
            "INSERT INTO deals(dedupe_key,store,item,normalized_item,payload,final_price,first_seen_at,last_seen_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (key, "Synthetic Store", f"Synthetic Widget {i}", f"synthetic widget {i}",
             json.dumps({"conditions": [], "evidence": {"final_price_quote": "$10"},
                         "price_published_at": at, "final_price": 10}), 10, at, at))
        deal_id = db.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for j in range(20):
            digest = hashlib.sha256(f"fact:{i}:{j}".encode()).hexdigest()
            db.conn.execute("INSERT INTO sightings(deal_id,source_name,source_url,content_hash,seen_at,final_price)"
                            " VALUES (?,?,?,?,?,?)", (deal_id, "synthetic-feed",
                            f"https://feed.example/post/synthetic-widget-{i}-{j}", digest, at, 10))
            db.conn.execute("INSERT INTO price_history(canonical_key,store,price,observed_at,content_hash)"
                            " VALUES (?,?,?,?,?)", (key, "Synthetic Store", 10, at, digest))
    db.conn.commit()


def measure(db, directory):
    db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    packed, encrypted = directory / "state.db.gz", directory / "state.db.enc"
    # Public fixture password only; no repository or environment secret is used.
    subprocess.run(["bash", "-c", 'source "$1"; state_encrypt "$2" "$3" "$4"', "fixture",
                    str(ROOT / "scripts/state_crypt.sh"), db.path, str(encrypted), str(packed)],
                   env={**os.environ, "STATE_DB_KEY": "synthetic-task296-fixture"}, check=True)
    return snapshot_sizes(Path(db.path), packed, encrypted)


def main():
    cfg = load_config()
    with tempfile.TemporaryDirectory(prefix="task296-model-", dir="/tmp") as scratch:
        directory = Path(scratch)
        db = DB(directory / "state.db")
        try:
            protected_facts(db)
            for day in range(-DAYS + 1, 1):
                cohort(db, day)
            before = measure(db, directory)
            db.prune(NOW, indexnow_batch_versions_days=cfg.indexnow_batch_versions_days,
                     appeal_scores_days=cfg.appeal_scores_days,
                     appeal_scores_ttl_h=cfg.appeal_score["ttl_h"])
            after = measure(db, directory)
            cohort(db, 1)
            next_unpruned = measure(db, directory)
            db.prune(NOW + timedelta(days=1), indexnow_batch_versions_days=cfg.indexnow_batch_versions_days,
                     appeal_scores_days=cfg.appeal_scores_days,
                     appeal_scores_ttl_h=cfg.appeal_score["ttl_h"])
            next_pruned = measure(db, directory)
            assert db.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            print(json.dumps({"days": DAYS, "batches_per_day": BATCHES_PER_DAY,
                "urls_per_batch": URLS_PER_BATCH, "before": before, "after": after,
                "next_unpruned": next_unpruned, "next_pruned": next_pruned}, indent=2))
        finally:
            db.close()


if __name__ == "__main__":
    main()
