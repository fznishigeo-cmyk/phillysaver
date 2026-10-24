"""Operator 2026-10-24: the "Price drop" flag reads "Hot Find" (hot_find)."""
import json

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import hot_find
from tests.history_pins import pre_hot_find, pre_social_profiles

JS = ('stamp: d.price_drop ? "Price drop" : d.pct_at_checkout == null ? "" : "-" + pct + "%",\n'
      'plain: (d.price_drop ? "Price drop " : d.pct_at_checkout == null ? "" : "-" + pct + "% ") + plain,\n'
      "if (pct == null && d.price_drop) media += '<span class=\"flag\">Price drop</span>';")
PAGE = '<span class="flag">Price drop</span><p><span class="chip">Price drop</span></p><p>Price drop alerts</p>'


def test_shipped_label_default_pin_and_validation(tmp_path):
    assert load_config().price_drop_label == "Hot Find"  # operator approved 2026-10-24
    assert AppConfig().price_drop_label == "Price drop" and not hot_find.enabled(AppConfig())
    assert pre_hot_find(load_config()).price_drop_label == "Price drop"
    assert pre_social_profiles(load_config()).price_drop_label == "Price drop"  # every older recorded fixture
    for bad in ('""', '"   "', "7", '"' + "x" * 21 + '"'):
        path = tmp_path / "config.yaml"
        path.write_text("publish:\n  price_drop_label: " + bad + "\n")
        with pytest.raises(ValueError, match="price_drop_label"):
            load_config(path)


def test_relabel_flag_chip_and_card_code_only():
    cfg = AppConfig(price_drop_label="Hot Find")
    js, page = hot_find.relabel(JS, cfg), hot_find.relabel(PAGE, cfg)
    assert "Price drop" not in js
    assert 'd.price_drop ? "Hot Find" :' in js and 'd.price_drop ? "Hot Find " :' in js
    assert '<span class="flag">Hot Find</span>' in js
    assert page == '<span class="flag">Hot Find</span><p><span class="chip">Hot Find</span></p><p>Price drop alerts</p>'
    odd = AppConfig(price_drop_label='Hot "<Find>"')
    assert 'd.price_drop ? ' + json.dumps('Hot "<Find>"') + ' :' in hot_find.relabel(JS, odd)
    assert '<span class="chip">Hot &quot;&lt;Find&gt;&quot;</span>' in hot_find.relabel(PAGE, odd)


def test_publish_rewrites_pages_and_versions_scripts(tmp_path):
    (tmp_path / "app.js").write_text(JS)
    (tmp_path / "index.html").write_text(PAGE + '<script src="/app.js?v=abc123"></script>')
    (tmp_path / "deal").mkdir()
    (tmp_path / "deal" / "index.html").write_text(PAGE)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    hot_find.publish(tmp_path, AppConfig())  # default: no byte changes
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    assert hot_find.site_version("v1", AppConfig()) == "v1"
    cfg = AppConfig(price_drop_label="Hot Find")
    hot_find.publish(tmp_path, cfg)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert "Price drop</span>" not in path.read_text() and '"Price drop' not in path.read_text()
    assert "/app.js?v=abc123" not in (tmp_path / "index.html").read_text()
    assert hot_find.site_version("v1", cfg) != "v1"
