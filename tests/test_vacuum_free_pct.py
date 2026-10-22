"""retention.vacuum_free_pct: compact once freed pages pass this share of state.db.

Freed SQLite pages keep their old bytes, and the gzip'd encrypted copy carries
them; at the old fixed 10% a ~6% free file (~17 MB, 2026-10-22) never compacted.
"""
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from dealradar.config import load_config
from dealradar.db import DB

NOW = datetime(2026, 10, 22, 9, 30, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def db(tmp_path):
    d = DB(str(tmp_path / "state.db"))
    yield d
    d.close()


def fragment(db, free_pct):
    # One 3000-byte row per page; free about free_pct of the file.
    db.conn.execute("CREATE TABLE fragmentation(id INTEGER PRIMARY KEY, payload BLOB)")
    db.conn.executemany("INSERT INTO fragmentation(payload) VALUES (?)", [(bytes(3000),)] * 400)
    db.conn.commit()
    pages = db.conn.execute("PRAGMA page_count").fetchone()[0]
    db.conn.execute("DELETE FROM fragmentation WHERE id <= ?", (pages * free_pct // 100,))
    db.conn.commit()
    return db.conn.execute("PRAGMA freelist_count").fetchone()[0]


@pytest.mark.parametrize("threshold,compacted", [(10, False), (3, True)])
def test_threshold_decides_compaction_and_keeps_data(db, threshold, compacted):
    free = fragment(db, 6)
    assert free > 0
    kept = [tuple(r) for r in db.conn.execute("SELECT * FROM fragmentation ORDER BY id")]
    db.prune(NOW, vacuum_free_pct=threshold)
    after = db.conn.execute("PRAGMA freelist_count").fetchone()[0]
    assert (after == 0) is compacted and (after == free) is not compacted
    assert [tuple(r) for r in db.conn.execute("SELECT * FROM fragmentation ORDER BY id")] == kept
    assert db.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_default_keeps_the_old_ten_percent_rule(db):
    fragment(db, 6)
    db.prune(NOW)
    assert db.conn.execute("PRAGMA freelist_count").fetchone()[0] > 0


def test_shipped_config_and_validation(tmp_path):
    assert load_config(str(ROOT / "config.yaml")).vacuum_free_pct == 3
    raw = yaml.safe_load((ROOT / "config.yaml").read_text())
    for bad in (0, 51, "3", True, None):
        raw["retention"]["vacuum_free_pct"] = bad
        path = tmp_path / "bad.yaml"
        path.write_text(yaml.safe_dump(raw))
        with pytest.raises(ValueError, match="vacuum_free_pct"):
            load_config(str(path))
