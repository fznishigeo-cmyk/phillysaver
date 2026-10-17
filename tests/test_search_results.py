"""Task 280: offline function, emitted page, historical bytes and browser QA."""

import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import yaml

from dealradar.config import AppConfig, load_config, validated_search
from dealradar.publish import site
from dealradar.publish.tracking import head_snippet
from scripts.publication_budget import analyze
from tests.phone_budget_fixture import publish_fixture
from tests.test_publish_site import make_deal
from tests.test_search_amazon import run_node
from tests.test_support import NOW, render
from tests.test_tracking import HARNESS, SAMPLE, _script


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task280 is offline")
    monkeypatch.setattr(httpx.Client, "send", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(site, "util_now", lambda: NOW)


@pytest.mark.parametrize("mode", ["off", "preview", "on", False, True])
def test_config_switch_and_shipped_defaults(tmp_path, mode):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"search": {"amazon_results": mode}}))
    expected = ("on" if mode else "off") if type(mode) is bool else mode
    assert load_config(path).search["amazon_results"] == expected
    assert AppConfig().search["amazon_results"] == "off"
    assert load_config().search == {"amazon_results": "preview", "cache_hours": 6,
                                    "tokens_per_day": 3000, "max_per_minute": 20}


@pytest.mark.parametrize("value", [[], "private", 1, {"amazon_results": "bad"},
    {"tokens_per_day": True}, {"tokens_per_day": -1}, {"cache_hours": 25},
    {"max_per_minute": "20"}, {"cache_hours": None}])
def test_invalid_config(value):
    with pytest.raises(ValueError, match="^search"):
        validated_search(value)


@pytest.mark.parametrize("mode", ["preview", "on"])
@pytest.mark.parametrize("support", [True, False])
@pytest.mark.parametrize("strip", [True, False])
def test_emitted_page_and_logic(tmp_path, mode, support, strip):
    cfg = AppConfig(search_amazon=True, affiliate_amazon_tag="example-20", support_enabled=support,
                    strip_comments=strip, search=validated_search({"amazon_results": mode}))
    out, *_ = render(tmp_path, cfg, [make_deal(item="Steel kitchen kettle", codes=[])])
    home = (out / "index.html").read_text()
    assert json.loads((out / "search-config.json").read_text()) == cfg.search
    assert "{{SEARCH_RESULTS}}" not in home
    for i, script in enumerate(re.findall(r"<script>(.*?)</script>", home, re.S)):
        path = tmp_path / f"script-{i}.js"
        path.write_text(script)
        check = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        assert check.returncode == 0, check.stderr
    result = run_node("search_results_logic.cjs", out / "index.html")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("support,open_access", [(True, True), (False, True), (False, False)])
def test_off_restores_every_file_and_idempotent_on_rerun(tmp_path, support, open_access):
    cfg = AppConfig(search_amazon=True, affiliate_amazon_tag="example-20", support_enabled=support,
                    paywall_open_access=open_access)
    def snapshot():
        out, *_ = render(tmp_path, cfg, [make_deal(codes=[])])
        return {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    original = snapshot()
    cfg.search["amazon_results"] = "preview"
    enabled = snapshot()
    assert enabled != original
    assert snapshot() == enabled
    cfg.search["amazon_results"] = "off"
    assert snapshot() == original


@pytest.mark.parametrize("fallback,tag", [(False, "example-20"), (True, "")])
def test_missing_tag_or_disabled_amazon_fails_closed(tmp_path, fallback, tag):
    cfg = AppConfig(search_amazon=fallback, affiliate_amazon_tag=tag,
                    search=validated_search({"amazon_results": "preview"}))
    out, *_ = render(tmp_path, cfg)
    assert not (out / "search-config.json").exists()
    assert "amazonResultsSettings" not in (out / "index.html").read_text()


def test_function_node_unit_tests():
    result = run_node("search_results_function.test.mjs")
    assert result.returncode == 0, result.stdout + result.stderr


def test_results_event_is_ga4_count_only(tmp_path):
    cfg = AppConfig(search_amazon=True, affiliate_amazon_tag="example-20", tracking=SAMPLE,
                    search=validated_search({"amazon_results": "preview"}))
    calls = [["search_amazon_results", {"count": count, "query": "PRIVATE", "url": "PRIVATE"}]
             for count in [0, 10, 11, -1, "1", True]]
    (tmp_path / "snippet.js").write_text(_script(head_snippet(cfg)))
    (tmp_path / "harness.cjs").write_text(HARNESS)
    (tmp_path / "scenarios.json").write_text(json.dumps({"open": {"calls": calls},
        "gpc": {"gpc": True, "calls": calls}, "out": {"storage": {"wp.adsOptOut": "1"}, "calls": calls}}))
    result = subprocess.run(["node", "harness.cjs", "snippet.js", "scenarios.json"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "PRIVATE" not in result.stdout
    for output in json.loads(result.stdout).values():
        assert output["error"] is None
        assert [e for e in output["dataLayer"] if e[:2] == ["event", "search_amazon_results"]] == [
            ["event", "search_amazon_results", {"count": n, "send_to": SAMPLE["ga4_measurement_id"]}] for n in [0, 10]]
        for key in ("fbq", "ttq", "pintrk", "uetq"):
            assert "search_amazon_results" not in str(output[key])


@pytest.mark.parametrize("lane", ["fast", "hourly"])
def test_pages_deploy_keeps_functions_without_touching_search_secret(lane):
    doc = yaml.safe_load(Path(f".github/workflows/{lane}.yml").read_text())
    job, = doc["jobs"].values()
    step = next(s for s in job["steps"] if s.get("name") == "Deploy site to Cloudflare Pages")
    assert step["working-directory"] == "."
    assert "KEEPA_KEY" not in step["env"]
    assert any(s.get("env", {}).get("KEEPA_KEY") == "${{ secrets.KEEPA_KEY }}"
               for s in job["steps"] if s is not step)
    assert "pages secret" not in str(doc)
    assert "test -f ./functions/api/search.js" in step["run"]
    assert "pages deploy site --project-name wowpriced" in step["run"]


def search_secret_workflow():
    doc = yaml.safe_load(Path(".github/workflows/install-search-secret.yml").read_text())
    assert doc["name"] == "install search secret"
    assert doc.get("on", doc.get(True)) == {"workflow_dispatch": None}
    job, = doc["jobs"].values()
    step = next(s for s in job["steps"] if s.get("name") == "Install Pages search secret")
    assert step["env"]["KEEPA_KEY"] == "${{ secrets.KEEPA_KEY }}"
    assert "pages secret list" not in str(doc)
    return step["run"]


@pytest.mark.parametrize("key", ["", "PRIVATE-test-key"])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_secret_install_is_manual_only_and_silent(tmp_path, key, exit_code):
    provision = search_secret_workflow()
    stub = tmp_path / "npx"
    stub.write_text(f"#!{sys.executable}\n" + '''import os, sys
from pathlib import Path
assert sys.argv[1:] == ['--yes', 'wrangler@4', 'pages', 'secret', 'put',
                         'KEEPA_KEY', '--project-name', 'wowpriced']
value = sys.stdin.read()
Path(os.environ['SAVED']).write_text(value)
print(value)  # Suppress both successful output and upstream error messages.
print(value, file=sys.stderr)
sys.exit(int(os.environ['EXIT_CODE']))
''')
    stub.chmod(0o755)
    saved = tmp_path / "received"
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "SAVED": str(saved), "KEEPA_KEY": key, "EXIT_CODE": str(exit_code),
           "CLOUDFLARE_API_TOKEN": "PRIVATE-cloudflare-token", "CLOUDFLARE_ACCOUNT_ID": "test-account"}
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", provision], env=env,
                            capture_output=True, text=True)
    assert result.returncode == (exit_code if key else 0)
    assert result.stdout == result.stderr == ""
    assert saved.exists() == bool(key)
    if saved.exists():
        assert saved.read_text() == key


def test_shipped_preview_phone_budget(tmp_path):
    cfg = load_config()
    cfg.phone_budget["enabled"] = True
    def inspect(db, out):
        result = analyze(out, cfg, expected_offers=1393)  # task 283: seven older photo-less offers
        assert result["ok"], result["metrics"]
    publish_fixture(tmp_path, cfg, inspect=inspect)


def test_real_chromium_preview(tmp_path):
    cfg = AppConfig(search_amazon=True, affiliate_amazon_tag="example-20", support_enabled=True,
                    search=validated_search({"amazon_results": "preview"}))
    out, *_ = render(tmp_path, cfg, [make_deal(item="Steel kitchen kettle", codes=[])])
    result = run_node("search_results_browser.cjs", out, "/opt/pw-browsers/chromium")
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
