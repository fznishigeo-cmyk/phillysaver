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
from dealradar.rank import front_page_mix, pick_order, pick_score, top_picks
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
    data.write_text(json.dumps([dict(c, state={"sortKey": "fresh", **c.get("state", {})}) for c in cases]))
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
    uncapped = dict(options, max_niche_top=24, niche_after=0, max_same_store_top=24, max_same_category_run=24,
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
])
def test_other_views_unchanged(tmp_path, cfg, db, options, view):
    cases = [dict(rows=rows(), options=o, **view) for o in (options, {})]
    actual, baseline = browser(tmp_path, cfg, db, cases)
    assert not actual["active"]
    assert actual["ordered"] == baseline["ordered"]
    assert actual["rendered"] == baseline["rendered"]


@pytest.mark.parametrize("source", ["@2", "Slickdeals"])
def test_removed_source_selection_is_ignored(tmp_path, cfg, db, options, source):
    cases = [dict(rows=rows(), options=options, controls=controls)
             for controls in ({"srcsel": source}, {})]
    restored, default = browser(tmp_path, cfg, db, cases)
    assert restored["active"]
    assert restored == default


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
    mixed = pick_order(doc["deals"], options)
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


def test_no_niche_card_on_the_first_screen():
    from dealradar.rank import front_page_mix
    opts = {"broad_categories": ["home", "beauty"], "niche_categories": ["auto"],
            "max_niche_top": 2, "niche_after": 8, "max_same_category_run": 2,
            "max_same_store_top": 99, "prefer_photos": False}
    rows = [{"category": "auto", "store": f"s{i}", "status": "alive"} for i in range(6)] + \
           [{"category": ("home", "beauty")[i % 2], "store": f"t{i}", "status": "alive"} for i in range(30)]
    top = front_page_mix(rows, opts)[:24]
    assert all(d["category"] != "auto" for d in top[:8])
    assert sum(d["category"] == "auto" for d in top) == 2


def pick_deal(**changes):
    return make_deal(**{"category": "home", "image_url": "https://img.example/photo.jpg",
                        "first_seen_at": "2026-10-09T14:00:00Z", "codes": [], **changes})


def test_pick_score_math_and_factors(cfg, options):
    cfg.front_page = options
    deal = pick_deal()
    import math
    quality = 80 + 12 * math.log10(41) + 8 + 5 + 3
    assert pick_score(deal, cfg, NOW) == pytest.approx(quality)
    assert pick_score(dict(deal, verify_tier="history-checked"), cfg, NOW) == pytest.approx(quality - 2)
    assert pick_score(dict(deal, verify_tier="unverified", store="Unknown", brand=""), cfg, NOW) == pytest.approx(quality - 16)
    assert pick_score(dict(deal, store=" WALMART "), cfg, NOW) == pytest.approx(quality)
    old = dict(deal, first_seen_at="2026-10-08T02:00:00Z")
    assert pick_score(old, cfg, NOW) == pytest.approx(quality / 2)
    for condition in ("used", "open_box"):
        assert pick_score(dict(deal, conditions=[condition]), cfg, NOW) == pytest.approx(quality * .9)
    assert pick_score(dict(deal, final_price=4.99), cfg, NOW) == pytest.approx(quality * .8)
    assert pick_score(dict(deal, final_price=5), cfg, NOW) == pytest.approx(quality)
    for changes in ({"stack_steps": ["Clip the coupon"]}, {"rebates": ["Mail-in rebate"]},
                    {"conditions": ["subscribe_save"]}, {"conditions": ["in_cart"]},
                    {"conditions": ["multi_buy"]}):
        assert pick_score(dict(deal, **changes), cfg, NOW) == pytest.approx(quality * .8)
    simple = dict(deal, codes=["SAVE10"], stack_steps=["Enter code SAVE10 at checkout"])
    assert pick_score(simple, cfg, NOW) == pytest.approx(quality)
    assert pick_score(dict(simple, stack_steps=["Use code SAVE10", "Clip the coupon"],
                           conditions=["used"], final_price=4), cfg, NOW) == pytest.approx(quality * .8 * .9 * .8)
    # Every score knob comes from config, including both weighted terms.
    cfg.front_page = dict(options, top_picks=dict(options["top_picks"], pct_weight=2,
                                                dollars_weight=0, brand_bonus=10))
    assert pick_score(deal, cfg, NOW) == pytest.approx(160 + 8 + 5 + 10)


@pytest.mark.parametrize("changes", [
    {"image_url": ""}, {"image_url": "http://img.example/a.jpg"},
    {"status": "dead"}, {"locked": True}, {"price_is_range": True},
    {"flags": ["range"]},
    *({"category": cat} for cat in ("auto", "office", "books_media", "other")),
    *({"conditions": [c]} for c in ("targeted", "new_customer", "in_store_only", "ymmv", "clearance_aisle")),
])
def test_pick_score_exclusions(cfg, options, changes):
    cfg.front_page = options
    assert pick_score(dict(pick_deal(), **changes), cfg, NOW) is None


def test_pick_score_disabled_and_custom_niche(cfg, options):
    assert pick_score(pick_deal(), cfg, NOW) is None
    cfg.front_page = dict(options, niche_categories=["home"])
    assert pick_score(pick_deal(), cfg, NOW) is None


def test_top_picks_skewed_catalogue_strict_caps(cfg, options):
    cfg.front_page = options
    cats = ["home", "kitchen", "beauty", "fashion", "electronics", "toys_games"]
    source = [pick_deal(dedupe_key=f"pick{i}", item=f"Product {i} special offer",
                        brand=f"Brand {i % 70}", category=cats[i % 6],
                        store=" Amazon " if i < 150 else f"Store {i % 4}") for i in range(350)]
    scored = [(d, pick_score(d, cfg, NOW)) for d in source]
    selected = top_picks(scored, options)
    assert len(selected) == 120
    assert max(Counter(d["store"].strip().lower() for d in selected).values()) == 40
    assert max(Counter(d["brand"].lower() for d in selected).values()) <= 3
    assert max(Counter(d["category"] for d in selected).values()) <= 24
    assert top_picks(scored, options) == selected
    assert max(len(list(g)) for _, g in groupby(selected[:8], key=lambda d: d["category"])) <= 2


@pytest.mark.parametrize("field,knob,values", [
    ("store", "max_same_store", [" Amazon ", "amazon", "AMAZON", "Target"]),
    ("brand", "max_same_brand", [" Lego ", "lego", "LEGO", "Sony"]),
    ("category", "max_same_category", ["home", "home", "home", "beauty"]),
    ("item", "max_title_twins", ["Great LEGO Set red", "great lego set blue", "Great-LEGO SET black", "Sony headphones"]),
])
def test_top_pick_caps_skip_instead_of_reorder(options, field, knob, values):
    options = dict(options, max_same_category_run=8,
                   top_picks=dict(options["top_picks"], **{knob: 2}))
    source = [dict(pick_deal(), dedupe_key=f"k{i}", item=f"Unique {i} title", **{field: value})
              if field != "item" else pick_deal(dedupe_key=f"k{i}", item=value)
              for i, value in enumerate(values)]
    selected = top_picks([(d, 100 - i) for i, d in enumerate(source)], options)
    assert [d["dedupe_key"] for d in selected] == ["k0", "k1", "k3"]


def test_top_pick_first_eight_run_rule_and_no_brand(cfg, options):
    cfg.front_page = options
    source = [pick_deal(dedupe_key=f"k{i}", item=f"Unique {i} title", brand="",
                        category="home" if i < 6 else ("beauty" if i % 2 else "electronics"))
              for i in range(15)]
    selected = top_picks([(d, 100 - i) for i, d in enumerate(source)], options)
    assert [d["dedupe_key"] for d in selected[:3]] == ["k0", "k1", "k6"]
    assert len(selected) == 11  # run violations stay skipped, even once eight are picked
    assert not any(d["dedupe_key"] == "k2" for d in selected)


def test_published_pick_ps_static_order_and_locked_stubs(tmp_path, cfg, db, options):
    cfg.front_page = options
    cfg.paywall_open_access = True
    source = [pick_deal(dedupe_key=f"k{i}", item=f"Product {i} title", brand="",
                        category="beauty" if i % 2 else "home", pct_at_checkout=40 + i)
              for i in range(45)]
    source.append(pick_deal(dedupe_key="range", item="Range deal", flags=["range"]))
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=source)
    public = json.loads((out / "deals.json").read_text())["deals"]
    picks = sorted((d for d in public if "pick" in d), key=lambda d: d["pick"])
    assert [d["pick"] for d in picks] == list(range(1, len(picks) + 1))
    assert picks[0]["dedupe_key"] == "k44"
    assert len(picks) == 40  # strict store cap; five eligible non-picks still have ps
    for raw, d in zip(source, public):
        value = pick_score(raw, cfg, NOW)
        assert d.get("ps") == (round(value, 1) if value is not None else None)
    assert "ps" not in public[-1] and "pick" not in public[-1]
    ordered_rows = pick_order(public, options)
    titles = lambda html: re.findall(r'<h3 class="title"><a href="[^"]+">([^<]+)</a>', html)
    wall = (out / "index.html").read_text().split('<div id="content">')[1].split('<div id="paywall"')[0]
    assert titles(wall) == [d["item"] for d in ordered_rows[:seo._STATIC_WALL_MAX]]
    assert titles((out / "best-deals-today/index.html").read_text()) == [d["item"] for d in ordered_rows[:30]]
    # Dormant paywall keeps the rotation; public score/selection never names a locked deal.
    cfg.paywall_open_access = False
    render_site(db, cfg, NOW, out_dir=out, deals=source)
    public = json.loads((out / "deals.json").read_text())["deals"]
    for d in public:
        if d.get("locked"):
            assert "pick" not in d and "ps" not in d
    assert sum("pick" in d for d in public) == cfg.paywall_teaser_count


def js_pick_rows():
    return [dict(key=f"k{i}", status="alive", category="home", store="Amazon", item=f"Deal {i}",
                 pick=i + 1, ps=200 - i, pct_at_checkout=60 if i % 2 else 80,
                 first_seen_at="2026-10-09T12:00:00Z", image_url="https://img.example/a.jpg")
            for i in range(120)] + [dict(key=f"rest{i}", status="alive", category="home", store="Amazon",
                                         pct_at_checkout=90, first_seen_at="2026-10-09T14:00:00Z")
                                    for i in range(30)]


def test_browser_top_blocks_new_arrivals_default_and_autoload(tmp_path, cfg, db, options):
    source = js_pick_rows()
    cases = [dict(rows=source, options=options, state={"sortKey": "top", "seed": seed})
             for seed in (1, 2, 1)]
    cases += [dict(rows=source, options=options, state={"sortKey": "top", "seed": 1,
                                                      "newKeys": {"k5": True, "k17": True, "rest0": True}})]
    results = browser(tmp_path, cfg, db, cases)
    for result in results:
        assert result["defaultSort"] == "top"
        assert result["ordered"] == result["rendered"]
        for block in range(10):
            assert set(result["ordered"][block * 12:(block + 1) * 12]) == {f"k{i}" for i in range(block * 12, (block + 1) * 12)}
        assert set(result["ordered"][120:]) == {f"rest{i}" for i in range(30)}
    assert results[0] == results[2]
    assert results[0]["ordered"][:12] != results[1]["ordered"][:12]
    assert results[3]["ordered"][0] == "k5" and results[3]["ordered"][12] == "k17"
    cases = [dict(rows=source, options=options, state={"sortKey": "top", "shown": shown, "catTab": tab})
             for tab, shown in ((0, 60), (0, 120), (0, 180), (2, 120))]
    results = browser(tmp_path, cfg, db, cases)
    assert [r["observed"] for r in results] == [1, 0, 0, 1]
    assert [len(r["rendered"]) for r in results[:2]] == [60, 120]


@pytest.mark.parametrize("view", [{"state": {"catTab": 2}}, {"controls": {"q": "deal"}},
                                  {"controls": {"storesel": "Amazon"}}, {"controls": {"minpct": "50"}}])
def test_browser_filtered_top_uses_score_blocks(tmp_path, cfg, db, options, view):
    source = js_pick_rows()
    for i, d in enumerate(source[:120]):
        d["ps"] = i  # opposite to pick order
    case = dict(rows=source, options=options, **view)
    case["state"] = {"sortKey": "top", **view.get("state", {})}
    result = browser(tmp_path, cfg, db, [case])[0]
    assert not result["active"]
    for block in range(10):
        assert set(result["ordered"][block * 12:(block + 1) * 12]) == {f"k{i}" for i in range(108 - block * 12, 120 - block * 12)}
    assert result["rendered"] == result["ordered"]


def test_browser_top_gone_spacing_and_fresh_still_works(tmp_path, cfg, db, options):
    source = js_pick_rows() + [dict(key=f"gone{i}", status="dead", pct_at_checkout=90) for i in range(30)]
    result = browser(tmp_path, cfg, db, [dict(rows=source, options=options, state={"sortKey": "top"})])[0]
    slots = [i for i, k in enumerate(result["rendered"]) if k.startswith("gone")]
    assert all(i >= 8 for i in slots)
    assert all(b - a >= 8 for a, b in zip(slots, slots[1:]))
    # Existing fresh/drop tests above cover the seeded order and mix. Scores and picks don't affect it.
    baseline = [{k: v for k, v in d.items() if k not in ("pick", "ps")} for d in source]
    with_picks, without = browser(tmp_path, cfg, db, [dict(rows=r, options=options) for r in (source, baseline)])
    assert with_picks == without


def test_browser_all_deals_tab_is_everything_newest_first(tmp_path, cfg, db, options):
    """Operator, 2026-10-12: a tab "that has everything, like endless scroll"."""
    from dealradar.publish import seo
    assert [g[0] for g in seo.CATGROUPS[:3]] == ["Trending", "All deals", "Used & Open-Box"]
    assert seo.CATGROUPS[1][1] is None and not seo.CATGROUPS[1][2]
    assert "All deals" not in [g[0] for g in seo.HUB_GROUPS]
    source = js_pick_rows()
    all_tab, fresh = browser(tmp_path, cfg, db, [
        dict(rows=source, options=options, state={"sortKey": "top", "catTab": 1, "shown": 120}),
        dict(rows=source, options=options, state={"sortKey": "fresh", "catTab": 1, "shown": 120})])
    assert all_tab["ordered"] == fresh["ordered"]
    assert all_tab["observed"] == 1  # endless: auto-load keeps going past 120
