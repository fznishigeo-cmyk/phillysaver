"""Task 340: plain See all copy, matching the header's rounded count."""
import hashlib
import json
import re

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import drop_fresh, fresh_more_plain, seo
from tests.history_pins import pre_fresh_more_plain, pre_tool_pages_hero
from tests.test_drop_fresh import offline  # noqa: F401
from tests.test_pwa import tree_hash
from tests.test_search_amazon import run_node
from tests.test_support import NOW, render


LABELS = [(1, '1 deal'), (85, '85 deals'), (99, '99 deals'), (100, '100+ deals'),
          (999, '900+ deals'), (1000, '1,000+ deals'), (1381, '1,000+ deals'),
          (1999, '1,000+ deals'), (2400, '1,000+ deals')]


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_bool(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'fresh_more_plain': value}))
    with pytest.raises(ValueError, match='^fresh_more_plain must be a boolean$'):
        load_config(path)


def test_default_shipped_and_history(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).fresh_more_plain is AppConfig().fresh_more_plain is False
    assert load_config().fresh_more_plain is True
    for value in (False, True):
        path.write_text(json.dumps({'fresh_more_plain': value}))
        assert load_config(path).fresh_more_plain is value
    assert pre_tool_pages_hero(load_config()).fresh_more_plain is False
    cfg = load_config()
    cfg.fresh_more_plain = 1
    assert not fresh_more_plain.enabled(cfg)


# Pins = the 332 + 337b release (21d30cd) with every later flag off; the first
# recording was taken on an unreleased 332 draft.
@pytest.mark.parametrize('mode,expected', [
    ('shipped', '1207ad47246ee8eb48dc4471c261b6cd3d46e0d69a98e4e1d4f720034d2fe14a'),
    ('inline', 'a0277dfd774f9a0fc21ad138de41954e6de29beb3084b0316f25cf9802237d2c'),
    ('membership', 'b511b72c251c93dd73dbe7fe74b0644d932643a1a7ebfbd32ccce9fa7c5ee47e'),
])
def test_off_previous_bytes_missing_and_back(tmp_path, mode, expected):
    cfg = pre_fresh_more_plain(load_config())
    cfg.tool_pages_1020 = False  # Sibling released together.
    if mode == 'inline':
        cfg.strip_comments = False
        cfg.search_analyze['lazy'] = False
    if mode == 'membership':
        cfg.support_enabled = False
    hashes = []
    for value in (False, None, True, True, False):
        if value is None:
            del cfg.fresh_more_plain
        else:
            cfg.fresh_more_plain = value
        out, *_ = render(tmp_path, cfg)
        hashes.append(tree_hash(out))
    assert hashes[0] == hashes[1] == hashes[4] == expected
    assert hashes[2] == hashes[3] != expected


@pytest.mark.parametrize('total,label', LABELS)
@pytest.mark.parametrize('plain', [False, True])
def test_static_live_count(monkeypatch, total, label, plain):
    cfg = load_config()
    cfg.fresh_more_plain = plain
    rows = [dict(key=f'live-{i}', status='alive') for i in range(total)]
    rows += [dict(key='dead', status='dead')]
    monkeypatch.setattr(drop_fresh, 'rotation', lambda rows, options: rows[:1])
    monkeypatch.setattr(drop_fresh, 'page', lambda pool, *args: pool)
    monkeypatch.setattr(seo, 'card_html', lambda *args, **kwargs: '')
    html = seo.static_wall(rows, True, now=NOW, fresh_config=cfg)
    controls = re.search(r'<div class="more fresh-more">.*?</div>', html)[0]
    expected = label if plain else f'{total:,}+ deals'
    assert f'<button type="button" id="fresh-all" class="pwbtn">See all {expected}</button>' in controls
    assert ('Full rotation:' in html) is (not plain)
    assert ('<p' in controls) is (not plain)


@pytest.mark.parametrize('strip', [False, True])
def test_emitted_count_header_parity_and_content_hash(tmp_path, strip):
    cfg = load_config()
    cfg.strip_comments = strip
    out, *_ = render(tmp_path, cfg)
    asset = out / 'drop-fresh.js'
    home = (out / 'index.html').read_text()
    assert 'Full rotation' not in home + asset.read_text()
    assert '/drop-fresh.js?v=' + hashlib.sha256(asset.read_bytes()).hexdigest()[:12] in home
    expected = tmp_path / 'labels.json'
    expected.write_text(json.dumps(LABELS))
    result = run_node('fresh_more_plain_logic.cjs', out / 'index.html', expected)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('switch', ['drop_fresh', 'drop_shuffle'])
def test_no_change_without_fresh_drop(tmp_path, switch):
    cfg = load_config()
    setattr(cfg, switch, False)
    before = None
    for plain in (False, True):
        cfg.fresh_more_plain = plain
        out, *_ = render(tmp_path, cfg)
        before = before or tree_hash(out)
        assert tree_hash(out) == before
