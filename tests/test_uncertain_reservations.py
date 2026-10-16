"""An uncertain (timed-out) call reserves the next day only when it ended near midnight."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from dealradar.db import DB

DAY = datetime(2026, 10, 15, tzinfo=timezone.utc)


def reserve(db, key, at, cost=.08):
    tomorrow = at + timedelta(days=1)
    db.reserve_llm_call(key, "run", "claude-sonnet", cost, at,
                        {at.date().isoformat(): 10, tomorrow.date().isoformat(): 10}, 5, kind="second_opinion")


@pytest.mark.parametrize("ended,tomorrow_usd", [
    (DAY.replace(hour=5, minute=24, second=46), 0),        # an ordinary timeout
    (DAY.replace(hour=23, minute=44, second=59), 0),       # 15 min 1 s before midnight
    (DAY.replace(hour=23, minute=45), .08),                # within the billing margin
    (DAY.replace(hour=23, minute=59, second=50), .08),
])
def test_uncertain_call_keeps_tomorrows_reservation_only_near_midnight(tmp_path, ended, tomorrow_usd):
    db = DB(tmp_path / "state.db")
    reserve(db, "lost", ended - timedelta(seconds=20))
    db.settle_llm_call("lost", .08, ended, uncertain=True)
    assert db.spend_usd_for_day(ended) == pytest.approx(.08)  # its own day pays the worst case
    assert db.spend_usd_for_day(ended + timedelta(days=1)) == pytest.approx(tomorrow_usd)
    db.close()


def test_a_call_that_ends_after_midnight_still_counts_on_both_days(tmp_path):
    db = DB(tmp_path / "state.db")
    reserve(db, "midnight", DAY.replace(hour=23, minute=59, second=55))
    after = DAY + timedelta(days=1, seconds=15)
    db.settle_llm_call("midnight", .08, after, uncertain=True)
    assert db.spend_usd_for_day(DAY) == pytest.approx(.08)
    assert db.spend_usd_for_day(after) == pytest.approx(.08)
    db.close()


def test_settled_and_released_calls_are_unchanged(tmp_path):
    db = DB(tmp_path / "state.db")
    at = DAY.replace(hour=9)
    reserve(db, "ok", at)
    reserve(db, "gone", at)
    db.settle_llm_call("ok", .01, at, usage=None)
    db.release_llm_call("gone")
    assert db.spend_usd_for_day(at) == pytest.approx(.01)
    assert db.spend_usd_for_day(at + timedelta(days=1)) == 0
    db.close()


def test_opening_the_db_releases_old_next_day_reservations_once(tmp_path):
    path = tmp_path / "state.db"
    db = DB(path)
    early, late = DAY.replace(hour=6), DAY.replace(hour=23, minute=55)
    for key, at in (("early", early), ("late", late)):
        reserve(db, key, at)
        db.settle_llm_call(key, .08, at + timedelta(seconds=20), uncertain=True)
    db.close()
    # Rows written before this change kept tomorrow's reservation for every uncertain call.
    conn = sqlite3.connect(path)
    conn.execute("INSERT OR IGNORE INTO llm_reservations(call_key, day, usd) VALUES ('early', '2026-10-16', .08)")
    conn.commit()
    conn.close()
    for _ in range(2):  # idempotent on every open
        db = DB(path)
        assert db.spend_usd_for_day(DAY) == pytest.approx(.16)
        assert db.spend_usd_for_day(DAY + timedelta(days=1)) == pytest.approx(.08)  # only the late call
        rows = db.conn.execute("SELECT call_key, day FROM llm_reservations ORDER BY 1, 2").fetchall()
        assert [tuple(r) for r in rows] == [("early", "2026-10-15"), ("late", "2026-10-15"), ("late", "2026-10-16")]
        db.close()


def test_a_call_never_settled_keeps_only_its_own_day_after_reopen(tmp_path):
    # A run cut off at its parse deadline can save state before a worker settles.
    path = tmp_path / "state.db"
    db = DB(path)
    reserve(db, "cut-off", DAY.replace(hour=6))
    reserve(db, "late", DAY.replace(hour=23, minute=50))
    db.close()
    db = DB(path)
    assert db.spend_usd_for_day(DAY) == pytest.approx(.16)  # still the worst case on its day
    assert db.spend_usd_for_day(DAY + timedelta(days=1)) == pytest.approx(.08)
    assert db.conn.execute("SELECT status FROM llm_calls WHERE call_key='cut-off'").fetchone()[0] == "reserved"
    db.close()
