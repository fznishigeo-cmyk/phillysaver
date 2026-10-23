"""Operator 2026-10-22 (A34): tool prices read like Amazon's item price; shipping shows under it."""
import hashlib
import json
import re
import subprocess

import pytest

from dealradar.config import AppConfig, load_config, validated_search, validated_search_more
from dealradar.publish import review_checker, shipping_note, site, tool_pages
from tests.test_publish_site import make_deal
from tests.test_search_amazon import run_node
from tests.test_search_more import config as search_more_config
from tests.test_support import NOW, render


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


# Codex review 2026-10-23 #6: the homepage's Amazon results and the Review Checker report show
# the same note as Fake Deal Spotter; off keeps every previous byte (recorded hashes pin it off).
@pytest.mark.parametrize("more", [False, True])
def test_home_amazon_results_show_the_note(tmp_path, monkeypatch, more):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = search_more_config() if more else AppConfig(search_amazon=True, affiliate_amazon_tag="example-20",
                                                       search=validated_search({"amazon_results": "on"}))
    if more:
        cfg.search_more = validated_search_more({"enabled": True})
    for on in (False, True):
        cfg.price_shipping_note = on
        (tmp_path / str(on)).mkdir()
        out, *_ = render(tmp_path / str(on), cfg, [make_deal(item="Steel kettle", codes=[])])
        result = run_node("search_more_logic.cjs" if more else "search_results_logic.cjs", out / "index.html")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "shipping note: " + ("on" if on else "off") in result.stdout
    if more:  # the asset changed, so its address did too
        bundle = (out / "search-more.js").read_bytes()
        assert "/search-more.js?v=" + hashlib.sha256(bundle).hexdigest()[:12] in (out / "index.html").read_text()


def test_review_checker_report_shows_the_note_only_when_on():
    cfg = load_config()
    on = review_checker.script(cfg)
    cfg.price_shipping_note = False
    off = review_checker.script(cfg)
    assert on.count(shipping_note.REVIEW_NOTE) == 1 and shipping_note.REVIEW_NOTE not in off
    assert on.replace(shipping_note.REVIEW_NOTE, shipping_note.REVIEW_PRICE, 1) == off
    assert shipping_note.review_css(cfg) == "" and ".result-shipping{" in shipping_note.review_css(load_config())
