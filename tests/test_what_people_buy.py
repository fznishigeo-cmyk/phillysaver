"""Task 273: offline title lifts, local-date seasons and factual scarcity."""

import copy
import json
import os
import re
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import yaml

from dealradar import appeal, dedupe, rank
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.models import Evidence, ExtractedDeal, RawPost, SavingsResult
from dealradar.parse.extractor import verify_evidence
from dealradar.publish import open_box, seo, site
from dealradar.util import iso
from scripts.publication_budget import analyze
from tests.phone_budget_fixture import visible_offer_count
from tests.phone_budget_fixture import publish_fixture
from tests.test_appeal import tree
from tests.test_front_page import pick_deal, browser
from tests.history_pins import pre_task_282

NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]
SHIPPED = load_config()
GROUPS = pre_task_282(copy.deepcopy(SHIPPED)).front_page["appeal"]["want"]["groups"]  # recorded before task 284


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task273 must remain offline")
    monkeypatch.setattr(httpx.Client, "send", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(site, "util_now", lambda: NOW)


def settings():
    cfg = pre_task_282(copy.deepcopy(SHIPPED))  # recorded before task 282
    cfg.front_page["popular_affordable"]["shadow"] = False
    cfg.front_page["top_picks"].pop("max_clothing_per_window", None)  # recorded before the first-screen mix
    return cfg


def deal(**over):
    result = pick_deal(**{"item": "Cotton socks", "final_price": 25, "first_seen_at": iso(NOW),
                       "price_rechecked_at": iso(NOW), "price_published_at": iso(NOW), **over})
    if result.get("asin"):
        result.setdefault("click_price_check", dict(asin=result["asin"], final=result["final_price"],
                                                   buybox=result["final_price"], at=iso(NOW)))
    return result


def observed_open_box(count=2, observed=NOW, cfg=None, statement=None, context="{quote}", **over):
    cfg = cfg or settings()
    d = deal(conditions=["open_box"], **over)
    statement = statement if statement is not None else f"only {count} left"
    quote = f'{d["item"]}: ${d["final_price"]}; {statement}'
    extracted = ExtractedDeal(item=d["item"], store=d["store"], source_name="source",
        source_url="https://source.example/offer", product_url=d["canonical_url"],
        conditions=["open_box"], final_price=d["final_price"],
        evidence=Evidence(final_price_quote=quote))
    post = RawPost(source_name="source", uid="offer", url=extracted.source_url,
        title=d["item"], text=context.format(quote=quote), published_at=observed, fetched_at=observed,
        parsed=[(extracted.item, extracted.product_url)])
    savings = SavingsResult(computable=True, final_price=d["final_price"])
    database = DB(":memory:")
    try:
        saved = dedupe.upsert_extracted(database, extracted, savings, post, cfg, observed)
        d.update(database.get_deal(saved["dedupe_key"]))
    finally:
        database.close()
    return d


def unlifted(d, cfg, now=NOW, **kwargs):
    baseline = copy.deepcopy(cfg)
    baseline.front_page["appeal"]["want"]["enabled"] = False
    baseline.front_page["appeal"]["season"] = []
    return rank.pick_score(d, baseline, now, **kwargs)


@pytest.mark.parametrize("group,word", [(g, w) for g, words in GROUPS.items() for w in words])
def test_every_shipped_group_word_is_literal_and_case_insensitive(group, word):
    cfg = settings()
    assert group in appeal.groups({"item": "Great " + word.upper() + " sale"}, cfg.front_page["appeal"])
    assert group not in appeal.groups({"item": "unrelated structural support"}, cfg.front_page["appeal"])


@pytest.mark.parametrize("word,title,expected", [
    ("bra", "brass fixtures", False), ("bra", "sports bras", True),
    ("watch", "watchband", False), ("watch", "watches", True),
    ("batteries", "AA battery", True), ("light bulbs", "LED light bulb", True),
    ("usb c cable", "2-pack USB-C cables", True), ("k cup", "K-Cups", True),
    ("dress", "Summer dresses", True), ("lip gloss", "Lip Gloss", True),
    ("pan", "panels", False), ("hat", "that charger", False),
    ("nail", "snail serum", False), ("dog treats", "dog treatment", False),
])
def test_word_boundaries_plural_and_phrase_separators(word, title, expected):
    assert appeal.matches(title, [word]) is expected


@pytest.mark.parametrize("title,group", [
    ("Cotton hoodie", "basics"), ("Cotton hoodies", "basics"),
    ("Cotton HOODIES", "basics"), ("Kitchen knife set", "kitchen"),
    ("Kitchen knives set", "kitchen"), ("Kitchen KNIVES sets", "kitchen"),
])
def test_common_plural_variants_receive_the_same_pick_lift(title, group):
    cfg = settings()
    d = deal(item=title)
    assert group in appeal.signals(d, cfg, NOW)[0]
    assert rank.pick_score(d, cfg, NOW) == pytest.approx(unlifted(d, cfg) * 1.2)


@pytest.mark.parametrize("price,expected", [(0, 1.2), (50, 1.2), (50.01, 1), (-1, 1), (None, 1),
                                            (float("nan"), 1), (float("inf"), 1), (True, 1)])
def test_price_boundary(price, expected):
    assert appeal.signals(deal(final_price=price), settings(), NOW)[2] == expected


def test_one_lift_for_overlapping_words_groups_and_seasons():
    cfg = settings()
    d = deal(item="Perfume gift set with satin robe and slippers")
    assert appeal.signals(d, cfg, NOW) == (["basics", "beauty", "allure"], ["gifts"], 1.2)
    assert rank.pick_score(d, cfg, NOW) == pytest.approx(unlifted(d, cfg) * 1.2)
    cfg.front_page["appeal"]["want"]["factor"] = 1.1
    assert appeal.signals(d, cfg, NOW)[2] == 1.15
    # The strongest seasonal window still shares want's configured ceiling.
    assert rank.pick_score(d, cfg, NOW) == pytest.approx(unlifted(d, cfg) * 1.1)


@pytest.mark.parametrize("condition", ["used", "open_box", "refurbished", "Open Box", "renewed", "like new"])
def test_non_new_conditions_never_lift_even_recovery_scoring(condition):
    cfg = settings()
    d = deal(conditions=[condition])
    assert appeal.signals(d, cfg, NOW)[2] == 1
    assert rank.pick_score(d, cfg, NOW, allow_used=True) == unlifted(d, cfg, allow_used=True)


@pytest.mark.parametrize("over", [
    {"price_unconfirmed": True}, {"status": "dead"}, {"locked": True},
    {"price_aged": "Posted days ago"}, {"image_url": ""},
    {"price_rechecked_at": "", "price_published_at": "", "first_seen_at": iso(NOW - timedelta(days=2))},
    {"item": "Watch charger dock for Apple Watch Series 11"},
    {"item": "Phone case for iPhone 17 with satin strap"},
])
def test_gates_and_niche_still_win(over):
    cfg = settings()
    d = deal(**over)
    assert rank.pick_score(d, cfg, NOW) == unlifted(d, cfg)


def test_stale_amazon_never_lifted_even_when_scored_for_recovery():
    cfg = settings()
    d = deal(store="Amazon", asin="B012345678", canonical_url="https://amazon.com/dp/B012345678",
             price_rechecked_at=iso(NOW - timedelta(hours=4)), price_published_at="")
    assert appeal.signals(d, cfg, NOW)[2] == 1
    assert rank.pick_score(d, cfg, NOW, require_confirmation=False) == unlifted(d, cfg, require_confirmation=False)
    assert rank.pick_score(d, cfg, NOW) is None


@pytest.mark.parametrize("word", settings().front_page["appeal"]["want"]["exclude_words"])
def test_adult_words_never_receive_any_lift(word):
    cfg = settings()
    d = deal(item=word + " perfume lingerie gift set", category="beauty")
    assert appeal.signals(d, cfg, NOW)[2] == 1
    baseline = copy.deepcopy(cfg)
    baseline.front_page["appeal"] = {}
    assert rank.pick_score(d, cfg, NOW) == rank.pick_score(d, baseline, NOW)


def test_combined_cap_and_stronger_sales_signal():
    cfg = settings()
    d = deal(store="Amazon", asin="B012345678", canonical_url="https://amazon.com/dp/B012345678",
             keepa_popularity={"asin": "B012345678", "monthly_sold": 1000, "sold_at": iso(NOW)})
    base = unlifted(d, cfg)
    cfg.front_page["popular_affordable"]["enabled"] = True
    assert rank.pick_score(d, cfg, NOW) == pytest.approx(base * 1.25)
    assert rank.pick_score(d, cfg, NOW, popularity=False) == pytest.approx(base * 1.2)
    assert rank.pick_popularity_factor(d, cfg, NOW) == pytest.approx(1.25 / 1.2)
    # Task 260 is absent in this checkout; its soft factor shares this helper.
    assert appeal.capped_lift([1.2, 1.12, 1.0], 1.2) == 1.2
    cfg.front_page["everyday_price"]["full_until"] = 10
    cfg.front_page["everyday_price"]["floor_from"] = 40
    assert rank.pick_score(d, cfg, NOW) <= base * 1.25
    # Stronger demand leaves no additional keyword lift to count.
    cfg.front_page["everyday_price"]["enabled"] = False
    assert appeal.summary([d], cfg, NOW) == {"want": {}, "season": {}}


@pytest.mark.parametrize("stamp,expected", [
    ("2026-11-01T03:59:59Z", 1), ("2026-11-01T04:00:00Z", 1.15),
    ("2026-12-25T04:59:59Z", 1.15), ("2026-12-25T05:00:00Z", 1),
])
def test_gift_window_uses_new_york_midnight(stamp, expected):
    now = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    cfg = settings()
    cfg.front_page["appeal"]["want"]["enabled"] = False
    d = deal(item="Gift set", first_seen_at=iso(now), price_published_at=iso(now), price_rechecked_at=iso(now))
    before = copy.deepcopy(d)
    assert appeal.signals(d, cfg, now)[2] == expected
    assert d == before
    assert appeal.signals(d, cfg, now)[2] == expected


@pytest.mark.parametrize("stamp,expected", [("2026-11-10T04:59:59Z", 1), ("2026-11-10T05:00:00Z", 1.15),
                                          ("2026-11-28T04:59:59Z", 1.15), ("2026-11-28T05:00:00Z", 1)])
def test_thanksgiving_window_boundaries(stamp, expected):
    now = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    cfg = settings()
    cfg.front_page["appeal"]["want"]["enabled"] = False
    assert appeal.signals(deal(item="Roasting pan", price_rechecked_at=iso(now)), cfg, now)[2] == expected


def test_year_wrapping_window():
    cfg = settings()
    cfg.front_page["appeal"]["want"]["enabled"] = False
    cfg.front_page["appeal"]["season"] = [{"from": "12-20", "to": "01-05", "words": ["socks"], "factor": 1.1}]
    for month, day, expected in ((12, 25, 1.1), (1, 1, 1.1), (1, 5, 1.1), (1, 6, 1), (12, 19, 1)):
        now = datetime(2027, month, day, 12, tzinfo=timezone.utc)
        assert appeal.signals(deal(price_rechecked_at=iso(now)), cfg, now)[2] == expected


def test_shipped_switches_on_missing_off(tmp_path):
    cfg = load_config()
    assert cfg.open_box_scarcity
    assert cfg.open_box_counts is False
    assert cfg.front_page["appeal"]["want"]["enabled"]
    assert cfg.front_page["appeal"]["want"]["factor"] == 1.2
    assert cfg.front_page["appeal"]["want"]["max_price"] == 50
    # Operator 2026-10-17: holiday items ~2 weeks before each holiday.
    assert [w["name"] for w in cfg.front_page["appeal"]["season"]] == [
        "halloween", "valentines", "mothers day", "fathers day", "fourth of july",
        "back to school", "thanksgiving", "gifts"]
    path = tmp_path / "empty.yaml"
    path.write_text("{}")
    assert not load_config(path).open_box_scarcity and not AppConfig().open_box_scarcity
    assert not load_config(path).open_box_counts and not AppConfig().open_box_counts
    assert not appeal.active(load_config(path).front_page.get("appeal", {}))
    path.write_text('site:\n  open_box_counts: true\n')
    assert load_config(path).open_box_counts is True


@pytest.mark.parametrize("section,value", [
    ("want", None), ("want", []), ("want", {"enabled": "yes"}),
    ("want", {"factor": True}), ("want", {"factor": .99}), ("want", {"factor": float("inf")}),
    ("want", {"factor": 10**1000}),
    ("want", {"max_price": -1}), ("want", {"max_price": float("nan")}),
    ("want", {"groups": []}), ("want", {"groups": {"bad": [".*"]}}),
    ("want", {"groups": {"bad": "socks"}}), ("want", {"exclude_words": []}),
    ("season", {}), ("season", [None]),
    *[("season", [{"from": "11-01", "to": "12-24", "words": ["gift"], "factor": 1.15, **over}])
      for over in ({"from": "11-31"}, {"to": "1-01"}, {"words": []}, {"factor": 1.151},
                   {"factor": True}, {"factor": float("nan")})],
])
def test_config_validation(tmp_path, section, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"publish": {"front_page": {"appeal": {section: value}}}}))
    with pytest.raises(ValueError, match="publish.front_page.appeal"):
        load_config(path)


@pytest.mark.parametrize("key", ["open_box_scarcity", "open_box_counts"])
@pytest.mark.parametrize("value", [0, 1, "false", None, [], {}])
def test_scarcity_config_requires_boolean(tmp_path, key, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"site": {key: value}}))
    with pytest.raises(ValueError, match=f"site.{key} must be a boolean"):
        load_config(path)


@pytest.mark.parametrize("context", [
    "{quote}", "Yesterday: {quote}. Restocked to 25 today.",
    "Yesterday: {quote}.", "{quote}. Restocked to 25 today.",
])
def test_stock_counts_default_off_even_when_quote_omits_context(context):
    cfg = settings()
    d = observed_open_box(context=context)
    # Even a fresh, previously captured quote must not publish a count.
    public = site._serialize_deal(d, NOW, cfg=cfg)
    assert "open_box_stock" not in public
    assert open_box.label(public, NOW) == "OPEN BOX · often only 1"
    assert "OPEN BOX · often only 1" in seo.card_html(public, now=NOW)
    detail = seo._deal_page("{{DEALS}}", public, "/deal/test/", "https://wowpriced.com", "", [], {}, [], NOW)
    assert "OPEN BOX · often only 1" in detail
    template = site._page_template((site.TEMPLATES_DIR / "index.html").read_text(), cfg, home=True)
    function = template[template.index("  function usedTag("):template.index("  function isNew(")]
    # A cached payload containing a count must also use the safe label.
    public["open_box_stock"] = {"count": 2, "checked_at": iso(NOW)}
    script = ('Date.now = () => ' + str(int(NOW.timestamp() * 1000)) + ';\n'
              + function + '\nprocess.stdout.write(usedTag(' + json.dumps(public) + '));')
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert result.stdout == "OPEN BOX · often only 1"


@pytest.mark.parametrize("count,age,expected", [(1, 0, "only 1 left"), (2, 2, "only 2 left"),
                                             (3, 1, "only 3 left"), (2, 2.001, "often only 1"),
                                             (4, 0, "often only 1"), (0, 0, "often only 1"),
                                             (2, -1, "often only 1"), (None, 0, "often only 1")])
@pytest.mark.parametrize("counts", [False, True])
def test_static_dynamic_and_detail_scarcity(count, age, expected, counts, tmp_path):
    cfg = settings()
    cfg.open_box_counts = counts
    expected = expected if counts else "often only 1"
    stock_at = NOW - timedelta(hours=age)
    d = observed_open_box(count=count, observed=stock_at)
    public = site._serialize_deal(d, NOW, cfg=cfg)
    assert open_box.label(public, NOW) == "OPEN BOX · " + expected
    card = seo.card_html(public, now=NOW)
    assert '<span class="condtag scarcity">OPEN BOX · ' + expected in card
    detail = seo._deal_page("{{DEALS}}", public, "/deal/test/", "https://wowpriced.com", "", [], {}, [], NOW)
    assert '<p class="detail-scarcity">OPEN BOX · ' + expected in detail
    template = site._page_template((site.TEMPLATES_DIR / "index.html").read_text(), cfg, home=True)
    function = template[template.index("  function usedTag("):template.index("  function isNew(")]
    script = function + '\nprocess.stdout.write(usedTag(' + json.dumps(public) + '));'
    script = 'Date.now = () => ' + str(int(NOW.timestamp() * 1000)) + ';\n' + script
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert result.stdout == "OPEN BOX · " + expected


def test_no_stock_clock_no_count_and_no_counts_from_pack_sizes():
    cfg = settings()
    for changes in ({"item": "3-pack socks"}, {"item": "Only 2 left", "price_published_at": ""},
                    {"item": "Only 2 left", "first_seen_at": iso(NOW - timedelta(days=2))},
                    {"item": "Only 2 left", "stack_steps": ["Only 4 left"]}):
        d = deal(conditions=["open_box"], **changes)
        public = site._serialize_deal(d, NOW, cfg=cfg)
        assert open_box.label(public, NOW) == "OPEN BOX · often only 1"
        assert "open_box_stock" not in public
    for condition in ("used", "refurbished", "new"):
        assert open_box.facts(deal(conditions=[condition]), NOW) is None
    assert open_box.facts(deal(conditions=["open_box"], status="dead"), NOW) is None
    fresh = site._serialize_deal(observed_open_box(), NOW, cfg=cfg)
    assert open_box.label(fresh, NOW + timedelta(hours=2, seconds=1)) == "OPEN BOX · often only 1"


@pytest.mark.parametrize("where", ["title", "unverified quote", "other offer", "roundup"])
def test_exact_stock_requires_verified_offer_quote(where):
    cfg = settings()
    item = "Open box socks only 2 left" if where == "title" else "Open box socks"
    quote = "$25" if where == "title" else f"{item}: $25; only 2 left"
    post = RawPost(source_name="source", uid="offer", url="https://source.example/offer",
        title=item, text="$25" if where in {"title", "unverified quote"} else quote,
        published_at=NOW, fetched_at=NOW, parsed=[(item, "https://store.example/socks")])
    if where == "other offer":
        quote = post.text = "Open box towels: $25; only 2 left"
    if where == "roundup":
        post.parsed.append(("Open box towels", "https://store.example/towels"))
    extracted = ExtractedDeal(item=item, store="Store", source_name="source", source_url=post.url,
        product_url="https://store.example/socks", conditions=["open_box"], final_price=25,
        evidence=Evidence(final_price_quote=quote))
    assert verify_evidence(extracted, post.text)[0] is (where != "unverified quote")
    database = DB(":memory:")
    try:
        result = dedupe.upsert_extracted(database, extracted, SavingsResult(computable=True, final_price=25), post, cfg, NOW)
        saved = database.get_deal(result["dedupe_key"])
    finally:
        database.close()
    public = site._serialize_deal(saved, NOW, cfg=cfg)
    assert "open_box_stock_evidence" not in saved
    assert "open_box_stock" not in public
    assert open_box.label(public, NOW) == "OPEN BOX · often only 1"


@pytest.mark.parametrize("statement", [
    "was only 2 left yesterday; restocked to 25 today",
    "was only 2 left", "only 2 left yesterday", "yesterday: only 2 left",
    "previously only 2 left", "last week: only 2 left",
    "not only 2 left", "no longer only 2 left", "if only 2 left",
    "will have only 2 left tomorrow", 'the old notice said "only 2 left"',
    "only 2 left; restocked to 25 today", "only 2 left, but 25 are available",
    "only 2 left after the next sale",
])
def test_historical_or_ambiguous_stock_never_becomes_current_scarcity(statement):
    cfg = settings()
    d = observed_open_box(statement=statement)
    assert "open_box_stock_evidence" not in d
    public = site._serialize_deal(d, NOW, cfg=cfg)
    assert "open_box_stock" not in public
    assert open_box.label(public, NOW) == "OPEN BOX · often only 1"
    assert "OPEN BOX · often only 1" in seo.card_html(public, now=NOW)
    detail = seo._deal_page("{{DEALS}}", public, "/deal/test/", "https://wowpriced.com", "", [], {}, [], NOW)
    assert "OPEN BOX · often only 1" in detail
    # Old rows captured by the permissive parser must also fail closed.
    legacy = observed_open_box()
    legacy["open_box_stock_evidence"]["quote"] = f'{legacy["item"]}: $25; {statement}'
    assert open_box.facts(legacy, NOW) == {}
    assert "open_box_stock" not in site._serialize_deal(legacy, NOW, cfg=cfg)


@pytest.mark.parametrize("statement", [
    "only 2 left", "Only 2 left in stock!", "currently only 2 left",
    "there are only 2 left in stock", "only 2 left today",
])
def test_unambiguous_current_stock_keeps_exact_scarcity(statement):
    cfg = settings()
    cfg.open_box_counts = True
    d = observed_open_box(statement=statement)
    assert open_box.facts(d, NOW) == {"count": 2, "checked_at": iso(NOW)}
    assert open_box.label(site._serialize_deal(d, NOW, cfg=cfg), NOW) == "OPEN BOX · only 2 left"


@pytest.mark.parametrize("change", [
    {"final_price": 26}, {"item": "Other socks"}, {"sightings": []},
    {"canonical_url": "https://store.example/other-offer"},
    {"first_seen_at": ""}, {"price_published_at": ""},
    {"first_seen_at": iso(NOW - timedelta(hours=3))},
])
def test_stock_evidence_cannot_move_to_other_offer_or_clock(change):
    cfg = settings()
    d = observed_open_box()
    assert open_box.facts(d, NOW) == {"count": 2, "checked_at": iso(NOW)}
    assert open_box.facts(dict(d, **change), NOW) == {}
    public = site._serialize_deal(d, NOW, cfg=cfg)
    assert "open_box_stock_evidence" not in public


def test_stock_rechecks_cannot_renew_original_observation():
    d = observed_open_box(observed=NOW - timedelta(hours=3))
    d.update(first_seen_at=iso(NOW), price_published_at=iso(NOW), price_rechecked_at=iso(NOW))
    assert open_box.facts(d, NOW) == {}
    cfg = settings()
    cfg.open_box_scarcity = False
    assert "open_box_stock_evidence" not in observed_open_box(cfg=cfg)


def test_allure_spacing_and_sparse_supply():
    cfg = settings()
    rows = [deal(dedupe_key=f"allure{i}", item="Satin perfume", pick=i + 1) for i in range(12)]
    rows += [deal(dedupe_key=f"normal{i}", pick=13 + i) for i in range(36)]
    before = copy.deepcopy(rows)
    output = seo.home_order(rows, cfg.front_page)
    positions = [i for i, d in enumerate(output) if appeal.is_allure(d, cfg.front_page["appeal"])]
    assert positions[0] == 6 and all(b - a >= 6 for a, b in zip(positions, positions[1:]))
    assert {d["dedupe_key"] for d in rows if d["item"] == "Cotton socks"} <= {d["dedupe_key"] for d in output}
    assert rows == before
    assert seo.home_order(rows[:12], cfg.front_page) == []
    assert rank.pick_order(rows[:12], cfg.front_page) == rows[:12]
    last = dict(rows[0], pick=100)
    assert seo.home_order(rows[-36:] + [last], cfg.front_page)[-1]["dedupe_key"] == "allure0"


@pytest.mark.parametrize("gone_allure", [False, True])
@pytest.mark.parametrize("allure_count,ordinary_count,gone_count", [
    (6, 8, 1), (12, 36, 6), (0, 6, 1), (0, 20, 3), (6, 9, 2),
])
@pytest.mark.parametrize("picks", [False, True])
def test_gone_and_allure_constraints_in_static_and_actual_js(tmp_path, allure_count, ordinary_count, gone_count, picks, gone_allure):
    cfg = settings()
    cfg.support_enabled = False
    cfg.engagement = {}
    if not picks:
        cfg.front_page.pop("top_picks")
    rows = [dict(deal(dedupe_key=f"a{i}", item=f"Satin perfume {i}"), key=f"a{i}", allure=True)
            for i in range(allure_count)]
    rows += [dict(deal(dedupe_key=f"g{i}", status="dead", item=f"Gone {'perfume' if gone_allure else 'socks'} {i}"),
                  key=f"g{i}", allure=gone_allure)
             for i in range(gone_count)]
    rows += [dict(deal(dedupe_key=f"n{i}", item=f"Socks {i}"), key=f"n{i}") for i in range(ordinary_count)]
    for i, row in enumerate(rows):
        row["pick"] = i + 1
    before = copy.deepcopy(rows)
    database = DB(":memory:")
    try:
        results = browser(tmp_path, cfg, database, [dict(rows=rows, options=cfg.front_page,
                   state={"catTab": tab, "sortKey": key})
                   for tab in (0, 1) for key in ("top", "fresh", "price")])
    finally:
        database.close()
    static = seo.static_wall(rows, True, front_page=cfg.front_page, now=NOW)
    title_keys = {d["item"]: d["key"] for d in rows}
    static_keys = [title_keys[re.sub("<[^>]+>", "", title)]
                   for title in re.findall(r'<h3 class="title">(.*?)</h3>', static)]
    ordered = [d["key"] for d in seo.home_order(rows, cfg.front_page)]
    assert static_keys == ordered[:seo._STATIC_WALL_MAX]
    for keys in [ordered, *[r["rendered"] for r in results]]:
        allure_keys = {d["key"] for d in rows if d.get("allure")}
        allure_positions = [i for i, key in enumerate(keys) if key in allure_keys]
        gone_positions = [i for i, key in enumerate(keys) if key.startswith("g")]
        assert all(i >= 6 for i in allure_positions)
        assert all(b - a >= 6 for a, b in zip(allure_positions, allure_positions[1:]))
        assert all(i >= 8 for i in gone_positions)
        assert all(b - a >= 8 for a, b in zip(gone_positions, gone_positions[1:]))
        assert {f"n{i}" for i in range(ordinary_count)} <= set(keys)
        if not allure_count and ordinary_count >= 8:
            assert gone_positions
    assert rows == before


@pytest.mark.parametrize("item", ["Synthetic widget", "Cotton socks"])
def test_configured_popularity_marginal_and_quota(item):
    cfg = settings()
    popular = cfg.front_page["popular_affordable"]
    popular.update(enabled=True, min_monthly_sold=500, weight=1.4)
    rows = [deal(dedupe_key=f"pop{i}", item=f"{item} {i}", store="Amazon", asin=f"B000000{i:03d}",
        canonical_url=f"https://amazon.com/dp/B000000{i:03d}", brand=f"Brand{i}",
        category=("home", "kitchen", "fashion")[i % 3],
        keepa_popularity={"asin": f"B000000{i:03d}", "monthly_sold": 600, "sold_at": iso(NOW)})
        for i in range(12)]
    for d in rows:
        base = rank.pick_score(d, cfg, NOW, popularity=False)
        assert rank.pick_popularity_factor(d, cfg, NOW, popular) == pytest.approx(rank.pick_score(d, cfg, NOW) / base)
        # Explicit caller options also win over the config's threshold/weight.
        assert rank.pick_popularity_factor(d, cfg, NOW, dict(popular, min_monthly_sold=700)) == 1
    ordinary = [dict(d, dedupe_key=f"ordinary{i}", item=f"Ordinary widget {i}", keepa_popularity=None)
                for i, d in enumerate(rows)]
    boosted = set()
    scored = [(d, 140) for d in rows] + [(d, 130) for d in ordinary]
    picked = rank.top_picks(scored, cfg.front_page, NOW, cfg=cfg, boosted=boosted)
    assert len(boosted) == int(popular["max_share"] * rank.PICK_WINDOW)
    assert len(picked) == 24
    assert {d["dedupe_key"] for d in picked[:6]} == {f"pop{i}" for i in range(6)}
    assert picked[6]["dedupe_key"].startswith("ordinary")


def test_allure_offers_stay_on_event_best_and_category_pages(tmp_path):
    cfg = settings()
    cfg.paywall_open_access = True
    rows = [deal(dedupe_key=f"perfume{i}", item=f"Black Friday perfume {i}", category="beauty")
            for i in range(10)]
    database = DB(":memory:")
    try:
        site.render_site(database, cfg, NOW, out_dir=tmp_path, deals=rows)
    finally:
        database.close()
    for path in ("black-friday/index.html", "best-deals-today/index.html", "deals/beauty-personal-care/index.html"):
        html = (tmp_path / path).read_text()
        assert "No qualifying deals right now" not in html
        for d in rows:
            assert d["item"] in html, path
    home = (tmp_path / "index.html").read_text()
    static = home.split('<div id="content"')[1].split('<div id="paywall"')[0]
    assert not re.search(r'class="card[^\"]*"', static)


@pytest.mark.parametrize("unfiltered_tab", [0, 1])
def test_real_browser_ordering_all_and_category(tmp_path, unfiltered_tab):
    cfg = settings()
    cfg.support_enabled = False
    cfg.engagement = {}
    rows = [dict(deal(dedupe_key=f"a{i}", item="Satin perfume"), key=f"a{i}", allure=True, pick=i + 1) for i in range(12)]
    rows += [dict(deal(dedupe_key=f"n{i}"), key=f"n{i}", pick=i + 13) for i in range(36)]
    database = DB(":memory:")
    try:
        results = browser(tmp_path, cfg, database, [dict(rows=rows, options=cfg.front_page, state={"catTab": unfiltered_tab}),
            dict(rows=rows, options=cfg.front_page, state={"catTab": 3}),
            dict(rows=rows, options=cfg.front_page, state={"catTab": unfiltered_tab, "sortKey": "price"}),
            dict(rows=rows, options=cfg.front_page, state={"catTab": unfiltered_tab}, controls={"q": "perfume"})])
    finally:
        database.close()
    positions = [i for i, key in enumerate(results[0]["rendered"]) if key.startswith("a")]
    assert positions[0] >= 6 and all(b - a >= 6 for a, b in zip(positions, positions[1:]))
    assert set(results[1]["rendered"]) == {d["key"] for d in rows}
    positions = [i for i, key in enumerate(results[2]["rendered"]) if key.startswith("a")]
    assert positions[0] >= 6 and all(b - a >= 6 for a, b in zip(positions, positions[1:]))
    assert set(results[3]["rendered"]) == {d["key"] for d in rows}


@pytest.mark.parametrize("allure_count", [0, 100])
def test_click_price_first_120_survives_allure_spacing(tmp_path, allure_count):
    cfg = settings()
    cfg.support_enabled, cfg.engagement = False, {}
    assert cfg.keepa_click_price_rule == "on" and cfg.static_photos and cfg.strip_comments
    rows = [dict(deal(dedupe_key=f"a{i}", item=f"Satin perfume {i}"), key=f"a{i}", allure=True)
            for i in range(allure_count)]
    rows += [dict(deal(dedupe_key=f"n{i}", item=f"Socks {i}"), key=f"n{i}")
             for i in range(120 - allure_count)]
    rows += [dict(deal(dedupe_key=f"u{i}", item=f"Unknown socks {i}"), key=f"u{i}", click_price_unknown=True)
             for i in range(12)]
    for i, row in enumerate(rows):
        row["pick"] = i + 1
    before = copy.deepcopy(rows)
    database = DB(":memory:")
    try:
        results = browser(tmp_path, cfg, database, [dict(rows=rows, options=cfg.front_page,
            state={"catTab": tab, "sortKey": key}) for tab in (0, 1) for key in ("top", "fresh", "price")])
    finally:
        database.close()
    static = seo.static_wall(rows, True, front_page=cfg.front_page, now=NOW, static_photos=True)
    titles = {d["item"]: d["key"] for d in rows}
    static_keys = [titles[re.sub("<[^>]+>", "", title)]
                   for title in re.findall(r'<h3 class="title">(.*?)</h3>', static)]
    assert static_keys == [d["key"] for d in seo.home_order(rows, cfg.front_page, click_price=True)][:seo._STATIC_WALL_MAX]
    for static, keys in [(True, static_keys), *[(False, r["rendered"]) for r in results]]:
        assert not any(key.startswith("u") for key in keys[:120])
        allure_positions = [i for i, key in enumerate(keys) if key.startswith("a")]
        assert all(i >= 6 for i in allure_positions)
        assert all(b - a >= 6 for a, b in zip(allure_positions, allure_positions[1:]))
        if not static:
            assert {f"n{i}" for i in range(120 - allure_count)} <= set(keys)
        if not allure_count and not static:
            assert any(key.startswith("u") for key in keys[120:])
    assert rows == before


@pytest.mark.parametrize("unfiltered_tab", [0, 1])
@pytest.mark.parametrize("allure", [False, True])
def test_allure_spotlight_is_hidden_on_all_and_available_in_search(unfiltered_tab, allure):
    cfg = settings()
    html = appeal.template((site.TEMPLATES_DIR / "index.html").read_text(), cfg.front_page["appeal"])
    start = html.index("  function activeSpotlight()")
    function = html[start:html.index("\n  function ", start + 4)]
    row = {"key": "spotlight", "status": "alive", "allure": allure,
           "spotlight": {"until": iso(NOW + timedelta(hours=1))}}
    script = ('let query = ""; const searchText = () => query, filtered = rows => rows; '
              'const state = {catTab:' + str(unfiltered_tab) + ', spotlight:[' + json.dumps(row) + ']};\n'
              'Date.now = () => ' + str(int(NOW.timestamp() * 1000)) + ';\n' + function + '\n'
              'const result = [activeSpotlight().length]; query="perfume"; result.push(activeSpotlight().length);'
              'query=""; state.catTab=3; result.push(activeSpotlight().length);'
              'process.stdout.write(JSON.stringify(result));')
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == [int(not allure), 1, 0]


@pytest.mark.parametrize("mode", ["membership", "open", "support", "shipped"])
def test_off_omitted_bytes_and_rerender_idempotency(tmp_path, mode):
    cfg = settings()
    cfg.open_box_scarcity = False
    cfg.front_page["appeal"]["want"]["enabled"] = False
    cfg.front_page["appeal"]["season"] = []
    cfg.support_enabled = mode in {"support", "shipped"}
    cfg.paywall_open_access = mode == "open"
    cfg.paywall_teaser_count = 8
    rows = [deal(dedupe_key=f"d{i}", item="Socks only 2 left", conditions=["open_box"] if i % 2 else []) for i in range(12)]
    database = DB(":memory:")
    try:
        out = tmp_path / "site"
        site.render_site(database, cfg, NOW, out_dir=out, deals=rows)
        before = tree(out)
        cfg.front_page["appeal"].pop("want")
        cfg.front_page["appeal"].pop("season")
        database.meta_set("teaser_recent", "[]")
        site.render_site(database, cfg, NOW, out_dir=out, deals=rows)
        assert tree(out) == before
        for data in before.values():
            assert b"want_season" not in data and b"open_box_stock" not in data
        cfg = settings()
        first = site.render_site(database, cfg, NOW, out_dir=out, deals=rows, last_run={"run_key": "fixed273"})
        enabled = tree(out)
        second = site.render_site(database, cfg, NOW, out_dir=out, deals=rows, last_run={"run_key": "fixed273"})
        assert tree(out) == enabled
        assert first["want_season"] == second["want_season"]
    finally:
        database.close()


def test_private_counts_only_for_selected_picks(tmp_path, caplog):
    cfg = settings()
    rows = [deal(dedupe_key="socks"), deal(dedupe_key="watch", item="Wrist watch"),
            deal(dedupe_key="bad", item="Adult perfume"), deal(dedupe_key="used", conditions=["used"])]
    database = DB(":memory:")
    try:
        result = site.render_site(database, cfg, NOW, out_dir=tmp_path, deals=rows)
        from dealradar import popular_affordable
        latest = popular_affordable.report_data(database, NOW)["latest"]
        assert latest["want_season"] == result["want_season"]
        assert '"want_season"' in "\n".join(popular_affordable.report_lines(
            popular_affordable.report_data(database, NOW)))
    finally:
        database.close()
    assert result["want_season"] == {"want": {"basics": 1, "small wins": 1}, "season": {"gifts": 1}}
    assert "want_season" not in caplog.text
    for path, data in tree(tmp_path).items():
        assert b"want_season" not in data, path
        assert b"exclude_words" not in data, path


def test_cli_omits_private_counts(monkeypatch, capsys):
    from argparse import Namespace
    from dealradar import cli, pipeline
    monkeypatch.setattr(pipeline, "run", lambda **kwargs: {
        "published": 3, "want_season": {"want": {"PRIVATE_WANT_GROUP": 2}, "season": {}}})
    assert cli.cmd_run(Namespace(fast_lane=False, db="unused", config="unused", dry_run_telegram=True)) == 0
    assert json.loads(capsys.readouterr().out) == {"published": 3}


def test_phone_budgets_with_all_switches_enabled(tmp_path):
    cfg = settings()
    cfg.max_photoless_h = SHIPPED.max_photoless_h  # all switches, including task 283's cutoff
    # Real task-206 1,400-offer input; never modify its recorded byte ceilings.
    publish_fixture(tmp_path, cfg)
    result = analyze(tmp_path / "site", cfg, expected_offers=visible_offer_count(cfg))
    for name, value in result["metrics"].items():
        if name in ("home_html", "home_js", "home_css", "deals_json"):
            print(f'int11 {name}: {value["gzip"]} <= {cfg.phone_budget[name + "_gzip_max_bytes"]} gzip bytes')
            assert value["gzip"] <= cfg.phone_budget[name + "_gzip_max_bytes"], (name, value)


def test_scarcity_css_preserves_price_row_and_aa_colors():
    assert "pricerow" not in open_box.STYLE
    assert "top:38px" in open_box.STYLE and "white-space:nowrap" in open_box.STYLE
    # Existing AA condition colors are shared by both static and dynamic tags.
    from tests.test_dark_mode import contrast, rgb
    assert contrast(rgb("#ffffff"), rgb("#94621a")) >= 4.5
    assert contrast(rgb("#3a2503"), rgb("#f2b544")) >= 4.5


def test_native_390px_light_dark_layout(tmp_path):
    from dealradar.publish import dark
    template = (site.TEMPLATES_DIR / "index.html").read_text()
    css = re.search(r"<style>(.*?)</style>", template, re.S)[1] + open_box.STYLE + dark.CSS
    cfg = settings()
    cards = []
    for item, enabled in (("Socks", False), ("Socks", True), ("Socks only 3 left", True)):
        cfg.open_box_scarcity = enabled
        public = site._serialize_deal(observed_open_box(item=item, count=3, cfg=cfg), NOW, cfg=cfg)
        cards.append(seo.card_html(public, now=NOW))
    page = tmp_path / "layout.html"
    page.write_text('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<style>' + css + '</style><main><div class="grid">' + ''.join(cards) + '</div></main>')
    result = subprocess.run(["node", str(Path(__file__).with_name("what_people_buy_layout.cjs")), str(page)],
                            capture_output=True, text=True, timeout=40, env={**os.environ, "TMPDIR": str(tmp_path)})
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("failure,expected", [("launch", 77), ("import", 1), ("assertion", 1)])
def test_native_layout_skip_only_on_actual_chromium_launch_failure(tmp_path, failure, expected):
    harness = Path(__file__).with_name("what_people_buy_layout.cjs")
    script = '''
const assert = require('node:assert/strict'), Module = require('node:module');
const id = require.resolve('playwright', {paths: ['/opt/node-tools', process.cwd()]});
const load = Module._load;
Module._load = function(name, ...args) {
  if (name !== id) return load.call(this, name, ...args);
  if (FAILURE === 'import') throw Error('broken playwright import');
  return {chromium: {launch: async options => {
    assert.equal(options.executablePath, '/opt/pw-browsers/chromium');
    if (FAILURE === 'launch') throw Error('actual launch failed');
    return {newPage: async () => { throw Error('post-launch assertion failed'); }, close: async () => {}};
  }}};
};
require(HARNESS);
'''.replace("FAILURE", json.dumps(failure)).replace("HARNESS", json.dumps(str(harness)))
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20,
                            env={**os.environ, "TMPDIR": str(tmp_path)})
    assert result.returncode == expected, result.stdout + result.stderr
    assert ("Chromium cannot launch" in result.stdout) is (failure == "launch")


def test_gadgets_and_halloween_get_the_subtle_lift():
    """Operator 2026-10-17: cheap gadgets entice (subtly), and holiday items
    go up ~2 weeks before the holiday (Halloween from Oct 17)."""
    options = SHIPPED.front_page["appeal"]
    assert "gadgets" in appeal.groups({"item": "Anker 10,000mAh Wireless Charger Stand"}, options)
    assert "gadgets" in appeal.groups({"item": "Phomemo D30 Bluetooth Label Maker"}, options)
    assert "gadgets" not in appeal.groups({"item": "Stainless Steel Mixing Bowls"}, options)
    # Operator 2026-10-23: electronics back to 1.0; the gadgets lift and the mix's 3 in 30 stay.
    assert SHIPPED.front_page["appeal"]["category_factors"]["electronics"] == 1.0
    assert SHIPPED.front_page["appeal"]["want"]["group_factors"]["gadgets"] == 1.3
    halloween = next(w for w in options["season"] if w["name"] == "halloween")
    assert (halloween["from"], halloween["to"]) == ("10-17", "10-31")
    assert appeal.matches("3-Pk Zesua Halloween Outdoor LED Ghost Decor", halloween["words"])
    assert not appeal.matches("Kids Fleece Hoodie", halloween["words"])


def test_kids_toys_lift_but_adult_toys_never():
    """Operator 2026-10-17: what women buy for their kids goes up; adult never."""
    options = SHIPPED.front_page["appeal"]
    assert not appeal.blocked({"item": "Melissa & Doug Wooden Toy Car Set for Toddlers"}, options)
    assert "kids" in appeal.groups({"item": "Toddler Toys Stacking Blocks"}, options)
    for title in ("Rose Toy for Women", "Couples Toy Gift", "Wand Massager Personal",
                  "Adult Toy Kit", "Vibrating Egg"):
        assert appeal.blocked({"item": title}, options), title


def test_everyday_staples_from_the_operator_list_lift():
    """Operator 2026-10-17: "items people always need: foil, soap ..."."""
    options = SHIPPED.front_page["appeal"]
    for title in ("Reynolds Wrap Aluminum Foil 200 sq ft", "Dial Liquid Hand Soap 3-Pack",
                  "Glad ForceFlex Trash Bags 80 Count", "Cascade Platinum Dishwasher Pods 62 ct",
                  "Ziploc Sandwich Bags 280 Count", "Huggies Pull Ups Training Pants"):
        assert "everyday" in appeal.groups({"item": title}, options), title
    assert "everyday" not in appeal.groups({"item": "Stainless Steel Mixing Bowls"}, options)


def test_bras_and_panties_are_allure_and_team_sweatshirts_get_no_lift():
    """Operator 2026-10-17: "where is the bras panties, sexy items?" and a first
    screen of NBA sweatshirts; Christmas waits for the gifts window."""
    from dealradar.rank import is_niche
    options = SHIPPED.front_page["appeal"]
    for title in ("Warner's Women's Easy Does It Wireless Bra", "Hanky Panky Lace Panties 3-Pack",
                  "Women's Ribbed Bodysuit", "Cupshe Women's Bikini Set"):
        assert appeal.is_allure({"item": title}, options), title
    for title in ("Blue 84 Portland Trail Blazers NBA Officially Licensed Full Zip Sweatshirt",
                  "Men's Flannel Hoodie", "32 Degrees Women's Lightweight Packable Shirt Jacket",
                  "Amazon Essentials Men's Slim Fit Jeans"):
        assert not appeal.groups({"item": title}, options), title
    tree = {"item": "National Tree Company 7.5ft Pre-Lit Winchester Pine Slim Christmas Tree",
            "category": "home"}
    assert is_niche(tree, SHIPPED.front_page)
    assert not is_niche({"item": "Halloween Pumpkin String Lights", "category": "home"}, SHIPPED.front_page)


def test_allure_group_lifts_more_and_missing_knob_keeps_the_shared_lift():
    """Operator 2026-10-17: sexy items catch attention; adult items never lift."""
    cfg = copy.deepcopy(SHIPPED)
    cfg.front_page["popular_affordable"]["enabled"] = False
    cfg.appeal_score = {}
    bra = deal(item="Warner's Women's Easy Does It Wireless Bra")
    socks = deal(item="Cotton socks")
    adult = deal(item="Couples vibrator gift set")
    assert appeal.signals(bra, cfg, NOW)[2] == 1.35
    assert appeal.signals(socks, cfg, NOW)[2] == 1.2
    assert appeal.signals(adult, cfg, NOW)[2] == 1.0
    assert rank.pick_score(bra, cfg, NOW) / unlifted(bra, cfg) == pytest.approx(1.35)
    assert rank.pick_score(socks, cfg, NOW) / unlifted(socks, cfg) == pytest.approx(1.2)
    off = copy.deepcopy(cfg)
    off.front_page["appeal"]["want"].pop("group_factors")
    assert appeal.signals(bra, off, NOW)[2] == 1.2
    assert rank.pick_score(bra, off, NOW) / unlifted(bra, off) == pytest.approx(1.2)


@pytest.mark.parametrize("value", [{"allure": 1.6}, {"allure": 0.9}, {"nope": 1.2}, {"allure": "1.3"}])
def test_group_factors_are_validated(value):
    options = copy.deepcopy(SHIPPED.front_page["appeal"])
    options["want"]["group_factors"] = value
    with pytest.raises(ValueError, match="group_factors"):
        appeal.validate(options)


def test_live_first_24_fixes_gadgets_lift_and_satin_bedding_is_not_allure():
    """Operator 2026-10-17 (live first 24): more gadgets; a satin comforter is not flirty;
    blouses and quarter zips count toward the clothing mix."""
    cfg = copy.deepcopy(SHIPPED)
    cfg.front_page["popular_affordable"]["enabled"] = False
    cfg.appeal_score = {}
    options = cfg.front_page["appeal"]
    assert appeal.signals(deal(item="Anker Bluetooth Speaker, Portable"), cfg, NOW)[2] == 1.3
    assert not appeal.is_allure({"item": "BEDELITE Satin Comforter Set, 7 ct"}, options)
    assert appeal.is_allure({"item": "Women's Satin Pajamas Set"}, options)
    picks = cfg.front_page["top_picks"]
    for title in ("Satin One-Shoulder Blouse", "Penn State Sierra Quarter Zip Team Color"):
        assert rank.is_clothing({"item": title, "category": "home"}, picks), title
    assert not rank.is_clothing({"item": "Laptop Stand, Adjustable", "category": "electronics"}, picks)
