"""Operator 2026-10-22 (A34): tool prices read like Amazon's item price; shipping shows under it."""
import json
import subprocess

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import tool_pages


def test_default_off_shipped_on_and_strict(tmp_path):
    assert AppConfig().price_shipping_note is False
    assert load_config().price_shipping_note is True
    path = tmp_path / 'config.yaml'
    for value in (None, 0, 1, 'true', [], {}):
        path.write_text(json.dumps({'price_shipping_note': value}))
        with pytest.raises(ValueError, match='^price_shipping_note must be'):
            load_config(path)


def test_off_leaves_the_script_unchanged():
    cfg = load_config()
    on = tool_pages.script(cfg)
    cfg.price_shipping_note = False
    off = tool_pages.script(cfg)
    assert on.count(tool_pages.SHIPPING_LINE) == 1 and tool_pages.SHIPPING_LINE not in off
    assert on.replace(tool_pages.SHIPPING_LINE, '', 1) == off


def test_shipping_line_only_when_shipping_is_charged():
    source = tool_pages.script(load_config())
    start = source.index('function toolPrice(item) {')
    body = source[start:source.index('\n}\n', start) + 3]
    run = body + ('for (const s of [2515, 0, undefined, 1.5]) console.log(toolPrice('
                  '{price_cents: 15384, list_price_cents: 19999, shipping_cents: s}));')
    lines = subprocess.run(['node', '-e', run], capture_output=True, text=True, check=True).stdout.splitlines()
    assert lines[0] == ('<div class="price">$153.84</div><p class="price-note">+ $25.15 shipping</p>'
                        '<p class="price-note"><s>$199.99</s> Amazon list price</p>')
    assert all('shipping' not in line and '$153.84' in line for line in lines[1:])
