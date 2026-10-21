"""Content-free deal/sighting size breakdown on the [commit_state] size line (from 357)."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from dealradar import state_size
from dealradar.db import DB
from dealradar.util import iso
from tests.test_product_entities import insert
from tests.test_state_growers import NOW


@pytest.fixture
def db(tmp_path):
    state = DB(tmp_path / "state.db")
    yield state
    state.close()


def payloads(db):
    return [r[0] for r in db.conn.execute("SELECT payload FROM deals ORDER BY id")]


def test_deal_status_age_measurements_and_five_largest_keys_only(db):
    for status in ("alive", "dead", "suspect"):
        for days in (0, 1, 7, 15):
            key = f"{status}:{days}"
            at = NOW - timedelta(days=days)
            # Dead age follows death, even when last seen is fresh.
            d = insert(db, key, now=NOW if status == "dead" else at,
                       died_at=iso(at), evidence={"quote": "SECRET_PRICE_QUOTE_é" * (days + 1)})
            db.update_deal(key, status=status)
            db.add_sighting(d["id"], source_name="partner", source_url="https://private.example/SECRET_URL",
                            content_hash=key, seen_at=NOW, final_price=10)
    report = state_size.deal_sizes(db.conn, NOW)
    for status in state_size.STATUSES:
        assert report["deals"]["by_status"][status]["rows"] == 4
        assert all(row["rows"] == 1 for row in report["deals"]["by_status_age"][status].values())
        assert report["sightings"]["by_deal_status"][status]["rows"] == 4
        assert report["sightings"]["by_deal_status"][status]["value_bytes"] > 0
    assert sum(r["payload_bytes"] for r in report["deals"]["by_status"].values()) == sum(
        len(p.encode()) for p in payloads(db))
    top = report["deals"]["largest_payloads"]
    assert len(top) == 5 and [r["payload_bytes"] for r in top] == sorted(
        (len(p.encode()) for p in payloads(db)), reverse=True)[:5]
    assert all("evidence" in r["key_json_bytes"] for r in top)
    assert "SECRET_" not in json.dumps(report)


def test_empty_size_buckets_and_exact_json_value_byte_sizes(db):
    empty = state_size.deal_sizes(db.conn, NOW)
    assert empty["deals"]["largest_payloads"] == []
    assert all(r == {"rows": 0, "payload_bytes": 0}
               for r in empty["deals"]["by_status"].values())
    facts = {"text": "é\n", "null": None, "yes": True, "no": False,
             "number": 123, "nested": {"quote": "PRIVATE"}, "array": [1, "é"]}
    d = insert(db, "unicode")
    raw = json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
    db.conn.execute("UPDATE deals SET payload=? WHERE id=?", (raw, d["id"]))
    report = state_size.deal_sizes(db.conn, NOW)
    largest, = report["deals"]["largest_payloads"]
    assert largest == {"payload_bytes": len(raw.encode()), "key_json_bytes": {
        k: len(json.dumps(v, ensure_ascii=False, separators=(",", ":")).encode()) for k, v in facts.items()}}
    assert "PRIVATE" not in json.dumps(report)


def test_size_line_is_one_compact_json_read_only_and_bounded_fallback(db, tmp_path, monkeypatch, capsys):
    insert(db, "fixture", evidence={"quote": "SECRET_QUOTE"})
    packed, encrypted, config = (tmp_path / n for n in ("state.gz", "state.enc", "config.yaml"))
    packed.write_bytes(b"fixture")
    encrypted.write_bytes(b"fixture")
    config.write_text("{}")
    before = db.conn.serialize()
    monkeypatch.setattr("sys.argv", ["state_size", str(db.path), str(packed), str(encrypted), str(config)])
    assert state_size.main() == 0
    line, = capsys.readouterr().out.splitlines()
    assert line.startswith("[commit_state] size ") and "SECRET_QUOTE" not in line
    report = json.loads(line.removeprefix("[commit_state] size "))
    assert report["deal_breakdown"] == "complete"
    assert report["deals"]["by_status"]["alive"]["rows"] == 1
    assert line.removeprefix("[commit_state] size ") == json.dumps(report, separators=(",", ":"), sort_keys=True)
    assert db.conn.serialize() == before
    def slow(conn, now=None):  # a scan far longer than the budget
        conn.execute("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n LIMIT 100000000)"
                     " SELECT count(*) FROM n").fetchone()
        raise AssertionError("the budget did not stop the scan")
    monkeypatch.setattr(state_size, "deal_sizes", slow)
    monkeypatch.setattr(state_size, "INSPECTION_BUDGET_S", .05)
    report = state_size.snapshot_sizes(Path(db.path), packed, encrypted)
    assert report["deal_breakdown"] == "budget_or_unavailable" and "deals" not in report
    # A stopped breakdown never costs the table sizes printed before it.
    assert report["method"] in {"dbstat", "page_counts"} and report["pages"] > 0
    assert db.conn.serialize() == before
