"""Offline private scorecard, UTC boundaries, migration and deadline counters."""

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from dealradar.config import AppConfig
from dealradar.db import DB
from dealradar.growth import hands_off, report
from tests.test_e2e_offline import env

NOW = datetime(2026, 10, 16, 1, tzinfo=timezone.utc)  # still Oct 15 in New York
DAY = datetime(2026, 10, 15, tzinfo=timezone.utc)


def run(db, key, kind, start, finish=None, left=0):
    db.start_run(key, kind, start)
    if finish is not None:
        db.finish_run(key, now=finish, items_fetched=0, posts_new=0, deals_parsed=0,
                      published=0, spend_usd=0, posts_left=left, deadline_hit=int(left > 0))


def two_days(db):
    for offset, last in ((-1, 274), (0, 328)):
        start = DAY + timedelta(days=offset)
        for hour in range(24):
            if offset == 0 and hour in (5, 6):
                continue
            run(db, f"{offset}-{hour}", "hourly", start + timedelta(hours=hour),
                start + timedelta(hours=hour, minutes=10),
                left=(400 if offset == 0 and hour == 12 else last))
        run(db, f"{offset}-fast", "fast", start + timedelta(hours=23, minutes=35),
            start + timedelta(hours=23, minutes=40), left=20 if offset else 40)
    run(db, "unfinished", "hourly", DAY + timedelta(hours=5))
    for status in ("uncertain", "reserved", "settled", "released"):
        db.conn.execute("INSERT INTO llm_calls(call_key,run_key,model,started_at,reserved_usd,status)"
                        " VALUES (?,?,?,?,?,?)", (status, "private-run", "fixture", DAY.isoformat(), 1, status))
    db.conn.commit()


def test_gaps_backlog_unfinished_and_lost_calls_are_exact_and_private(tmp_path):
    path = tmp_path / "state.db"
    db = DB(path)
    two_days(db)
    # Content in unrelated state must never enter the section, including keys.
    db.start_run("https://private.example/$199-POST-TEXT", "manual", DAY)
    db.close()
    before = path.read_bytes()
    data = hands_off.collect(path, NOW)
    assert path.read_bytes() == before
    yesterday = data["yesterday"]
    assert yesterday == {
        "day": "2026-10-15", "hours_published": 22, "longest_gap_min": 180,
        "runs_unfinished": 2, "posts_left": 328, "posts_left_max": 400,
        "posts_left_change": 54, "ai_calls_lost": 2,
        "kinds": {
            "fast": dict(runs_started=1, runs_finished=1, runs_unfinished=0,
                         posts_left=40, posts_left_max=40, posts_left_change=20),
            "hourly": dict(runs_started=23, runs_finished=22, runs_unfinished=1,
                           posts_left=328, posts_left_max=400, posts_left_change=54),
        },
    }
    assert hands_off.summary_line(data) == (
        "CHECK Hands-off: 22/24 hours published; longest gap 180 min; parse backlog 328 "
        "(+54 vs day before); 2 runs never finished; 2 AI calls lost.")
    assert [d["day"] for d in data["history"]] == [
        f"2026-10-{day:02}" for day in range(14, 7, -1)]
    assert data["history"][0]["hours_published"] == 24
    assert data["history"][0]["posts_left"] == 274
    assert data["history"][1]["hours_published"] is None
    text = json.dumps(data) + "\n".join(hands_off.report_lines(data))
    for private in ("https:", "private.example", "$199", "POST-TEXT", "private-run", "fixture"):
        assert private not in text


def test_clean_day_is_ok_and_zero_calls_are_measured(tmp_path):
    path = tmp_path / "state.db"
    db = DB(path)
    for offset in (-1, 0):
        for hour in range(24):
            start = DAY + timedelta(days=offset, hours=hour)
            for kind, minute in (("hourly", 5), ("fast", 35)):
                run(db, f"{offset}-{hour}-{kind}", kind, start,
                    start + timedelta(minutes=minute))
    db.close()
    data = hands_off.collect(path, NOW)
    assert hands_off.summary_line(data) == (
        "OK Hands-off: 24/24 hours published; longest gap 30 min; parse backlog 0 "
        "(+0 vs day before); 0 runs never finished; 0 AI calls lost.")
    assert data["yesterday"]["kinds"]["fast"]["runs_finished"] == 24


@pytest.mark.parametrize("cause", ["growth", "unfinished", "missing_previous"])
def test_full_hours_still_check_when_a_lane_grows_or_evidence_is_missing(tmp_path, cause):
    path = tmp_path / "state.db"
    db = DB(path)
    for offset in ((0,) if cause == "missing_previous" else (-1, 0)):
        for hour in range(24):
            start = DAY + timedelta(days=offset, hours=hour)
            for kind in hands_off.KINDS:
                # Headline stays 100, but the smaller fast backlog grows.
                left = 100 if kind == "hourly" else (5 if offset == -1 else 6)
                run(db, f"{offset}-{hour}-{kind}", kind, start,
                    start + timedelta(minutes=10), left=left if cause == "growth" else 0)
    if cause == "unfinished":
        run(db, "crashed", "fast", DAY)
    db.close()
    data = hands_off.collect(path, NOW)
    assert data["yesterday"]["hours_published"] == 24
    assert hands_off.summary_line(data).startswith("CHECK Hands-off:")
    if cause == "growth":
        assert data["yesterday"]["posts_left_change"] == 0
        assert data["yesterday"]["kinds"]["fast"]["posts_left_change"] == 1


@pytest.mark.parametrize("edge", ["leading", "trailing"])
def test_gaps_include_day_edges_and_all_run_kinds(tmp_path, edge):
    path = tmp_path / "state.db"
    db = DB(path)
    hours = range(5, 24) if edge == "leading" else range(18)
    if edge == "leading":
        run(db, "prior", "manual", DAY - timedelta(hours=2), DAY - timedelta(hours=2))
    for hour in hours:
        start = DAY + timedelta(hours=hour)
        run(db, str(hour), "manual", start, start + timedelta(minutes=10))
    db.close()
    data = hands_off.collect(path, NOW)["yesterday"]
    assert data["longest_gap_min"] == (430 if edge == "leading" else 410)
    assert data["hours_published"] == len(hours)
    assert data["posts_left"] is None  # no finished fast/hourly reading


def test_cross_midnight_runs_and_calls_use_utc_boundaries(tmp_path):
    path = tmp_path / "state.db"
    db = DB(path)
    run(db, "before", "fast", DAY - timedelta(minutes=2), DAY + timedelta(minutes=1), left=7)
    run(db, "after", "hourly", DAY + timedelta(hours=23), DAY + timedelta(days=1), left=99)
    run(db, "late", "fast", DAY + timedelta(hours=23))
    # Offset dates differ from UTC; billed_at must not move a started call.
    for key, at, billed in (("in", "2026-10-14T23:30:00-02:00", "2026-10-16T00:10:00Z"),
                            ("out", "2026-10-15T23:30:00-02:00", None)):
        db.conn.execute("INSERT INTO llm_calls(call_key,run_key,model,started_at,billed_at,reserved_usd,status)"
                        " VALUES (?, 'fixture', 'fixture', ?, ?, 1, 'uncertain')", (key, at, billed))
    db.conn.commit()
    db.close()
    data = hands_off.collect(path, NOW)["yesterday"]
    assert data["hours_published"] == 1 and data["longest_gap_min"] == 1439
    assert data["ai_calls_lost"] == 1 and data["runs_unfinished"] == 1
    assert data["kinds"]["fast"] == dict(runs_started=1, runs_finished=1, runs_unfinished=1,
                                         posts_left=7, posts_left_max=7, posts_left_change=None)
    assert data["kinds"]["hourly"]["runs_started"] == 1
    assert data["kinds"]["hourly"]["runs_finished"] == 0


@pytest.mark.parametrize("state", ["absent", "missing_file", "missing_table", "empty", "legacy"])
def test_unknown_snapshots_are_ok_read_only_and_make_no_requests(tmp_path, monkeypatch, state):
    path = tmp_path / "state.db"
    if state == "absent":
        path = None
    elif state not in {"missing_file"}:
        with sqlite3.connect(path) as conn:
            if state in {"empty", "legacy"}:
                conn.execute("CREATE TABLE run_stats(run_key TEXT PRIMARY KEY, kind TEXT,"
                             " started_at TEXT, finished_at TEXT)")
            if state == "legacy":
                conn.execute("INSERT INTO run_stats VALUES ('old','fast',?,?)",
                             (DAY.isoformat(), (DAY + timedelta(hours=1)).isoformat()))
    before = path.read_bytes() if path and path.exists() else None
    monkeypatch.setattr(report, "get_secret", lambda _: "")
    cfg = AppConfig(custom_domain="", timezone="America/New_York")
    with httpx.Client(transport=httpx.MockTransport(lambda req: pytest.fail("unexpected request"))) as client:
        data, status = report.collect(cfg, NOW, client, db_path=path)
    assert status["hands_off"] == "ok"
    day = data["hands_off"]["yesterday"]
    assert day["posts_left"] is None and day["ai_calls_lost"] is None
    assert day["hours_published"] == (1 if state == "legacy" else None)
    line = hands_off.summary_line(data["hands_off"])
    assert "unknown" in line and line.startswith("CHECK")
    if path:
        assert (path.read_bytes() if path.exists() else None) == before


def test_old_database_migration_and_nonzero_rerun_values_are_idempotent(tmp_path):
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE run_stats(run_key TEXT PRIMARY KEY,kind TEXT NOT NULL DEFAULT 'hourly',"
                     "started_at TEXT NOT NULL,finished_at TEXT,items_fetched INTEGER DEFAULT 0,"
                     "posts_new INTEGER DEFAULT 0,deals_parsed INTEGER DEFAULT 0,"
                     "published INTEGER DEFAULT 0,spend_usd REAL DEFAULT 0)")
        conn.execute("INSERT INTO run_stats(run_key,started_at) VALUES ('old',?)", (DAY.isoformat(),))
    db = DB(path)
    columns = {r["name"]: r for r in db.conn.execute("PRAGMA table_info(run_stats)")}
    for name in ("posts_left", "deadline_hit"):
        assert columns[name]["type"] == "INTEGER" and columns[name]["dflt_value"] == "0"
    assert tuple(db.conn.execute("SELECT posts_left,deadline_hit FROM run_stats").fetchone()) == (0, 0)
    run(db, "new", "fast", DAY, DAY + timedelta(minutes=5), left=328)
    run(db, "new", "fast", DAY, DAY + timedelta(minutes=6), left=0)
    db.close()
    db = DB(path)  # migration and replay survive a reopen
    assert [tuple(r) for r in db.conn.execute(
        "SELECT run_key,posts_left,deadline_hit FROM run_stats ORDER BY run_key")] == [
            ("new", 328, 1), ("old", 0, 0)]
    db.close()


@pytest.mark.parametrize("fast_lane", [False, True])
def test_pipeline_records_all_deadline_posts_without_counting_intake_caps(env, monkeypatch, fast_lane):
    from dealradar import pipeline
    from tests import test_e2e_offline as offline
    from tests.test_extractor import make_post

    posts = [make_post(uid=str(i), text=f"Widget {i} was $100 now $20") for i in range(5)]
    monkeypatch.setattr(pipeline, "_fetch_all", lambda *a, **kw: (posts, {}))
    parse = pipeline._parse_new_posts

    def expired(posts, parser, cfg, now, tzs, deadline, db):
        cfg.max_posts_per_run = 3
        return parse(posts, parser, cfg, now, tzs, 0, db=db)

    monkeypatch.setattr(pipeline, "_parse_new_posts", expired)
    result = pipeline.run(db_path=env["db"], config_path=env["cfg"], fast_lane=fast_lane,
                          now=offline.NOW, sources_override=[], skip_validation=True,
                          transport=httpx.MockTransport(lambda req: pytest.fail("unexpected request")),
                          claude_client=offline._FakeClaudeClient(), dry_run_telegram=True)
    counts = result["parse"]["post_counts"]
    assert sum(c["evaluation_gap"] == "deadline" for c in counts.values()) == 3
    assert sum(c["evaluation_gap"] == "max_posts" for c in counts.values()) == 2
    assert result["parse"]["deadline_hit"] is True
    db = DB(env["db"])
    assert tuple(db.conn.execute("SELECT kind,posts_left,deadline_hit FROM run_stats").fetchone()) == (
        "fast" if fast_lane else "hourly", 3, 1)
    assert db.conn.execute("SELECT COUNT(*) FROM posts_seen").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM source_audit_posts WHERE parsed_at IS NOT NULL").fetchone()[0] == 0
    public = json.loads((env["site"] / "deals.json").read_text())
    assert not {"posts_left", "deadline_hit"} & public["meta"]["last_run"].keys()
    db.close()


def test_oldest_history_day_compares_with_the_day_before_the_window(tmp_path):
    path = tmp_path / "state.db"
    db = DB(path)
    for offset in (-8, -7):
        start = DAY + timedelta(days=offset)
        for kind in hands_off.KINDS:
            run(db, f"{offset}-{kind}", kind, start, start + timedelta(minutes=10), left=10 + offset)
    db.close()
    oldest = hands_off.collect(path, NOW)["history"][-1]
    assert oldest["day"] == "2026-10-08"
    assert oldest["posts_left_change"] == 1
    assert oldest["kinds"]["fast"]["posts_left_change"] == 1


def test_day_with_only_unfinished_runs_has_no_publications(tmp_path):
    path = tmp_path / "state.db"
    db = DB(path)
    run(db, "crashed", "hourly", DAY)
    db.close()
    day = hands_off.collect(path, NOW)["yesterday"]
    assert day["hours_published"] == 0 and day["runs_unfinished"] == 1
    assert day["longest_gap_min"] == 1440
    assert day["posts_left"] is None


@pytest.mark.parametrize("mode", ["free", "open", "closed", "support"])
def test_private_counters_and_scorecard_leave_all_public_bytes_unchanged(tmp_path, monkeypatch, mode):
    from dealradar.publish.site import render_site
    from tests.test_publish_site import NOW as PUBLISH_AT
    from tests.test_seo import _deals

    monkeypatch.setattr("dealradar.publish.site.util_now", lambda: PUBLISH_AT)
    cfg = AppConfig(custom_domain="wowpriced.com", paywall_enabled=mode != "free",
                    paywall_open_access=mode == "open", support_enabled=mode == "support",
                    paywall_teaser_count=2, seo_hub_min_items=3)
    outputs = []
    payloads = []
    for measured in (False, True):
        path = tmp_path / f"{measured}.db"
        db = DB(path)
        if measured:
            two_days(db)
            before = "\n".join(db.conn.iterdump())
            data = hands_off.collect(path, NOW)
            report.build_report(cfg, {"hands_off": data}, {"hands_off": "ok"}, DAY.date())
            assert "\n".join(db.conn.iterdump()) == before
        out = tmp_path / f"site-{measured}"
        result = render_site(db, cfg, PUBLISH_AT, out_dir=out, deals=_deals())
        outputs.append({str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()})
        payloads.append(result["full_payload"])
        db.close()
    assert len(outputs[0]) >= 30
    assert outputs[0] == outputs[1] and payloads[0] == payloads[1]


def test_report_starts_with_scorecard_and_preserves_other_text():
    cfg = AppConfig()
    data = hands_off.collect(None, NOW)
    _, text = report.build_report(cfg, {"hands_off": data}, {"hands_off": "ok"}, date(2026, 10, 15))
    assert text.splitlines()[0] == hands_off.summary_line(data)
    other_summary = [*report._visitor_scoreboard({}, date(2026, 10, 15)),
                     *report._scoreboard(cfg, {}, date(2026, 10, 15)),
                     *report._plain_summary(cfg, {}, date(2026, 10, 15))]
    assert text.splitlines()[1:1 + len(other_summary)] == other_summary
    assert "2026-10-08: CHECK Hands-off:" in text
    assert text.rstrip().endswith("Sections: hands_off ok")
    assert text.count(report.TECHNICAL_HEADING) == 1
