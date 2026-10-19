"""Task 295: deferred Analyze, existing release bytes and interaction races."""

import hashlib
import json
import re
import socket
import subprocess
from pathlib import Path

import httpx
import pytest

from dealradar.config import load_config, validated_search_analyze
from dealradar.publish import site
from tests.history_pins import pre_search_polish_1019
from tests.test_offscreen_cards import offers
from tests.test_pwa import tree_hash
from tests.test_search_more import config as search_config
from tests.test_search_amazon import run_node
from tests.test_support import NOW, render
from tests.history_pins import pre_task_320


def config(mode):
    cfg = pre_search_polish_1019(load_config()) if mode.startswith("current") else search_config(mode)
    pre_task_320(cfg)  # These harnesses exercise the preceding Analyze behavior.
    cfg.same_product_spacing = False  # the harness slices render(); spacing has its own tests (task 302)
    cfg.drop_fresh = False  # This search harness does not mount the separate drop module.
    cfg.search_more = {"enabled": mode != "more-off", "pages": 3, "min_query_chars": 2}
    if mode == "boost-off":
        cfg.search_boost["enabled"] = False
    if mode.endswith("unstripped"):
        cfg.strip_comments = False
    return cfg


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task295 is offline")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(site, "util_now", lambda: NOW)


BASELINE = json.loads(Path("tests/fixtures/search_analyze_lazy_off.json").read_text())


@pytest.mark.parametrize("mode", BASELINE)
def test_off_missing_and_back_off_preserve_inline_bytes(tmp_path, mode):
    cfg = config(mode)
    # Task 297 deliberately changes search_more ON, including inline Analyze.
    # Keep the recorded OFF pin; compare ON against the current inline output
    # across lazy toggles without re-pinning any historical hashes.
    expected = None if cfg.search_more["enabled"] else BASELINE[mode]
    for lazy in (False, None, True, False):
        if lazy is None:
            cfg.search_analyze.pop("lazy", None)
        else:
            cfg.search_analyze["lazy"] = lazy
        out, *_ = render(tmp_path, cfg, offers())
        if lazy:
            assert tree_hash(out) != expected
            before = tree_hash(out)
            render(tmp_path, cfg, offers())
            assert tree_hash(out) == before
        else:
            if expected is None:
                expected = tree_hash(out)
            assert tree_hash(out) == expected
            assert not (out / "search-analyze.js").exists()


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false", [], {}])
def test_lazy_requires_a_boolean(value):
    with pytest.raises(ValueError, match=r"^search_analyze.lazy must be a boolean$"):
        validated_search_analyze({"enabled": True, "lazy": value})


def test_native_browser_fixtures_without_launching_chromium(tmp_path):
    result = run_node("search_analyze_browser_logic.cjs", tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Analyze browser harness passed:" in result.stdout


@pytest.mark.parametrize("mode", ["support", "open", "membership", "boost-off", "more-off", "current", "current-unstripped"])
def test_emitted_loader_module_and_search_more_logic(tmp_path, mode):
    cfg = config(mode)
    cfg.search_analyze["lazy"] = True
    cfg.affiliate_amazon_tag = "example-20"
    out, *_ = render(tmp_path, cfg, offers())
    page = (out / "index.html").read_text()
    asset = (out / "search-analyze.js").read_text()
    assert 'import("/search-analyze.js?v=' in page
    assert "function analyzeLookup" not in page
    assert "function analyzeCompare" not in page
    assert "analyze-progress" not in page
    # First-paint rules stay inline (live 2026-10-18: without them the button sat unstyled under the box,
    # +95 px on phones); only the analysis panel rules wait for the module.
    if getattr(cfg, "all_discounts_button", False):
        # Task 317: the twin pill rules ship in the render-blocking header stylesheet
        # linked from <head>, so the button is still styled at first paint.
        head = page.split("</head>", 1)[0]
        link = re.search(r'<link rel="stylesheet" href="/header-polish\.css\?v=[a-f0-9]+">', head)
        assert link, "header stylesheet must block first paint"
        shared = (out / "header-polish.css").read_text()
        assert ":is(#analyze-search,.all-discounts){" in shared and "#search-hint{" in shared and ".analyze-steps" in asset
    else:
        assert "#analyze-search{" in page and "#search-hint{" in page and ".analyze-steps" in asset
    assert 'price_error:"Amazon' in asset and "keepa" not in asset.lower()
    assert ('analyzeBox.addEventListener("blur"' not in asset) is cfg.search_more["enabled"]
    assert ("host.searchSpellingState()" in asset) is cfg.search_more["enabled"]
    assert '/search-analyze.js?v=' + hashlib.sha256(asset.encode()).hexdigest()[:12] in page
    for i, code in enumerate([*re.findall(r"<script>(.*?)</script>", page, re.S), asset]):
        script = tmp_path / f"script-{i}.js"
        script.write_text(code)
        result = subprocess.run(["node", "--check", script], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    harnesses = ["search_analyze_lazy_logic.cjs"]
    if cfg.search_more["enabled"]:
        harnesses.append("search_more_logic.cjs")
    for harness in harnesses:
        result = run_node(harness, out / "index.html")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "logic passed" in result.stdout, "every asynchronous scenario must finish"


@pytest.mark.parametrize("dependency", ["analyze", "results", "amazon", "tag"])
def test_disabling_dependencies_removes_the_asset(tmp_path, dependency):
    cfg = config("support")
    cfg.search_analyze["lazy"] = True
    out, *_ = render(tmp_path, cfg, offers())
    assert (out / "search-analyze.js").exists()
    if dependency == "analyze":
        cfg.search_analyze["enabled"] = False
    elif dependency == "results":
        cfg.search["amazon_results"] = "off"
    elif dependency == "amazon":
        cfg.search_amazon = False
    else:
        cfg.affiliate_amazon_tag = ""
    render(tmp_path, cfg, offers())
    assert not (out / "search-analyze.js").exists()
    assert "analyzeLoad" not in (out / "index.html").read_text()
