"""Task 291: offline link analysis, privacy, frozen publication and native QA."""
import json
import re
import socket
import subprocess
from pathlib import Path

import httpx
import pytest

from dealradar.config import AppConfig, load_config, validated_search_analyze
from dealradar.publish import site
from dealradar.publish.tracking import head_snippet
from tests.history_pins import pre_task_290_install, pre_task_291
from tests.test_e2e_offline import env, test_rerun_is_idempotent as check_rerun
from tests.test_offscreen_cards import offers
from tests.test_publish_site import make_deal
from tests.test_pwa import baseline_config, tree_hash
from tests.test_search_amazon import run_node
from tests.test_search_boost import config
from tests.test_support import NOW, render
from tests.test_tracking import HARNESS, SAMPLE, _script


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task291 is offline")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(site, "util_now", lambda: NOW)


@pytest.mark.parametrize("value", [None, {}, {"enabled": False}, {"enabled": True}])
def test_config_defaults_and_shipped_on(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"search_analyze": value}))
    assert load_config(path).search_analyze == {"enabled": bool(value and value.get("enabled")), "lazy": False}
    path.write_text("{}")
    assert load_config(path).search_analyze == AppConfig().search_analyze == {"enabled": False, "lazy": False}
    assert load_config().search_analyze == {"enabled": True, "lazy": True}


@pytest.mark.parametrize("value", [False, True, [], "on", 1, {"other": True},
                                    *({"enabled": v} for v in (None, 0, 1, "true", [], {}))])
def test_invalid_config(value):
    with pytest.raises(ValueError, match="^search_analyze"):
        validated_search_analyze(value)


# Captured from the supplied, unpushed release BEFORE implementation. Never re-pin.
@pytest.mark.parametrize("mode,expected", [
    ("membership", "169be60f7df581d048d16036cc926f3519867bb2db6ee4f158c8c1ba11c7dafc"),
    ("open", "d813fd0efc16aba7fd734ebfbe992e97341e36f70b84716ef8f065c3a3f78c66"),
    ("support", "dca92097f3c9e3cf16fbf2d7f1e805901732bb5804fec14dab1a90ffc8b0e62e"),
    ("shipped", "930e4c1238d1c5b9baadb340cabe336042fb48aec4b83e245b0cef4afebb9ff7"),
    ("current", "a8546631232c476f3155aee079f8937719202f2946c1730dd451b2af0143d33c"),
])
def test_off_missing_and_back_off_preserve_every_byte(tmp_path, mode, expected):
    cfg = pre_task_290_install(pre_task_291(load_config())) if mode == "current" else baseline_config(mode)
    for setting in ({"enabled": False}, {}, {"enabled": True}, {"enabled": False}):
        cfg.search_analyze = setting
        out, *_ = render(tmp_path, cfg, offers())
        if setting.get("enabled") and mode == "current":
            assert tree_hash(out) != expected
        else:
            assert tree_hash(out) == expected
    del cfg.search_analyze
    render(tmp_path, cfg, offers())
    assert tree_hash(out) == expected


def analyze_config(mode="on", support=True, strip=False):
    cfg = config(mode)
    cfg.search_analyze = {"enabled": True}
    cfg.support_enabled = support
    cfg.strip_comments = strip
    cfg.amazon_history = "hide"
    return cfg


@pytest.mark.parametrize("support,strip,mode,boost", [(True, False, "on", True), (True, True, "on", True),
    (False, False, "preview", True), (False, True, "on", True), (True, False, "on", False)])
def test_emitted_logic_and_syntax(tmp_path, support, strip, mode, boost):
    cfg = analyze_config(mode, support, strip)
    cfg.search_boost["enabled"] = boost
    out, *_ = render(tmp_path, cfg, [make_deal(codes=[])])
    html = (out / "index.html").read_text()
    assert 'placeholder="Search any product or paste an Amazon link"' in html
    assert "Free price check · no sign-up" in html
    assert "prefers-reduced-motion:reduce" in html
    assert "query: analyzeSearchText().slice(0, 500)" in html
    assert 'query: document.getElementById("q").value' not in html
    for i, code in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
        path = tmp_path / f"script-{i}.js"
        path.write_text(code)
        result = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    result = run_node("search_analyze_logic.cjs", out / "index.html")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("fallback,mode,tag", [(False, "on", "example-20"), (True, "off", "example-20"), (True, "on", "")])
def test_dependency_switches_fail_closed(tmp_path, fallback, mode, tag):
    cfg = analyze_config(mode)
    cfg.search_amazon, cfg.affiliate_amazon_tag = fallback, tag
    out, *_ = render(tmp_path, cfg)
    assert "analyzeSubmit" not in (out / "index.html").read_text()


@pytest.mark.parametrize("ga4", [True, False])
def test_events_have_only_allowlisted_counts_and_booleans(tmp_path, ga4):
    cfg = analyze_config()
    cfg.tracking = {k: v for k, v in SAMPLE.items() if ga4 or k != "ga4_measurement_id"}
    private = {k: "PRIVATE" for k in ("url", "asin", "query", "store", "method")}
    calls = [[event, {**private, "link": value}] for event in ("search_analyze", "paste_amazon_link")
             for value in (True, False, 1, "yes", None)]
    calls += [["analyze_result", {**private, "count": count, "live": True, "matched": False}]
              for count in (0, 16, 17, -1, True, "1")]
    calls += [["analyze_result", {"count": 1, "live": "PRIVATE", "matched": False}]]
    (tmp_path / "snippet.js").write_text(_script(head_snippet(cfg)))
    (tmp_path / "harness.cjs").write_text(HARNESS)
    (tmp_path / "scenarios.json").write_text(json.dumps({"open": {"calls": calls},
        "gpc": {"gpc": True, "calls": calls}, "out": {"storage": {"wp.adsOptOut": "1"}, "calls": calls}}))
    result = subprocess.run(["node", "harness.cjs", "snippet.js", "scenarios.json"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "PRIVATE" not in result.stdout
    events = {"search_analyze", "paste_amazon_link", "analyze_result"}
    expected = [["event", event, {"send_to": SAMPLE["ga4_measurement_id"], "link": link}]
                for event in ("search_analyze", "paste_amazon_link") for link in (True, False)]
    expected += [["event", "analyze_result", {"send_to": SAMPLE["ga4_measurement_id"], "count": count,
                                            "live": True, "matched": False}] for count in (0, 16)]
    for output in json.loads(result.stdout).values():
        assert output["error"] is None
        assert [e for e in output["dataLayer"] if len(e) > 1 and e[1] in events] == (expected if ga4 else [])
        for key in ("fbq", "ttq", "pintrk", "uetq"):
            assert not any(event in str(output[key]) for event in events)


@pytest.mark.parametrize("audit_mode", ["shadow", "enforce"])
def test_enabled_pipeline_rerun_is_idempotent(env, capsys, audit_mode):
    assert load_config(env["cfg"]).search_analyze["enabled"]
    check_rerun(env, capsys, audit_mode)
    assert "analyzeSubmit" in (env["site"] / "index.html").read_text()


@pytest.mark.parametrize("lazy", [False, True])
def test_real_chromium_light_dark_phone_desktop(tmp_path, lazy):
    cfg = analyze_config(strip=True)
    cfg.search_analyze["lazy"] = lazy
    cfg.static_photos = True
    out, *_ = render(tmp_path, cfg, [make_deal(item="Steel kitchen kettle", codes=[])])
    result = run_node("search_analyze_browser.cjs", out, "/opt/pw-browsers/chromium",
                      Path("docs/research/analyze-search").resolve() / ("lazy" if lazy else "inline"))
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
