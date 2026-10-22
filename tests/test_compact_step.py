"""Hourly compaction step (2026-10-22): hourly #321 deferred VACUUM at 4.4% free.

The pipeline's VACUUM waits for spare run time that hourly runs never have, so
a separate step compacts after the pipeline and before the save.
"""
from pathlib import Path

import pytest
import yaml

from dealradar import cli
from dealradar.db import DB

ROOT = Path(__file__).resolve().parent.parent


def fragmented(path, free_pct):
    db = DB(path)
    db.conn.execute("CREATE TABLE fragmentation(id INTEGER PRIMARY KEY, payload BLOB)")
    db.conn.executemany("INSERT INTO fragmentation(payload) VALUES (?)", [(bytes(3000),)] * 400)
    db.conn.commit()
    pages = db.conn.execute("PRAGMA page_count").fetchone()[0]
    db.conn.execute("DELETE FROM fragmentation WHERE id <= ?", (pages * free_pct // 100,))
    db.conn.commit()
    return db


@pytest.mark.parametrize("threshold,compacted", [(10, False), (3, True)])
def test_compact_follows_the_threshold_and_keeps_data(tmp_path, threshold, compacted):
    db = fragmented(tmp_path / "state.db", 6)
    kept = db.conn.execute("SELECT * FROM fragmentation ORDER BY id").fetchall()
    result = db.compact(threshold)
    assert result["compacted"] is compacted and result["free"] > 0
    free_now = db.conn.execute("PRAGMA freelist_count").fetchone()[0]
    assert (free_now == 0) is compacted
    assert db.conn.execute("SELECT * FROM fragmentation ORDER BY id").fetchall() == kept
    assert db.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    db.close()


def test_cli_prints_one_line_and_never_fails(tmp_path, capsys):
    fragmented(tmp_path / "state.db", 6).close()
    assert cli.main(["compact", "--db", str(tmp_path / "state.db")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("[compact] state compacted: pages=") and out.count("\n") == 1
    assert cli.main(["compact", "--db", str(tmp_path / "state.db")]) == 0
    assert "[compact] not needed: free 0 of" in capsys.readouterr().out


def test_hourly_step_sits_between_pipeline_and_save_and_is_best_effort():
    steps = yaml.safe_load((ROOT / ".github/workflows/hourly.yml").read_text())["jobs"]
    steps = next(iter(steps.values()))["steps"]
    names = [s.get("name") for s in steps]
    i = names.index("Compact state")
    assert names[i - 1] == "Run pipeline" and names[i + 1] == "Commit state to data branch"
    step = steps[i]
    assert step["run"] == "python -m dealradar compact" and step["continue-on-error"] is True
    assert step["timeout-minutes"] <= 5 and step["if"] == "${{ steps.pipeline.outcome == 'success' }}"
    assert "env" not in step  # plain state.db on the runner; no secrets needed
