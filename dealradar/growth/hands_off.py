"""Private UTC run scorecard, using only the existing state snapshot."""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from ..db import DB

KINDS = ("fast", "hourly")
RUN_FIELDS = ("runs_started", "runs_finished", "runs_unfinished",
              "posts_left", "posts_left_max", "posts_left_change")
DAY_FIELDS = ("hours_published", "longest_gap_min", "runs_unfinished",
              "posts_left", "posts_left_max", "posts_left_change", "ai_calls_lost")


def _empty(day):
    return {"day": day.isoformat(), **dict.fromkeys(DAY_FIELDS),
            "kinds": {kind: dict.fromkeys(RUN_FIELDS) for kind in KINDS}}


def _stamp(value):
    if not value:
        return None
    stamp = datetime.fromisoformat(value)
    return stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).astimezone(timezone.utc)


def collect(db_path, now):
    """Yesterday and its seven preceding UTC days; None means unknown.

    Starts determine unfinished-run counts; finishes determine publication
    hours and backlog snapshots, including runs that cross midnight. The
    larger lane backlog is used in the headline: the lanes can share posts.
    """
    day = now.astimezone(timezone.utc).date() - timedelta(days=1)
    days = [_empty(day - timedelta(days=i)) for i in range(8)]
    result = {"yesterday": days[0], "history": days[1:]}
    if not db_path or not Path(db_path).is_file():
        return result
    try:
        db = DB(db_path, read_only=True)
    except sqlite3.Error:
        return result
    try:
        columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(run_stats)")}
        if not {"run_key", "kind", "started_at", "finished_at"} <= columns:
            return result
        backlog = "posts_left" if "posts_left" in columns else "NULL AS posts_left"
        first, end = day - timedelta(days=8), day + timedelta(days=1)
        prior = db.conn.execute(
            "SELECT finished_at FROM run_stats WHERE julianday(finished_at)<julianday(?)"
            " ORDER BY julianday(finished_at) DESC LIMIT 1", (first.isoformat(),)).fetchone()
        last_finish = _stamp(prior[0]) if prior else None
        rows = [dict(row) for row in db.conn.execute(
            f"SELECT run_key,kind,started_at,finished_at,{backlog} FROM run_stats"
            " WHERE (date(started_at)>=? AND date(started_at)<?)"
            " OR (date(finished_at)>=? AND date(finished_at)<?)",
            (first.isoformat(), end.isoformat(), first.isoformat(), end.isoformat()))]
        for row in rows:
            row["start"] = _stamp(row["started_at"])
            row["finish"] = _stamp(row["finished_at"])
        rows.sort(key=lambda r: (r["finish"] or r["start"], r["start"], r["run_key"]))
        has_calls = {row["name"] for row in db.conn.execute("PRAGMA table_info(llm_calls)")}
        lost = dict(db.conn.execute(
            "SELECT date(started_at),COUNT(*) FROM llm_calls"
            " WHERE date(started_at)>=? AND date(started_at)<?"
            " AND status IN ('uncertain','reserved') GROUP BY date(started_at)",
            (first.isoformat(), end.isoformat()))) if {"started_at", "status"} <= has_calls else None
        # One extra day supplies the comparison for the oldest displayed day.
        previous = None
        for offset in range(9):
            current_day = first + timedelta(days=offset)
            current = _empty(current_day)
            start = datetime.combine(current_day, time(), timezone.utc)
            stop = start + timedelta(days=1)
            started = [r for r in rows if start <= r["start"] < stop]
            finished = [r for r in rows if r["finish"] and start <= r["finish"] < stop]
            if started or finished:
                current["hours_published"] = len({r["finish"].hour for r in finished})
                # Keep the actual gap across midnight and the trailing gap.
                # Without an earlier reading, the day's start is the boundary.
                times = [last_finish or start, *[r["finish"] for r in finished], stop]
                current["longest_gap_min"] = max(
                    (b - a).total_seconds() / 60 for a, b in zip(times, times[1:]))
                current["runs_unfinished"] = sum(r["finish"] is None for r in started)
                current["ai_calls_lost"] = lost.get(current_day.isoformat(), 0) if lost is not None else None
                for kind in KINDS:
                    starts = [r for r in started if r["kind"] == kind]
                    finishes = [r for r in finished if r["kind"] == kind]
                    values = [r["posts_left"] for r in finishes]
                    latest = values[-1] if values else None
                    before = previous["kinds"][kind]["posts_left"] if previous else None
                    current["kinds"][kind] = {
                        "runs_started": len(starts), "runs_finished": len(finishes),
                        "runs_unfinished": sum(r["finish"] is None for r in starts),
                        "posts_left": latest,
                        "posts_left_max": max(values) if values and None not in values else None,
                        "posts_left_change": latest - before if latest is not None and before is not None else None,
                    }
                latest = [v["posts_left"] for v in current["kinds"].values()]
                current["posts_left"] = max(latest) if None not in latest else None
                values = [r["posts_left"] for r in finished]
                current["posts_left_max"] = max(values) if values and None not in values else None
                before = previous["posts_left"] if previous else None
                if current["posts_left"] is not None and before is not None:
                    current["posts_left_change"] = current["posts_left"] - before
            if finished:
                last_finish = finished[-1]["finish"]
            if current_day == day:
                result["yesterday"] = current
            elif current_day >= day - timedelta(days=7):
                days[(day - current_day).days] = current
            previous = current
        result["history"] = days[1:]
        return result
    except sqlite3.Error:
        return {"yesterday": _empty(day),
                "history": [_empty(day - timedelta(days=i)) for i in range(1, 8)]}
    finally:
        db.close()


def _number(value):
    return "unknown" if value is None else f"{value:,}"


def _change(value):
    return "unknown" if value is None else f"{value:+,}"


def summary_line(data):
    current = (data or {}).get("yesterday") or {}
    changes = [current.get("kinds", {}).get(kind, {}).get("posts_left_change") for kind in KINDS]
    ok = (current.get("hours_published") == 24 and current.get("runs_unfinished") == 0
          and all(change is not None and change <= 0 for change in changes))
    gap = current.get("longest_gap_min")
    return (f"{'OK' if ok else 'CHECK'} Hands-off: {_number(current.get('hours_published'))}/24 hours published; "
            f"longest gap {_number(math.ceil(gap) if gap is not None else None)} min; "
            f"parse backlog {_number(current.get('posts_left'))} "
            f"({_change(current.get('posts_left_change'))} vs day before); "
            f"{_number(current.get('runs_unfinished'))} runs never finished; "
            f"{_number(current.get('ai_calls_lost'))} AI calls lost.")


def report_lines(data):
    lines = ["== Hands-off (UTC; lane backlogs may overlap) =="]
    for day in [(data or {}).get("yesterday"), *(data or {}).get("history", [])]:
        if not day:
            continue
        lines.append(f"{day['day']}: " + summary_line({"yesterday": day}))
        lines.append(f"  Maximum posts left: {_number(day['posts_left_max'])}")
        for kind in KINDS:
            lane = day["kinds"][kind]
            lines.append(f"  {kind}: {_number(lane['runs_started'])} started, "
                         f"{_number(lane['runs_finished'])} finished, {_number(lane['runs_unfinished'])} unfinished; "
                         f"posts left {_number(lane['posts_left'])}, max {_number(lane['posts_left_max'])}, "
                         f"change {_change(lane['posts_left_change'])}")
    return lines
