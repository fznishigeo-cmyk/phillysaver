"""Home polish against rendered HTML and its actual clock/tagline functions."""
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from dealradar.config import AppConfig, load_config
from tests.test_lighthouse_polish import contrast, phone_rule, rgb
from tests.test_support import render


DEFAULTS = {"warm_s": 300, "hot_s": 120, "final_s": 30}


@pytest.mark.parametrize("value", [None, [], "bad", {},
    {"warm_s": 120}, {"hot_s": 30}, {"final_s": 0}, {"final_s": -1},
    {"final_s": 120}, {"warm_s": 900}, {"warm_s": 1000},
    {"hot_s": "120"}, {"final_s": True}, {"warm_s": float("inf")},
    {"hot_s": float("nan")}])
def test_invalid_clock_thresholds_fall_back_together(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"publish": {"drop_clock": value}}))
    assert load_config(path).drop_clock == DEFAULTS


def test_clock_knobs_reach_public_and_member_meta(tmp_path):
    assert AppConfig().drop_clock == load_config().drop_clock == DEFAULTS
    path = tmp_path / "config.yaml"
    values = {"warm_s": 90, "hot_s": 40, "final_s": 10}
    path.write_text(yaml.safe_dump({"publish": {"drop_minutes": 2, "drop_clock": values}}))
    cfg = load_config(path)
    assert cfg.drop_clock == values
    _, public, full, _ = render(tmp_path, cfg)
    assert public["meta"]["drop_clock"] == full["meta"]["drop_clock"] == values
    path.write_text(yaml.safe_dump({"publish": {"drop_minutes": 1, "drop_clock": values}}))
    assert load_config(path).drop_clock == DEFAULTS
    # Direct AppConfig users get the same publication validation.
    cfg.drop_clock = {"final_s": -1}
    _, public, _, _ = render(tmp_path, cfg)
    assert public["meta"]["drop_clock"] == DEFAULTS


@pytest.mark.parametrize("support", [False, True])
def test_clock_tagline_and_removed_filters_offline(tmp_path, support):
    out, *_ = render(tmp_path, AppConfig(support_enabled=support))
    home = (out / "index.html").read_text()
    for removed in ("newonly", "plainonly", "newOnly", "plainOnly", "New condition only", "No extra steps"):
        assert removed not in home
    assert 'role="timer" aria-live="off"' in home
    assert 'width:72px;flex:none' in home and 'width:50px;line-height:23px' in home
    assert 'flex-wrap:wrap;justify-content:center;gap:7px' in home
    assert 'flex-wrap:nowrap;justify-content:flex-start;overflow-x:auto' in home
    css = re.search(r"<style>(.*?)</style>", home, re.S)[1]
    assert re.findall(r"font-size:(\d+)px", phone_rule(css, ".tag"))[-1] == "13"
    tag_rules = [rules for selectors, rules in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
                 if ".tag" in [s.strip() for s in selectors.split(",")]]
    assert all(int(size) >= 12 for rules in tag_rules for size in re.findall(r"font-size:(\d+)px", rules))
    assert [color for rules in tag_rules for color in re.findall(r"(?:^|;)color:([^;]+)", rules)] == ["var(--fg)"]
    result = subprocess.run(["node", str(Path(__file__).with_name("home_polish_logic.cjs")),
                             str(out / "index.html")], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_new_text_colors_pass_aa_in_both_headers_and_ticker(tmp_path):
    out, *_ = render(tmp_path, AppConfig())
    css = re.search(r"<style>(.*?)</style>", (out / "index.html").read_text(), re.S)[1]
    # Assert the actual declared pairs as well as their unrounded contrast.
    pairs = [("#073b35", "#4fd8c6"), ("#3a2500", "#ffc94d"),
             ("#f3f7f8", "#0f1b22"), ("#ffc94d", "#0f1b22"),
             ("#f3f0e8", "#22303f"), ("#22303f", "#ffc94d")]
    for background in ("#073b35", "#18252c"):
        pairs.extend((fg, background) for fg in ("#f3f7f8", "#ffc94d", "#ff9870"))
    # The last minutes heat the pill: deep amber-brown, then deep red.
    for background in ("#3d1d10", "#5c1610"):
        pairs.extend((fg, background) for fg in ("#f3f7f8", "#c7ded8", "#ff9870"))
    for fg, bg in pairs:
        assert fg in css and bg in css
        assert contrast(rgb(fg), rgb(bg)) >= 4.5, (fg, bg)
    assert '--tk-band:#22303f;--tk-on:#f3f0e8;--tk-poster:#ffc94d' in css
