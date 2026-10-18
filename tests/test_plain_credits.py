"""Task 294: plain site attribution, unchanged destinations and sparse gone cards."""

import json
import re
import socket
import subprocess
from collections import Counter
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from dealradar.config import AppConfig, load_config, validated_checker
from dealradar.db import DB
from dealradar.publish import seo, seo_audit, site
from dealradar.util import iso
from tests.history_pins import pre_task_294
from tests.test_publish_site import NOW, make_deal
from tests.test_pwa import tree_hash


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task294 is offline")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(site, "util_now", lambda: NOW)


def config(mode):
    # "current" = the release before task 294 (team-merch demote and Trending limit pinned too).
    cfg = pre_task_294(load_config()) if mode == "current" else AppConfig(custom_domain="wowpriced.com")
    if mode != "current":
        cfg.support_enabled = mode == "support"
        cfg.paywall_open_access = mode != "membership"
        cfg.seo_store_page_min_deals = cfg.seo_price_page_min_deals = 1
        cfg.affiliate_amazon_tag = "example-20"
        cfg.seo_product_pages_enabled = True
        cfg.checker = validated_checker({"enabled": True})
        cfg.seo_event_pages_enabled = True
        cfg.traffic_feed_enabled = True
    return cfg


def deals():
    return [make_deal(dedupe_key=f"credit-{i}", item=f"Black Friday kitchen kettle {i}",
                     store="Amazon" if i % 2 else "Walmart", category="kitchen",
                     asin=f"B{i:09d}" if i % 2 else "",
                     canonical_url=f"https://www.amazon.com/dp/B{i:09d}" if i % 2 else f"https://www.walmart.com/ip/{10000+i}",
                     codes=["SAVE10"] if i % 4 == 0 else [], conditions=[],
                     image_url=f"https://m.media-amazon.com/images/I/{i}.jpg",
                     price_rechecked_at=iso(NOW), first_seen_at=iso(NOW - timedelta(hours=1)),
                     status="dead" if i % 3 == 0 else "alive", death="confirmed", died_at=iso(NOW))
            for i in range(45)]


def publish(root, cfg, *, rows=None, records=False):
    root.mkdir(parents=True, exist_ok=True)
    with_db = DB(":memory:")
    try:
        rows = deals() if rows is None else rows
        if records:
            from tests.test_product_entities import insert
            stored = []
            for d in rows:
                row = insert(with_db, d["dedupe_key"], **{k: v for k, v in d.items() if k != "dedupe_key"})
                with_db.update_deal(d["dedupe_key"], status=d["status"])
                stored.append(dict(d, id=row["id"], product_id=row["product_id"]))
            rows = stored
        result = site.render_site(with_db, cfg, NOW, out_dir=root, deals=rows,
                                  last_run={"run_key": "task294-fixed"})
    finally:
        with_db.close()
    return result


# Captured from the supplied unpushed release before any production edits.
BASELINE = {
    "membership": "5d69aa41fd03e3ee4fe888d58aa675ff047e3cc39f6c37b70da2f2c935dd1531",
    "open": "855862070a82a744804a6ea87229a272fea18012188c5320e76114e44ccbf6de",
    "support": "df3c87389f08d4ad304415e19e814a22e453b35231d6635ff459800b1d25bbd2",
    # Captured on the release this task ships on top of (its first capture came from an unpushed
    # intermediate tree that never shipped).
    "current": "2ba45c57e4f0560aea997b1b5b6dc6dc8c39e83413fcd258a46d1c13cddde5aa"
}


@pytest.mark.parametrize("mode", ["membership", "open", "support", "current"])
def test_links_true_missing_and_back_true_preserve_recorded_bytes(tmp_path, mode):
    cfg = config(mode)
    cfg.credit_links = True
    publish(tmp_path, cfg)
    assert tree_hash(tmp_path) == BASELINE[mode]
    del cfg.credit_links
    publish(tmp_path, cfg)
    assert tree_hash(tmp_path) == BASELINE[mode]
    cfg.credit_links = False
    publish(tmp_path, cfg)
    assert tree_hash(tmp_path) != BASELINE[mode]
    cfg.credit_links = True
    publish(tmp_path, cfg)
    assert tree_hash(tmp_path) == BASELINE[mode]


@pytest.mark.parametrize("value", [False, True])
def test_strict_config_and_shipped_plain_credits(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"site": {"credit_links": value}}))
    assert load_config(path).credit_links is value
    path.write_text("{}")
    assert load_config(path).credit_links is AppConfig().credit_links is True
    assert load_config().credit_links is False


@pytest.mark.parametrize("value", [0, 1, "true", "false", None, [], {}])
def test_non_boolean_credit_links_rejected(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"site": {"credit_links": value}}))
    with pytest.raises(ValueError, match=r"^site.credit_links must be a boolean$"):
        load_config(path)


def assert_spacing(rows):
    assert all(d.get("status") != "dead" for d in rows[:9])
    for i in range(len(rows)):
        assert sum(d.get("status") == "dead" for d in rows[i:i + 10]) <= 1


def page_links(page):
    return Counter(n.attrs["href"] for n in page.root.nodes() if n.tag == "a" and n.has("getdeal"))


@pytest.mark.parametrize("mode", ["membership", "open", "support", "current"])
def test_full_publish_plain_credits_and_unchanged_get_deal_and_feed(tmp_path, mode):
    cfg = config(mode)
    if mode != "current":
        cfg.seo_event_pages_events = [{"slug": "black-friday", "name": "Black Friday", "date": "2026-11-27",
                                      "window_days": [-7, 1], "keywords": ["black friday"]}]
    rows = deals()
    for d in rows:
        if d["status"] == "alive":
            d["death"] = ""
            d["critic"] = {"plausible": True, "confidence": .9}
            d["click_price_check"] = {"asin": d["asin"], "final": 10.0, "at": iso(NOW), "buybox": 10.0}
    cfg.credit_links = True
    before = publish(tmp_path / "linked", cfg, rows=rows, records=True)
    cfg.credit_links = False
    after = publish(tmp_path / "plain", cfg, rows=rows, records=True)
    assert after["public_deals"] == before["public_deals"]
    assert after["full_payload"]["deals"] == before["full_payload"]["deals"]
    families, credit_count = set(), 0
    for path in (tmp_path / "plain").rglob("*.html"):
        relative = path.relative_to(tmp_path / "plain")
        page = seo_audit.Page(path.read_text(), "/" + relative.as_posix())
        old = seo_audit.Page((tmp_path / "linked" / relative).read_text(), page.path)
        # The tighter home cap may expose more live cards within its fixed budget.
        if relative.as_posix() != "index.html":
            assert page_links(page) == page_links(old), relative
        else:
            assert all(href in page_links(page) for href in page_links(old))
        for card in page.cards:
            credit = page.first(cls="credit", scope=card)
            assert credit is not None and "via Slickdeals" in credit.text(), relative
        for node in page.root.nodes():
            if node.has("credit") or node.has("detail-sources"):
                credit_count += 1
                families.add(relative.parts[0])
                assert "via Slickdeals" in node.text(), relative
                assert not any(n.tag == "a" for n in node.nodes()), relative
        if relative.parts[0] == "p" and relative.as_posix() != "p/index.html":
            assert "via Slickdeals" in page.scope.text()
            credits = [n for n in page.root.nodes() if n.has("credit")]
            assert credits and all(n.attrs.get("style") == "color:var(--muted)" for n in credits)
            assert not any(n.tag == "a" and n.text() == "Slickdeals" for n in page.root.nodes())
            families.add("p")
        # Covers credits outside known containers, including dormant product lists.
        assert not any(n.tag == "a" and n.text() in {"Slickdeals", "Hip2Save"}
                       for n in page.root.nodes()), relative
    assert credit_count > 30
    assert {"index.html", "deal", "deals", "best-deals-today"} <= families
    if mode != "current":
        assert {"p", "stores", "black-friday"} <= families
    for path in (tmp_path / "linked").rglob("*.xml"):
        relative = path.relative_to(tmp_path / "linked")
        if "feed" in path.name:
            assert (tmp_path / "plain" / relative).read_bytes() == path.read_bytes()
    home = seo_audit.Page((tmp_path / "plain/index.html").read_text(), "/")
    cards = home.cards
    old = seo_audit.Page((tmp_path / "linked/index.html").read_text(), "/")
    live_titles = lambda page: [page.first(cls="title", scope=c).text() for c in page.cards if not c.has("gone")]
    assert live_titles(home) == live_titles(old)
    assert_spacing([{"status": "dead" if c.has("gone") else "alive"} for c in cards])


@pytest.mark.parametrize("pattern", ["D" * 30 + "L" * 130, "LD" * 90, "L" * 12 + "DD" + "L" * 25,
                                    "D" * 10, "LLD", "L" * 9 + "D", "L" * 30])
def test_static_spacing_preserves_every_live_deal(pattern):
    rows = [{"key": str(i), "status": "dead" if c == "D" else "alive"} for i, c in enumerate(pattern)]
    ordered = seo.place_gone(rows, every=10)
    assert_spacing(ordered)
    assert [d for d in ordered if d["status"] == "alive"] == [d for d in rows if d["status"] == "alive"]
    assert seo.place_gone(ordered, every=10) == ordered
    if pattern.startswith("D" * 30):
        assert [i for i, d in enumerate(ordered) if d["status"] == "dead"] == list(range(9, len(ordered), 10))
        assert sum(d["status"] == "dead" for d in ordered[:60]) == 6


@pytest.mark.parametrize("mode,strip", [("support", False), ("support", True), ("open", False), ("current", True)])
def test_emitted_home_credits_order_filters_and_pagination(tmp_path, mode, strip):
    cfg = config(mode)
    cfg.strip_comments = strip
    cfg.credit_links = True
    publish(tmp_path / "linked", cfg)
    cfg.credit_links = False
    publish(tmp_path / "plain", cfg)
    home = (tmp_path / "plain/index.html").read_text()
    styles = "\n".join(re.findall(r"<style>(.*?)</style>", home, re.S))
    assert '.credit a' not in styles and '.detail-sources a' not in styles
    for retained in ('.brand a::after', '.card .title a::after', '.code::after', '.credit{'):
        assert retained in styles
    result = subprocess.run(["node", str(Path(__file__).with_name("plain_credits_logic.cjs")),
                             str(tmp_path / "plain/index.html"), str(tmp_path / "plain/deals.json"),
                             str(tmp_path / "linked/index.html")], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_plain_publish_rerun_preserves_files_and_database(tmp_path):
    cfg = load_config()
    assert cfg.credit_links is False
    db = DB(":memory:")
    try:
        def run():
            return site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=deals(),
                                    last_run={"run_key": "task294-fixed"})
        first = run()
        files, state = tree_hash(tmp_path), list(db.conn.iterdump())
        assert run() == first
        assert tree_hash(tmp_path) == files
        assert list(db.conn.iterdump()) == state
    finally:
        db.close()


@pytest.mark.parametrize("links", [False, True])
def test_emitted_deal_check_controller(tmp_path, links):
    from tests.test_checker_page import config as checker_config, render as render_checker

    cfg = checker_config()
    cfg.credit_links = links
    db = DB(":memory:")
    try:
        render_checker(db, tmp_path, cfg)
    finally:
        db.close()
    tests = Path(__file__).parent
    from dealradar.publish import checker
    aliases = json.loads((tests / "fixtures/checker_aliases.json").read_text())
    for row in aliases:
        row["shards"] = {str(count): [checker.shard(a, count) for a in row["aliases"]]
                         for count in (16, 32, 64, 128, 256, 512, 1024)}
    fixture = tmp_path / "parity.json"
    fixture.write_text(json.dumps(aliases))
    result = subprocess.run(["node", str(tests / "checker_page_logic.cjs"), str(tmp_path / "check/index.html"),
        str(fixture), "linked" if links else "plain-credits"],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_full_publish_comparison_pages_keep_store_links(tmp_path, monkeypatch):
    from tests.test_compare import NOW as compare_now, TERM, seed

    terms = tmp_path / "terms.yaml"
    terms.write_text(json.dumps([TERM]))
    cfg = config("open")
    cfg.compare.update(publish="on", terms_file=str(terms))
    products = json.loads((Path(__file__).parent / "fixtures/compare-keepa.json").read_text())["products"]
    monkeypatch.setattr(site, "util_now", lambda: compare_now)
    outputs = []
    for linked in (True, False):
        cfg.credit_links = linked
        db = DB(":memory:")
        try:
            seed(db, products)
            out = tmp_path / str(linked)
            site.render_site(db, cfg, compare_now, out_dir=out, deals=deals())
        finally:
            db.close()
        pages = sorted((out / "compare").rglob("*.html"))
        assert len(pages) >= 2
        outputs.append({p.relative_to(out).as_posix(): p.read_bytes() for p in pages})
    assert outputs[0] == outputs[1]


def test_plain_credits_remove_only_credit_click_tracking(tmp_path):
    from dealradar.publish.tracking import head_snippet
    from tests.test_tracking import HARNESS, SAMPLE, _script

    cfg = AppConfig(credit_links=False, tracking=SAMPLE)
    events = [["credit_click", {"partner": "Slickdeals"}],
              ["deal_click", {"store": "Amazon", "category": "kitchen", "method": "home"}]]
    (tmp_path / "snippet.js").write_text(_script(head_snippet(cfg)))
    (tmp_path / "harness.cjs").write_text(HARNESS)
    (tmp_path / "scenarios.json").write_text(json.dumps({"plain": {"calls": events}}))
    result = subprocess.run(["node", "harness.cjs", "snippet.js", "scenarios.json"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    output = json.loads(result.stdout)["plain"]
    assert output["error"] is None and "credit_click" not in result.stdout
    assert [e for e in output["dataLayer"] if e[0] == "event"] == [
        ["event", "deal_click", {"store": "Amazon", "category": "kitchen", "method": "home",
                                 "send_to": SAMPLE["ga4_measurement_id"]}]]


def test_css_compaction_retains_literals_urls_escapes_and_descendant_selectors():
    from dealradar.publish.credits import _compact_css

    literal = '''"  price  \\"  "'''
    source = r'''.credit  span { content:"two  words"; --quoted:'two  words';
background:url("data:image/svg+xml;a)  b"); --raw:url(data:two\)  words);
--escaped:\ ; } .\31  .child { width:calc(100% - 2px); }
/* keep  comment */'''
    compact = _compact_css(source)
    for keep in ('"two  words"', "'two  words'", 'url("data:image/svg+xml;a)  b")',
                 r'url(data:two\)  words)', r'--escaped:\ ;', r'.\31  .child',
                 'calc(100% - 2px)', '/* keep  comment */'):
        assert keep in compact
    assert '.credit span {' in compact and '\n' not in compact
    assert _compact_css(literal) == literal
