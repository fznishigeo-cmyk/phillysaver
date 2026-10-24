"""Accuracy-lite changes real pipeline prices and keeps same-run idempotency."""

from pathlib import Path

import pytest
import yaml

from dealradar import pipeline
from dealradar.db import DB
from dealradar.models import RawPost
from tests.test_e2e_offline import NOW, _offline_env, _run


@pytest.mark.parametrize("enabled", [False, True])
def test_pipeline_savings_switch_and_rerun(tmp_path, monkeypatch, caplog, enabled):
    from tests.test_accuracy_lite_counts import ZERO

    caplog.set_level("INFO", logger="dealradar.pipeline")
    env = _offline_env(tmp_path, monkeypatch)
    path = Path(env["cfg"])
    raw = yaml.safe_load(path.read_text())
    raw["savings"] = {"accuracy_lite": enabled}
    raw["woot"]["condition_lock"] = enabled
    raw["amazon"]["renewed_title_lock"] = enabled
    raw["parse"]["critic_tiering"]["enabled"] = False
    path.write_text(yaml.safe_dump(raw))
    post = RawPost(
        source_name="blogone", uid="subscription", url="https://blogone.example/subscription",
        title="Chef Pro Knife Set", text="Chef Pro Knife Set. Regular $12.99. Subscribe & Save $8.49.",
        published_at=NOW, fetched_at=NOW, structured={
            "item": "Chef Pro Knife Set", "store": "Test", "regular_price": 12.99,
            "category": "home", "stack_steps": ["Subscribe & Save $8.49"],
            "evidence": {"regular_price_quote": "Regular $12.99"},
        })
    monkeypatch.setattr(pipeline, "_fetch_all", lambda *a, **kw: ([post], {}))
    monkeypatch.setenv("DEALRADAR_RUN_KEY", "accuracy-lite-rerun")
    first = _run(env)
    assert first["accuracy_lite"] == ZERO | {"subscribe_save_unknown": int(enabled)}
    line = "accuracy_lite: " + " ".join(f"{k}={v}" for k, v in first["accuracy_lite"].items())
    assert caplog.messages.count(line) == 1
    db = DB(env["db"])
    try:
        rows = db.all_deals()
        assert len(rows) == 1
        assert rows[0]["final_price"] == (None if enabled else 4.50)
        assert rows[0]["basis"] == ("" if enabled else "stack")
        tables = ("deals", "sightings", "price_history", "alerts_sent", "run_stats")
        before = {table: db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in tables}
        second = _run(env)
        assert second["accuracy_lite"] == ZERO
        assert first["accuracy_lite"] == ZERO | {"subscribe_save_unknown": int(enabled)}
        assert first["run_key"] == second["run_key"] == "accuracy-lite-rerun"
        assert before == {table: db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                          for table in tables}
    finally:
        db.close()


def test_failed_pipeline_restores_counter_scope(tmp_path, monkeypatch, caplog):
    from dealradar import accuracy_lite_counts
    from tests.test_accuracy_lite_counts import ZERO, recording

    env = _offline_env(tmp_path, monkeypatch)
    caplog.set_level("INFO", logger="dealradar.pipeline")

    def fail(*args, **kwargs):
        accuracy_lite_counts.increment("renewed_title_lane")
        raise RuntimeError("offline fetch failure")

    monkeypatch.setattr(pipeline, "_fetch_all", fail)
    with recording() as outer:
        with pytest.raises(RuntimeError, match="offline fetch failure"):
            _run(env)
        assert outer == ZERO
        accuracy_lite_counts.increment("minimum_spend")
    assert outer == ZERO | {"minimum_spend": 1}
    assert sum(m.startswith("accuracy_lite: ") for m in caplog.messages) == 1
    assert "renewed_title_lane=1" in caplog.text
