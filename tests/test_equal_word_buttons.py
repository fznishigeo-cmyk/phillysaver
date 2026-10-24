"""Operator (Nishi) 2026-10-24 ~03:55Z: the typed-words box's two buttons the same width on both tools (equal_word_buttons)."""
import json
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import equal_word_buttons
from tests.history_pins import pre_social_profiles
from tests.test_tools_merge import build, files
from tests.test_word_links import PAGES

SHEETS = {"deal-spotter.css": equal_word_buttons.SPOTTER_CSS, "review-checker.css": equal_word_buttons.REVIEW_CSS}


def version(out):
    return json.loads((out / "deals.json").read_text())["meta"]["site_version"].encode()


@pytest.fixture(scope="module")
def sites(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("equal-word-buttons")
    with pytest.MonkeyPatch.context() as monkeypatch:
        on, _ = build(tmp, monkeypatch, True, "on")
        off, _ = build(tmp, monkeypatch, True, "off", equal_word_buttons=False)
        # The same build with the feature removed altogether.
        for name in ("spotter_css", "review_css"):
            monkeypatch.setattr(equal_word_buttons, name, lambda cfg: "")
        monkeypatch.setattr(equal_word_buttons, "site_version", lambda version, cfg: version)
        without, _ = build(tmp, monkeypatch, True, "without", equal_word_buttons=False)
    return {"on": on, "off": off, "without": without}


def test_switch_shipped_on_default_off_strict_and_pinned(tmp_path):
    assert load_config().equal_word_buttons is True  # operator 2026-10-24 ~03:55Z
    assert AppConfig().equal_word_buttons is False
    assert pre_social_profiles(AppConfig(equal_word_buttons=True)).equal_word_buttons is False
    (tmp_path / "missing.yaml").write_text("{}\n")
    assert load_config(tmp_path / "missing.yaml").equal_word_buttons is False
    for value in ("1", "'true'", "null", "[]"):
        path = tmp_path / "config.yaml"
        path.write_text(f"equal_word_buttons: {value}\n")
        with pytest.raises(ValueError, match="^equal_word_buttons must be a boolean$"):
            load_config(path)
    # The words box is tools_merge's own.
    assert not equal_word_buttons.enabled(AppConfig(equal_word_buttons=True))


def test_off_and_missing_change_no_byte(sites):
    off = files(sites["off"])
    assert off == files(sites["without"])
    assert not any(css.encode() in data for data in off.values() for css in SHEETS.values())


def test_on_adds_one_rule_to_each_tool_stylesheet(sites):
    on, off = files(sites["on"]), files(sites["off"])
    for name, css in SHEETS.items():
        assert on[name] == off[name] + css.encode(), name
    # Each rule starts above that tool's phone rule, where both buttons are already full width.
    assert b"@media(max-width:760px){" in off["deal-spotter.css"] and "min-width:761px" in SHEETS["deal-spotter.css"]
    assert b"@media(max-width:700px){.words-box{padding:14px}.words-buttons{flex-direction:column}" in off["review-checker.css"]
    assert "min-width:701px" in SHEETS["review-checker.css"]
    # Only the stylesheets change, and the pages that load them (their ?v=), with the publication's version stamp.
    old, new = version(sites["off"]), version(sites["on"])
    assert old != new and on.keys() == off.keys()
    pages = {page for script in PAGES for page in PAGES[script]}
    assert {name for name in on if on[name] != off[name].replace(old, new)} == set(SHEETS) | pages


def test_browser_both_tools_phone_and_laptop(tmp_path):
    result = subprocess.run(["node", str(Path(__file__).with_name("spotter_vacuum_browser.cjs")), str(tmp_path)],
                            capture_output=True, text=True, timeout=600, cwd=Path(__file__).parent.parent)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    for size in ("1440x900", "390x844"):
        assert f"{size}: ok" in result.stdout, result.stdout[-2000:]
