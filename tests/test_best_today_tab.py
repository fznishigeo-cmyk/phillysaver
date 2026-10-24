"""Operator 2026-10-23 ~20:00Z (A48): a "Best today" link pill after Trending in the home tab row."""
import re
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import best_today_tab, site
from scripts.publication_budget import analyze
from tests.history_pins import pre_front_mix, pre_short_cards, pre_social_profiles
from tests.phone_budget_fixture import publish_fixture, visible_offer_count
from tests.test_publish_site import make_deal
from tests.test_support import NOW, render

PILL = '<a class="cattab" href="/best-deals-today/">Best today</a>'


def rows():
    return [make_deal(dedupe_key=f"best-tab-{i}", item=f"Electric kettle 1.7L model {i}", store="Amazon",
                      category="kitchen", verify_tier="verified", codes=[], conditions=["new"],
                      final_price=24.99 + i, regular_price=49.99, asin=f"B{i:09d}",
                      canonical_url=f"https://amazon.com/dp/B{i:09d}", first_seen_at=NOW.isoformat(),
                      price_rechecked_at=NOW.isoformat(),
                      critic={"plausible": True, "confidence": .9, "reason": "Backed basis"},
                      image_url="https://m.media-amazon.com/images/I/photo.jpg") for i in range(6)]


def build(tmp_path, monkeypatch, on, name="site"):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = load_config()
    if on is None:
        del cfg.best_today_tab
    else:
        cfg.best_today_tab = on
    (tmp_path / name).mkdir()
    out, _, full, _ = render(tmp_path / name, cfg, rows())
    return out, full["meta"]["site_version"].encode()


def files(out):
    return {str(p.relative_to(out)): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


def test_shipped_on_default_off_strict(tmp_path):
    assert load_config().best_today_tab is True  # operator approved 2026-10-23 ~20:00Z
    assert AppConfig().best_today_tab is False
    assert pre_social_profiles(AppConfig(best_today_tab=True)).best_today_tab is False
    for value in ("1", "'true'", "null", "[]"):
        path = tmp_path / "config.yaml"
        path.write_text(f"best_today_tab: {value}\n")
        with pytest.raises(ValueError, match="^best_today_tab must be a boolean$"):
            load_config(path)


def test_off_and_missing_change_no_byte(tmp_path, monkeypatch):
    off, _ = build(tmp_path, monkeypatch, False, "off")
    assert files(build(tmp_path, monkeypatch, None, "missing")[0]) == files(off)
    # The same build with the feature removed altogether.
    monkeypatch.setattr(best_today_tab, "publish", lambda out, cfg: None)
    monkeypatch.setattr(best_today_tab, "site_version", lambda version, cfg: version)
    without, _ = build(tmp_path, monkeypatch, False, "without")
    assert files(off) == files(without)
    assert not any(b">Best today</a>" in data for data in files(off).values())


def test_on_pill_after_trending_on_the_home_row_only(tmp_path, monkeypatch):
    off, old = build(tmp_path, monkeypatch, False, "off")
    on, new = build(tmp_path, monkeypatch, True, "on")
    assert new != old
    before, after = files(off), files(on)
    assert after.keys() == before.keys()
    # Hubs, store pages and the best-deals page itself keep their bytes (only the version moves).
    assert {k for k in after if after[k] != before[k].replace(old, new)} == {"index.html"}
    home = after["index.html"].decode()
    row = re.search(r'<nav id="cattabs"[^>]*>(.*?)</nav>', home, re.S)[1]
    assert row.startswith('<a class="cattab active" href="/" data-i="0">Trending</a>' + PILL
                          + '<a class="cattab" href="/" data-i="1">All deals</a>')
    assert home.count(PILL) == 2  # the server's row and renderTabs' redraw
    # renderTabs draws it after Trending (i = 0) and binds its filter only to data-i tabs.
    tabs = re.search(r"  function renderTabs\(\) \{.*?\n  \}\n", home, re.S)[0]
    assert "esc(g[0]) + \"</a>\" + (i ? \"\" : '" + PILL + "');" in tabs
    assert 'nav.querySelectorAll(".cattab[data-i]").forEach(' in tabs
    assert 'querySelectorAll(".cattab")' not in home
    assert (on / "best-deals-today" / "index.html").exists()
    # The template's every other byte is unchanged.
    for token, replacement in best_today_tab.SLOTS:
        home = home.replace(replacement, token)
    assert home.encode() == before["index.html"].replace(old, new)
    # The same build again gives the same bytes.
    assert files(build(tmp_path, monkeypatch, True, "again")[0]) == after


def test_phone_budget_cost_is_bounded(tmp_path):
    """The home reserve tests pin the pill off; its own cost is bounded here, under every ceiling."""
    cfg = pre_short_cards(pre_front_mix(load_config()))  # measured before short_cards moved the script's bytes
    cfg.phone_budget["enabled"] = True
    metrics = {}
    for on in (False, True):
        cfg.best_today_tab = on
        def inspect(db, out):
            report = analyze(out, cfg, expected_offers=visible_offer_count(cfg))
            assert report["ok"], report
            metrics[on] = {k: v["gzip"] for k, v in report["metrics"].items()}
        publish_fixture(tmp_path / str(on), cfg, inspect=inspect)
    for name in metrics[True]:
        print(f"{name} gzip {metrics[False][name]} -> {metrics[True][name]}; "
              f"headroom {cfg.phone_budget[name + '_gzip_max_bytes'] - metrics[True][name]}")
    cost = {name: metrics[True][name] - metrics[False][name] for name in metrics[True]}
    # The row's markup (HTML) and renderTabs' copy of it (inline script, counted in both).
    assert 0 < cost.pop("home_html") <= 64 and 0 < cost.pop("home_js") <= 40
    assert cost.pop("home_css") == 0  # CSS unchanged
    assert abs(cost.pop("deals_json")) <= 4 and not cost  # deals.json: only its site_version stamp differs


def test_pill_in_real_chromium(tmp_path):
    result = subprocess.run(["node", str(Path(__file__).with_name("best_today_tab_browser.cjs")), str(tmp_path)],
                            capture_output=True, text=True, timeout=300, cwd=Path(__file__).parent.parent)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
    for line in ("phone light: pill after Trending", "phone dark: pill after Trending",
                 "desktop light: pill after Trending", "desktop dark: pill after Trending",
                 "phone off: no pill; same row height", "desktop off: no pill; same row height"):
        assert line in result.stdout, result.stdout
