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


def test_recording_preserves_stored_deals_and_every_published_byte(tmp_path, monkeypatch):
    from dealradar import accuracy_lite_counts
    from tests.test_accuracy_lite_counts import ZERO

    snapshots = []
    for record in (False, True):
        directory = tmp_path / str(record)
        directory.mkdir()
        with monkeypatch.context() as patch:
            env = _offline_env(directory, patch)
            fetch_all = pipeline._fetch_all

            def fetch_with_rule_hits(*args, **kwargs):
                posts, errors = fetch_all(*args, **kwargs)
                posts.append(RawPost(
                    source_name="blogone", uid="rule-hits",
                    url="https://blogone.example/rule-hits", title="Chef Pro Knife Set",
                    text="Chef Pro Knife Set. Regular $20. $5 off $25. Subscribe & Save $8.49. $2 rewards rebate.",
                    published_at=NOW, fetched_at=NOW, structured={
                        "item": "Chef Pro Knife Set", "store": "Test", "regular_price": 20,
                        "category": "home", "stack_steps": ["$5 off $25", "Subscribe & Save $8.49"],
                        "rebates": ["$2 rewards rebate"],
                        "evidence": {"regular_price_quote": "Regular $20"},
                    }))
                return posts, errors

            patch.setattr(pipeline, "_fetch_all", fetch_with_rule_hits)
            if not record:
                patch.setattr(accuracy_lite_counts, "increment", lambda name: None)
            summary = _run(env)
            assert summary["accuracy_lite"] == ZERO | {
                "minimum_spend": int(record), "subscribe_save_unknown": int(record),
                "rewards_deferred": int(record)}
            db = DB(env["db"])
            try:
                rows = db.all_deals()
            finally:
                db.close()
            files = {str(p.relative_to(env["site"])): p.read_bytes()
                     for p in env["site"].rglob("*") if p.is_file()}
            assert rows and "index.html" in files and "deals.json" in files
            snapshots.append((rows, files))
    assert snapshots[0] == snapshots[1]
