"""Task 330: strict switch, exact rollback and position-only phone CSS."""
import json
import re
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import phone_timer_raise as timer
from tests.history_pins import pre_phone_timer_raise, pre_task_290_install_raw, pre_timer_pill_ring
from tests.test_pwa import tree_hash
from tests.test_search_more import offline  # noqa: F401
from tests.test_support import render


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_bool(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'phone_timer_raise': value}))
    with pytest.raises(ValueError, match='^phone_timer_raise must be a boolean$'):
        load_config(path)


def test_defaults_and_history(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).phone_timer_raise is AppConfig().phone_timer_raise is False
    assert load_config().phone_timer_raise is True
    for value in (True, False):
        path.write_text(json.dumps({'phone_timer_raise': value}))
        assert load_config(path).phone_timer_raise is value
    assert pre_phone_timer_raise(load_config()).phone_timer_raise is False
    assert pre_timer_pill_ring(load_config()).phone_timer_raise is False
    assert 'phone_timer_raise' not in pre_task_290_install_raw({'phone_timer_raise': True})
    assert not timer.enabled(type('Config', (), {'phone_timer_raise': 1})())
    assert timer.template('unchanged', AppConfig()) == 'unchanged'


@pytest.mark.parametrize('mode', ['shipped', 'inline', 'membership'])
def test_off_missing_on_rerun_back_off_bytes(tmp_path, mode):
    cfg = pre_phone_timer_raise(load_config())
    if mode == 'inline':
        cfg.strip_comments = False
        cfg.search_analyze['lazy'] = False
    if mode == 'membership':
        cfg.support_enabled = False
    render(tmp_path, cfg)  # Compare with this configuration's own OFF render.
    hashes = []
    for value in (False, None, True, True, False):
        if value is None:
            del cfg.phone_timer_raise
        else:
            cfg.phone_timer_raise = value
        out, *_ = render(tmp_path, cfg)
        hashes.append(tree_hash(out))
        assert (timer.CSS in (out / 'index.html').read_text()) is bool(value)
    assert hashes[0] == hashes[1] == hashes[4] != hashes[2] == hashes[3]


@pytest.mark.parametrize('header,tagline', [(True, True), (True, False), (False, True)])
def test_only_phone_position_rule_changes(tmp_path, header, tagline):
    cfg = pre_phone_timer_raise(load_config())
    cfg.header_polish = header
    cfg.phone_tagline_count = tagline
    out, _, full, _ = render(tmp_path, cfg)
    before = {p.relative_to(out): p.read_bytes() for p in out.rglob('*') if p.is_file()}
    version = full['meta']['site_version'].encode()
    cfg.phone_timer_raise = True
    out, _, full, _ = render(tmp_path, cfg)
    updated = full['meta']['site_version'].encode()
    assert version != updated
    assert set(before) == {p.relative_to(out) for p in out.rglob('*') if p.is_file()}
    for name, body in before.items():
        expected = body.replace(version, updated)
        actual = (out / name).read_bytes()
        if name == Path('index.html'):
            actual = actual.replace(timer.CSS.encode(), b'', 1)
        assert actual == expected, name
    # Shared header CSS (including task 329's ring), scripts, DOM, themes and
    # every >=900px rule stay identical; only the phone's relative top changes.
    home = (out / 'index.html').read_text()
    assert home.count(timer.CSS) == 1
    assert '@media (max-width:640px){' + timer.CSS in home
    assert re.findall(r'\{([^{}]*)\}', timer.CSS) == ['top:-10px']
    header_html = lambda html: re.search(r'<header>.*?</header>', html, re.S)[0]
    assert header_html(home) == header_html(before[Path('index.html')].decode())
