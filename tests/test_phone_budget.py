"""Offline payload/config/parser/workflow checks; all output lives in tmp_path."""
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import yaml

from dealradar.config import AppConfig, PHONE_BYTE_METRICS, load_config
from scripts.publication_budget import AnalysisError, analyze, main, sizes, summary
from tests.phone_budget_fixture import manifest, publish_fixture, reference_sizes

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests/fixtures/phone_budget_baseline.json"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task206 network unavailable")
    monkeypatch.setattr(httpx.Client, "send", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)


def config_file(tmp_path, budget=None):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"quality": {"phone_budget": budget or {}}}))
    return path


def minimal_publication(tmp_path, content="", *, cfg=None):
    cfg = cfg or AppConfig(custom_domain="wowpriced.com")
    out = tmp_path / "output"
    out.mkdir()
    (out / "index.html").write_text('<!doctype html><html><body><main id="content"></main>'
                                  '<!--fixture-version-->' + content + '</body></html>')
    (out / "deals.json").write_text(json.dumps({"meta": {"site_version": "fixture-version"},
                                               "deals": [{"key": "fixture-1"}]}))
    return out, cfg


def file_deltas(actual, expected):
    """Bounded file labels/digests/sizes, never a public payload excerpt."""
    changed = sorted(k for k in actual.keys() | expected.keys() if actual.get(k) != expected.get(k))
    return {"changed_count": len(changed), "files": [
        {"path": k, "before": expected.get(k), "after": actual.get(k)} for k in changed[:20]]}


def test_ceiling_derivation_and_shipped_switch():
    baseline = json.loads(BASELINE.read_text())
    assert not AppConfig().phone_budget["enabled"]
    cfg = load_config()
    assert not cfg.phone_budget["enabled"] and cfg.amazon_history == "hide"  # Amazon's email 2026-10-17
    assert cfg.phone_budget["lab_lcp_max_ms"] == 2500
    assert cfg.phone_budget["lab_cls_max"] == 0.1
    for name in PHONE_BYTE_METRICS:
        largest = max(mode["sizes"][name]["gzip"] for mode in baseline["modes"].values())
        assert cfg.phone_budget[f"{name}_gzip_max_bytes"] == (largest * 105 + 99) // 100
        assert AppConfig().phone_budget[f"{name}_gzip_max_bytes"] == cfg.phone_budget[f"{name}_gzip_max_bytes"]


@pytest.mark.parametrize("enabled", [None, False, True])
def test_omitted_and_boolean_config(tmp_path, enabled):
    budget = {} if enabled is None else {"enabled": enabled}
    cfg = load_config(config_file(tmp_path, budget))
    assert cfg.phone_budget["enabled"] is (enabled is True)
    assert cfg.phone_budget == AppConfig().phone_budget | budget


@pytest.mark.parametrize("value", [0, 1, "false", "true", None, [], {}])
def test_boolean_is_strict_and_errors_do_not_echo_values(tmp_path, value):
    with pytest.raises(ValueError, match=r"^quality.phone_budget.enabled must be a boolean$"):
        load_config(config_file(tmp_path, {"enabled": value}))


@pytest.mark.parametrize("name", [f"{metric}_gzip_max_bytes" for metric in PHONE_BYTE_METRICS]
                         + ["lab_lcp_max_ms", "lab_cls_max"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), -float("inf"), True,
                                  "PRIVATE-SECRET", None, [], {}])
@pytest.mark.parametrize("enabled", [False, True])
def test_invalid_limits_fail_with_safe_config_errors(tmp_path, name, value, enabled):
    with pytest.raises(ValueError) as caught:
        load_config(config_file(tmp_path, {"enabled": enabled, name: value}))
    assert str(caught.value).startswith(f"quality.phone_budget.{name} must be ")
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("name", [f"{metric}_gzip_max_bytes" for metric in PHONE_BYTE_METRICS])
def test_byte_ceilings_are_integers(tmp_path, name):
    with pytest.raises(ValueError, match="positive integer"):
        load_config(config_file(tmp_path, {"enabled": True, name: 1.5}))


@pytest.mark.parametrize("document", ["[]", "1", '"PRIVATE"', "quality: []",
                                       "quality: null", "quality: {phone_budget: null}",
                                       "quality: {phone_budget: []}",
                                       "quality: {phone_budget: {PRIVATE: true}}"])
def test_malformed_config_roots_have_safe_errors(tmp_path, document):
    path = tmp_path / "config.yaml"
    path.write_text(document)
    with pytest.raises(ValueError) as caught:
        load_config(path)
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("metric", PHONE_BYTE_METRICS)
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_each_gzip_boundary_is_inclusive(tmp_path, metric, offset):
    out, cfg = minimal_publication(tmp_path, '<script>const sample = 1;</script><style>body{color:red}</style>')
    measured = analyze(out, cfg, expected_offers=1)["metrics"][metric]["gzip"]
    cfg.phone_budget |= {"enabled": True, f"{metric}_gzip_max_bytes": measured + offset}
    result = analyze(out, cfg, expected_offers=1)
    assert result["ok"] is (offset >= 0)
    assert result["metrics"][metric]["status"] == ("pass" if offset >= 0 else "fail")
    cfg.phone_budget["enabled"] = False
    assert analyze(out, cfg, expected_offers=1)["ok"]


def test_mime_boundaries_unique_local_assets_and_external_references(tmp_path):
    script = 'const inline = "雪 &amp;";'
    css = 'body { color: teal; }'
    linked_js = b'const linked = 1;'
    linked_css = b'.card { display: block; }'
    markup = '{"@type":"WebSite","name":"Fixture"}'
    html = (f'<script type="Text/JavaScript; charset=utf-8">{script}</script>'
            '<script src="/assets/app.js?v=PRIVATE"></script>'
            '<script type="module" src="https://wowpriced.com:443/assets/./app.js#fragment"></script>'
            '<link rel="modulepreload" href="assets/x/../app.js?v=other">'
            '<script type="application/json">PRIVATE inert JSON</script>'
            f'<script type="application/ld+json">{markup}</script>'
            '<script type="application/json" src="/not-downloaded.js"></script>'
            f'<style type="text/css">{css}</style>'
            '<style type="text/less">PRIVATE inert LESS</style>'
            '<link rel="stylesheet" href="/assets/site.css?secret=PRIVATE">'
            '<link rel="preload" as="style" href="/assets/%73ite.css#fragment">'
            '<script src="https://remote.example.invalid/app.js?SECRET"></script>'
            '<script src="https://remote.example.invalid/app.js?SECRET"></script>'
            '<link rel="stylesheet" href="//remote.example.invalid/style.css?SECRET">')
    out, cfg = minimal_publication(tmp_path, html)
    (out / "assets").mkdir()
    (out / "assets/app.js").write_bytes(linked_js)
    (out / "assets/site.css").write_bytes(linked_css)
    report = analyze(out, cfg, expected_offers=1)
    for key, data in (("home_js", script.encode() + linked_js), ("home_css", css.encode() + linked_css)):
        assert {k: report["metrics"][key][k] for k in ("raw", "gzip")} == sizes(data)
    assert report["json_ld_markup"] == sizes(markup.encode())
    assert report["local_assets"] == {"js": 1, "css": 1}
    assert report["inline_elements"] == {"js": 1, "css": 1, "json_ld": 1}
    assert report["external_count"] == len(report["external"]) == 2
    assert "SECRET" not in json.dumps(report) and "PRIVATE" not in summary(report)
    assert report["metrics"]["home_html"]["raw"] == len((out / "index.html").read_bytes())
    assert sizes(b"abc") == {"raw": 3, "gzip": 23}


def test_preload_mime_types_and_external_script_bodies(tmp_path):
    out, cfg = minimal_publication(tmp_path,
        '<link rel="preload" as="script" type="application/json" href="inert.js">'
        '<link rel="modulepreload" type="text/plain" href="inert-module.js">'
        '<link rel="preload" as="style" type="text/less" href="inert.css">'
        '<link rel="preload" as="Script" type="Text/JavaScript; charset=utf-8" href="app.js">'
        '<link rel="preload" as="Style" type="Text/CSS; charset=utf-8" href="site.css">'
        '<script src="app.js">ignored external-script body</script>')
    (out / "app.js").write_bytes(b"const loaded = true;")
    (out / "site.css").write_bytes(b"body { color: teal; }")
    report = analyze(out, cfg, expected_offers=1)
    for name, file in (("home_js", "app.js"), ("home_css", "site.css")):
        assert {key: report["metrics"][name][key] for key in ("raw", "gzip")} == sizes((out / file).read_bytes())
    assert report["inline_elements"] == {"js": 0, "css": 0, "json_ld": 0}
    assert report["local_assets"] == {"js": 1, "css": 1}


@pytest.mark.parametrize("reference", ["../PRIVATE.js", "%2e%2e/PRIVATE.js", "/%2e%2e/PRIVATE.js",
                                       "/a/../../PRIVATE.js", "/bad%00.js", "/bad%ff.js", "/bad%QZ.js",
                                       "/bad\\file.js", "file:///PRIVATE.js", "data:text/javascript,PRIVATE",
                                       "/missing.js", "/folder.js", "/PRIVATE.txt", ""])
def test_missing_or_unsafe_local_assets_fail_without_payloads(tmp_path, reference):
    out, cfg = minimal_publication(tmp_path, f'<script src="{reference}"></script>')
    (out / "folder.js").mkdir()
    with pytest.raises(AnalysisError) as caught:
        analyze(out, cfg, expected_offers=1)
    assert "home js asset:" in str(caught.value) and "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("file", ["index.html", "deals.json", "app.js", "assets"])
def test_symlinks_cannot_escape_output(tmp_path, file):
    out, cfg = minimal_publication(tmp_path, '<script src="assets/app.js"></script>')
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "app.js").write_text("PRIVATE-CONTENT")
    (outside / "index.html").write_bytes((out / "index.html").read_bytes())
    (outside / "deals.json").write_bytes((out / "deals.json").read_bytes())
    (out / "assets").mkdir()
    (out / "assets/app.js").write_text("const local = 1;")
    target = out / ("assets/app.js" if file == "app.js" else file)
    if target.is_dir():
        (target / "app.js").unlink()
        target.rmdir()
    else:
        target.unlink()
    target.symlink_to(outside if file == "assets" else outside / file)
    with pytest.raises(AnalysisError, match="missing or unsafe file"):
        analyze(out, cfg, expected_offers=1)


@pytest.mark.parametrize("document", [None, [], "PRIVATE", 1, {}, {"meta": [], "deals": []},
                                       {"meta": {"site_version": "fixture-version"}, "deals": [None]},
                                       {"meta": {"site_version": "fixture-version"}, "deals": [{"key": 1}]},
                                       {"meta": {"site_version": "other"}, "deals": [{"key": "one"}]}])
def test_malformed_publication_roots(tmp_path, document):
    out, cfg = minimal_publication(tmp_path)
    (out / "deals.json").write_text(json.dumps(document))
    with pytest.raises(AnalysisError):
        analyze(out, cfg, expected_offers=1)


def test_intended_output_count_markup_and_root_validation(tmp_path):
    out, cfg = minimal_publication(tmp_path)
    for root in (tmp_path / "absent", out / "index.html", "https://PRIVATE.invalid/site"):
        with pytest.raises(AnalysisError, match="local publication"):
            analyze(root, cfg, expected_offers=1)
    with pytest.raises(AnalysisError, match="fixture offer count"):
        analyze(out, cfg, expected_offers=1400)
    for html in ('<html><!--fixture-version--></html>',
                 '<body id="content"><!--fixture-version--><script>unfinished',
                 '<body id="content"><!--fixture-version--><base href="https://PRIVATE.invalid/">'):
        (out / "index.html").write_text(html)
        with pytest.raises(AnalysisError):
            analyze(out, cfg, expected_offers=1)


def test_cli_is_deterministic_bounded_and_safe(tmp_path, capsys):
    out, _ = minimal_publication(tmp_path, ''.join(
        f'<script src="https://remote.example.invalid/{i}.js?PRIVATE"></script>' for i in range(40)))
    cfg_path = config_file(tmp_path, {"enabled": True, "home_html_gzip_max_bytes": 1})
    args = [str(out), "--config", str(cfg_path), "--expected-offers", "1"]
    before = manifest(tmp_path)
    assert main(args) == 1
    first = capsys.readouterr()
    report = json.loads(first.out)
    assert report["external_count"] == 40 and len(report["external"]) == 20
    assert len(first.out) < 5000 and "PRIVATE" not in first.out + first.err
    assert "FAIL" in first.err and "gzip" in first.err
    assert main(args) == 1
    assert capsys.readouterr() == first
    assert manifest(tmp_path) == before
    cli = subprocess.run([sys.executable, "-m", "scripts.publication_budget", *args],
                         cwd=ROOT, capture_output=True, text=True)
    assert cli.returncode == 1 and (cli.stdout, cli.stderr) == (first.out, first.err)
    cfg_path.write_text('quality: {phone_budget: {enabled: "PRIVATE"}}')
    assert main(args) == 1
    failure = capsys.readouterr()
    assert "PRIVATE" not in failure.out + failure.err and "invalid config" in failure.err


def test_missing_css_and_empty_publication_are_measured_safely(tmp_path):
    out, cfg = minimal_publication(tmp_path, '<link rel="stylesheet" href="missing.css">')
    with pytest.raises(AnalysisError, match="home css asset: missing or unsafe file"):
        analyze(out, cfg, expected_offers=1)
    (out / "index.html").write_text('<html><body id="content"><!--fixture-version--></body></html>')
    (out / "deals.json").write_text('{"meta":{"site_version":"fixture-version"},"deals":[]}')
    assert analyze(out, cfg, expected_offers=0)["offers"] == 0


def test_file_delta_diagnostics_name_files_and_sizes_only():
    before = {"index.html": {"raw": 30, "gzip": 40, "sha256": "before"}}
    after = {"index.html": {"raw": 31, "gzip": 41, "sha256": "after"}}
    assert file_deltas(after, before) == {"changed_count": 1, "files": [
        {"path": "index.html", "before": before["index.html"], "after": after["index.html"]}]}


def test_node_enabled_lighthouse_logic(tmp_path):
    subprocess.run(["node", str(ROOT / "tests/phone_budget_lighthouse.cjs"), str(tmp_path)],
                   check=True, capture_output=True, text=True)


@pytest.mark.parametrize("explicit_off", [False, True])
def test_disabled_parser_matches_actual_pre_edit_output(explicit_off):
    baseline = json.loads(BASELINE.read_text())
    env = {**os.environ, "GITHUB_STEP_SUMMARY": ""}
    env.pop("LH_PHONE_BUDGET", None)
    if explicit_off:
        env["LH_PHONE_BUDGET"] = '{"enabled":false}'
    for case in baseline["lighthouse_disabled"]:
        env["LH_MIN"] = case["minimum"]
        result = subprocess.run(["node", str(ROOT / "scripts/lighthouse_summary.js"), *case["files"]],
                                cwd=ROOT, env=env, capture_output=True, text=True)
        assert (result.returncode, result.stdout, result.stderr) == (case["status"], case["stdout"], case["stderr"])


@pytest.mark.parametrize("enabled", [False, True])
def test_workflow_executes_config_to_env_to_parser_without_audit(tmp_path, enabled):
    workflow = yaml.safe_load((ROOT / ".github/workflows/lighthouse.yml").read_text())
    gate = next(s for s in workflow["jobs"]["scan"]["steps"]
                if "lighthouse_summary.js" in s.get("run", ""))
    assert gate["if"] == "always()" and gate["env"] == {"LH_MIN": "85", "LH_NOINDEX_OK": "deal"}
    assert "load_config" in gate["run"] and "LH_PHONE_BUDGET" in gate["run"]
    (tmp_path / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
    path = config_file(tmp_path, {"enabled": enabled, "lab_lcp_max_ms": 1000, "lab_cls_max": 0.02})
    reports = tmp_path / "lighthouse-reports"
    reports.mkdir()
    report = json.loads((ROOT / "tests/fixtures/lighthouse_pass.json").read_text())
    report["audits"] |= {"largest-contentful-paint": {"numericValue": 1000},
                         "cumulative-layout-shift": {"numericValue": 0.02}}
    for page in ("home", "hub", "deal", "best-today"):
        (reports / f"{page}.json").write_text(json.dumps(report))
    env = {**os.environ, **gate["env"], "PYTHONPATH": str(ROOT), "GITHUB_STEP_SUMMARY": ""}
    env.pop("LH_PHONE_BUDGET", None)

    def run():
        return subprocess.run(["bash", "-e", "-o", "pipefail", "-c", gate["run"]],
                              cwd=tmp_path, env=env, capture_output=True, text=True)

    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert ("LCP: 1000 ms; limit 1000 ms" in result.stdout) is enabled
    report["audits"]["largest-contentful-paint"]["numericValue"] = 1001
    (reports / "hub.json").write_text(json.dumps(report))
    result = run()
    assert result.returncode == int(enabled), result.stdout + result.stderr
    assert ("FAIL" in result.stdout) is enabled
    path.write_text('quality: {phone_budget: {enabled: "PRIVATE"}}')
    result = run()
    assert result.returncode == 1 and "invalid quality config" in result.stderr
    assert "PRIVATE" not in result.stdout + result.stderr
