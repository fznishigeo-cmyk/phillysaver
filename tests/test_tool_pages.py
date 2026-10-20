"""Task 328: isolated previews, public discovery and browser-only tool logic."""

import json
import re
import socket
import subprocess
from pathlib import Path

import httpx
import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import indexnow, site, tool_pages
from dealradar.publish.seo_audit import Page
from scripts.publication_budget import analyze
from tests.phone_budget_fixture import publish_fixture, visible_offer_count
from tests.test_established_look import TRUST_LINKS
from tests.test_home_comment_strip import files
from tests.test_pwa import tree_hash
from tests.test_support import NOW, render
from tests.test_tracking import HARNESS, _script
from tests.history_pins import pre_timer_pill_ring


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task328 is offline")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(site, "util_now", lambda: NOW)


def config(public=False):
    cfg = load_config()
    cfg.tool_pages = {"enabled": True, "public": public}
    return cfg


def test_defaults_and_shipped_preview(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("{}")
    assert load_config(path).tool_pages == AppConfig().tool_pages == {"enabled": False, "public": False}
    assert load_config().tool_pages == {"enabled": True, "public": True}
    for value in ({}, {"enabled": True}, {"enabled": True, "public": True}):
        path.write_text(json.dumps({"tool_pages": value}))
        assert load_config(path).tool_pages == {"enabled": False, "public": False, **value}


@pytest.mark.parametrize("value", [None, False, True, [], 1, "true", {"unknown": False},
    {"public": True}, *({key: value} for key in ("enabled", "public")
                       for value in (None, 0, 1, "true", "false", [], {}))])
def test_strict_config(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"tool_pages": value}))
    with pytest.raises(ValueError, match="^tool_pages"):
        load_config(path)


@pytest.mark.parametrize("mode,expected", [
    ("shipped", "c2ef7a407847d12b7cf1d05aadb2f1ded8685c9f91626e633bda6d7f20a7bf9b"),
    ("inline", "7e8a2ccb578c806ec8e8bd85ec927bb2f36757a069aba2fdd419728fbb76919b"),
    ("membership", "a3b46b13701e706d53cd8dbb737de77c039b31d67cc1bbc42af5c250397f907b"),
])
def test_off_matches_release_and_preview_preserves_every_old_file(tmp_path, mode, expected):
    # Recorded before the parallel timer-ring feature and phone timer raise.
    cfg = pre_timer_pill_ring(config())
    if mode == "inline":
        cfg.search_analyze["lazy"] = False
        cfg.strip_comments = False
    if mode == "membership":
        cfg.support_enabled = False
    cfg.tool_pages = {"enabled": False, "public": False}
    out, *_ = render(tmp_path, cfg)
    assert tree_hash(out) == expected
    before = files(out)
    cfg.tool_pages["enabled"] = True
    render(tmp_path, cfg)
    after = files(out)
    assert set(after) - set(before) == {"tool-pages.css", "tool-pages.js", *(
        slug + "/index.html" for slug in tool_pages.PAGES)}
    assert {name: after[name] for name in before} == before
    for value in ({"enabled": False}, {}):
        cfg.tool_pages = value
        render(tmp_path, cfg)
        assert files(out) == before
    del cfg.tool_pages
    render(tmp_path, cfg)
    assert files(out) == before


@pytest.mark.parametrize("public", [False, True])
def test_metadata_visible_faq_copy_sitemap_and_indexnow(tmp_path, public):
    cfg = config(public)
    cfg.seo_indexnow_key = "task328offlinefixture0001"
    out, *_ = render(tmp_path, cfg)
    sitemap = ''.join(p.read_text() for p in out.glob("sitemap*.xml"))
    due = json.loads(indexnow.due_list_path(out).read_text())
    for slug, spec in tool_pages.PAGES.items():
        url = "https://wowpriced.com/" + slug + "/"
        html = (out / slug / "index.html").read_text()
        page = Page(html, "/" + slug + "/")
        assert page.first("title").text() == spec["title"]
        assert spec["name"] in page.first("h1").text()
        assert page.indexable is public
        assert ('<meta name="robots" content="noindex,nofollow">' in html) is (not public)
        assert '<link rel="canonical" href="' + url + '">' in html
        for name in ("og:title", "og:description", "og:url", "twitter:title", "twitter:description", "twitter:card"):
            assert name in html
        assert (url in sitemap) is public
        assert (url in due) is public
        schemas = {s["@type"]: s for s in page.schemas}
        assert set(schemas) == {"WebApplication", "FAQPage"}
        assert schemas["WebApplication"]["url"] == url
        questions = schemas["FAQPage"]["mainEntity"]
        faq = page.first(ident="faq-title")
        assert faq.text() == "How it works"
        visible = page.first(cls="faq")
        assert [n.text() for n in visible.nodes() if n.tag == "h3"] == [q["name"] for q in questions]
        assert [n.text() for n in visible.nodes() if n.tag == "p"] == [q["acceptedAnswer"]["text"] for q in questions]
        assert len(questions) == 5
        assert not re.search(r"average|lowest|best price|stars|reviews|fakespot|\b(?:soon|launch|beta|new)\b", page.root.text(), re.I)
        assert "As an Amazon Associate" in page.first("footer").text()
        links = {n.attrs.get("href"): n.text() for n in page.first("footer").nodes() if n.tag == "a"}
        assert TRUST_LINKS.items() <= links.items()
        assert "{{" not in html
    for path in out.rglob("*.html"):
        page = Page(path.read_text(), "/" + path.relative_to(out).as_posix())
        footer = page.first(ident="footerlinks")
        if public and footer:
            assert all(spec["name"] in footer.text() for spec in tool_pages.PAGES.values()), path
        if not public and path.parent.name not in tool_pages.PAGES:
            assert not any("/" + slug + "/" in page.hrefs for slug in tool_pages.PAGES), path


def test_preview_public_preview_and_off_reruns(tmp_path):
    cfg = config()
    out, *_ = render(tmp_path, cfg)
    preview = files(out)
    cfg.tool_pages["public"] = True
    render(tmp_path, cfg)
    public = files(out)
    urls = {"https://wowpriced.com/" + slug + "/" for slug in tool_pages.PAGES}
    assert urls <= set(json.loads(indexnow.due_list_path(out).read_text()))
    assert urls <= set(json.loads(indexnow.manifest_path(out).read_text())["versions"])
    render(tmp_path, cfg)
    assert files(out) == public
    cfg.tool_pages["public"] = False
    render(tmp_path, cfg)
    assert files(out) == preview
    assert not urls & set(json.loads(indexnow.due_list_path(out).read_text()))
    assert not urls & set(json.loads(indexnow.manifest_path(out).read_text())["versions"])


def test_public_phone_keeps_existing_budget_and_1000_bytes_reserve(tmp_path):
    cfg = config(True)
    cfg.phone_budget["enabled"] = True
    def inspect(db, out):
        report = analyze(out, cfg, expected_offers=visible_offer_count(cfg))
        assert report["ok"], report
        size = report["metrics"]["home_html"]["gzip"]
        print("Public tool footer home gzip bytes:", size)
        assert size < cfg.phone_budget["home_html_gzip_max_bytes"] - 1000
    publish_fixture(tmp_path, cfg, inspect=inspect)


def test_emitted_node_logic_and_browser_syntax(tmp_path):
    cfg = config()
    cfg.affiliate_amazon_tag = "example-20"
    out, *_ = render(tmp_path, cfg)
    for args in (["node", "--check", str(out / "tool-pages.js")],
                 ["node", "tests/tool_pages_logic.cjs", str(out / "tool-pages.js"), str(out / "index.html")],
                 ["node", "--check", "tests/tool_pages_browser.cjs"]):
        result = subprocess.run(args, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr


def test_tool_tracking_is_local_and_has_only_safe_fields(tmp_path):
    cfg = config()
    cfg.tracking = {"ga4_measurement_id": "G-TEST3280"}
    out, *_ = render(tmp_path, cfg)
    home = (out / "index.html").read_text()
    assert 'page_view: {ga4Only:true}' not in home
    assert 'ga4Config.send_page_view = false;' not in home
    for slug in tool_pages.PAGES:
        html = (out / slug / "index.html").read_text()
        assert 'page_view: {ga4Only:true}' in html
        assert 'if (name === "page_view") p.source = "tool_page";' in html
        assert 'ga4Config.send_page_view = false;' in html


@pytest.mark.parametrize("ids", [{"google_ads_id": "AW-123456"}, {"ga4_measurement_id": "G-TEST3280"}])
def test_tool_tracking_dispatch_and_ads_only_config(tmp_path, ids):
    cfg = config()
    cfg.tracking = ids
    (tmp_path / "snippet.js").write_text(_script(tool_pages._tracking(cfg)))
    (tmp_path / "harness.cjs").write_text(HARNESS)
    calls = [["page_view", {"source": "PRIVATE", "url": "PRIVATE", "query": "PRIVATE"}],
             ["analyze", {"query": "PRIVATE"}], ["search_discounts", {"query": "PRIVATE"}]]
    (tmp_path / "scenarios.json").write_text(json.dumps({"open": {"calls": calls}, "gpc": {"gpc": True, "calls": calls}}))
    result = subprocess.run(["node", "harness.cjs", "snippet.js", "scenarios.json"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "PRIVATE" not in result.stdout
    assert all(output["error"] is None for output in json.loads(result.stdout).values())
    if "ga4_measurement_id" in ids:
        for output in json.loads(result.stdout).values():
            assert ["event", "page_view", {"source": "tool_page", "send_to": ids["ga4_measurement_id"]}] in output["dataLayer"]
