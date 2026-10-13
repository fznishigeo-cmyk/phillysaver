"""Persist pre-call reservations before a production runner can lose them."""

import hashlib
import os
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

from .config import ROOT, get_secret
from .util import iso


def delivery_run_key(now, run_key: str | None = None) -> str:
    return run_key or os.environ.get("DEALRADAR_RUN_KEY") or iso(now)


def message_id(channel: str, token: str) -> str:
    return "<" + hashlib.sha256(f"{channel}|{token}".encode()).hexdigest() + "@wowpriced.com>"


def prepare_delivery(db, channel: str, token: str, now, run_key: str | None = None) -> bool:
    if not db.claim_outgoing(channel, token, delivery_run_key(now, run_key), now):
        return False
    try:
        checkpoint_state(db)
    except Exception:
        # No remote request was made. A failed pre-send checkpoint is a
        # definite rejection locally, rather than an uncertain delivery.
        db.finish_outgoing(channel, token, now, rejected=True)
        raise
    return True


def checkpoint_state(db) -> None:
    # Local commands retain SQLite durability. Workflows additionally save
    # the encrypted snapshot before any irreversible external operation.
    with closing(sqlite3.connect(db.path)) as conn:
        busy, _, _ = conn.execute("PRAGMA wal_checkpoint(FULL)").fetchone()
        if busy:
            raise OSError("state checkpoint is busy")
    if os.environ.get("DEALRADAR_DURABLE_STATE") != "1":
        return
    if not get_secret("STATE_DB_KEY") or Path(db.path).resolve() != ROOT / "state.db":
        raise OSError("encrypted state checkpoint is unavailable")
    subprocess.run(["bash", str(ROOT / "scripts/commit_state.sh")], cwd=ROOT,
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=60)
