"""All/default top-24 selection and Python/browser parity, entirely offline."""

import json
import re
import subprocess
from collections import Counter
from itertools import groupby

import pytest
import yaml

from dealradar.config import load_config
from dealradar.publish import seo
from dealradar.publish.site import render_site
from dealradar.rank import front_page_mix
from tests.test_publish_site import NOW, make_deal
from tests.test_seo import ROOT, cfg, db


@pytest.fixture
def options():
    return load_config(ROOT / "config.yaml").front_page


def rows():
    categories = ["auto"] * 35 + ["home"] * 30 + ["kitchen", "beauty", "electronics", "toys_games"] * 20
    return [dict(key=f"k{i}", dedupe_key=f"k{i}", item=f"Item {i}", status="alive",
                 category=cat, store="Amazon" if i < 65 else f"Store {i % 4}",
                 pct_at_checkout=80, first_seen_at="2026-10-09T12:00:00Z",
                 image_url="https://img.example/item.jpg" if i % 3 else "")
            for i, cat in enumerate(categories)]


def caps(mixed, options):
    top = mixed[:24]
    assert sum(d["category"] in options["niche_categories"] for d in top) <= options["max_niche_top"]
    assert max(Counter(d["store"].strip().lower() for d in top).values()) <= options["max_same_store_top"]
    assert max(len(list(g)) for _, g in groupby(top, key=lambda d: d["category"])) <= options["max_same_category_run"]


def browser(tmp_path, cfg, db, cases):
    # Use actual published HTML, including the render path and seeded shuffle.
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=[])
    data = tmp_path / "cases.json"
    data.write_text(json.dumps(cases))
    result = subprocess.run(["node", str(ROOT / "tests/front_page_logic.cjs"),
                             str(out / "index.html"), str(data)],
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_caps_photos_and_stable_remainder(options):
    source = rows()
    original = source.copy()
    mixed = front_page_mix(source, options)
    caps(mixed, options)
    assert all(d["image_url"] for d in mixed[:24])
    assert Counter(d["key"] for d in mixed) == Counter(d["key"] for d in source)
    picked = {d["key"] for d in mixed[:24]}
    assert mixed[24:] == [d for d in source if d["key"] not in picked]
    assert source == original
    assert front_page_mix(source, options) == mixed
    assert any(d["category"] == "auto" for d in mixed[24:])


def test_photos_lead_when_photographed_supply_is_limited(options):
    source = rows()[65:]
    for i, d in enumerate(source):
        d["image_url"] = "photo" if i < 8 else ""
    mixed = front_page_mix(source, options)
    caps(mixed, options)
    assert [bool(d["image_url"]) for d in mixed[:24]] == [True] * 8 + [False] * 16
    # A run of photographed home deals cannot break the category-run cap.
    for d in source[:8]:
        d["category"] = "home"
    mixed = front_page_mix(source, options)
    caps(mixed, options)
    assert [bool(d["image_url"]) for d in mixed[:24]] == [True] * 2 + [False] * 22


def test_config_switches_and_short_supply(tmp_path, options):
    source = rows()
    assert front_page_mix(source, {}) is source
    uncapped = dict(options, max_niche_top=24, max_same_store_top=24, max_same_category_run=24,
                    prefer_photos=False)
    assert front_page_mix(source, uncapped) == source
    photos = front_page_mix(source, dict(uncapped, prefer_photos=True))
    assert all(d["image_url"] for d in photos[:24])
    custom = dict(options, max_niche_top=0, max_same_store_top=5, max_same_category_run=1)
    caps(front_page_mix(source, custom), custom)
    # Explicit niche wins; unclassified categories are conservatively niche.
    restricted = dict(options, broad_categories=["kitchen"], max_niche_top=0)
    assert all(d["category"] == "kitchen" for d in front_page_mix(source, restricted)[:2])
    for n in (0, 1, 8, 24, 40):
        scarce = source[:n]
        assert Counter(d["key"] for d in front_page_mix(scarce, options)) == Counter(d["key"] for d in scarce)
    path = tmp_path / "config.yaml"
    for setting in ({}, False, custom):
        path.write_text(yaml.safe_dump({"publish": {"front_page": setting}}))
        assert load_config(path).front_page == (setting or {})


def test_seeded_browser_caps_render_and_python_parity(tmp_path, cfg, db, options):
    source = rows()
    cases = [dict(rows=source, options=options, state={"seed": seed}) for seed in range(20)]
    results = browser(tmp_path, cfg, db, cases + cases[:1])
    by_key = {d["key"]: d for d in source}
    for result in results:
        base = [by_key[k] for k in result["base"]]
        expected = front_page_mix(base, options)
        assert result["ordered"] == result["rendered"] == [d["key"] for d in expected]
        assert result["mixed"] == [d["key"] for d in front_page_mix(source, options)]
        caps(expected, options)
        assert all(d["image_url"] for d in expected[:24])
    assert results[0] == results[-1]
    assert len({tuple(r["ordered"][:24]) for r in results}) > 1
    assert [r["seed"] for r in results[:-1]] == list(range(20))


@pytest.mark.parametrize("view", [
    {"state": {"catTab": 1}}, {"state": {"catTab": 4}},
    {"state": {"sortKey": "pct"}}, {"state": {"sortKey": "price"}},
    {"state": {"sortDir": 1}}, {"controls": {"q": "car"}},
    {"controls": {"storesel": "Amazon"}}, {"controls": {"minpct": "80"}},
    {"controls": {"srcsel": "@2"}}, {"controls": {"srcsel": "slickdeals.net"}},
])
def test_other_views_unchanged(tmp_path, cfg, db, options, view):
    cases = [dict(rows=rows(), options=o, **view) for o in (options, {})]
    actual, baseline = browser(tmp_path, cfg, db, cases)
    assert not actual["active"]
    assert actual["ordered"] == baseline["ordered"]
    assert actual["rendered"] == baseline["rendered"]


def test_gone_slots_and_free_picks_preserved(tmp_path, cfg, db, options):
    source = rows()[65:]
    for i, d in enumerate(source):
        d["locked"] = i >= 8
    source.insert(8, dict(source[-1], key="gone", status="dead", locked=False))
    mixed = front_page_mix(source, options)
    assert mixed[8]["key"] == "gone"
    assert {d["key"] for d in mixed[:8]} == {d["key"] for d in source[:8]}
    assert all(d.get("locked") for d in mixed[9:])
    result = browser(tmp_path, cfg, db, [dict(rows=source, options=options)])[0]
    assert result["mixed"] == [d["key"] for d in mixed]
    assert result["rendered"][8] == "gone"


def test_caps_reserve_space_for_gone_cards(tmp_path, cfg, db, options):
    source = rows()
    source.insert(8, dict(source[0], key="gone", status="dead", image_url="photo"))
    mixed = front_page_mix(source, options)
    caps(mixed, options)
    assert mixed[8]["key"] == "gone"
    result = browser(tmp_path, cfg, db, [dict(rows=source, options=options)])[0]
    assert result["mixed"] == [d["key"] for d in mixed]
    by_key = {d["key"]: d for d in source}
    caps([by_key[k] for k in result["rendered"]], options)


def test_drop_mixes_once_and_deeper_strong_section_survives(tmp_path, cfg, db, options):
    source = rows()
    for d in source[65:]:
        d["pct_at_checkout"] = 60
    result = browser(tmp_path, cfg, db, [dict(rows=source, options=options, drop=True)])[0]
    assert result["dropRenders"] == 1
    assert result["seed"] == 42
    assert result["strongSection"]
    by_key = {d["key"]: d for d in source}
    caps([by_key[k] for k in result["rendered"]], options)
    picked = set(result["rendered"][:24])
    base = [by_key[k] for k in result["base"]]
    tiered = [d for d in base if d["pct_at_checkout"] >= 70] + [d for d in base if d["pct_at_checkout"] < 70]
    assert result["rendered"][24:] == [d["key"] for d in tiered if d["key"] not in picked]


def test_mix_does_not_change_free_rotation(tmp_path, cfg, db, options):
    source = [make_deal(**{k: v for k, v in d.items() if k != "key"}) for d in rows()]
    rotations = []
    for setting in ({}, options):
        cfg.front_page = setting
        db.meta_set("teaser_recent", "[]")
        drops = []
        for _ in range(2):
            out = tmp_path / "site"
            render_site(db, cfg, NOW, out_dir=out, deals=source)
            public = json.loads((out / "deals.json").read_text())["deals"]
            drops.append({d["key"] for d in public if not d.get("locked")})
            assert len(public) == len(source)
        assert not drops[0] & drops[1]
        rotations.append(drops)
    assert rotations[0] == rotations[1]


def test_static_wall_best_today_and_category_unchanged(tmp_path, cfg, db, options):
    cfg.paywall_open_access = True
    cfg.front_page = options
    source = [make_deal(**{k: v for k, v in d.items() if k != "key"}) for d in rows()]
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=source)
    doc = json.loads((out / "deals.json").read_text())
    assert doc["meta"]["front_page"] == options
    # JSON stays in rank order so category views and the free rotation don't change.
    assert [d["dedupe_key"] for d in doc["deals"]] == [d["dedupe_key"] for d in source]
    mixed = front_page_mix(doc["deals"], options)
    titles = lambda html: re.findall(r'<h3 class="title"><a href="[^"]+">([^<]+)</a>', html)
    wall = (out / "index.html").read_text().split('<div id="content">')[1].split('<div id="paywall"')[0]
    assert titles(wall) == [d["item"] for d in mixed[:seo._STATIC_WALL_MAX]]
    best = (out / "best-deals-today/index.html").read_text()
    assert titles(best) == [d["item"] for d in mixed[:30]]
    auto = (out / "deals/automotive/index.html").read_text()
    assert titles(auto) == [d["item"] for d in doc["deals"] if d["category"] == "auto"]
    cfg.front_page = {}
    render_site(db, cfg, NOW, out_dir=out, deals=source)
    assert titles((out / "best-deals-today/index.html").read_text()) == [d["item"] for d in source[:30]]
