"""Operator 2026-10-20/21: strict switch, exact rollback, centred countdown on phones and laptops."""
import hashlib
import json
import re

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import timer_center as timer
from tests.history_pins import pre_phone_timer_raise, pre_task_290_install_raw, pre_timer_center
from tests.test_pwa import tree_hash
from tests.test_search_more import offline  # noqa: F401
from tests.test_support import render

PHONE = '.dropt{font-size:16px;width:50px;line-height:23px;text-align:center}'


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_bool(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'timer_center': value}))
    with pytest.raises(ValueError, match='^timer_center must be a boolean$'):
        load_config(path)


def test_defaults_and_history(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).timer_center is AppConfig().timer_center is False
    assert load_config().timer_center is True
    assert pre_timer_center(load_config()).timer_center is False
    assert pre_phone_timer_raise(load_config()).timer_center is False
    assert 'timer_center' not in pre_task_290_install_raw({'timer_center': True})
    assert not timer.enabled(type('Config', (), {'timer_center': 1})())
    assert timer.template('unchanged', AppConfig()) == 'unchanged'
    assert timer.site_version('old', AppConfig()) == 'old'


def test_desktop_rule_uses_the_dial_hiding_query():
    style = ('a{b:c}@media(min-width:900px) and (max-width:1599px){x{y:z}'
             '.hrow .drop .dial,.hrow .droplbl{display:none}}')
    assert timer.css(style) == ('@media(min-width:900px) and (max-width:1599px)'
                                '{.hrow>.drop{justify-content:center}.hrow .dropt{text-align:center}}')
    assert timer.css('a{b:c}') == ''


def test_off_missing_on_rerun_back_off_bytes(tmp_path):
    cfg = pre_timer_center(load_config())
    hashes = []
    for value in (False, None, True, True, False):
        if value is None:
            del cfg.timer_center
        else:
            cfg.timer_center = value
        out, *_ = render(tmp_path, cfg)
        hashes.append(tree_hash(out))
        html = (out / 'index.html').read_text()
        header = (out / 'header-polish.css').read_text()
        assert (PHONE in html) is bool(value)
        assert html.count('.dropt{font-size:16px;width:50px;line-height:23px') == 1
        assert (timer.DESKTOP_CSS in header) is bool(value)
        if value:
            assert header.count(timer.DESKTOP_CSS) == 1
            assert re.search(r'@media\(min-width:900px\) and \(max-width:1599px\)\{'
                             + re.escape(timer.DESKTOP_CSS) + r'\}$', header)
            version = hashlib.sha256(header.encode()).hexdigest()[:12]
            assert f'/header-polish.css?v={version}"' in html
    assert hashes[0] == hashes[1] == hashes[4] != hashes[2] == hashes[3]
