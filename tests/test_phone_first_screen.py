"""phone_first_screen (operator 2026-10-23: the first 3 deals and their buttons on a phone's first screen)."""
import json
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import hide_timer_ticker, keep_first_cards, phone_first_screen as feature, site
from scripts.publication_budget import analyze
from tests.history_pins import (pre_best_today_tab, pre_front_mix, pre_phone_first_screen, pre_short_cards,
                                pre_social_profiles)
from tests.phone_budget_fixture import NOW, fixture_offers, publish_fixture, visible_offer_count
from tests.test_keep_first_cards import files, probe
from tests.test_search_amazon import run_node

BROWSER = Path(__file__).with_name("phone_first_screen_browser.cjs")


def config(on, **changes):
    cfg = load_config()
    cfg.tracking = {}
    cfg.hide_deal_site_links = False  # keep the coded partner offers, as the budget fixture does
    pre_short_cards(cfg)  # short_cards replaces the How box and its link: tests/test_short_cards.py
    for name, value in changes.items():
        setattr(cfg, name, value)
    if on is None:
        del cfg.phone_first_screen
    else:
        cfg.phone_first_screen = on
    return cfg


def build(root, on, rows=300, **changes):
    """The phone-budget offers (every fourth coded: step deals on the first screen); on=None: missing."""
    cfg = config(on, **changes)
    db = DB(":memory:")
    try:
        with patch.object(site, "util_now", lambda: NOW):
            site.render_site(db, cfg, NOW, out_dir=root, deals=fixture_offers()[:rows],
                             last_run={"run_key": "phone-first-screen"})
    finally:
        db.close()
    return root


def test_switch_strict_default_shipped_off_and_pinned(tmp_path):
    assert load_config().phone_first_screen is True  # operator approved 2026-10-24 (A54, the exact phrase)
    assert AppConfig().phone_first_screen is False
    on = load_config()
    on.phone_first_screen = True
    assert feature.enabled(on)
    assert pre_phone_first_screen(on).phone_first_screen is False
    on.phone_first_screen = True
    assert pre_social_profiles(on).phone_first_screen is False  # every older recorded fixture
    for value in (1, "true", None, [], {}):
        path = tmp_path / "config.yaml"
        path.write_text(json.dumps({"phone_first_screen": value}))
        with pytest.raises(ValueError, match="^phone_first_screen must be a boolean$"):
            load_config(path)
    on = load_config()
    on.phone_first_screen, on.keep_first_cards = True, False
    assert not feature.enabled(on)  # the static cards must draw what the script draws
    assert not feature.enabled(type("Config", (), {"phone_first_screen": 1, "keep_first_cards": True,
                                                   "drop_fresh": True, "drop_shuffle": True})())


def test_off_missing_rerun_back_changes_no_byte(tmp_path, monkeypatch):
    off = files(build(tmp_path / "off", False, rows=120))
    with monkeypatch.context() as m:  # the same build without the feature at all
        m.setattr(feature, "enabled", lambda cfg: False)
        m.setattr(feature, "site_version", lambda version, cfg: version)
        m.setattr(feature, "publish", lambda out, cfg: None)
        assert files(build(tmp_path / "without", False, rows=120)) == off
    assert files(build(tmp_path / "missing", None, rows=120)) == off
    assert b"deal-steps" not in off["index.html"]  # the tool pages' own cards have always had theirs
    on = files(build(tmp_path / "on", True, rows=120))
    assert {k for k in on if on[k] != off.get(k)} == {"index.html", "deals.json"} and on.keys() == off.keys()
    assert files(build(tmp_path / "on", True, rows=120)) == on  # a rerun is byte-stable
    assert files(build(tmp_path / "on", False, rows=120)) == off  # and back


def test_on_home_markup_css_and_script(tmp_path):
    out = build(tmp_path / "on", True, rows=120)
    home = (out / "index.html").read_text()
    assert home.count(feature.CSS) == 1 and home.index(feature.CSS) < home.index("</head>")
    assert feature.HOW_LINK in home
    # Phones only: outside the 640px query the CSS only hides the new link.
    assert feature.CSS.startswith('<style>.deal-steps{display:none}@media(max-width:640px){')
    assert feature.CSS.endswith('}}</style>') and feature.CSS.count('@media') == 1
    # Each static link follows its card's How box, to the card's own deal page.
    cards = re.findall(r'<article class="card.*?</article>', home, flags=re.S)
    assert sum('class="deal-steps"' in card for card in cards) >= 10  # every fourth fixture offer is coded
    for card in cards:
        link = re.search(r'<a class="deal-steps" href="([^"]+)">How to get this price</a>', card)
        if link:
            assert re.search(r'<div class="how" data-requirements>.*?</div>' + re.escape(link[0]) + '<div class="metarow">',
                             card, flags=re.S)
            assert f'<h3 class="title"><a href="{link[1]}">' in card
    # The hidden ticker's rules make the room; its markup and hiding rule stay.
    assert ".tk{--tk-teal" not in home and ".tksr{" not in home
    assert 'id="ticker"' in home and hide_timer_ticker.SNIPPET in home
    # No past-price wording, ever (2026-10-17); the only new words are the tool pages' link text.
    added = re.sub(r"<[^>]+>", " ", feature.STEPS + feature.CSS + feature.HOW_LINK)
    assert not re.search(r"\b(was|now|history|lowest|before)\b", added, re.I)
    for other in out.rglob("*.html"):
        if other != out / "index.html":
            assert "deal-steps" not in other.read_text(), other


def test_ticker_rules_stay_while_the_ticker_shows(tmp_path):
    home = (build(tmp_path, True, rows=40, hide_timer_ticker_confetti=False) / "index.html").read_text()
    assert ".tk{--tk-teal" in home and ".tksr{" in home and feature.CSS in home


@pytest.mark.parametrize("tidy", [True, False])
def test_static_link_matches_the_script(tmp_path, tidy):
    cfg = config(True, card_tidy=tidy)
    out = build(tmp_path / "on", True, rows=40, card_tidy=tidy)
    doc = json.loads((out / "deals.json").read_text())
    base = dict(next(d for d in doc["deals"] if d.get("deal_path")), status="alive")
    rows = [dict(base, key="steps", needs_steps=True, how=["Clip the coupon"], codes=["SAVE5"]),
            dict(base, key="extra", needs_steps=True, how=["Clip"], checkout=dict(base.get("checkout") or {},
                                                                              card="Rebate after purchase")),
            dict(base, key="plain", needs_steps=False, how=[], codes=[]),
            dict(base, key="nopage", needs_steps=True, how=["Clip"], deal_path=""),
            dict(base, key="dead", status="dead", needs_steps=True, how=["Gone"])]
    cases = [dict(row=row, how=keep_first_cards.how_html(row, cfg),
                  steps=feature.steps_html(row, row.get("deal_path"), cfg)) for row in rows]
    assert [bool(c["steps"]) for c in cases] == [True, False, False, False, False]
    path = tmp_path / "parity.json"
    path.write_text(json.dumps(cases))
    result = run_node("drop_card_parity.cjs", out / "index.html", out / "deals.json", path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("links", [False, "chips"])
def test_phone_budget_with_the_switch_on(tmp_path, links):
    """The flip keeps the phone budgets and the 1,000-byte HTML reserve (test_drop_fresh's fixture;
    with the declined home tool links, test_tool_pages_1020's, where the ticker rules sit in home-tools.css)."""
    sizes = {}
    for on in (False, True) if not links else (True,):
        cfg = pre_best_today_tab(pre_front_mix(load_config()))  # the pill's bytes: test_best_today_tab
        pre_short_cards(cfg)  # this switch's own bytes; with short_cards: tests/test_short_cards.py
        cfg.phone_first_screen = on
        cfg.phone_budget["enabled"] = True
        if links:
            cfg.tool_pages_1020_home_links, cfg.tool_pages_1020_phone_links = True, links

        def inspect(db, out):
            report = analyze(out, cfg, expected_offers=visible_offer_count(cfg))
            assert report["ok"], report
            assert cfg.phone_budget["home_html_gzip_max_bytes"] - report["metrics"]["home_html"]["gzip"] >= 1000
            sizes[on] = {k: v["gzip"] for k, v in report["metrics"].items()}
            if links:
                assert not (out / "home-tools.css").read_text().startswith(".tk{")
        publish_fixture(tmp_path / str(on), cfg, inspect=inspect)
    print("phone_first_screen gzip OFF -> ON:", json.dumps(sizes, sort_keys=True))
    if not links:
        assert sizes[True]["home_css"] < sizes[False]["home_css"] and sizes[True]["home_html"] < sizes[False]["home_html"]


@pytest.fixture(scope="module")
def sites(tmp_path_factory):
    root = tmp_path_factory.mktemp("phone-first-screen")
    return {on: build(root / ("on" if on else "off"), on) for on in (True, False)}


def test_browser_first_screen(sites):
    """Real Chromium: 390x844 (light, dark), 375x667, 430x932, 360x740 off vs on; 1280x800 unchanged."""
    result = subprocess.run(["node", str(BROWSER), str(sites[False]), str(sites[True]), "/opt/pw-browsers/chromium"],
                            capture_output=True, text=True, timeout=600, cwd=BROWSER.parent.parent)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    report = json.loads(result.stdout.splitlines()[-1])
    print(json.dumps(report))
    assert report["390x844-light"]["off"]["buttons"] == 0 and report["390x844-light"]["on"]["buttons"] >= 3


def test_browser_keeps_the_first_cards(sites):
    """keep_first_cards' probe (390x844, 4x CPU) with the switch on: no first-screen card or LCP redrawn."""
    run, result = probe(sites[True], NOW.isoformat().replace("+00:00", "Z"), preserved=True)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-3000:]
    assert result["lostOriginal"] == 0 and result["removedFirstScreen"] == 0 and result["lcpScripted"] == []
