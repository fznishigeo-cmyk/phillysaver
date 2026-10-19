"""Task 300: a clock mark changes Trending even with an unchanged publish."""
import json
import socket
from pathlib import Path

import httpx
import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import site
from tests.history_pins import pre_header_polish, pre_same_product_spacing
from tests.test_offscreen_cards import offers
from tests.test_pwa import tree_hash
from tests.test_search_amazon import run_node
from tests.test_support import NOW, render


def config(mode="support"):
    cfg = load_config()
    cfg.drop_shuffle = False
    # Isolate the drop rollback from task 300's separate search_more change and task 301's glow.
    cfg.search_more = {"enabled": False}
    cfg.search_box_glow = False
    cfg.card_tidy = False
    pre_same_product_spacing(cfg)
    pre_header_polish(cfg)
    cfg.search = dict(cfg.search, tokens_per_day=5000)  # recorded before the ad-day raise
    cfg.support_enabled = mode == "support"
    cfg.paywall_open_access = mode != "membership"
    cfg.strip_comments = mode != "unstripped"
    return cfg


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task300 is offline")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(site, "util_now", lambda: NOW)


@pytest.mark.parametrize("mode", ["support", "membership", "unstripped"])
def test_off_missing_and_back_off_bytes(tmp_path, mode):
    baseline = json.loads(Path("tests/fixtures/drop_shuffle_off.json").read_text())
    cfg = config(mode)
    for value in (None, False, True, False):
        if value is None:
            del cfg.drop_shuffle
        else:
            cfg.drop_shuffle = value
        out, *_ = render(tmp_path, cfg, offers())
        assert (tree_hash(out) == baseline[mode]) is (not value)


@pytest.mark.parametrize("value", [False, True])
def test_config(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"site": {"drop_shuffle": value}}))
    assert load_config(path).drop_shuffle is value
    path.write_text("{}")
    assert load_config(path).drop_shuffle is AppConfig().drop_shuffle is False
    assert load_config().drop_shuffle is True


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_invalid_config(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"site": {"drop_shuffle": value}}))
    with pytest.raises(ValueError, match="site.drop_shuffle must be a boolean"):
        load_config(path)


@pytest.mark.parametrize("manifest,strip", [(False, False), (True, True)])
def test_emitted_drop_logic(tmp_path, manifest, strip):
    cfg = load_config()
    cfg.drop_fresh = False
    cfg.tracking = {}
    cfg.manifest_refresh["enabled"] = manifest
    cfg.strip_comments = strip
    out, *_ = render(tmp_path, cfg)
    result = run_node("drop_shuffle_logic.cjs", out / "index.html", out / "deals.json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Clock-only drops" in result.stdout


def test_browser_fixture_without_launching_chromium():
    result = run_node("drop_shuffle_browser.cjs", "--build-only")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Drop browser fixture passed" in result.stdout
