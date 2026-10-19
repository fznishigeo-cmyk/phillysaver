"""Operator 2026-10-17 ("is all clothes where are the tech gadgets"): one brand's
80%-off team sweatshirts filled the first screen. Clothing is spaced in the
first 24 picks and a missing brand is read from the title."""
import copy
from contextlib import closing

import pytest

from dealradar import rank
from dealradar.config import load_config

SHIPPED = load_config()


def picks_options(**over):
    options = copy.deepcopy(SHIPPED.front_page["top_picks"])
    options.update(over)
    return options


def deal(n, item, category, brand=""):
    return {"dedupe_key": f"k{n}", "item": item, "category": category, "brand": brand,
            "store": "Amazon"}


def flood():
    teams = ["Winnipeg Jets", "Chicago Bulls", "San Antonio Spurs", "Utah Jazz", "Indiana Pacers",
             "Portland Trail Blazers", "Seattle Kraken", "Boston Celtics"]
    # Live: the team shirts alternated fashion/sports categories, so the
    # category run rule never stopped them; the title words do.
    shirts = [deal(i, f"Blue 84 {t} Officially Licensed Crewneck Sweatshirt",
                   "fashion" if i % 2 else "sports_outdoors") for i, t in enumerate(teams)]
    shirts += [deal(10 + i, f"Champion Women's Sports Bra Style {i}", "fashion", "Champion")
               for i in range(6)]
    gadgets = [deal(30 + i, name, "electronics") for i, name in enumerate(
        ["Anker Power Bank 10000mAh", "JBL Go 4 Bluetooth Speaker", "Phomemo Label Maker",
         "Soundcore Earbuds", "Govee LED Strip Lights", "Amazon Fire TV Stick",
         "Kasa Smart Plug 4-Pack", "Wyze Cam v4 Security Camera"])]
    home = [deal(50 + i, name, "home") for i, name in enumerate(
        ["Utopia Bath Towels Set", "Bedsure Fleece Blanket", "Yankee Candle Large Jar",
         "Sterilite Storage Bins 6-Pack"])]
    kitchen = [deal(70 + i, name, "kitchen") for i, name in enumerate(
        ["Ninja Air Fryer", "Owala FreeSip Bottle", "Lodge Cast Iron Skillet", "OXO Salad Spinner",
         "Rubbermaid Food Storage Set", "Keurig K-Mini Coffee Maker", "Cuisinart Knife Set",
         "Stanley Quencher Tumbler"])]
    return shirts + gadgets + home + kitchen


def scored(rows):
    # Clothing scores highest, as in the live flood.
    return [(d, 100.0 - i) for i, d in enumerate(rows)]


def front_page(picks):
    options = copy.deepcopy(SHIPPED.front_page)
    options["top_picks"] = picks
    options["max_same_category_run"] = 99  # isolate the mix rule from the run rule
    options.get("checkout_price_deals", {})["enabled"] = False
    options.get("popular_affordable", {})["enabled"] = False
    return options


def clothing(d):
    return "Sweatshirt" in d["item"] or "Bra" in d["item"]


def test_first_screens_hold_at_most_two_clothing_per_six_and_nothing_is_dropped():
    rows = flood()
    off = rank.top_picks(scored(rows), front_page(picks_options(
        max_clothing_per_window=0, brand_from_title=False, max_same_brand=99)))
    on = rank.top_picks(scored(rows), front_page(picks_options(brand_from_title=False, max_same_brand=99)))
    assert sum(map(clothing, off[:6])) == 6
    for start in range(0, 24, 6):
        assert sum(map(clothing, on[start:start + 6])) <= 2
    assert sorted(d["dedupe_key"] for d in on) == sorted(d["dedupe_key"] for d in off)
    shirts = [d["dedupe_key"] for d in on if clothing(d)]
    assert shirts == [d["dedupe_key"] for d in off if clothing(d)]  # their own order kept
    assert any(d["category"] == "electronics" for d in on[:6])


def test_missing_brand_is_read_from_the_title():
    rows = flood()
    on = rank.top_picks(scored(rows), front_page(picks_options(max_clothing_per_window=0)))
    assert sum(d["item"].startswith("Blue 84") for d in on) == 2
    assert sum(d["brand"] == "Champion" for d in on) == 2
    off = rank.top_picks(scored(rows), front_page(picks_options(
        max_clothing_per_window=0, brand_from_title=False)))
    assert sum(d["item"].startswith("Blue 84") for d in off) == len(
        [d for d in rows if d["item"].startswith("Blue 84")])


def test_title_brand_skips_leading_numbers():
    picks = {"brand_from_title": True}
    assert rank.title_brand({"item": "Blue 84 Utah Jazz Hoodie"}, picks) == "blue 84"
    assert rank.title_brand({"item": "2-Pack Kooper Flood Lights"}, picks) == ""
    assert rank.title_brand({"item": "Blue 84 Utah Jazz Hoodie"}, {}) == ""


def test_shipped_mix_knobs():
    picks = SHIPPED.front_page["top_picks"]
    assert picks["max_clothing_per_window"] == 2 and picks["mix_window"] == 6
    assert picks["mix_slots"] == 24 and picks["brand_from_title"] and picks["max_same_brand"] == 2


def test_deferred_clothing_reenters_before_lower_scores_with_caps_checked_on_insertion():
    rows = [deal(i, f"Shirt {i} Style", "fashion", f"Brand{i}") for i in range(12)]
    rows += [deal(20 + i, f"Gadget {i} Model", ["electronics", "home"][i % 2], f"Tech{i}") for i in range(30)]
    options = front_page(picks_options(max_same_brand=99, max_same_category=99,
                                      max_title_twins=99, max_same_store=99))
    picked = rank.top_picks(scored(rows), options)
    assert picked[:2] == rows[:2]
    assert picked[6:8] == rows[2:4]
    assert picked[12:14] == rows[4:6]
    assert picked[18:20] == rows[6:8]
    assert picked[24:28] == rows[8:12]
    assert len(picked) == len(rows)
    # A deferred offer still competes against every other cap when re-offered.
    options['top_picks']['max_same_category'] = 3
    limited = rank.top_picks(scored(rows), options)
    assert sum(d['category'] == 'fashion' for d in limited) == 3
    assert limited[6] == rows[2]


@pytest.mark.parametrize('popular', [False, True])
def test_clothing_selection_preserves_checkout_spacing_and_category_runs(popular):
    from dealradar.config import AppConfig
    from tests.test_checkout_price_deals import enable, offer
    from tests.test_price_accuracy_engine import NOW

    cfg = AppConfig()
    cfg.front_page = front_page(picks_options(max_same_brand=99, max_same_category=99,
                                             max_title_twins=99, max_same_store=99))
    cfg.front_page['max_same_category_run'] = 2
    cfg.front_page['popular_affordable']['enabled'] = popular
    enable(cfg)
    rows = [offer(i, codes=['SAVE'] if i in (0, 5) else []) for i in range(36)]
    # Before the old post-pass: promos at 0/5; shirt overflow at 4.
    for i, row in enumerate(rows):
        row['item'] = f"Unique{i} Model{i} Product"
        row['category'] = ['home', 'kitchen'][i % 2]
        if i in (1, 3, 4):
            row['item'] += ' Shirt'
    picked = rank.top_picks(scored(rows), cfg.front_page, NOW, cfg=cfg)
    assert rows[5] not in picked  # Can't advance a validated promo into slot four.
    for i in range(len(picked)):
        assert sum(bool(d['codes']) for d in picked[i:i + 5]) <= 1
    assert picked.index(rows[4]) == 6  # Deferred clothing is re-offered first.
    assert all(sum(rank.is_clothing(d, cfg.front_page['top_picks']) for d in picked[i:i + 6]) <= 2
               for i in range(0, 24, 6))
    assert all(len({d['category'] for d in picked[i:i + 3]}) > 1 for i in range(6))


def assert_final_caps(rows, options):
    picks = options['top_picks']
    for start in range(0, picks['mix_slots'], picks['mix_window']):
        assert sum(bool(d.get('cl')) for d in rows[start:start + picks['mix_window']]) <= picks['max_clothing_per_window']
    checkout = options['checkout_price_deals']
    for start in range(len(rows)):
        assert sum(bool(d.get('pick_checkout')) for d in rows[start:start + checkout['top_pick_window']]) <= checkout['top_pick_max_in_window']
    for start in range(min(6, len(rows) - 2)):
        assert len({d['category'] for d in rows[start:start + 3]}) > 1
    from dealradar.appeal import allure_spacing
    first, every = allure_spacing(options.get('appeal') or {})  # configured since task 282
    last_allure = -every
    for i, d in enumerate(rows):
        if d.get('allure'):
            assert i >= first and i - last_allure >= every
            last_allure = i


def browser_rows(concentrated=False):
    clothing_positions = {i for i in range(72) if i % 6 < 2}
    if concentrated:
        clothing_positions -= set(range(24, 48))
        clothing_positions |= set(range(24, 30)) | {36, 37}
    return [dict(key=str(i), dedupe_key=str(i), pick=i + 1,
                 item=f'Unique{i} Model{i} ' + ('Shirt' if i in clothing_positions else 'Gadget'),
                 category='home' if i % 2 else 'kitchen', store=f'store{i % 5}',
                 status='alive', image_url='photo', final_price=20,
                 first_seen_at='2026-10-09T12:00:00Z', pick_checkout=i % 5 == 0,
                 **({'cl': 1} if i in clothing_positions else {})) for i in range(72)]


@pytest.mark.parametrize('concentrated', [False, True])
@pytest.mark.parametrize('windows,checkout', [(3, True), (0, True), (3, False), (0, False)])
@pytest.mark.parametrize('shuffle', [False, True])
def test_real_emitted_rotation_and_arrivals_keep_clothing_slots(tmp_path, concentrated, windows, checkout, shuffle):
    from dealradar.db import DB
    from tests.test_front_page import browser

    cfg = copy.deepcopy(SHIPPED)
    cfg.drop_shuffle = shuffle
    cfg.drop_fresh = False  # These assertions cover the legacy full-wall rotation.
    cfg.tracking = {}
    cfg.strip_comments = True
    cfg.drop_rotation_windows = windows
    cfg.front_page['checkout_price_deals']['enabled'] = checkout
    cfg.front_page['appeal']['want']['enabled'] = False
    rows = browser_rows(concentrated)
    public_options = copy.deepcopy(cfg.front_page)
    if not checkout:
        public_options.pop('checkout_price_deals')  # Publisher omits the disabled section.
    arrivals = [{}, {'24': True, '25': True, '26': True, '31': True}, {'7': True, '48': True}]
    cases = [dict(rows=rows, options=public_options,
                  state=dict(sortKey='top', seed=seed, newKeys=fresh))
             for fresh in arrivals for seed in (-1, 0, 1, 2, 3, 2147483648)]
    # Dense checkout inventories may need the validated server-order fallback.
    # Also require an actual rotation when class-compatible pages are feasible.
    cases.append(dict(rows=[dict(d, pick_checkout=False) for d in rows], options=public_options,
                      state=dict(sortKey='top', seed=1, newKeys={})))
    with closing(DB(':memory:')) as db:
        results = browser(tmp_path, cfg, db, cases)
    for case, result in zip(cases, results):
        by_key = {d['key']: d for d in case['rows']}
        ordered = [by_key[k] for k in result['ordered']]
        if not shuffle and windows == 0:
            assert result['ordered'] == [d['key'] for d in rows]
        elif not shuffle and case['state']['seed'] == 1 and not any(d['pick_checkout'] for d in case['rows']):
            assert set(result['ordered'][:24]) == {d['key'] for d in rows[24:48]}
        assert set(result['ordered']) == set(by_key)
        if not shuffle:
            assert result['rendered'] == result['ordered']
        else:
            assert set(result['rendered']) == set(by_key)
            ordered = [by_key[k] for k in result['rendered']]
        assert [bool(d.get('cl')) for d in ordered] == [bool(d.get('cl')) for d in rows]
        assert_final_caps(ordered, cfg.front_page)
    if shuffle:
        heads = [result['rendered'][:15] for result in results[:5]]
        assert all(a != b for a, b in zip(heads, heads[1:])), 'every successive mark changes the first 15'


@pytest.mark.parametrize('sparse', [False, True])
@pytest.mark.parametrize('windows,checkout', [(3, True), (0, True), (3, False), (0, False)])
@pytest.mark.parametrize('shuffle', [False, True])
def test_final_static_and_emitted_allure_repair_preserve_mix_and_checkout(tmp_path, sparse, windows, checkout, shuffle):
    from dealradar.db import DB
    from dealradar.publish import seo
    from tests.test_front_page import browser

    cfg = copy.deepcopy(SHIPPED)
    cfg.drop_shuffle = shuffle
    cfg.drop_fresh = False  # Fresh-page mix and static parity have their own tests.
    cfg.tracking = {}
    cfg.drop_rotation_windows = windows
    cfg.front_page['checkout_price_deals']['enabled'] = checkout
    rows = browser_rows()
    for i, row in enumerate(rows):
        row['allure'] = i in ({0, 24, 71} if not sparse else set(range(19)))
    # The final static order must validate the same policies after Allure moves.
    static = seo.home_order(rows, cfg.front_page, click_price=True)
    assert_final_caps(static, cfg.front_page)
    public_options = copy.deepcopy(cfg.front_page)
    if not checkout:
        public_options.pop('checkout_price_deals')
    cases = [dict(rows=rows, options=public_options,
                  state=dict(sortKey='top', seed=seed, newKeys={'24': True, '30': True}))
             for seed in range(4)]
    with closing(DB(':memory:')) as db:
        results = browser(tmp_path, cfg, db, cases)
    by_key = {d['key']: d for d in rows}
    for result in results:
        assert_final_caps([by_key[k] for k in result['rendered']], cfg.front_page)
    # Seed zero has no arrivals in the static mirror.
    with closing(DB(':memory:')) as db:
        baseline, = browser(tmp_path, cfg, db, [dict(rows=rows, options=public_options,
            state=dict(sortKey='top', seed=0))])
    assert baseline['rendered'] == [d['key'] for d in static]


def test_public_rotation_class_is_only_on_clothing_picks_and_absent_when_off(tmp_path, monkeypatch):
    from dealradar.db import DB
    from dealradar.publish import site
    from tests.test_publish_site import NOW, make_deal

    cfg = copy.deepcopy(SHIPPED)
    cfg.drop_fresh = False  # Fresh rotation also labels eligible rows below the picks.
    cfg.tracking = {}
    cfg.keepa_click_price_rule = 'off'
    cfg.front_page['popular_affordable']['enabled'] = False
    cfg.front_page['top_picks'].update(max_same_store=120, max_same_category=120,
                                      max_same_brand=120, max_title_twins=120)
    monkeypatch.setattr(site, 'util_now', lambda: NOW)
    monkeypatch.setattr(site, 'pick_score', lambda d, *a, **k: 200 - int(d['dedupe_key']))
    rows = [make_deal(dedupe_key=str(i), item=f'Unique{i} Model{i} ' + ('Shirt' if i % 3 == 0 else 'Gadget'),
                      brand=f'Brand{i}', category='home' if i % 2 else 'kitchen', codes=[],
                      first_seen_at=NOW.isoformat(), image_url='https://images.example.invalid/card.png')
            for i in range(96)]
    with closing(DB(':memory:')) as db:
        result = site.render_site(db, cfg, NOW, out_dir=tmp_path / 'on', deals=rows)
        for d in result['public_deals']:
            assert ('cl' in d) == (bool(d.get('pick')) and rank.is_clothing(d, cfg.front_page['top_picks']))
            if 'cl' in d:
                assert d['cl'] == 1
        assert sum('cl' in d for d in result['public_deals']) > 0
        cfg.front_page['top_picks']['max_clothing_per_window'] = 0
        result = site.render_site(db, cfg, NOW, out_dir=tmp_path / 'off', deals=rows)
        assert all('cl' not in d for d in result['public_deals'])
