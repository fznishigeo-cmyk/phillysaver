"""Persist pre-call reservations before a production runner can lose them."""

import hashlib
import logging
import os
import signal
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path

from .config import ROOT, get_secret
from .util import iso

log = logging.getLogger(__name__)

# commit_state.sh retries a raced push with 2+4+8+16 s of backoff plus a
# fetch each time; 60 s timed out live (2026-10-13 hourly Pinterest step).
CHECKPOINT_TIMEOUT_S = 150


def delivery_run_key(now, run_key: str | None = None) -> str:
    return run_key or os.environ.get("DEALRADAR_RUN_KEY") or iso(now)


def message_id(channel: str, token: str) -> str:
    return "<" + hashlib.sha256(f"{channel}|{token}".encode()).hexdigest() + "@wowpriced.com>"


def prepare_delivery(db, channel: str, token: str, now, run_key: str | None = None) -> bool:
    if getattr(db, "checkpoint_failed", False):
        return False  # one failed save stops every send in this process
    if not db.claim_outgoing(channel, token, delivery_run_key(now, run_key), now):
        return False
    try:
        checkpoint_state(db)
    except Exception as e:
        # No remote request was made. A failed pre-send checkpoint is a
        # definite rejection locally (retried next run), not an uncertain
        # delivery; skip the send instead of failing the caller's step.
        db.finish_outgoing(channel, token, now, rejected=True)
        db.checkpoint_failed = True
        log.warning("%s: state checkpoint failed (%s); no sends this run", channel, e)
        return False
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
    started = time.monotonic()
    proc = subprocess.Popen(["bash", str(ROOT / "scripts/commit_state.sh")], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        code = proc.wait(timeout=CHECKPOINT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        # Kill the script's git children too: an orphaned push would race
        # the workflow's end-of-run save in the same worktree.
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise OSError(f"state checkpoint timed out after {CHECKPOINT_TIMEOUT_S}s")
    if code:
        raise OSError(f"state checkpoint failed (exit {code})")
    log.info("state checkpoint saved in %.1fs", time.monotonic() - started)
