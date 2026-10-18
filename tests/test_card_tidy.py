"""card_tidy: folded checkout steps, cart chip wording, AI Analyze colors, no search filler."""
import json
import subprocess

import pytest

from dealradar.config import load_config
from dealradar.publish import card_tidy
from tests.test_offscreen_cards import offers
from tests.test_pwa import tree_hash
from tests.test_search_more import offline  # noqa: F401 (autouse: no network, fixed clock)
from tests.test_support import render


def test_off_and_missing_keep_bytes_on_changes_them(tmp_path):
    cfg = load_config()
    hashes = []
    render(tmp_path, cfg, offers())  # the first render into a fresh state differs in deals.json
    for value in (False, None, True):
        if value is None:
            del cfg.card_tidy
        else:
            cfg.card_tidy = value
        out, *_ = render(tmp_path, cfg, offers())
        hashes.append(tree_hash(out))
    assert hashes[0] == hashes[1] != hashes[2]


@pytest.mark.parametrize('value', [None, 0, 1, 'true', [], {}])
def test_strict_config(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'card_tidy': value}))
    with pytest.raises(ValueError, match='^card_tidy must be a boolean$'):
        load_config(path)


def test_shipped_on_and_emitted(tmp_path):
    cfg = load_config()
    assert cfg.card_tidy is True
    out, *_ = render(tmp_path, cfg, offers())
    page = (out / 'index.html').read_text()
    analyze = (out / 'search-analyze.js').read_text()
    assert card_tidy.CSS in page and 'More details' in page and 'price drops in cart' in page
    assert 'price drops\\u00a0→' in page and 'See all &quot;\' +\n      esc(q)' not in page
    for phrase in ('busy. Please', 'unavailable right now', 'took too long', 'reached their limit'):
        assert phrase not in analyze
    assert 'task.related.filter(function (d) { return d.image_url; })' in analyze and "See today's Amazon price drops →" in analyze
    assert "Get today's best Amazon price drops." in analyze and 'Analyze</span>' in page


def test_steps_fold_after_two_and_toggle():
    js = card_tidy.SHORT + r'''
      console.log(JSON.stringify({steps}));'''
    script = ('function esc(s){return String(s).replace(/</g,"&lt;");}\n'
              'var d = {how:["Shipping: Free on $35+", "Use code A", "use code a", "Extra 50% off", "In cart"]};\n' + js)
    out = json.loads(subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True).stdout)
    steps = out['steps']
    assert steps.startswith('Shipping: Free on $35+ &rarr; Use code A<span class="how-rest" hidden>')
    assert steps.count('Use code A') == 1 and 'use code a' not in steps
    assert 'Extra 50% off &rarr; In cart</span><br><button type="button" class="how-more"' in steps
    short = ('function esc(s){return s;}\nvar d = {how:["One", "Two"]};\n' + card_tidy.SHORT +
             'console.log(JSON.stringify({steps}));')
    assert json.loads(subprocess.run(['node', '-e', short], capture_output=True, text=True,
                                     check=True).stdout)['steps'] == 'One &rarr; Two'
