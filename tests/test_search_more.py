"""Task 293: frozen release bytes, emitted paging/spelling, and private analytics."""

import gzip
import json
import re
import socket
import subprocess
from pathlib import Path

import httpx
import pytest

from dealradar.config import AppConfig, load_config, validated_search, validated_search_boost, validated_search_more
from dealradar.publish import search_more, site
from dealradar.publish.tracking import head_snippet
from tests.history_pins import pre_task_293
from tests.test_offscreen_cards import offers
from tests.test_publish_site import make_deal
from tests.test_pwa import tree_hash
from tests.test_search_amazon import run_node
from tests.test_support import NOW, render
from tests.test_tracking import HARNESS, SAMPLE, _script

BASELINE = json.loads(Path("tests/fixtures/search_more_off.json").read_text())


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task293 is offline")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(site, "util_now", lambda: NOW)


def config(mode="support"):
    if mode.startswith("current"):
        cfg = pre_task_293(load_config())
        if mode.endswith("unstripped"):
            cfg.strip_comments = False
        return cfg
    return AppConfig(search_amazon=True, affiliate_amazon_tag="example-20",
        search=validated_search({"amazon_results": "on"}),
        search_boost=validated_search_boost({"enabled": True, "exclude_words": ["anal", "sex toys", "lubricant"]}),
        search_circle=True, search_analyze={"enabled": True}, pwa_enabled=True, pwa_install_button=True,
        support_enabled=mode == "support", paywall_open_access=mode == "open")


@pytest.mark.parametrize("value", [None, False, {}, {"enabled": False}, {"enabled": True},
                                    {"enabled": True, "pages": 1, "min_query_chars": 4}])
def test_config_defaults_and_shipped_on(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"search_more": value}))
    expected = {"enabled": False, "pages": 3, "min_query_chars": 2} | (value or {})
    assert load_config(path).search_more == expected
    path.write_text("{}")
    assert load_config(path).search_more == AppConfig().search_more == {
        "enabled": False, "pages": 3, "min_query_chars": 2}
    assert load_config().search_more == {"enabled": True, "pages": 3, "min_query_chars": 2}


@pytest.mark.parametrize("value", [True, [], "on", 1, {"unknown": 1}, {"enabled": 1},
    {"enabled": "true"}, {"pages": 0}, {"pages": 11}, {"pages": True}, {"pages": 1.5},
    {"pages": "3"}, {"min_query_chars": 1}, {"min_query_chars": 61}, {"min_query_chars": None}])
def test_invalid_config(value):
    with pytest.raises(ValueError, match="^search_more"):
        validated_search_more(value)


@pytest.mark.parametrize("mode", BASELINE)
def test_off_missing_and_back_off_preserve_every_recorded_byte(tmp_path, mode):
    cfg = config(mode)
    for setting in (None, False, {}, {"enabled": False}, {"enabled": True}, {"enabled": False}):
        if setting is None:
            del cfg.search_more
        else:
            cfg.search_more = setting
        out, *_ = render(tmp_path, cfg, offers())
        if setting and setting.get("enabled"):
            enabled = tree_hash(out)
            assert enabled != BASELINE[mode]
            render(tmp_path, cfg, offers())
            assert tree_hash(out) == enabled
        else:
            assert tree_hash(out) == BASELINE[mode]


@pytest.mark.parametrize("mode,strip,analyze,boost", [("support", False, True, True),
    ("support", True, True, True), ("open", False, False, False), ("membership", True, True, False)])
def test_emitted_logic_and_syntax(tmp_path, mode, strip, analyze, boost):
    cfg = config(mode)
    cfg.strip_comments = strip
    cfg.search_analyze["enabled"] = analyze
    cfg.search_boost["enabled"] = boost
    cfg.search_more = validated_search_more({"enabled": True})
    out, *_ = render(tmp_path, cfg, [make_deal(item="Wireless mouse", codes=[])])
    page = (out / "index.html").read_text()
    bundle = (out / "search-more.js").read_text()
    assert json.loads((out / "search-config.json").read_text()) == {**cfg.search, "search_more": cfg.search_more}
    assert 'conic-gradient(from var(--wpspin)' in bundle
    assert 'search-more [data-search-amazon]{animation:none' in bundle
    for i, code in enumerate([*re.findall(r"<script>(.*?)</script>", page, re.S), bundle]):
        script = tmp_path / f"script-{i}.js"
        script.write_text(code)
        result = subprocess.run(["node", "--check", script], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    result = run_node("search_more_logic.cjs", out / "index.html")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Search more logic passed:" in result.stdout, "harness must finish every async scenario"


@pytest.mark.parametrize("fallback,mode,tag", [(False, "on", "example-20"), (True, "off", "example-20"), (True, "on", "")])
def test_dependencies_preserve_off_output(tmp_path, fallback, mode, tag):
    cfg = config()
    cfg.search_amazon, cfg.search["amazon_results"], cfg.affiliate_amazon_tag = fallback, mode, tag
    out, *_ = render(tmp_path, cfg)
    before = tree_hash(out)
    cfg.search_more = {"enabled": True}
    render(tmp_path, cfg)
    assert tree_hash(out) == before
    assert "searchMoreSettings" not in (out / "index.html").read_text()


def test_bundled_shopping_vocabulary_is_small():
    assert len(gzip.compress(search_more.WORDS.read_bytes(), mtime=0)) < 3000
    assert {"hair", "tie", "mouse", "iphone"} <= set(search_more.WORDS.read_text().split())


def test_browser_fixture_and_timeout_diagnostics(tmp_path):
    result = run_node("search_more_browser_logic.cjs", tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Search more browser harness passed:" in result.stdout


@pytest.mark.parametrize("on,ga4", [(True, True), (True, False), (False, True)])
def test_events_allow_only_page_or_corrected_boolean(tmp_path, on, ga4):
    cfg = config()
    cfg.search_more = {"enabled": on, "pages": 3}
    cfg.tracking = {k: v for k, v in SAMPLE.items() if ga4 or k != "ga4_measurement_id"}
    private = {k: "PRIVATE" for k in ("query", "url", "asin", "store", "method")}
    calls = [["search_more_page", {**private, "page": n}] for n in [0, 1, 2, 3, -1, True, "1", 1.5]]
    calls += [["search_spelling", {**private, "corrected": v}] for v in [True, False, 0, 1, "true", None]]
    (tmp_path / "snippet.js").write_text(_script(head_snippet(cfg)))
    (tmp_path / "harness.cjs").write_text(HARNESS)
    (tmp_path / "scenarios.json").write_text(json.dumps({"open": {"calls": calls},
        "gpc": {"gpc": True, "calls": calls}, "out": {"storage": {"wp.adsOptOut": "1"}, "calls": calls}}))
    result = subprocess.run(["node", "harness.cjs", "snippet.js", "scenarios.json"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "PRIVATE" not in result.stdout
    expected = [["event", "search_more_page", {"send_to": SAMPLE["ga4_measurement_id"], "page": n}] for n in [0, 1, 2]]
    expected += [["event", "search_spelling", {"send_to": SAMPLE["ga4_measurement_id"], "corrected": v}] for v in [True, False]]
    for output in json.loads(result.stdout).values():
        assert output["error"] is None
        assert [e for e in output["dataLayer"] if e[:2] in [["event", "search_more_page"], ["event", "search_spelling"]]] == (
            expected if on and ga4 else [])
        for key in ("fbq", "ttq", "pintrk", "uetq"):
            assert not any(event in str(output[key]) for event in ("search_more_page", "search_spelling"))


def test_shipped_site_never_names_the_price_supplier(tmp_path):
    # Live site check 2026-10-18: the Analyze reason key "keepa_error" put the supplier's name in index.html.
    out, doc, *_ = render(tmp_path, load_config(), offers())
    assert "keepa" not in (out / "index.html").read_text().lower()
    assert "keepa" not in (out / search_more.ASSET).read_text().lower()
    assert "keepa" not in json.dumps(doc).lower()
