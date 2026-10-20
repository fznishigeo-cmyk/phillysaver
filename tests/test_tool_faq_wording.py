"""Operator 2026-10-20: tool-page FAQ wording, behind a switch."""
import json

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import tool_faq_wording as feature
from tests.history_pins import pre_checker_similar_heading, pre_tool_faq_wording
from tests.test_pwa import tree_hash
from tests.test_support import render
from tests.test_tool_pages import offline  # noqa: F401


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_boolean(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'tool_faq_wording': value}))
    with pytest.raises(ValueError, match='^tool_faq_wording must be a boolean$'):
        load_config(path)


def test_defaults_history_and_shipped(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).tool_faq_wording is AppConfig().tool_faq_wording is False
    assert load_config().tool_faq_wording is True
    for helper in (pre_tool_faq_wording, pre_checker_similar_heading):
        assert helper(load_config()).tool_faq_wording is False


def build(tmp_path, name, cfg):
    (tmp_path / name).mkdir()
    return render(tmp_path / name, cfg)[0]


def test_each_page_speaks_for_itself_and_off_keeps_previous_bytes(tmp_path):
    cfg = pre_tool_faq_wording(load_config())
    off = tree_hash(build(tmp_path, 'off', cfg))
    del cfg.tool_faq_wording
    assert tree_hash(build(tmp_path, 'missing', cfg)) == off
    cfg.tool_faq_wording = True
    out = build(tmp_path, 'on', cfg)
    checker = (out / 'amazon-deal-checker' / 'index.html').read_text()
    finder = (out / 'amazon-discount-finder' / 'index.html').read_text()
    for html in (checker, finder):
        assert html.count('Yes. This tool is free to use.') == 2  # visible FAQ + JSON-LD
        assert 'Both tools' not in html
    assert 'Discount Finder' not in checker.split('<h3>Is it free?</h3>', 1)[1].split('<h3>', 1)[0]
    assert 'does not run a live price check' not in finder
    assert finder.count('opens Amazon') == 2 and finder.count('best discount results.') == 2
    assert 'own discount results' not in finder
    assert 'checks the live Amazon price in seconds' in checker  # the Checker's own answers stay
    cfg.tool_faq_wording = False
    assert tree_hash(build(tmp_path, 'back', cfg)) == off
