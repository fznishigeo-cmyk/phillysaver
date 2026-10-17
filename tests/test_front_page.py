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
    options = load_config(ROOT / "config.yaml").front_page
    options.pop("demote_words", None)  # recorded before task 267b
    options["appeal"]["want"]["enabled"] = False  # recorded before task 273
    options["appeal"]["season"] = []
    options["top_picks"]["max_same_brand"] = 3  # recorded before the first-screen mix
    options["top_picks"].pop("brand_from_title", None)
    options["top_picks"].pop("max_clothing_per_window", None)
    return options


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
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("live", [0, 9, 10, 11])
@pytest.mark.parametrize("support", [False, True])
def test_amazon_strip_threshold_link_and_text_only(tmp_path, cfg, db, live, support):
    cfg.support_enabled = support
    cfg.paywall_open_access = True
    deals = [make_deal(dedupe_key=f"amazon-{i}", store=("Amazon", " amazon.COM ", "AMAZON")[i % 3])
             for i in range(live)]
    deals += [make_deal(dedupe_key="gone-amazon", store="Amazon", status="dead", death="confirmed",
                        died_at="2026-10-09T13:00:00+00:00"),
              make_deal(dedupe_key="different-store", store="Amazon Fresh")]
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=deals)
    home = (out / "index.html").read_text()
    strip = re.search(r'<aside id="amazon-strip"([^>]*)>(.*?)</aside>', home, re.S)
    assert strip
    assert bool(re.search(r"\bhidden\b", strip[1])) is (live < 10)
    assert "Top <strong>Amazon</strong> deals right now" in strip[2]
    assert 'href="?store=amazon&amp;sort=top#content">See deals ›</a>' in strip[2]
    assert not re.search(r"<(img|svg|object|iframe)\b", strip[2], re.I)
    assert home.index("</header>") < strip.start() < home.index('id="cattabs"') < home.index('id="content"')


def test_wide_grid_preserves_card_room_and_small_screen_rules():
    for name in ("index.html", "hub.html"):
        template = (ROOT / "dealradar/publish/templates" / name).read_text()
        assert re.search(r"@media \(min-width:1280px\)\{[^@]*?"
                         r"\.grid\{grid-template-columns:repeat\(5,minmax\(0,1fr\)\)\}", template)
        assert "max-width:1360px" in template
        assert "grid-template-columns:repeat(auto-fill,minmax(235px,1fr))" in template
        assert "grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px" in template
        # At the breakpoint, each card retains the old 235px minimum.
        assert (1280 - 2 * 16 - 4 * 14) / 5 >= 235
    home = (ROOT / "dealradar/publish/templates/index.html").read_text()
    assert "-webkit-line-clamp:2" in home
    assert re.search(r"#cattabs\{[^}]*flex-wrap:wrap", home)
    assert ".cattab::before" in home and ".cattab:focus-visible" in home


def test_static_phone_card_keeps_store_price_steps_and_source_credit():
    card = seo.card_html(dict(key="phone", store="Amazon", item="A two-line product title",
                             status="alive", url="https://amazon.com/dp/B012345678",
                             final_price=10, regular_price=50, pct_at_checkout=80,
                             abs_savings=40, badge="history-checked", conditions=["subscribe_save"],
                             needs_steps=True, codes=["SAVE10"], how=["Clip the coupon"],
                             credits=[dict(name="Hip2Save", url="https://hip2save.com/deal")]))
    assert '<div class="metarow"><span class="chip storechip">Amazon</span>' in card
    assert 'class="badge tier-history-checked"' in card
    assert 'class="chip">subscribe save</span>' in card
    for required in ('class="now">$10.00', 'class="flag">-80%', '>Get deal</a>',
                     'How to get this price:', 'class="code">SAVE10', 'class="credit">via '):
        assert required in card
    assert '<a href="https://hip2save.com/deal" rel="noopener">Hip2Save</a>' in card


def test_intake_controls_do_not_change_published_wall_or_front_page_mix(tmp_path, cfg, db, monkeypatch):
    monkeypatch.setattr("dealradar.publish.site.util_now", lambda: NOW)
    cfg.support_enabled = True
    cfg.paywall_open_access = True
    deals = [make_deal(dedupe_key=f"intake-{i}", store="Amazon" if i < 12 else "Target",
                      category="home" if i % 2 else "kitchen") for i in range(30)]
    out = tmp_path / "site"
    outputs = []
    for mode in ("off", "shadow", "on"):
        cfg.intake_mode = mode
        render_site(db, cfg, NOW, out_dir=out, deals=deals)
        outputs.append(((out / "index.html").read_bytes(), (out / "deals.json").read_bytes()))
    assert outputs[0] == outputs[1] == outputs[2]
    assert b"proposed_fetch_order" not in outputs[0][0] + outputs[0][1]


@pytest.mark.parametrize("name", ["index", "about", "how-it-works", "subscribe", "join", "privacy", "hub"])
def test_design_templates_add_no_external_urls(name):
    template = (ROOT / f"dealradar/publish/templates/{name}.html").read_text()
    # Existing namespaces and the dormant Google login script are the only
    # literal external URLs; no new fonts, images, CDNs or protocol-relative assets.
    existing = {"http://www.w3.org/2000/svg"}
    if name == "index":
        existing |= {"https://schema.org", "https://accounts.google.com/gsi/client",
                     # task 259: the tagged Amazon search link is built only on click
                     "https://www.amazon.com/s", "https://www.amazon.com/"}
    assert set(re.findall(r"https?://[\w./-]+", template)) == existing
    assert not re.search(r'(?:src|href)=["\']//|@import\b|url\(["\']?(?:https?:)?//', template)


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
                    prefer_photos=False, niche_last=False)
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
    options.pop("checkout_price_deals", None)  # predates the checkout-price switch
    cfg.paywall_open_access = True
    cfg.front_page = options
    source = [make_deal(**{k: v for k, v in d.items() if k != "key"}) for d in rows()]
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=source)
    doc = json.loads((out / "deals.json").read_text())
    assert doc["meta"]["front_page"] == {k: v for k, v in options.items() if k not in {"appeal", "popular_affordable", "everyday_price"}}
    # JSON stays in rank order so category views and the free rotation don't change.
    assert [d["dedupe_key"] for d in doc["deals"]] == [d["dedupe_key"] for d in source]
    mixed = pick_order(doc["deals"], options)
    titles = lambda html: re.findall(r'<h3 class="title"><a href="[^"]+">([^<]+)</a>', html)
    wall = (out / "index.html").read_text().split('<div id="content" data-clarity-unmask="true">')[1].split('<div id="paywall"')[0]
    assert titles(wall) == [d["item"] for d in mixed[:seo._STATIC_WALL_MAX]]
    best = (out / "best-deals-today/index.html").read_text()
    assert titles(best) == [d["item"] for d in mixed[:30]]
    auto = (out / "deals/automotive/index.html").read_text()
    assert titles(auto) == [d["item"] for d in doc["deals"] if d["category"] == "auto"]
    cfg.front_page = {}
    render_site(db, cfg, NOW, out_dir=out, deals=source)
    assert titles((out / "best-deals-today/index.html").read_text()) == [d["item"] for d in source[:30]]


def test_best_today_has_a_category_summary(tmp_path, cfg, db, options):
    # Its own words (where the picks come from), not just a copy of the home wall.
    cfg.paywall_open_access = True
    cfg.front_page = options
    categories = ["home", "beauty", "electronics", "kitchen", "auto"] * 8
    source = [make_deal(dedupe_key=f"k{i}", item=f"Item {i}", category=cat, pct_at_checkout=80,
                        image_url="https://img.example/item.jpg") for i, cat in enumerate(categories)]
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=source)
    best = (out / "best-deals-today/index.html").read_text()
    assert "ranked like the live wall" not in best
    summary = re.search(r'<p class="best-summary">(.*?)</p>', best).group(1)
    assert summary.startswith("Top categories today: ")
    assert len(re.findall(r'<a href="/deals/[a-z-]+/">', summary)) == 3
    assert 'href="/deals/home-kitchen/"' in summary
    assert "Automotive" not in summary  # car parts sink below the 30 picks


def test_best_summary_needs_two_categories():
    assert seo._best_summary([dict(category="home")] * 5) == ""
    html = seo._best_summary([dict(category="home")] * 3 + [dict(category="beauty")] * 2
                             + [dict(category="auto")])
    assert html == ('<p class="best-summary">Top categories today: '
                    '<a href="/deals/home-kitchen/">Home &amp; Kitchen</a> (3), '
                    '<a href="/deals/beauty-personal-care/">Beauty &amp; Personal Care</a> (2) and '
                    '<a href="/deals/automotive/">Automotive</a> (1).</p>')


def test_home_static_wall_draws_the_first_four_photos_like_the_script(tmp_path, cfg, db, options):
    # Phone speed (2026-10-16): the largest image starts with the HTML, not after the script.
    cfg.paywall_open_access = True
    cfg.front_page = options
    source = [make_deal(dedupe_key=f"k{i}", item=f"Item {i}", category="home", pct_at_checkout=80,
                        image_url=f"https://img.example/{i}.jpg" if i != 1 else "") for i in range(8)]
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=source)
    wall = (out / "index.html").read_text().split('<div id="content" data-clarity-unmask="true">')[1].split('<div id="paywall"')[0]
    photos = re.findall(r'<img fetchpriority="high" loading="eager" referrerpolicy="no-referrer" src="([^"]+)"', wall)
    assert len(photos) == 4 and wall.count("<img") == 4
    assert all(src.startswith("https://img.example/") for src in photos)
    assert wall.count('class="ph"') == 8  # every card keeps its tile; photo cards hide it until an error
    best = (out / "best-deals-today/index.html").read_text()
    assert "<img" not in best.split('<div class="grid">')[1].split("</div></div>")[0]


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
                        "first_seen_at": "2026-10-09T14:00:00Z",
                        "price_rechecked_at": "2026-10-09T14:00:00Z", "codes": [], **changes})


def test_pick_score_math_and_factors(cfg, options):
    options["appeal"]["enabled"] = False  # predates the broad-appeal switch
    cfg.front_page = options
    deal = pick_deal()
    import math
    factor = options["top_picks"]["no_earn_factor"]
    quality = (80 + 12 * math.log10(41) + 8 + 5 + 3) * factor
    assert pick_score(deal, cfg, NOW) == pytest.approx(quality)
    assert pick_score(dict(deal, verify_tier="history-checked"), cfg, NOW) == pytest.approx(quality - 2 * factor)
    assert pick_score(dict(deal, verify_tier="unverified", store="Unknown", brand=""), cfg, NOW) == pytest.approx(quality - 16 * factor)
    assert pick_score(dict(deal, store=" WALMART "), cfg, NOW) == pytest.approx(quality)
    old = dict(deal, first_seen_at="2026-10-08T02:00:00Z")
    assert pick_score(old, cfg, NOW) == pytest.approx(quality / 2)
    # One-unit used items sell out to the first buyer: never a top pick
    # (operator, 2026-10-13); with the exclusion off the old factor applies.
    for condition in ("used", "open_box", "refurbished"):
        assert pick_score(dict(deal, conditions=[condition]), cfg, NOW) is None
    cfg.front_page = dict(options, top_picks=dict(options["top_picks"], exclude_conditions=[]))
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
    cfg.front_page = options
    # Every score knob comes from config, including both weighted terms.
    cfg.front_page = dict(options, top_picks=dict(options["top_picks"], pct_weight=2,
                                                dollars_weight=0, brand_bonus=10))
    assert pick_score(deal, cfg, NOW) == pytest.approx((160 + 8 + 5 + 10) * factor)


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
    wall = (out / "index.html").read_text().split('<div id="content" data-clarity-unmask="true">')[1].split('<div id="paywall"')[0]
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
    for result, seed in zip(results[:3], (1, 2, 1)):
        # Each drop mark brings the next 24 picks to the top (operator,
        # 2026-10-13: a drop must look new, not shuffled), dealt in blocks of 12.
        turned = list(range(120))[seed * 24:] + list(range(120))[:seed * 24]
        assert result["defaultSort"] == "top"
        assert result["ordered"] == result["rendered"]
        for block in range(10):
            assert set(result["ordered"][block * 12:(block + 1) * 12]) == {
                f"k{i}" for i in turned[block * 12:(block + 1) * 12]}
        assert set(result["ordered"][120:]) == {f"rest{i}" for i in range(30)}
    assert results[0] == results[2]
    # Consecutive drops share no deal in the top 24.
    assert not set(results[0]["ordered"][:24]) & set(results[1]["ordered"][:24])
    # Picks that arrived since the last drop lead it.
    assert set(results[3]["ordered"][:2]) == {"k5", "k17"}
    assert set(results[3]["ordered"][120:]) == {f"rest{i}" for i in range(30)}
    source += [dict(key=f"extra{i}", status="alive", category="home", store="Amazon",
                    pct_at_checkout=90, first_seen_at="2026-10-09T13:00:00Z") for i in range(250)]
    cases = [dict(rows=source, options=options, state={"sortKey": "top", "shown": shown, "catTab": tab})
             for tab, shown in ((0, 60), (0, 120), (0, 180), (0, 240), (0, 300), (0, 360), (1, 300), (3, 300))]
    results = browser(tmp_path, cfg, db, cases)
    assert [r["observed"] for r in results] == [1, 1, 1, 1, 0, 0, 1, 1]
    assert [len(r["rendered"]) for r in results] == [60, 120, 180, 240, 300, 360, 300, 300]
    assert set(results[4]["rendered"][:120]) == {f"k{i}" for i in range(120)}
    assert results[4]["rendered"][120:150] == results[0]["ordered"][120:150]


def test_trending_rest_is_newest_first_after_picks(tmp_path, cfg, db, options):
    source = js_pick_rows()[:120] + [
        dict(key="older-photo", status="alive", pct_at_checkout=90,
             first_seen_at="2026-10-09T10:00:00Z", image_url="https://img.example/a.jpg"),
        dict(key="newest-no-photo", status="alive", pct_at_checkout=60,
             first_seen_at="2026-10-09T14:00:00Z"),
        dict(key="middle-photo", status="alive", pct_at_checkout=80,
             first_seen_at="2026-10-09T12:00:00Z", image_url="https://img.example/a.jpg"),
    ]
    result = browser(tmp_path, cfg, db, [dict(rows=source, options=options, state={"sortKey": "top"})])[0]
    assert set(result["ordered"][:120]) == {f"k{i}" for i in range(120)}
    assert result["ordered"][120:] == ["newest-no-photo", "middle-photo", "older-photo"]


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


def test_one_credit_per_source_name():
    """Two feeds of one partner read "via Hip2Save" once (live 2026-10-13)."""
    from datetime import datetime, timezone
    from dealradar.publish.site import _serialize_deal
    deal = {"dedupe_key": "k", "sightings": [
        {"source_name": "hip2save", "source_url": "https://hip2save.com/a"},
        {"source_name": "slickdeals_frontpage", "source_url": "https://slickdeals.net/b"},
        {"source_name": "hip2save_amazon", "source_url": "https://hip2save.com/c"}]}
    credits = _serialize_deal(deal, datetime(2026, 10, 13, tzinfo=timezone.utc))["credits"]
    assert [c["url"] for c in credits] == ["https://hip2save.com/a", "https://slickdeals.net/b"]


def daily_deal(source="meh", **changes):
    seen = changes.get("first_seen_at", "2026-10-09T12:00:00Z")
    price = changes.get("final_price", 10)
    return pick_deal(**{"dedupe_key": source, "store": source, "first_seen_at": seen, "sightings": [{
        "source_name": source, "source_url": f"https://{source}.com/deals/today",
        "seen_at": seen, "final_price": price}], **changes})


@pytest.mark.parametrize("changes", [
    {"status": "dead"}, {"image_url": ""}, {"image_url": "http://img.example/a.jpg"},
    {"image_url": "https://meh.com/photo.jpg"}, {"online_only": "unknown"},
    {"online_only": "false"}, {"currency": "EUR"}, {"region": "CA"},
    {"flags": ["suspect_msrp"]}, {"flags": ["unbacked_price"]},
    {"abs_savings": 4.99}, {"abs_savings": None}, {"pct_at_checkout": 69.9},
    {"final_price": None}, {"final_price": -1}, {"final_price": float("nan")},
    {"pct_at_checkout": 100}, {"first_seen_at": "2026-10-08T12:00:00Z"},
    {"first_seen_at": "2026-10-09T15:00:00Z"},
    {"price_published_at": "2026-10-08T12:00:00Z"},
    {"expires_at": "2026-10-09T14:00:00Z"},
    {"sightings": [{"source_name": "keepa_amazon_drops"}]},
])
def test_spotlight_preserves_publish_gates(cfg, options, changes):
    from dealradar.rank import daily_spotlight
    cfg.front_page = options
    assert daily_spotlight([daily_deal(**changes)], cfg, NOW) == []


def test_spotlight_selection_known_pct_then_newest(cfg, options):
    from dealradar.rank import daily_spotlight
    cfg.front_page = options
    unknown = daily_deal(pct_at_checkout=None, regular_price=None, abs_savings=None,
                         first_seen_at="2026-10-09T13:00:00Z")
    lower = daily_deal("sidedeal", pct_at_checkout=70)
    best = daily_deal("morningsave", pct_at_checkout=85)
    assert daily_spotlight([unknown, lower, best], cfg, NOW)[0]["dedupe_key"] == "morningsave"
    tie = daily_deal("meh", pct_at_checkout=85, first_seen_at="2026-10-09T13:00:00Z")
    assert daily_spotlight([best, tie], cfg, NOW)[0]["dedupe_key"] == "meh"
    cfg.front_page = dict(options, spotlight=dict(options["spotlight"], max_cards=2))
    assert [d["dedupe_key"] for d in daily_spotlight([unknown, best, tie], cfg, NOW)] == ["meh", "morningsave"]
    cfg.front_page = dict(options, spotlight={"sources": ["sidedeal"], "max_cards": 1})
    assert daily_spotlight([tie], cfg, NOW) == []
    cfg.front_page = dict(options, spotlight=dict(options["spotlight"], max_cards=0))
    assert daily_spotlight([tie], cfg, NOW) == []


def test_spotlight_price_only_exception_is_narrow(cfg, options):
    from dealradar.rank import daily_spotlight, is_publishable
    cfg.front_page = options
    deal = daily_deal(pct_at_checkout=None, regular_price=None, abs_savings=None)
    assert daily_spotlight([deal], cfg, NOW)
    assert not is_publishable(deal, cfg)[0]  # Alerts/social never opt in.
    for changes in ({"abs_savings": 4.99}, {"conditions": ["used"]},
                    {"regular_price": 50}, {"flags": ["unbacked_price"]}):
        assert daily_spotlight([dict(deal, **changes)], cfg, NOW) == []
    assert not is_publishable(daily_deal("hip2save", pct_at_checkout=None,
                                         regular_price=None, abs_savings=None), cfg,
                              allow_spotlight_price=True)[0]
    # An old daily source that no longer supports the active price cannot win.
    stale = daily_deal()
    stale["sightings"][0]["final_price"] = 20
    assert daily_spotlight([stale], cfg, NOW) == []
    # Keepa's critic requirement also applies when a daily site credits it.
    deal["sightings"].append({"source_name": "keepa_amazon_drops"})
    assert daily_spotlight([deal], cfg, NOW) == []
    deal["critic"] = {"plausible": True}
    assert daily_spotlight([deal], cfg, NOW)


@pytest.mark.parametrize("seen,end,hours", [
    ("2026-03-08T06:00:00Z", "2026-03-09T05:00:00Z", 23),
    ("2026-11-01T05:00:00Z", "2026-11-02T06:00:00Z", 25),
    ("2026-10-09T01:00:00Z", "2026-10-09T05:00:00Z", 4),
])
def test_meh_end_is_central_midnight_across_dst(seen, end, hours):
    from dealradar.fetch.meh import deal_end
    from dealradar.util import parse_iso
    stamp = parse_iso(seen)
    assert deal_end(stamp) == parse_iso(end)
    assert (deal_end(stamp) - stamp).total_seconds() == hours * 3600


def test_spotlight_end_unknown_or_explicit_and_day_boundary(cfg, options):
    from datetime import timedelta
    from dealradar.rank import daily_spotlight
    from dealradar.util import parse_iso
    cfg.front_page = options
    meh = daily_spotlight([daily_deal()], cfg, NOW)[0]
    assert parse_iso(meh["spotlight"]["ends_at"]) == parse_iso("2026-10-10T05:00:00Z")
    assert daily_spotlight([daily_deal()], cfg, parse_iso("2026-10-10T05:00:00Z")) == []
    for source in ("sidedeal", "morningsave"):
        assert daily_spotlight([daily_deal(source)], cfg, NOW)[0]["spotlight"]["ends_at"] == ""
        deal = daily_deal(source, expires_at="2026-10-09T19:12:00Z")
        info = daily_spotlight([deal], cfg, NOW)[0]["spotlight"]
        assert seo.spotlight_time(info["ends_at"], NOW) == "ends in 5h 12m"
        assert seo.spotlight_time(info["ends_at"], NOW + timedelta(hours=6)) == ""


@pytest.mark.parametrize("unknown", [False, True])
def test_static_spotlight_first_unique_and_price_only(tmp_path, cfg, db, options, unknown):
    cfg.front_page = options
    cfg.paywall_open_access = True
    changes = {"pct_at_checkout": None, "regular_price": None, "abs_savings": None} if unknown else {}
    deal = daily_deal(item="TODAY OFFER", **changes)
    render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=[pick_deal(item="GRID OFFER"), deal])
    doc = json.loads((tmp_path / "site/deals.json").read_text())
    spot = doc["spotlight"][0]
    home = (tmp_path / "site/index.html").read_text()
    wall = home.split('<div id="content" data-clarity-unmask="true">')[1].split('<div id="paywall"')[0]
    assert wall.index("TODAY OFFER") < wall.index("GRID OFFER")
    assert len(re.findall(r'<h3 class="title">.*?TODAY OFFER', wall)) == 1
    assert 'role="region" aria-label="Today only deal"' in wall
    assert "Today only · Meh" in wall and "ends in 15h 0m" in wall
    assert 'src="https://img.example/photo.jpg"' in wall
    if unknown:
        assert all(d["dedupe_key"] != "meh" for d in doc["deals"])
        card = seo.card_html(dict(spot, regular_price=500, abs_savings=490), now=NOW)
        assert 'class="now">$10.00' in card
        assert not re.search(r'%|save \$|<s>', card)
        for path in (tmp_path / "site").rglob("*.html"):
            if path.name != "index.html" or path.parent != tmp_path / "site":
                assert "TODAY OFFER" not in path.read_text()
    else:
        assert doc["deals"][1]["dedupe_key"] == "meh"
        assert 'class="flag">-80%' in wall


def test_price_only_verified_ingest_publish_isolated_and_idempotent(tmp_path, cfg, db, options):
    from dealradar.dedupe import upsert_extracted
    from dealradar.models import Evidence, ExtractedDeal, RawPost
    from dealradar.parse.extractor import verify_evidence
    from dealradar.rank import published_deals, site_deals, spotlight_sources
    from dealradar.savings import compute_savings
    cfg.front_page = options
    cfg.paywall_open_access = True
    post = RawPost("meh", "today", "https://meh.com/deals/today", "Today's gadget",
                   "Price: $19.00", NOW, NOW, image_url="https://img.example/today.jpg")
    extracted = ExtractedDeal("Today's gadget", "Meh", post.url, "meh", final_price=19,
                              online_only="true", evidence=Evidence(final_price_quote="Price: $19.00"))
    assert verify_evidence(extracted, post.text)[0]
    assert not verify_evidence(extracted, "No price here")[0]
    assert not compute_savings(extracted).computable  # Default math unchanged.
    result = compute_savings(extracted, allow_price_only=post.source_name in spotlight_sources(cfg))
    assert result.final_price == 19
    assert result.regular_price is result.pct_at_checkout is result.abs_savings is None
    first = upsert_extracted(db, extracted, result, post, cfg, NOW)
    second = upsert_extracted(db, extracted, result, post, cfg, NOW)
    assert first["is_new_deal"] and not second["is_new_deal"]
    assert len(db.sightings_for(first["deal_id"])) == 1
    assert published_deals(db, cfg, NOW) == site_deals(db, cfg, NOW) == []
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out)
    doc = json.loads((out / "deals.json").read_text())
    assert doc["deals"] == [] and doc["spotlight"][0]["final_price"] == 19
    assert not list((out / "deal").glob("*/index.html"))
    cfg.front_page = {}
    render_site(db, cfg, NOW, out_dir=out)
    assert "spotlight" not in json.loads((out / "deals.json").read_text())
    # An ordinary source cannot publish a price-only row even via explicit input.
    render_site(db, cfg, NOW, out_dir=out, deals=[daily_deal("hip2save", pct_at_checkout=None,
                  regular_price=None, abs_savings=None)])
    assert json.loads((out / "deals.json").read_text())["deals"] == []


def test_no_spotlight_home_unchanged_and_phone_accessibility(tmp_path, cfg, db, options):
    cfg.front_page = options
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=[pick_deal()])
    before = (out / "index.html").read_bytes()
    assert "spotlight" not in json.loads((out / "deals.json").read_text())
    cfg.front_page = {k: v for k, v in options.items() if k != "spotlight"}
    render_site(db, cfg, NOW, out_dir=out, deals=[pick_deal()])
    assert (out / "index.html").read_bytes() == before
    template = (ROOT / "dealradar/publish/templates/index.html").read_text()
    assert re.search(r'@media \(max-width:640px\)\{[^@]*\.spotlight-grid \.spotlight\{grid-column:span 2\}', template)
    assert re.search(r'@media \(prefers-reduced-motion: reduce\)\{[^@]*\.card\.spotlight,\.card\.spotlight \*\{animation:none;transition:none\}', template)
    assert '.card.spotlight:focus-within{outline:2px solid var(--accent)' in template


@pytest.mark.parametrize("unknown", [False, True])
def test_spotlight_browser_render_filters_expiry_and_rotation(tmp_path, cfg, db, options, unknown):
    cfg.front_page = options
    cfg.paywall_open_access = True
    changes = {"pct_at_checkout": None, "regular_price": None, "abs_savings": None} if unknown else {}
    deals = [daily_deal(item="TODAY OFFER", **changes)] + [
        pick_deal(dedupe_key=f"grid-{i}", item=f"Grid offer {i}", brand=f"Brand{i}") for i in range(30)]
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=deals)
    result = subprocess.run(["node", str(ROOT / "tests/spotlight_logic.cjs"),
                             str(out / "index.html"), str(out / "deals.json")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_spotlight_preserves_closed_paywall_rotation(tmp_path, cfg, db, options):
    source = [pick_deal(dedupe_key=f"grid-{i}", store=f"Store {i}") for i in range(6)]
    source += [daily_deal(), daily_deal("morningsave", pct_at_checkout=None,
                                       regular_price=None, abs_savings=None)]
    rotations = []
    for setting in (options, {k: v for k, v in options.items() if k != "spotlight"}):
        cfg.front_page = setting
        db.meta_set("teaser_recent", "[]")
        drops = []
        for _ in range(4):
            render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=source)
            doc = json.loads((tmp_path / "site/deals.json").read_text())
            free = {d["key"] for d in doc["deals"] if not d.get("locked")}
            assert len(free) == cfg.paywall_teaser_count
            assert all(d["pct_at_checkout"] is None or d["key"] in free for d in doc.get("spotlight", []))
            memory = json.loads(db.meta_get("teaser_recent"))
            assert not any(d["key"] in memory for d in doc.get("spotlight", []) if d["pct_at_checkout"] is None)
            drops.append(free)
        rotations.append(drops)
    assert rotations[0] == rotations[1]


def test_price_only_spotlight_shipping_and_other_math_gates():
    from dealradar.models import ExtractedDeal
    from dealradar.savings import compute_savings
    raw = dict(item="Gadget", store="Meh", source_url="https://meh.com/today", source_name="meh", final_price=19)
    result = compute_savings(ExtractedDeal(**raw, shipping_cost_or_threshold="$5 shipping"), allow_price_only=True)
    assert result.final_price == 24 and result.pct_at_checkout is result.abs_savings is None
    for changes in ({"currency": "EUR"}, {"conditions": ["in_store_only"]},
                    {"regular_price_unit": "pack", "final_price_unit": "per_unit"},
                    {"final_price": -1}, {"regular_price": 0}, {"price_is_range": True}):
        assert not compute_savings(ExtractedDeal(**{**raw, **changes}), allow_price_only=True).computable
    known = ExtractedDeal(**raw, regular_price=100)
    assert compute_savings(known, allow_price_only=True) == compute_savings(known)


def test_price_only_does_not_retain_an_old_discount_page(tmp_path, cfg, db, options):
    cfg.front_page = options
    cfg.paywall_open_access = True
    deal = daily_deal()
    db.insert_deal(dedupe_key="meh", store="Meh", item=deal["item"], normalized_item="gadget",
                   canonical_url=deal["canonical_url"], payload=deal, final_price=10,
                   pct_at_checkout=80, abs_savings=40, online_only="true", expires_at="", now=NOW)
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out)
    old_pages = list((out / "deal").glob("*/index.html"))
    assert old_pages
    db.update_deal("meh", payload=dict(deal, regular_price=None, death="confirmed"),
                   final_price=10, pct_at_checkout=None, abs_savings=None,
                   status="dead", replace_prices=True)
    render_site(db, cfg, NOW, out_dir=out)
    assert not any(p.exists() for p in old_pages)


def test_spotlight_is_a_full_width_banner_on_wider_screens():
    # A lone spotlight card left the rest of its desktop row empty
    # (review 2026-10-13); phones keep the stacked, full-row card.
    home = (ROOT / "dealradar/publish/templates/index.html").read_text()
    banner = home[home.index("@media (min-width:641px){\n  .spotlight-grid"):]
    banner = banner[:banner.index("\n}\n")]
    assert ".spotlight-grid{grid-template-columns:minmax(0,1fr)}" in banner
    assert ".spotlight-grid .card.spotlight{flex-direction:row" in banner
    assert ".spotlight-grid .spotlight{grid-column:span 2}" in home


def test_stale_non_amazon_price_is_no_top_pick(cfg, options):
    """Live (2026-10-14): a $7 Walmart price posted four days earlier led
    Trending. Keepa can't re-check other stores, so their price must have been
    restated or re-checked within non_amazon_max_age_h to be a top pick."""
    cfg.front_page = options
    limit = options["top_picks"]["non_amazon_max_age_h"]
    assert limit == 8  # operator 2026-10-17: Woot coat ended after 9 h (was 24)
    stale = pick_deal(store="Walmart", canonical_url="https://www.walmart.com/ip/1",
                      price_published_at="2026-10-05T14:00:00Z", price_rechecked_at="")
    assert pick_score(stale, cfg, NOW) is None
    assert pick_score(dict(stale, price_rechecked_at="2026-10-09T13:00:00Z"), cfg, NOW) is not None
    assert pick_score(dict(stale, price_published_at="2026-10-09T07:00:00Z"), cfg, NOW) is not None
    assert pick_score(dict(stale, price_published_at="2026-10-09T02:00:00Z"), cfg, NOW) is None  # 12 h
    # Internal recovery scoring (no confirmation required) is unaffected.
    assert pick_score(stale, cfg, NOW, require_confirmation=False) is not None
