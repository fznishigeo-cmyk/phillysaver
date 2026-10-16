"""One strict home slot, with full pre-refactor output bytes (offline)."""

import base64
import json
import lzma
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import pytest

from dealradar.config import AppConfig
from dealradar.db import DB
from dealradar.publish import indexnow, site
from dealradar.publish.template_slots import replace_slot
from dealradar.util import iso

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
BASELINE = Path(__file__).with_name("fixtures") / "template_slots.json.xz"


@lru_cache(maxsize=1)
def baseline():
    # Captured before editing site.py on ac1355719ecb, including accepted
    # 119–121/210/211 changes. Contains configs, facts, versions and actual
    # file bytes; no hash replacement or current-code baseline generation.
    return json.loads(lzma.decompress(BASELINE.read_bytes()))


def files(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def assert_bytes(name, actual, expected):
    if actual != expected:
        offset = next((i for i, (a, b) in enumerate(zip(actual, expected)) if a != b),
                      min(len(actual), len(expected)))
        line = expected[:offset].count(b"\n") + 1
        pytest.fail(f"{name}: first difference at byte {offset}, "
                    f"line {line}; "
                    f"expected {expected[max(0, offset-50):offset+100]!r}, "
                    f"actual {actual[max(0, offset-50):offset+100]!r}")


def assert_files(actual, expected):
    assert actual.keys() == expected.keys(), (
        f"missing files: {sorted(expected.keys() - actual.keys())}; "
        f"extra files: {sorted(actual.keys() - expected.keys())}")
    for name in expected:
        assert_bytes(name, actual[name], expected[name])


def seed(db, rows):
    db.meta_set("teaser_recent", '["before"]')
    for row in rows:
        db.insert_deal(dedupe_key=row["dedupe_key"], store=row["store"], item=row["item"],
                       normalized_item=row["item"].lower(), canonical_url=row["canonical_url"],
                       payload=row, final_price=row["final_price"],
                       pct_at_checkout=row["pct_at_checkout"], abs_savings=row["abs_savings"],
                       online_only=row["online_only"], expires_at="", now=NOW)
        for i in range(6):
            db.record_price(row["dedupe_key"], row["store"], 10 + i,
                            NOW - timedelta(days=2*i), f"own-{i}")


def publish(root, cfg, rows):
    db = DB(":memory:")
    try:
        seed(db, rows)
        result = site.render_site(db, cfg, NOW, out_dir=root / "site", deals=rows,
                                  last_run={"run_key": "task205"})
        return result
    finally:
        db.close()


MODES = [f"{mode}-{pwa}-{history}" for mode in
         ("support", "support-no-links", "open", "closed", "shipped")
         for pwa in ("pwa-off", "pwa-on") for history in ("hide", "show")]
MODES += ["support-custom-saved-off", "support-custom-saved-on", "closed-custom", "open-empty"]


def test_slot_substitution_is_literal_and_not_recursive():
    replacement = r'\1\g<0> $1 $$ {{OTHER}} {{BROWSE_SCRIPT}} <!--saved-->'
    assert replace_slot("before{{BROWSE_SCRIPT}}after", "{{BROWSE_SCRIPT}}", replacement,
                        template_name="index.html", slot_name="BROWSE_SCRIPT") == (
                            "before" + replacement + "after")


@pytest.mark.parametrize("count,reason", [(0, "missing"), (2, "duplicate")])
def test_invalid_slots_have_safe_distinct_diagnostics(count, reason):
    with pytest.raises(ValueError) as caught:
        replace_slot("PRIVATE FACT " + "{{PRIVATE_TOKEN}}" * count, "{{PRIVATE_TOKEN}}",
                     "PRIVATE REPLACEMENT", template_name="index.html", slot_name="BROWSE_SCRIPT")
    assert str(caught.value) == f"index.html: {reason} slot BROWSE_SCRIPT"
    with pytest.raises(ValueError) as caught:
        replace_slot("PRIVATE FACT", "{{PRIVATE_TOKEN}}", "SECRET",
                     template_name="PRIVATE /path\ncontent", slot_name="SECRET /name")
    assert str(caught.value) == "template: missing slot slot"


@pytest.mark.parametrize("mode", ["support", "open", "closed"])
@pytest.mark.parametrize("count", [0, 2])
def test_bad_home_preserves_tree_state_and_receipts_then_recovers(tmp_path, monkeypatch, mode, count):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    templates = tmp_path / "templates"
    shutil.copytree(site.TEMPLATES_DIR, templates)
    monkeypatch.setattr(site, "TEMPLATES_DIR", templates)
    home = templates / "index.html"
    original = home.read_text()
    root = tmp_path / "published"
    out = root / "site"
    cfg = AppConfig(custom_domain="wowpriced.com", paywall_enabled=True,
                    support_enabled=mode == "support", paywall_open_access=mode == "open",
                    seo_indexnow_key="0123456789abcdef0123456789abcdef")
    rows = baseline()["facts"][:2]
    db = DB(":memory:")
    try:
        seed(db, rows)
        site.render_site(db, cfg, NOW, out_dir=out, deals=rows, last_run={"run_key": "old"})
        versions = {url: row["content_hash"] for url, row in db.indexnow_pages().items()}
        assert versions
        assert db.indexnow_claim(indexnow.INDEXNOW_ENDPOINT, "old", versions, NOW,
                                {url: iso(NOW) for url in versions})
        db.indexnow_finish(indexnow.INDEXNOW_ENDPOINT, "old", NOW, 200)
        assert all(row["received_at"] for row in db.indexnow_pages().values())
        # Retain obsolete outputs too: an invalid render cannot clean them up.
        (out / "health.html").write_text("old private health")
        (out / "stale.bin").write_bytes(b"old output")
        before_files = files(root)
        before_db = list(db.conn.iterdump())
        bad = original.replace("{{BROWSE_SCRIPT}}", "{{BROWSE_SCRIPT}}" * count)
        home.write_text(bad + "<!--PRIVATE TEMPLATE FACT-->")
        with pytest.raises(ValueError, match=r"^index\.html: (missing|duplicate) slot BROWSE_SCRIPT$"):
            site.render_site(db, cfg, NOW, out_dir=out, deals=rows, last_run={"run_key": "new"})
        assert_files(files(root), before_files)
        assert list(db.conn.iterdump()) == before_db
        # The DB-backed path must also fail before ranking or meta writes.
        with pytest.raises(ValueError):
            site.render_site(db, cfg, NOW, out_dir=out, last_run={"run_key": "new"})
        assert_files(files(root), before_files)
        assert list(db.conn.iterdump()) == before_db
        absent = tmp_path / "never-created" / "site"
        with pytest.raises(ValueError):
            site.render_site(db, cfg, NOW, out_dir=absent, deals=rows)
        assert not absent.parent.exists()
        assert list(db.conn.iterdump()) == before_db
        home.write_text(original)
        result = site.render_site(db, cfg, NOW, out_dir=out, deals=rows, last_run={"run_key": "new"})
        assert result["published"] == len(rows)
        assert not (out / "health.html").exists()
        recovered_files, recovered_db = files(root), list(db.conn.iterdump())
        again = site.render_site(db, cfg, NOW, out_dir=out, deals=rows, last_run={"run_key": "new"})
        assert again["full_payload"] == result["full_payload"]
        assert_files(files(root), recovered_files)
        assert list(db.conn.iterdump()) == recovered_db
    finally:
        db.close()


def test_home_validation_version_and_insertion_share_one_read(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    templates = tmp_path / "templates"
    shutil.copytree(site.TEMPLATES_DIR, templates)
    monkeypatch.setattr(site, "TEMPLATES_DIR", templates)
    home = templates / "index.html"
    browse = templates / "browse.js"
    original = home.read_text()
    read_text = Path.read_text
    reads = []
    browse_reads = []

    def changing_home(path, *args, **kwargs):
        if path == home:
            reads.append(path)
            return original if len(reads) == 1 else original.replace("{{BROWSE_SCRIPT}}", "")
        if path == browse:
            browse_reads.append(path)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", changing_home)
    publish(tmp_path / "published", AppConfig(support_enabled=True,
            engagement={"saved_deals": True}, amazon_as_of_enabled=True), baseline()["facts"])
    assert reads == [home]
    assert browse_reads == [browse]
    assert "function initBrowse()" in (tmp_path / "published/site/index.html").read_text()


@pytest.mark.parametrize("wrapped", [
    "<!--support-->{{BROWSE_SCRIPT}}<!--/support-->",
    "<!--saved-->{{BROWSE_SCRIPT}}<!--/saved-->",
    "<!--engagement-->{{BROWSE_SCRIPT}}<!--/engagement-->",
    "/*photoless*/{{BROWSE_SCRIPT}}/*/photoless*/",
])
def test_slot_moved_into_disabled_region_also_fails_before_mutation(tmp_path, monkeypatch, wrapped):
    templates = tmp_path / "templates"
    shutil.copytree(site.TEMPLATES_DIR, templates)
    home = templates / "index.html"
    home.write_text(home.read_text().replace("{{BROWSE_SCRIPT}}", wrapped))
    monkeypatch.setattr(site, "TEMPLATES_DIR", templates)
    db = DB(":memory:")
    try:
        before = list(db.conn.iterdump())
        out = tmp_path / "absent" / "site"
        with pytest.raises(ValueError, match="missing slot BROWSE_SCRIPT"):
            site.render_site(db, AppConfig(), NOW, out_dir=out, deals=[])
        assert not out.parent.exists()
        assert list(db.conn.iterdump()) == before
    finally:
        db.close()


@pytest.mark.parametrize("saved", [False, True])
def test_resolved_custom_browse_runs_existing_node_harness(tmp_path, monkeypatch, saved):
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    # Include the harness's known presets alongside non-default ones.
    cfg = AppConfig(support_enabled=True, engagement={"saved_deals": saved},
                    seo_price_pages=[10, 15, 25, 35, 50], amazon_history="hide")
    publish(tmp_path, cfg, baseline()["facts"])
    result = subprocess.run(["node", str(Path(__file__).with_name("browse_logic.cjs")),
                             str(tmp_path / "site/index.html"), str(tmp_path / "site/deals.json")],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    home = (tmp_path / "site/index.html").read_text()
    for i, script in enumerate(re.findall(r"<script>(.*?)</script>", home, re.S)):
        path = tmp_path / f"script-{i}.js"
        path.write_text(script)
        subprocess.run(["node", "--check", str(path)], check=True, capture_output=True, timeout=30)
