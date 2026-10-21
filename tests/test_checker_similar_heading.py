"""Operator 2026-10-20: Deal Checker lead opens the product; the top-sellers heading is the similar-deals button."""
import json
import subprocess

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import checker_similar_heading as feature, tool_pages
from tests.history_pins import pre_buttons_to_tools, pre_checker_similar_heading, pre_tools_new_tab
from tests.test_pwa import tree_hash
from tests.test_support import render
from tests.test_tool_pages import offline  # noqa: F401


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_boolean(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'checker_similar_heading': value}))
    with pytest.raises(ValueError, match='^checker_similar_heading must be a boolean$'):
        load_config(path)


def test_defaults_history_and_shipped(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).checker_similar_heading is AppConfig().checker_similar_heading is False
    assert load_config().checker_similar_heading is True
    for helper in (pre_checker_similar_heading, pre_tools_new_tab, pre_buttons_to_tools):
        assert helper(load_config()).checker_similar_heading is False
    assert not feature.enabled(type('Config', (), {'checker_similar_heading': 1, 'buttons_to_tools': True})())
    # The similar-deals helper only exists with task 344's tool script.
    assert not feature.enabled(type('Config', (), {'checker_similar_heading': True, 'buttons_to_tools': False})())


def test_script_moves_similar_deals_to_the_heading(tmp_path):
    cfg = load_config()
    cfg.checker_similar_typed_words = False  # this button's original name-first search; typed words has its own test
    on = tool_pages.script(cfg)
    assert "toolButton(toolAmazon(item.asin), lead ? 'See it on Amazon →' : 'View at Amazon', 'search_amazon')" in on
    assert "toolSimilar(item), 'See similar deals on Amazon →'" not in on
    assert 'top.hidden = toolSimilarHeading(top, task.lead) && !task.sellers.length;' in on
    assert "var words = toolQuery(shorten(item.title || '')) ||" in on
    cfg.checker_similar_heading = False
    off = tool_pages.script(cfg)
    assert "toolSimilar(item), 'See similar deals on Amazon →'" in off and 'toolSimilarHeading' not in off
    script = tmp_path / 'tool-pages.js'
    script.write_text(on)
    subprocess.run(['node', '--check', str(script)], check=True)


def build(tmp_path, name, cfg):
    (tmp_path / name).mkdir()
    return render(tmp_path / name, cfg)[0]


def test_off_keeps_previous_bytes(tmp_path):
    cfg = pre_checker_similar_heading(load_config())
    off = tree_hash(build(tmp_path, 'off', cfg))
    del cfg.checker_similar_heading
    assert tree_hash(build(tmp_path, 'missing', cfg)) == off
    cfg.checker_similar_heading = True
    out = build(tmp_path, 'on', cfg)
    assert '#top-title .amazon-button{margin-top:0}' in (out / 'tool-pages.css').read_text()
    assert 'toolSimilarHeading' in (out / 'tool-pages.js').read_text()
    cfg.checker_similar_heading = False
    assert tree_hash(build(tmp_path, 'back', cfg)) == off
