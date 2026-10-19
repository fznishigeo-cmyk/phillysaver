"""Task 326: local URL derivation, unchanged OFF bytes and emitted clicks."""
import json
import subprocess

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import discounts_from_link
from tests.history_pins import pre_discounts_from_link, pre_task_320
from tests.test_pwa import tree_hash
from tests.test_search_more import offline  # noqa: F401
from tests.test_support import render


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_bool(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'discounts_from_link': value}))
    with pytest.raises(ValueError, match='^discounts_from_link must be a boolean$'):
        load_config(path)


def test_defaults_and_history(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).discounts_from_link is AppConfig().discounts_from_link is False
    assert load_config().discounts_from_link is True
    assert pre_discounts_from_link(load_config()).discounts_from_link is False
    assert pre_task_320(load_config()).discounts_from_link is False
    for value in (False, True):
        path.write_text(json.dumps({'discounts_from_link': value}))
        assert load_config(path).discounts_from_link is value
    assert not discounts_from_link.enabled(type('Config', (), {'all_discounts_button': True, 'discounts_from_link': 1})())


@pytest.mark.parametrize('mode,expected', [
    ('shipped', 'c32a7d80b62393e37d1a9f2431ce47bea684191d84daa4d23074525bc0dfa2cc'),
    ('inline', 'fdb4471c053d50479ff38e0c6463b730fcea0e58d2fc2d7cd3f3f6dc63e130ba'),
    ('membership', '0aaf3936923300b10c7a89a5949b32714955c750cf9b9d7f79d909f70b63ddb4'),
])
def test_original_release_off_bytes(tmp_path, mode, expected):
    cfg = pre_discounts_from_link(load_config())
    if mode == 'inline':
        cfg.search_analyze['lazy'] = False
        cfg.strip_comments = False
    if mode == 'membership':
        cfg.support_enabled = False
    for value in (False, None, True, True, False):
        if value is None:
            del cfg.discounts_from_link
        else:
            cfg.discounts_from_link = value
        out, *_ = render(tmp_path, cfg)
        assert (tree_hash(out) == expected) is (value is not True)


@pytest.mark.parametrize('mode', ['shipped', 'inline', 'no-analyze', 'no-search-amazon', 'no-adult-words'])
def test_emitted_logic(tmp_path, mode):
    cfg = load_config()
    cfg.affiliate_amazon_tag = 'example-20'
    if mode == 'inline':
        cfg.search_analyze['lazy'] = False
    if mode == 'no-analyze':
        cfg.search_analyze['enabled'] = False
    if mode == 'no-search-amazon':
        cfg.search_amazon = False
    if mode == 'no-adult-words':
        cfg.search_boost['exclude_words'] = []
    out, *_ = render(tmp_path, cfg)
    for name in ('index.html', 'black-friday/index.html', 'cyber-monday/index.html'):
        result = subprocess.run(['node', 'tests/search_discounts_from_link_logic.cjs', str(out / name),
                                 str(mode != 'no-adult-words').lower()],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('support', [False, True])
@pytest.mark.parametrize('amazon', [False, True])
def test_home_compaction_preserves_active_styles_and_prompt(tmp_path, support, amazon):
    cfg = load_config()
    cfg.support_enabled = support
    cfg.search_amazon = amazon
    out, *_ = render(tmp_path, cfg)
    home = (out / 'index.html').read_text()
    assert ('.modal{' in home) is (not support)
    assert ('.mcard form{' in home) is (not support)
    assert ('.acct .email{' in home) is (not support)
    assert ('id="authmodal"' in home) is (not support)
    # The dismiss button and shared phone input rule also serve Join.
    assert '.mx{position:absolute;' in home
    assert '#controls #surprise,.mcard input,.joincard input{font-size:16px}' in home
    assert ('placeholder = live ?' in home) is (not amazon)
    assert ('placeholder = "Search deals or any Amazon product"' in home) is amazon
    # No other page loses membership styling or its search fallback.
    event = (out / 'black-friday/index.html').read_text()
    assert 'function discountsLink()' in event
