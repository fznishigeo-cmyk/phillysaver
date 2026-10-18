"""Retained facts publish identically, including optional Deal Check readers.

The browser test is run natively; the Node controller harness needs no browser.
"""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from dealradar.db import DB
from dealradar.publish import amazon_as_of, checker, site
from dealradar.util import iso
from tests.test_checker_index import URLS, files
from tests.test_checker_page import config, node, render
from tests.test_publish_site import NOW
from tests.test_state_size import RETENTION, batch


@pytest.fixture
def publication(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    db = DB(tmp_path / "state.db")
    out = tmp_path / "site"
    try:
        cfg = config()
        cfg.tracking = {"ga4_measurement_id": "G-ABC123", "clarity_project_id": "ywsuwmrauy",
                        "meta_pixel_id": "12345678"}
        render(db, out, cfg)
        # Exercise retained post observations outside every bounded history
        # window as well as current facts and ended-offer Deal Check readers.
        for d in db.all_deals():
            db.record_price(d["dedupe_key"], d["store"], 50,
                            NOW - timedelta(days=400), "old-post")
        rows = db.all_deals()
        site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
        before = files(out)
        batch(db, "obsolete-map", NOW - timedelta(days=8))
        db.conn.execute("INSERT INTO appeal_scores VALUES ('expired-score',10,4,?)",
                        (iso(NOW - timedelta(days=8)),))
        db.prune(NOW, **RETENTION)
        assert db.conn.execute("SELECT versions FROM indexnow_batches WHERE run_key='obsolete-map'").fetchone()[0] == "{}"
        assert not db.conn.execute("SELECT * FROM appeal_scores").fetchall()
        site.render_site(db, cfg, NOW, out_dir=out, deals=db.all_deals())
        assert files(out) == before  # Every HTML/JSON/asset byte, not just counts.
        (out / "browser-fixtures.json").write_text(json.dumps({"urls": URLS, "now": iso(NOW),
            "amazon_as_of": amazon_as_of.time_text(NOW)}))
        yield out
    finally:
        db.close()


def test_retained_publication_and_node_controller(publication):
    rows = json.loads((Path(__file__).parent / "fixtures/checker_aliases.json").read_text())
    for row in rows:
        assert row["aliases"] == checker.aliases(row["url"])
        row["shards"] = {str(count): [checker.shard(a, count) for a in row["aliases"]]
                         for count in (16, 32, 64, 128, 256, 512, 1024)}
    fixture = publication / "parity.json"
    fixture.write_text(json.dumps(rows))
    node("checker_page_logic.cjs", publication / "check/index.html", fixture)


def test_retained_publication_browser(publication):
    # Native keyboard, live/ended/unknown results, credits, freshness, history
    # and privacy assertions; all external traffic intercepted by the harness.
    node("checker_page_browser.cjs", publication)
