"""Operator 2026-10-21: similar deals search the typed words; price age in minutes and hours."""
import json
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import checker_fixes_1021 as feature
from dealradar.publish import tool_pages
from tests.history_pins import pre_checker_fixes_1021, pre_search_focus_ring, pre_task_290_install_raw

NAMES = ('checker_similar_typed_words', 'price_age_minutes')


@pytest.mark.parametrize('name', NAMES)
@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_bool(tmp_path, name, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({name: value}))
    with pytest.raises(ValueError, match=f'^{name} must be a boolean$'):
        load_config(path)


def test_defaults_and_history(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    for name in NAMES:
        assert getattr(load_config(path), name) is getattr(AppConfig(), name) is False
        assert getattr(load_config(), name) is True
        for helper in (pre_checker_fixes_1021, pre_search_focus_ring):
            assert getattr(helper(load_config()), name) is False
        assert name not in pre_task_290_install_raw({name: True})
    assert feature.script('unchanged', AppConfig()) == 'unchanged'


def test_off_keeps_script_bytes_and_on_changes_only_the_two_spots():
    cfg = pre_checker_fixes_1021(load_config())
    off = tool_pages.script(cfg)
    for name in NAMES:
        delattr(cfg, name)
    assert tool_pages.script(cfg) == off
    assert feature.NAME_FIRST in off and feature.CARD_AGE in off and feature.TICK_AGE in off
    on = tool_pages.script(load_config())
    assert feature.NAME_FIRST not in on and feature.NAME_COMMENT not in on
    assert "  var words = input.kind === 'query' ? toolQuery(raw) :" in on
    assert ' sec ago on Amazon' not in on and on.count('function toolAge(asOf)') == 1
    cfg.checker_similar_heading = False
    assert not feature.typed_words(cfg)


@pytest.mark.parametrize('seconds,text', [(0, '0 sec ago'), (59, '59 sec ago'), (60, '1 min ago'),
                                          (2638, '43 min ago'), (3599, '59 min ago'), (3600, '1 hr ago'),
                                          (7300, '2 hrs ago')])
def test_age_wording(seconds, text):
    js = feature.AGE + (f"Date.now = () => Date.parse('2026-10-21T00:00:00Z') + {seconds * 1000};\n"
                        "console.log(toolAge('2026-10-21T00:00:00Z'));")
    out = subprocess.run(['node', '-e', js], capture_output=True, text=True, check=True).stdout.strip()
    assert out == text


def test_typed_words_and_minutes_in_real_chromium(tmp_path):
    result = subprocess.run(['node', str(Path(__file__).with_name('checker_fixes_1021_browser.cjs')), str(tmp_path)],
                            capture_output=True, text=True, timeout=240, cwd=Path(__file__).parent.parent)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'similar k=headphones; price checked 4' in result.stdout
