"""Task 286: fresh Home links, private history and frozen OFF bytes, offline."""
import json
import socket
import subprocess
from pathlib import Path

import httpx
import pytest

from tests.history_pins import pre_task_288, pre_task_290

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import site, tool_pages
from dealradar.publish.seo_audit import Page
from dealradar.publish.tracking import head_snippet
from tests.test_offscreen_cards import offers
from tests.test_pwa import baseline_config, tree_hash
from tests.test_support import NOW, render
from tests.test_tracking import HARNESS, SAMPLE, _script


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task286 network unavailable")
    monkeypatch.setattr(httpx.Client, "send", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(site, "util_now", lambda: NOW)


@pytest.mark.parametrize("value", [False, True])
def test_boolean_and_shipped_config(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"site": {"home_link_resets": value}}))
    assert load_config(path).home_link_resets is value
    path.write_text("{}")
    assert load_config(path).home_link_resets is AppConfig().home_link_resets is False
    assert load_config().home_link_resets is True


@pytest.mark.parametrize("value", [0, 1, "true", "false", None, [], {}])
def test_non_boolean_rejected(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"site": {"home_link_resets": value}}))
    with pytest.raises(ValueError, match=r"^site.home_link_resets must be a boolean$"):
        load_config(path)


# Captured from the untouched publisher before task 286; never re-pin.
@pytest.mark.parametrize("mode,expected", [
    ("membership", "169be60f7df581d048d16036cc926f3519867bb2db6ee4f158c8c1ba11c7dafc"),
    ("open", "d813fd0efc16aba7fd734ebfbe992e97341e36f70b84716ef8f065c3a3f78c66"),
    ("support", "dca92097f3c9e3cf16fbf2d7f1e805901732bb5804fec14dab1a90ffc8b0e62e"),
    ("shipped", "930e4c1238d1c5b9baadb340cabe336042fb48aec4b83e245b0cef4afebb9ff7"),
    ("current", "a21f1be1f0bd043582131b4c487cebacb4511fd9b8cee92e95c324c7464f7690"),
])
def test_off_and_missing_preserve_recorded_bytes(tmp_path, mode, expected):
    cfg = pre_task_290(pre_task_288(load_config())) if mode == "current" else baseline_config(mode)  # recorded before task 288
    cfg.home_link_resets = False
    out, *_ = render(tmp_path, cfg, offers())
    assert tree_hash(out) == expected
    assert b"home_reset" not in (out / "index.html").read_bytes()
    del cfg.home_link_resets
    render(tmp_path, cfg, offers())
    assert tree_hash(out) == expected
    cfg.home_link_resets = True
    render(tmp_path, cfg, offers())
    assert tree_hash(out) != expected
    home = (out / "index.html").read_text()
    assert home.count('title="Back to all deals"') == 2
    assert home.count('style="cursor:pointer"') == 2
    assert 'tabindex' not in home.split('id="drop"', 1)[1].split('>', 1)[0]
    cfg.home_link_resets = False
    render(tmp_path, cfg, offers())
    assert tree_hash(out) == expected


@pytest.mark.parametrize("mode", ["membership", "open", "support"])
@pytest.mark.parametrize("strip_comments", [False, True])
def test_emitted_home_links_and_history(tmp_path, mode, strip_comments):
    cfg = baseline_config(mode)
    cfg.home_link_resets = True
    cfg.strip_comments = strip_comments
    out, *_ = render(tmp_path, cfg, offers())
    for scenario in ("home-links", "pagination-restore"):
        result = subprocess.run(["node", str(Path(__file__).with_name("browse_logic.cjs")),
                                 str(out / "index.html"), str(out / "deals.json"), scenario],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("enabled", [False, True])
def test_tracking_only_sends_valid_methods_without_private_input(tmp_path, enabled):
    cfg = AppConfig(home_link_resets=enabled, tracking=SAMPLE)
    calls = [["home_reset", {"method": method, "query": "PRIVATE", "url": "PRIVATE"}]
             for method in ("logo", "ticker", "timer", "PRIVATE", "", None)]
    scenarios = {"open": {"calls": calls}, "gpc": {"gpc": True, "calls": calls},
                 "out": {"storage": {"wp.adsOptOut": "1"}, "calls": calls},
                 "internal": {"search": "?internal=1", "calls": calls}}
    (tmp_path / "snippet.js").write_text(_script(head_snippet(cfg)))
    (tmp_path / "harness.cjs").write_text(HARNESS)
    (tmp_path / "scenarios.json").write_text(json.dumps(scenarios))
    result = subprocess.run(["node", "harness.cjs", "snippet.js", "scenarios.json"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "PRIVATE" not in result.stdout
    for output in json.loads(result.stdout).values():
        assert output["error"] is None
        expected = [["event", "home_reset", {"method": method, "send_to": SAMPLE["ga4_measurement_id"]}]
                    for method in ("logo", "ticker", "timer")] if enabled else []
        assert [e for e in output["dataLayer"] if e[:2] == ["event", "home_reset"]] == expected
        assert not any("home_reset" in str(e) for key in ("fbq", "ttq", "pintrk", "uetq")
                       for e in (output[key] or []))


def test_enabled_publish_rerun_is_idempotent(tmp_path):
    cfg = AppConfig(home_link_resets=True, support_enabled=True,
                    engagement={"saved_deals": True})
    db = DB(":memory:")
    try:
        out = tmp_path / "site"
        def publish():
            return site.render_site(db, cfg, NOW, out_dir=out, deals=offers(),
                                    last_run={"run_key": "task286-fixed"})
        first = publish()
        before, state = tree_hash(out), list(db.conn.iterdump())
        assert publish() == first
        assert tree_hash(out) == before
        assert list(db.conn.iterdump()) == state
    finally:
        db.close()


@pytest.mark.parametrize("tools", ["off", "preview", "public"])
def test_home_links_in_real_chromium(tmp_path, tools):
    cfg = AppConfig(home_link_resets=True, support_enabled=True, offscreen_cards=True,
                    engagement={"saved_deals": True}, tracking={"ga4_measurement_id": SAMPLE["ga4_measurement_id"]})
    cfg.tool_pages = {"enabled": tools != "off", "public": tools == "public"}
    out, *_ = render(tmp_path, cfg, offers())
    home = Page((out / "index.html").read_text(), "/")
    footer = home.first(ident="footerlinks")
    footer_hrefs = {n.attrs.get("href") for n in footer.nodes() if n.tag == "a"}
    for slug in tool_pages.PAGES:
        assert (out / slug / "index.html").exists() is (tools != "off")
        assert ("/" + slug + "/" in home.hrefs) is (tools == "public")
        assert ("/" + slug + "/" in footer_hrefs) is (tools == "public")
    result = subprocess.run(["node", str(Path(__file__).with_name("home_links_browser.cjs")), str(out), tools],
                            capture_output=True, text=True, timeout=120)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout)
