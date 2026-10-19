"""Task 324: exact rollback, phone-only copy, shared count rules and budgets."""
import json
import re
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import phone_tagline_count as tagline
from tests.history_pins import pre_phone_tagline_count, pre_task_320, pre_task_290_install_raw
from tests.test_pwa import tree_hash
from tests.test_search_more import offline  # noqa: F401
from tests.test_support import render


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_bool(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'phone_tagline_count': value}))
    with pytest.raises(ValueError, match='^phone_tagline_count must be a boolean$'):
        load_config(path)


def test_defaults_and_history(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).phone_tagline_count is AppConfig().phone_tagline_count is False
    assert load_config().phone_tagline_count is True
    assert pre_phone_tagline_count(load_config()).phone_tagline_count is False
    assert pre_task_320(load_config()).phone_tagline_count is False
    assert 'phone_tagline_count' not in pre_task_290_install_raw({'phone_tagline_count': True})
    for value in (True, False):
        path.write_text(json.dumps({'phone_tagline_count': value}))
        assert load_config(path).phone_tagline_count is value
    assert not tagline.enabled(type('Config', (), {'phone_tagline_count': 1})())
    assert tagline.template('unchanged', AppConfig()) == 'unchanged'


@pytest.mark.parametrize('mode', ['shipped', 'inline', 'membership'])
def test_off_missing_on_rerun_back_off_bytes(tmp_path, mode):
    cfg = pre_phone_tagline_count(load_config())
    if mode == 'inline':
        cfg.strip_comments = False
        cfg.search_analyze['lazy'] = False
    if mode == 'membership':
        cfg.support_enabled = False
    render(tmp_path, cfg)  # Initialise state before comparing this config's own OFF.
    hashes = []
    for value in (False, None, True, True, False):
        if value is None:
            del cfg.phone_tagline_count
        else:
            cfg.phone_tagline_count = value
        out, *_ = render(tmp_path, cfg)
        hashes.append(tree_hash(out))
        assert ('var tagShort =' in (out / 'index.html').read_text()) is bool(value)
    assert hashes[0] == hashes[1] == hashes[4] != hashes[2] == hashes[3]


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('header', [False, True])
def test_markup_css_and_actual_count_builder(tmp_path, enabled, header):
    cfg = load_config()
    cfg.strip_comments = False
    cfg.phone_tagline_count = False
    cfg.header_polish = header
    out, _, full, _ = render(tmp_path, cfg)
    before = {p.relative_to(out): p.read_bytes() for p in out.rglob('*') if p.is_file()}
    version = full['meta']['site_version'].encode()
    cfg.phone_tagline_count = enabled
    out, _, full, _ = render(tmp_path, cfg)
    updated = full['meta']['site_version'].encode()
    assert set(before) == {p.relative_to(out) for p in out.rglob('*') if p.is_file()}
    for name, body in before.items():
        if name != Path('index.html'):
            assert (out / name).read_bytes() == body.replace(version, updated), name
    home = (out / 'index.html').read_text()
    if enabled:
        assert version != updated
        assert tagline.CSS + '@media (max-width:899px){' + tagline.PHONE_CSS in home
        # Only display and the tagline's minimum width change; inherit type.
        rules = ';'.join(re.findall(r'\{([^{}]*)\}', tagline.CSS + tagline.PHONE_CSS))
        assert set(re.findall(r'([\w-]+):', rules)) == {'display', 'min-width'}
        assert '#tagline:has(.tag-short){min-width:200px}' in tagline.PHONE_CSS
        assert 'tagLead.className = live ? "tag-full tag-lead" : ""' in home
        assert 'tagCount.className = live ? "tag-full tag-count" : ""' in home
    else:
        assert version == updated
        assert tagline.CSS not in home
    result = subprocess.run(['node', 'tests/phone_tagline_count_logic.cjs', str(out / 'index.html')],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_site_check_accepts_visible_phone_and_desktop_counts():
    source = Path('scripts/site_check.js').read_text()
    guard = re.search(r'check\((/.*?/i)\.test\(shown.tagline\)', source)[1]
    code = r"""
const assert = require('node:assert/strict');
const guard = GUARD;
for (const text of ['1,200+ VERIFIED DEALS', '1 VERIFIED DEAL', '2 verified deals',
                    'verified deals · 1,000+ live deals', 'verified deals · 1 live deal']) assert(guard.test(text), text);
for (const text of ['', 'verified deals', '1,000+ other deals']) assert(!guard.test(text), text);
""".replace('GUARD', guard)
    result = subprocess.run(['node', '-e', code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    result = subprocess.run(['node', 'tests/site_check_logic.cjs'], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
