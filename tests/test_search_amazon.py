"""Tagged home search recovery, privacy and payload budgets; entirely offline."""
import json
import re
import shutil
import socket
import subprocess
from pathlib import Path

import httpx
import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import site
from dealradar.publish.tracking import head_snippet
from scripts.publication_budget import analyze
from tests.phone_budget_fixture import fixture_config, publish_fixture
from tests.test_publish_site import make_deal
from tests.test_support import NOW, render
from tests.test_tracking import HARNESS, SAMPLE, _script


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task259 network unavailable")
    monkeypatch.setattr(httpx.Client, "send", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)


@pytest.mark.parametrize("value", [False, True])
def test_config_boolean_and_shipped_on(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"site": {"search_amazon": value}}))
    assert load_config(path).search_amazon is value
    path.write_text("{}")
    assert load_config(path).search_amazon is False
    assert AppConfig().search_amazon is False
    assert load_config().search_amazon is True


@pytest.mark.parametrize("value", [0, 1, "true", "false", None, [], {}])
def test_config_rejects_non_boolean(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"site": {"search_amazon": value}}))
    with pytest.raises(ValueError, match=r"^site.search_amazon must be a boolean$"):
        load_config(path)


def run_node(script, *args):
    if not shutil.which("node"):
        pytest.skip("Node unavailable")
    return subprocess.run(["node", str(Path(__file__).with_name(script)), *map(str, args)],
                          capture_output=True, text=True, timeout=90)


@pytest.mark.parametrize("support,open_access", [(True, True), (False, True), (False, False)])
def test_published_search_logic(tmp_path, monkeypatch, support, open_access):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = AppConfig(search_amazon=True, affiliate_amazon_tag="example-20",
                    support_enabled=support, paywall_open_access=open_access,
                    engagement={"saved_deals": True})
    out, *_ = render(tmp_path, cfg, [make_deal(item="Steel kitchen kettle", codes=[])])
    home = (out / "index.html").read_text()
    assert "{{SEARCH_AMAZON_TAG}}" not in home and "/*search-amazon*/" not in home
    for i, code in enumerate(re.findall(r"<script>(.*?)</script>", home, re.S)):
        path = tmp_path / f"script-{i}.js"
        path.write_text(code)
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    result = run_node("search_amazon_logic.cjs", out / "index.html")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("history", ["hide", "show"])
def test_history_switch_keeps_links_and_disabled_output_has_no_feature(tmp_path, history):
    cfg = AppConfig(search_amazon=True, affiliate_amazon_tag="example-20", amazon_history=history)
    out, *_ = render(tmp_path, cfg)
    assert "function searchAmazonHtml" in (out / "index.html").read_text()
    cfg.search_amazon = False
    out, *_ = render(tmp_path, cfg)
    assert "search-amazon" not in (out / "index.html").read_text()
    cfg.search_amazon, cfg.affiliate_amazon_tag = True, ""
    out, *_ = render(tmp_path, cfg)
    assert "search-amazon" not in (out / "index.html").read_text()


@pytest.mark.parametrize("ga4", [False, True])
def test_tracking_is_parameterless_and_uses_existing_privacy_gates(tmp_path, ga4):
    if not shutil.which("node"):
        pytest.skip("Node unavailable")
    ids = {k: v for k, v in SAMPLE.items() if ga4 or k != "ga4_measurement_id"}
    cfg = AppConfig(search_amazon=True, affiliate_amazon_tag="example-20", tracking=ids)
    private = {key: "PRIVATE-QUERY" for key in ("query", "url", "store", "category", "method")}
    calls = [["search_amazon", private]]
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
    for name, output in json.loads(result.stdout).items():
        assert output["error"] is None
        assert [e for e in output["dataLayer"] if e[:2] == ["event", "search_amazon"]] == (
            [["event", "search_amazon", {"send_to": ids["ga4_measurement_id"]}]] if ga4 else [])
        if name == "open":
            assert [e for e in output["fbq"] if e[:2] == ["trackCustom", "SearchAmazon"]] == [
                ["trackCustom", "SearchAmazon", {}]]
        else:
            assert output["fbq"] is None
        assert not any("SearchAmazon" in str(e) or "search_amazon" in str(e)
                       for key in ("ttq", "pintrk", "uetq") for e in (output[key] or []))


@pytest.mark.parametrize("support", [True, False])
def test_search_in_chromium(tmp_path, support):
    cfg = AppConfig(search_amazon=True, affiliate_amazon_tag="example-20",
                    support_enabled=support, paywall_open_access=True,
                    engagement={"saved_deals": True})
    out, *_ = render(tmp_path, cfg, [make_deal(item="Steel kitchen kettle", codes=[])])
    result = run_node("search_amazon_browser.cjs", out, "/opt/pw-browsers/chromium")
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr


def test_shipped_home_stays_within_phone_budgets(tmp_path):
    cfg = fixture_config("shipped")
    assert cfg.search_amazon and cfg.affiliate_amazon_tag
    cfg.phone_budget["enabled"] = True

    def inspect(db, out):
        result = analyze(out, cfg, expected_offers=1400)
        assert result["ok"], result["metrics"]
        print(json.dumps({k: v["gzip"] for k, v in result["metrics"].items()}, sort_keys=True))

    publish_fixture(tmp_path, cfg, inspect=inspect)
