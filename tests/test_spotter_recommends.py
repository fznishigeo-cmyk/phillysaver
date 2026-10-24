"""Operator (Nishi) 2026-10-24 ~04:00Z: no "wowpriced recommends" strip in the analyzer box (hide_spotter_recommends)."""
import json
import re

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import spotter_recommends
from tests.history_pins import pre_social_profiles
from tests.test_tools_merge import build, files

PAGES = {"deal-spotter/index.html", "amazon-deal-checker/index.html", "amazon-discount-finder/index.html"}
STRIP = re.compile(r'<div class="recommends" aria-labelledby="recommends-title">.*?</div></div>(?=<div class="modal-bottom">)', re.S)


def version(out):
    return json.loads((out / "deals.json").read_text())["meta"]["site_version"].encode()


@pytest.fixture(scope="module")
def sites(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("spotter-recommends")
    with pytest.MonkeyPatch.context() as monkeypatch:
        on, _ = build(tmp, monkeypatch, True, "on")
        off, _ = build(tmp, monkeypatch, True, "off", hide_spotter_recommends=False)
        # The same build with the feature removed altogether.
        monkeypatch.setattr(spotter_recommends, "tokens", lambda tokens, cfg: tokens)
        monkeypatch.setattr(spotter_recommends, "site_version", lambda version, cfg: version)
        without, _ = build(tmp, monkeypatch, True, "without", hide_spotter_recommends=False)
    return {"on": on, "off": off, "without": without}


def test_switch_shipped_on_default_off_strict_and_pinned(tmp_path):
    assert load_config().hide_spotter_recommends is True  # operator 2026-10-24 ~04:00Z
    assert AppConfig().hide_spotter_recommends is False
    assert pre_social_profiles(AppConfig(hide_spotter_recommends=True)).hide_spotter_recommends is False
    (tmp_path / "missing.yaml").write_text("{}\n")
    assert load_config(tmp_path / "missing.yaml").hide_spotter_recommends is False
    for value in ("1", "'true'", "null", "[]"):
        path = tmp_path / "config.yaml"
        path.write_text(f"hide_spotter_recommends: {value}\n")
        with pytest.raises(ValueError, match="^hide_spotter_recommends must be a boolean$"):
            load_config(path)
    # The strip is tools_merge's own analyzer box.
    assert not spotter_recommends.enabled(AppConfig(hide_spotter_recommends=True))


def test_off_changes_no_byte_and_keeps_the_strip(sites):
    off = files(sites["off"])
    assert off == files(sites["without"])
    assert {name for name, data in off.items() if b'class="recommends"' in data} == PAGES
    for name in PAGES:
        html = off[name].decode()
        strip = STRIP.search(html)[0]
        assert strip.count("TODAY’S DEAL") == 3 and "wowpriced recommends" in strip
        assert html.index('<dialog id="analyze-modal"') < html.index(strip) < html.index("</dialog>")


def test_on_removes_the_strip_from_every_page_that_had_it(sites):
    on, off = files(sites["on"]), files(sites["off"])
    old, new = version(sites["off"]), version(sites["on"])
    assert old != new and on.keys() == off.keys()
    # Only those pages change (and the publication's version stamp); scripts and styles keep their bytes.
    assert {name for name in on if on[name] != off[name].replace(old, new)} == PAGES
    for name in PAGES:
        assert on[name] == STRIP.sub("", off[name].decode()).encode(), name
        modal = re.search(r'<dialog id="analyze-modal".*?</dialog>', on[name].decode(), re.S)[0]
        for kept in ('class="modal-progress"', 'id="modal-step"', 'id="modal-deals" class="modal-cta" href="/"'):
            assert kept in modal, (name, kept)
    assert not any(b'class="recommends"' in data or "TODAY’S DEAL".encode() in data for data in on.values())
    # No script reads the strip; its leftover rules only style class names no page draws now.
    for name in ("deal-spotter.js", "review-checker.js"):
        assert b"recommends" not in on[name] and b"rec-card" not in on[name]
