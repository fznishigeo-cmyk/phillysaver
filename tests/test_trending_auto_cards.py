"""Operator 2026-10-17: Trending keeps loading as visitors scroll (was 300, then a button)."""

import json

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import site
from tests.test_publish_site import make_deal
from tests.test_support import NOW, render


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)


def test_defaults_shipped_and_invalid(tmp_path):
    assert AppConfig().trending_auto_cards is None
    assert load_config().trending_auto_cards == 1200
    for bad in (59, 5001, "1200", True):
        path = tmp_path / "config.yaml"
        path.write_text(json.dumps({"site": {"trending_auto_cards": bad}}))
        with pytest.raises(ValueError, match="^site.trending_auto_cards"):
            load_config(path)


def test_missing_keeps_bytes_and_setting_changes_only_the_limit(tmp_path):
    pages = {}
    for value in (None, 1200):
        cfg = AppConfig(trending_auto_cards=value)
        (tmp_path / str(value)).mkdir()
        out, *_ = render(tmp_path / str(value), cfg, [make_deal(item="Steel kitchen kettle", codes=[])])
        pages[value] = (out / "index.html").read_text()
    assert "  var AUTO_CARDS = 300;" in pages[None]
    assert "  var AUTO_CARDS = 1200;" in pages[1200] and "  var AUTO_CARDS = 300;" not in pages[1200]


def test_click_price_home_fill_keeps_every_verified_deal_after_the_confirmed_ones(tmp_path):
    import subprocess
    cfg = load_config()
    assert cfg.click_price_home_fill is True and cfg.keepa_click_price_rule == "on"
    (tmp_path / "site").mkdir()
    out, *_ = render(tmp_path / "site", cfg, [make_deal(item="Steel kitchen kettle", codes=[])])
    html = (out / "index.html").read_text()
    assert "return known.concat(rows.filter(function (d) { return d.click_price_unknown; }));" in html
    assert "known.length >= 120" not in html
    script = html[html.index("  function clickPriceHomeRows(rows)"):html.index("  function nicheLast(")]
    check = ("const vm=require('vm');const ctx={state:{catTab:0},CATGROUPS:[['Trending',null],['All deals',null]],"
             "document:{getElementById:()=>({value:''})}};vm.createContext(ctx);vm.runInContext(process.argv[1],ctx);"
             "const rows=ctx.clickPriceHomeRows([{key:'u',click_price_unknown:true},{key:'k'}]);"
             "if(rows.map(d=>d.key).join()!=='k,u')throw new Error(rows.map(d=>d.key).join());")
    result = subprocess.run(["node", "-e", check, script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
