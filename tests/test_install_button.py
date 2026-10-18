"""Task 290: frozen OFF output, install flows and local-only Chromium checks."""

import hashlib
import json
import re
import socket
import subprocess
from pathlib import Path

import httpx
import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import pwa, site
from dealradar.publish.tracking import head_snippet
from tests.history_pins import pre_task_290, pre_task_290_install
from tests.test_pwa import baseline_config, sample_deals, tree_hash
from tests.test_support import NOW, render
from tests.test_tracking import HARNESS, SAMPLE, _script

ROOT = Path(__file__).resolve().parents[1]
BASELINE = json.loads((ROOT / "tests/fixtures/install_button_off.json").read_text())


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task290 network unavailable")
    monkeypatch.setattr(httpx.Client, "send", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(site, "util_now", lambda: NOW)


@pytest.mark.parametrize("value", [False, True])
def test_boolean_defaults_and_shipped_config(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"pwa": {"install_button": value}}))
    assert load_config(path).pwa_install_button is value
    path.write_text("{}")
    assert load_config(path).pwa_install_button is AppConfig().pwa_install_button is False
    assert load_config().pwa_install_button is True


@pytest.mark.parametrize("value", [0, 1, "true", "false", None, [], {}])
def test_non_boolean_rejected(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"pwa": {"install_button": value}}))
    with pytest.raises(ValueError, match=r"^pwa.install_button must be a boolean$"):
        load_config(path)


@pytest.mark.parametrize("mode", BASELINE)
def test_off_missing_and_return_to_off_match_every_recorded_file(tmp_path, mode):
    # These per-file hashes were captured from the untouched task-290 publisher.
    cfg = pre_task_290(pre_task_290_install(load_config())) if mode.startswith("current") or mode == "android" else baseline_config(mode)
    cfg.pwa_enabled = mode != "current-pwa-off"
    if mode == "android":
        cfg.pwa_android["enabled"] = True

    def publish():
        out, *_ = render(tmp_path, cfg, sample_deals())
        return {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(out.rglob("*")) if p.is_file()}

    assert publish() == BASELINE[mode]
    del cfg.pwa_install_button
    assert publish() == BASELINE[mode]
    cfg.pwa_install_button = True
    assert (publish() != BASELINE[mode]) is cfg.pwa_enabled
    cfg.pwa_install_button = False
    assert publish() == BASELINE[mode]


@pytest.mark.parametrize("strip_comments", [False, True])
@pytest.mark.parametrize("android", [False, True])
def test_enabled_markup_footer_manifest_and_emitted_logic(tmp_path, strip_comments, android):
    cfg = AppConfig(pwa_enabled=True, pwa_install_button=True, support_enabled=True,
                    strip_comments=strip_comments)
    cfg.pwa_android["enabled"] = android
    out, *_ = render(tmp_path, cfg, sample_deals())
    home = (out / "index.html").read_text()
    assert home.count('id="pwa-header-install"') == 1
    assert home.index('id="pwa-header-install"') < home.index('id="subscribe"') < home.index('</header>')
    assert 'aria-label="Install app" hidden' in home
    assert '<span class="pwa-label-compact" aria-hidden="true">App</span>' in home
    for page in out.rglob("*.html"):
        if page.name == "offline.html":
            continue
        html = page.read_text()
        assert html.count('id="pwa-help"') == 1
        assert '<dialog id="pwa-help" aria-labelledby="pwa-help-title" aria-describedby="pwa-help-steps" hidden>' in html
        assert '<button id="pwa-install" class="pwa-link" type="button" hidden>Install app</button>' in html
        assert html.index('id="pwa-actions"') > html.index('<footer>')
        assert 'then Add to Home Screen' in html
        if not android:
            assert 'serviceWorker' not in html and 'caches.open' not in html
    manifest = json.loads((out / "manifest.webmanifest").read_text())
    assert manifest["display"] == "standalone" and manifest["id"] == manifest["start_url"] == "/"
    assert {i["sizes"] for i in manifest["icons"] if i["purpose"] == "any"} == {"192x192", "512x512"}
    script = next(s for s in re.findall(r"<script>(.*?)</script>", home, re.S) if "beforeinstallprompt" in s)
    emitted = tmp_path / "install.js"
    emitted.write_text(script)
    result = subprocess.run(["node", str(ROOT / "tests/install_button_logic.cjs"), str(emitted)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    if not strip_comments:
        before = tree_hash(out)
        pwa.publish(out, cfg)
        assert tree_hash(out) == before


@pytest.mark.parametrize("pwa_enabled,enabled,ga4", [(True, True, True), (True, False, True),
                                                    (False, True, True), (True, True, False)])
def test_install_events_are_ga4_only_and_never_include_input(tmp_path, pwa_enabled, enabled, ga4):
    ids = dict(SAMPLE)
    if not ga4:
        ids.pop("ga4_measurement_id")
    cfg = AppConfig(pwa_enabled=pwa_enabled, pwa_install_button=enabled, tracking=ids)
    events = ("install_click", "install_accepted", "install_dismissed", "installed")
    calls = [[name, {"email": "PRIVATE", "url": "PRIVATE", "method": "PRIVATE", "count": 42}]
             for name in events]
    scenarios = {"plain": {"calls": calls}, "gpc": {"gpc": True, "calls": calls},
                 "opted_out": {"storage": {"wp.adsOptOut": "1"}, "calls": calls}}
    (tmp_path / "snippet.js").write_text(_script(head_snippet(cfg)))
    (tmp_path / "harness.cjs").write_text(HARNESS)
    (tmp_path / "scenarios.json").write_text(json.dumps(scenarios))
    result = subprocess.run(["node", "harness.cjs", "snippet.js", "scenarios.json"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "PRIVATE" not in result.stdout
    for output in json.loads(result.stdout).values():
        assert output["error"] is None
        expected = [["event", event, {"send_to": SAMPLE["ga4_measurement_id"]}] for event in events
                    if pwa_enabled and ga4 and (enabled or event == "install_accepted")]
        assert [e for e in output["dataLayer"] or [] if e[0] == "event"] == expected
        assert not any(event in json.dumps(output[k]) for event in events
                       for k in ("fbq", "ttq", "pintrk", "uetq"))


@pytest.mark.parametrize("shipped", [False, True])
def test_enabled_rerun_preserves_every_byte_and_database_row(tmp_path, shipped):
    cfg = load_config() if shipped else AppConfig(pwa_enabled=True, pwa_install_button=True, support_enabled=True)
    db = DB(":memory:")
    try:
        out = tmp_path / "site"
        def publish():
            return site.render_site(db, cfg, NOW, out_dir=out, deals=sample_deals(),
                                    last_run={"run_key": "task290-fixed"})
        first = publish()
        before, state = tree_hash(out), list(db.conn.iterdump())
        assert publish() == first
        assert tree_hash(out) == before
        assert list(db.conn.iterdump()) == state
    finally:
        db.close()


def test_real_chromium_installability_layout_and_screenshots(tmp_path):
    cfg = load_config()
    cfg.tracking = {}
    cfg.max_photoless_h = None
    out, *_ = render(tmp_path, cfg, sample_deals())
    result = subprocess.run(["node", str(ROOT / "tests/install_button_browser.cjs"), str(out),
                             str(ROOT / "docs/research/install-button")],
                            capture_output=True, text=True, timeout=120)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout)
