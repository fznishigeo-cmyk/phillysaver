"""Operator 2026-10-22/23: every 30 front cards hold about 7 sexy items women
wear for their partners, 7 beauty, 4 essentials, 4 kitchen and dining, 3
electronics and 5 of the rest (Trending drops and Best deals today). Adult
titles never join a bucket; every existing spacing rule wins over the mix."""
import copy
import json
import re
from collections import Counter

import pytest

from dealradar import front_mix
from dealradar.config import load_config
from dealradar.publish import drop_fresh, site
from tests.test_search_amazon import run_node
from tests.test_support import NOW, render

SHIPPED = load_config()
TARGET = {"sexy_for_her": 7, "beauty": 7, "essentials": 4, "kitchen_dining": 4, "electronics": 3, "rest": 5}


def bucket(item, category="fashion"):
    return front_mix.bucket({"item": item, "category": category}, SHIPPED.front_page)


def test_shipped_shares_in_tie_order():
    assert list(front_mix.shares(SHIPPED.front_page).items()) == list(TARGET.items())
    assert front_mix.shares({}) is None and bucket("Lace bralette", "fashion") == "sexy_for_her"


@pytest.mark.parametrize("item,category,expected", [
    ("Women's Lace Lingerie Set Babydoll", "fashion", "sexy_for_her"),
    ("Satin Pajama Set for Women", "fashion", "sexy_for_her"),
    ("Lace Teddy Bodysuit", "fashion", "sexy_for_her"),
    ("Sexy Chemise Nightgown", "fashion", "sexy_for_her"),
    # Ties: sexy before beauty, words before categories.
    ("Lingerie gift box with perfume", "beauty", "sexy_for_her"),
    ("Hydrating face serum", "beauty", "beauty"),
    # Men's items are never in the women's bucket; they fall to their own category or the rest.
    ("Calvin Klein Men's Cologne 3.4 oz", "beauty", "beauty"),
    ("Men's Silk Robe", "fashion", None),
    ("Men's Boxer Briefs 5 Pack", "fashion", None),
    ("Swim Trunks Quick Dry", "fashion", None),
    # Bare teddy/slip never read as lingerie.
    ("Plush Teddy Bear", "toys_games", None),
    ("Non-Slip Bath Mat", "home", None),
    ("Portable Air Conditioner", "home", None),
    # Kitchen versus essentials: staples are essentials even in the kitchen category.
    ("Dish Soap 3 Pack", "kitchen", "essentials"),
    ("Ziploc Freezer Bags 60 Count", "kitchen", "essentials"),
    ("Air Fryer 6 Quart", "kitchen", "kitchen_dining"),
    ("Dinnerware Set 16 Piece", "home", "kitchen_dining"),
    ("Garlic press", "kitchen", "kitchen_dining"),
    ("Ground Coffee 2 lb", "grocery", "essentials"),
    ("Bluetooth Speaker Waterproof", "home", "electronics"),
    ("USB C Hub", "computers", "electronics"),
    ("Orthopedic Dog Bed", "pets", None),
])
def test_classifier(item, category, expected):
    assert bucket(item, category) == expected


@pytest.mark.parametrize("item", ["Rose Toy Vibrator for Women", "Adult Couples Toy Lingerie",
                                  "Personal Massager Wand", "Sexy Lingerie Sex Toy Set"])
def test_adult_titles_never_join_or_lift(item):
    from tests.test_publish_site import make_deal
    for category in ("beauty", "fashion", "electronics", "kitchen", "health"):
        assert bucket(item, category) is None
    cfg = load_config()
    cfg.keepa_click_price_rule = "off"
    row = site._serialize_deal(make_deal(item=item, category="beauty", conditions=[], codes=[],
                                         price_rechecked_at=NOW.isoformat()), NOW, cfg=cfg)
    assert "mx" not in row and row["fresh_blocked"] is True
    assert drop_fresh.rotation([row], cfg.front_page) == []
    assert front_mix.lead([dict(row, pick=1)], cfg.front_page)[0].get("mx") is None


def mix_options(**changes):
    mix = copy.deepcopy(SHIPPED.raw["publish"]["front_page"]["mix"])
    mix.update(changes)
    return mix


@pytest.mark.parametrize("change,message", [
    (dict(enabled="yes"), "mix.enabled must be a boolean"),
    (dict(per=29), "shares must add up to per"),
    (dict(per=0), "per must be a positive integer"),
    (dict(extra=1), "supported settings only"),
    (dict(buckets={"beauty": {"share": 30, "categories": ["beauty"]}}), "includes rest"),
    (dict(buckets={"rest": {"share": 25, "words": ["x"]}, "beauty": {"share": 5, "categories": ["beauty"]}}),
     "rest takes a share only"),
    (dict(buckets={"rest": {"share": 25}, "beauty": {"share": 5}}), "needs words, groups or categories"),
    (dict(buckets={"rest": {"share": 25}, "beauty": {"share": 5, "groups": ["nope"]}}), "want groups"),
    (dict(buckets={"rest": {"share": 30}, "beauty": {"share": 0, "categories": ["beauty"]}}), "share must be"),
    (dict(buckets={"rest": {"share": 25}, "beauty": {"share": 5.0, "categories": ["beauty"]}}), "share must be"),
    (dict(buckets={"rest": {"share": 25}, "beauty": {"share": 5, "words": ["lip*"]}}), "literal words"),
    (dict(buckets={"rest": {"share": 25}, "beauty": {"share": 5, "categories": "beauty"}}), "category names"),
])
def test_strict_config(tmp_path, change, message):
    front_page = {"appeal": SHIPPED.raw["publish"]["front_page"]["appeal"], "mix": mix_options(**change)}
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"publish": {"front_page": front_page}}))
    with pytest.raises(ValueError, match=re.escape(message)):
        load_config(path)


def test_absent_or_off_changes_nothing():
    rows = [dict(key=f"k{i}", pick=i, mx="beauty" if i % 2 else None) for i in range(40)]
    for options in ({}, {"mix": mix_options(enabled=False)}):
        assert front_mix.shares(options) is None and front_mix.lead(rows, options) is rows
        assert front_mix.bucket({"item": "Lace bralette"}, options) is None


def test_schedule_is_exact_and_smooth():
    order = front_mix.schedule(TARGET, 90, dict.fromkeys(TARGET, 90))
    for start in range(0, 90, 30):
        assert Counter(order[start:start + 30]) == TARGET
    assert order[0] == "sexy_for_her" and order[1] == "beauty"  # ties keep the configured order
    assert all(a != b for a, b in zip(order, order[1:]))  # never two of a bucket in a row
    # Thin supply: a bucket yields its turns to the others by share; never a hole.
    thin = front_mix.schedule(TARGET, 30, dict(TARGET, sexy_for_her=2, rest=90))
    assert len(thin) == 30 and Counter(thin)["sexy_for_her"] == 2
    assert sorted(front_mix.schedule(TARGET, 30, dict.fromkeys(TARGET, 1))) == sorted(TARGET)


CATEGORIES = {"sexy_for_her": ["fashion"], "beauty": ["beauty"], "essentials": ["health", "grocery"],
              "kitchen_dining": ["kitchen"], "electronics": ["electronics", "computers"],
              "rest": ["home", "pets", "toys_games", "kids_baby"]}


def inventory(supply):
    """Ranked public rows by bucket; picks interleave by rank like the live wall."""
    rows = []
    for name, count in supply.items():
        for i in range(count):
            cats = CATEGORIES[name]
            rows.append(dict(key=f"{name}-{i}", item=f"{name} item {i}", category=cats[i % len(cats)],
                             store=["Amazon", "Walmart", "Target"][i % 3], final_price=12 + i % 30,
                             status="alive", image_url="https://image.test/photo", conditions=[],
                             allure=name == "sexy_for_her", cl=name == "rest" and i % 9 == 4,
                             pick_checkout=name != "sexy_for_her" and i % 11 == 6, earns=True,
                             **({} if name == "rest" else {"mx": name})))
    rows.sort(key=lambda d: (int(d["key"].rsplit("-", 1)[1]), d["key"]))
    for n, d in enumerate(rows[:120], 1):
        d["pick"] = n
    return rows


def fresh_pages(rows, marks):
    options = SHIPPED.front_page
    pool = drop_fresh.rotation(rows, options)
    return [front_mix.page(pool, mark, options, drop_fresh.rules(options)) for mark in marks]


def spacing_holds(page):
    assert len({d["key"] for d in page}) == len(page)
    assert not any(d["allure"] for d in page[:2])
    for i in range(len(page)):
        assert sum(d["allure"] for d in page[i:i + 4]) <= 1
        assert sum(d["pick_checkout"] for d in page[i:i + 5]) <= 1
        assert i + 3 > len(page) or len({d["category"] for d in page[i:i + 3]}) > 1
    for i in range(0, 24, 6):
        assert sum(d["cl"] for d in page[i:i + 6]) <= 2


AMPLE = dict(sexy_for_her=60, beauty=60, essentials=40, kitchen_dining=40, electronics=30, rest=300)


def test_every_drop_holds_the_mix_with_ample_supply():
    pages = fresh_pages(inventory(AMPLE), range(12))
    for page in pages:
        assert len(page) == 90
        assert Counter(front_mix.label(d, TARGET) for d in page[:30]) == TARGET
        # The allure spacing places the seven sexy cards: cards 3, 7, 11, ... 27.
        assert [i for i, d in enumerate(page[:30], 1) if d["allure"]] == [3, 7, 11, 15, 19, 23, 27]
        spacing_holds(page)
    # Rotation: consecutive drops lead with different cards.
    for before, after in zip(pages, pages[1:]):
        assert not {d["key"] for d in before[:30]} & {d["key"] for d in after[:30]}


def test_thin_supply_falls_back_without_holes():
    supply = dict(AMPLE, sexy_for_her=3, electronics=1)
    for page in fresh_pages(inventory(supply), range(6)):
        counts = Counter(front_mix.label(d, TARGET) for d in page[:30])
        assert len(page) == 90 and counts["sexy_for_her"] == 3 and counts["electronics"] == 1
        assert sum(counts.values()) == 30
        spacing_holds(page)


def test_tiny_inventory_shows_every_card_once():
    rows = inventory(dict(sexy_for_her=2, beauty=3, essentials=1, kitchen_dining=0, electronics=2, rest=4))
    for page in fresh_pages(rows, range(-2, 4)):
        assert sorted(d["key"] for d in page) == sorted(d["key"] for d in rows)


def test_best_deals_today_lead():
    rows = inventory(AMPLE)
    head = front_mix.lead(rows, SHIPPED.front_page)[:30]
    assert Counter(front_mix.label(d, TARGET) for d in head) == TARGET
    for name in TARGET:  # each bucket keeps its rank order
        ranks = [d["pick"] for d in head if front_mix.label(d, TARGET) == name]
        assert ranks == sorted(ranks)
    # Only picks fill the shares; a short bucket yields to the best other picks.
    thin = [d for d in rows if d.get("mx") != "sexy_for_her" or d.get("pick", 99) < 20]
    head = front_mix.lead(thin, SHIPPED.front_page)[:30]
    picked_sexy = [d for d in thin if d.get("mx") == "sexy_for_her" and "pick" in d]
    assert len(head) == 30 and all("pick" in d for d in head)
    assert sum(d.get("mx") == "sexy_for_her" for d in head) == len(picked_sexy) < 7
    few = [d if d.get("pick", 99) <= 10 else {k: v for k, v in d.items() if k != "pick"} for d in rows]
    assert front_mix.lead(few, SHIPPED.front_page)[10:] == [d for d in few if "pick" not in d]


def test_published_labels_rules_and_best_deals_today(tmp_path):
    from tests.test_publish_site import make_deal
    titles = [("Women's Lace Bralette", "fashion"), ("Matte Lipstick Set", "beauty"),
              ("Toilet Paper 24 Rolls", "home"), ("Air Fryer 6 Quart", "kitchen"),
              ("Bluetooth Speaker", "electronics"), ("Throw Blanket", "home")] * 6
    deals = [make_deal(dedupe_key=f"mix|{i}", item=f"{title} {i}", category=category, brand=f"B{i}",
                       codes=[], conditions=[], image_url=f"https://images.example.invalid/{i}.jpg",
                       first_seen_at=NOW.isoformat(), last_seen_at=NOW.isoformat(),
                       price_published_at=NOW.isoformat(), price_rechecked_at=NOW.isoformat(),
                       critic={"plausible": True, "confidence": .9, "reason": "Backed basis"})
             for i, (title, category) in enumerate(titles)]
    cfg = load_config()
    cfg.hide_deal_site_links = False
    out, doc, *_ = render(tmp_path, cfg, deals)
    labels = {d["item"]: d.get("mx") for d in doc["deals"]}
    assert labels["Women's Lace Bralette 0"] == "sexy_for_her" and labels["Throw Blanket 5"] is None
    assert "mix" not in doc["meta"]["front_page"]
    assert 'freshRules.buckets = {"sexy_for_her":7,"beauty":7,' in (out / "drop-fresh.js").read_text()
    best = (out / "best-deals-today" / "index.html").read_text()
    order = [labels[re.sub(r"<[^>]+>", "", t).replace("&#x27;", "'")]
             for t in re.findall(r'<h3 class="title">(.*?)</h3>', best)]
    assert len(order) == 30 and Counter(order[:6]) == Counter(
        ["sexy_for_her", "beauty", "essentials", "kitchen_dining", "electronics", None])


def test_mixed_fresh_page_stays_within_phone_budget(tmp_path):
    from scripts.publication_budget import analyze
    from tests.phone_budget_fixture import publish_fixture, visible_offer_count
    cfg = load_config()
    cfg.phone_budget["enabled"] = True
    expected = visible_offer_count(cfg)

    def inspect(db, out):
        report = analyze(out, cfg, expected_offers=expected)
        assert report["ok"], report
    publish_fixture(tmp_path, cfg, inspect=inspect)


def test_mix_never_worsens_the_worst_first_paint(tmp_path, monkeypatch):
    """The mix changes which cards are pre-rendered, not their markup: over 40
    drops of the phone-budget fixture its largest home page is no larger than
    the largest without it (2026-10-23: 77673 vs 77798 gzip bytes). One
    publish; the off side re-renders the same rows with pre_front_mix."""
    import gzip
    from dealradar.publish import seo
    from tests.history_pins import pre_front_mix
    from tests.phone_budget_fixture import publish_fixture
    cfg = load_config()
    calls, original = [], seo.static_wall
    monkeypatch.setattr(seo, "static_wall", lambda *a, **k: calls.append((a, k)) or original(*a, **k))
    homes = []
    publish_fixture(tmp_path, cfg, inspect=lambda db, out: homes.append((out / "index.html").read_text()))
    (args, kwargs), = calls
    wall = original(*args, **kwargs)
    assert homes[0].count(wall) == 1

    def worst(front_page):
        return max(len(gzip.compress(homes[0].replace(wall, original(
            *args[:4], front_page, *args[5:], **dict(kwargs, fresh_mark=mark))).encode(), 9, mtime=0))
            for mark in range(kwargs["fresh_mark"] - 20, kwargs["fresh_mark"] + 20))
    assert worst(cfg.front_page) <= worst(pre_front_mix(copy.deepcopy(cfg)).front_page)


def test_browser_drops_match_the_server(tmp_path):
    cfg = load_config()
    cfg.tracking = {}
    out, *_ = render(tmp_path, cfg)
    rows = inventory(dict(AMPLE, sexy_for_her=25, electronics=12))
    pool = drop_fresh.rotation(rows, cfg.front_page)
    data = tmp_path / "mix.json"
    data.write_text(json.dumps(dict(rows=rows, pool=[d["key"] for d in pool], pages=[
        [d["key"] for d in front_mix.page(pool, mark, cfg.front_page, drop_fresh.rules(cfg.front_page))]
        for mark in range(8)])))
    result = run_node("front_mix_logic.cjs", out / "index.html", out / "deals.json", data)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Front mix passed" in result.stdout
