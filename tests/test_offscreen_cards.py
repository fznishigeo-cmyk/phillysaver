"""Exact restore and optional card containment, using synthetic local offers."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import site
from tests.test_publish_site import make_deal
from tests.test_pwa import baseline_config, tree_hash
from tests.test_support import NOW, render


def offers():
    return [make_deal(dedupe_key=f"task237-{i}", item=f"Kitchen storage set {i}",
                      codes=[], conditions=[], category="kitchen",
                      image_url="https://images.example.invalid/card.png") for i in range(180)]


@pytest.mark.parametrize("value", [False, True])
def test_boolean_config(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"site": {"offscreen_cards": value}}))
    assert load_config(path).offscreen_cards is value
    path.write_text("{}")
    assert load_config(path).offscreen_cards is False
    assert AppConfig().offscreen_cards is False
    assert load_config().offscreen_cards is True  # shipped after the 390/1280 Chromium restore checks


@pytest.mark.parametrize("value", [0, 1, "false", "true", None, [], {}])
def test_non_boolean_rejected(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"site": {"offscreen_cards": value}}))
    with pytest.raises(ValueError, match=r"^site.offscreen_cards must be a boolean$"):
        load_config(path)


# Task 237 deliberately re-pins the shared restore/sizing JS. The offscreen
# switch itself adds only CSS and its derived version, in all access modes.
@pytest.mark.parametrize("mode,expected", [
    ("membership", "489d1d02f241e1ebe54e84f2a4ae5c36daadbade57a7784b78263ea03f0477c1"),
    ("open", "b26de959ff433cb2687af92e517ca60100df9bc0113e4acbf0df09aea68e936a"),
    ("support", "91c77988b99c3ff7ba842caeb623e67d0a981f996fbec09cb0bc6b4ab972c230"),
    ("shipped", "145e417e40d46eda3289bcf930eb694149ace00c9c739d30eb838eba2a08686e"),
])
@pytest.mark.usefixtures("pre_extension_privacy")
def test_off_bytes_and_on_only_css(tmp_path, monkeypatch, mode, expected):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = baseline_config(mode)
    cfg.offscreen_cards = False
    out, before, full_before, _ = render(tmp_path, cfg, offers())
    original = {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert tree_hash(out) == expected
    assert b"content-visibility:auto" not in original["index.html"]
    cfg.offscreen_cards = True
    out, after, full_after, _ = render(tmp_path, cfg, offers())
    enabled = {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    old_version = before["meta"]["site_version"].encode()
    new_version = after["meta"]["site_version"].encode()
    assert old_version != new_version
    assert original.keys() == enabled.keys()
    for name, content in enabled.items():
        if name == "index.html":
            assert content.count(site.OFFSCREEN_CARDS_STYLE.encode()) == 1
            content = content.replace(site.OFFSCREEN_CARDS_STYLE.encode(), b"")
        else:
            assert b"content-visibility:auto" not in content, name
        assert content.replace(new_version, old_version) == original[name], name
    full_after["meta"]["site_version"] = full_before["meta"]["site_version"]
    assert full_after == full_before
    cfg.offscreen_cards = False
    out, _, _, _ = render(tmp_path, cfg, offers())
    assert tree_hash(out) == expected


@pytest.mark.parametrize("enabled", [False, True])
def test_local_browser_restore(tmp_path, monkeypatch, enabled):
    if not shutil.which("node"):
        pytest.skip("Node unavailable")
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    # The shipped config filters these synthetic offers out of the public
    # wall; the open-access baseline publishes all 180.
    cfg = baseline_config("open")
    cfg.offscreen_cards = enabled
    out, doc, *_ = render(tmp_path, cfg, offers())
    assert len(doc["deals"]) >= 120
    result = subprocess.run(["node", str(Path(__file__).with_name("offscreen_cards_browser.cjs")), str(out)],
                            capture_output=True, text=True, timeout=90)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout)


def test_pagination_and_restore_when_auto_loading_removes_button(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    out, *_ = render(tmp_path, baseline_config("open"), [])
    result = subprocess.run(["node", str(Path(__file__).with_name("browse_logic.cjs")),
                             str(out / "index.html"), str(out / "deals.json"), "pagination-restore"],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
