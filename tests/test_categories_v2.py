"""Offline category contracts: Keepa names, indexed titles, repair and supply."""
import json
import time
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from dealradar import verify
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.fetch.keepa import KeepaDealsAdapter, _Pull, _product_row
from dealradar.keepa_budget import KeepaBudget
from dealradar.keepa_categories import NAMES_META, ROOTS_META, category_from_name, cached_meta, root_id
from dealradar.parse.claude_client import categorize_titles
from dealradar.parse.extractor import parse_posts, recategorize_live_deals
from dealradar.parse.schema import CATEGORY_RULES, SYSTEM_PROMPT
from tests.test_ai_spend import deal, insert, parser_for
from tests.test_keepa import NOW, SRC, _amazon_row, _wh_row


@pytest.mark.parametrize("name,expected", [
    ("Beauty & Personal Care", "beauty"), ("Health & Household", "health"),
    ("Grocery & Gourmet Food", "grocery"), ("Home & Kitchen", "home"),
    ("Tools & Home Improvement", "home"), ("Appliances", "kitchen"),
    ("Clothing, Shoes & Jewelry", "fashion"), ("Electronics", "electronics"),
    ("Cell Phones & Accessories", "electronics"), ("Camera & Photo", "electronics"),
    ("Musical Instruments", "electronics"), ("Computers", "computers"),
    ("Computers & Accessories", "computers"), ("Video Games", "gaming"),
    ("Toys & Games", "toys_games"), ("Baby", "kids_baby"),
    ("Baby Products", "kids_baby"), ("Sports & Outdoors", "sports"),
    ("Patio, Lawn & Garden", "outdoors"), ("Automotive", "auto"),
    ("Pet Supplies", "pets"), ("Office Products", "office"),
    ("Books", "books_media"), ("Kindle Store", "books_media"),
    ("Movies & TV", "books_media"), ("CDs & Vinyl", "books_media"),
    ("Industrial & Scientific", ""),
])
def test_root_name_mapping(name, expected):
    assert category_from_name(name) == expected
    assert category_from_name("  " + name.upper() + "  ") == expected


def test_home_root_preserves_kitchen_only_for_home_and_kitchen():
    assert category_from_name("Home & Kitchen", "kitchen") == "kitchen"
    assert category_from_name("Tools & Home Improvement", "kitchen") == "home"


def test_unknown_root_sentinels_have_no_opinion():
    for value in (0, -1, 9223372036854775807, True, None):
        assert root_id({"rootCat": value}) == ""
    assert root_id({"rootCategory": 101}) == "101"


def pull_for(db, tokens, handler, **over):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    budget = KeepaBudget(tokens_left=tokens, refill_rate=0, seen_at=NOW, db=db)
    data = dict(client=client, key="offline", budget=budget, db=db, now=NOW,
                deadline=None, reserve=300, interval_s=1, margin_s=30,
                sleep=lambda _: None, category_names=cached_meta(db, NAMES_META),
                categories_tried=set())
    data.update(over)
    return _Pull(**data)


def test_category_names_batch_cache_forever_and_feed_shared_budget(tmp_path):
    db = DB(tmp_path / "state.db")
    calls = []
    def handler(request):
        assert request.url.path == "/category"
        assert request.url.params["domain"] == "1"
        assert request.url.params["parents"] == "0"
        ids = request.url.params["category"].split(",")
        calls.append(ids)
        return httpx.Response(200, json={"categories": {
            cid: {"catId": int(cid), "name": "Beauty & Personal Care"} for cid in ids},
            "tokensLeft": 310 - len(calls), "refillRate": 0})
    pull = pull_for(db, 310, handler)
    sleeps = []
    pull.sleep = sleeps.append
    adapter = KeepaDealsAdapter()
    ids = [str(i) for i in range(1, 13)]
    adapter._lookup_categories(pull, ids)
    assert list(map(len, calls)) == [10, 2]
    assert sleeps == [1]
    pull.budget.save(db)
    assert KeepaBudget.load(db).available(NOW) == 308
    assert len(cached_meta(db, NAMES_META)) == 12
    second = pull_for(db, 1000, handler)
    adapter._lookup_categories(second, ids)
    assert len(calls) == 2
    db.close()


@pytest.mark.parametrize("tokens", [None, 300, 300.5])
def test_category_lookup_never_spends_reserved_or_unknown_tokens(tmp_path, tokens):
    db = DB(tmp_path / "state.db")
    pull = pull_for(db, tokens, lambda req: pytest.fail("reserved category request"))
    KeepaDealsAdapter()._lookup_categories(pull, ["1"], all_roots=True)
    assert db.meta_get(NAMES_META) is None
    db.close()


def test_category_lookup_stops_between_batches_on_low_response(tmp_path):
    db = DB(tmp_path / "state.db")
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"tokensLeft": 300, "categories": {}})
    pull = pull_for(db, 302, handler)
    KeepaDealsAdapter()._lookup_categories(pull, [str(i) for i in range(1, 22)])
    assert len(calls) == 1
    db.close()


def test_successful_unknown_category_lookup_is_cached(tmp_path):
    db = DB(tmp_path / "state.db")
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"categories": {}, "tokensLeft": 400})
    adapter = KeepaDealsAdapter()
    adapter._lookup_categories(pull_for(db, 401, handler), ["101"])
    adapter._lookup_categories(pull_for(db, 401, handler), ["101"])
    assert len(calls) == 1
    assert cached_meta(db, NAMES_META) == {"101": ""}
    db.close()


@pytest.mark.parametrize("warehouse", [False, True])
def test_structured_root_category_beats_asin_reuse_and_titles(tmp_path, warehouse):
    db = DB(tmp_path / "state.db")
    row = dict(_wh_row(1) if warehouse else _amazon_row(1), rootCat=101)
    mapper = KeepaDealsAdapter._map_warehouse_row if warehouse else KeepaDealsAdapter._map_row
    post = mapper(row, SRC, NOW, category_names={"101": "Beauty & Personal Care"})
    assert post.structured["category"] == "beauty"
    insert(db, deal(asin=row["asin"], category="electronics"))
    parser, messages = parser_for()
    pairs, stats = parse_posts([post], parser, parser.cfg, NOW, {}, db=db)
    assert pairs[0][1].category == "beauty"
    assert messages.calls == []
    assert stats["structured_parse"]["dropped_evidence"] == 0
    db.close()


def test_structured_home_root_preserves_known_kitchen(tmp_path):
    db = DB(tmp_path / "state.db")
    row = dict(_amazon_row(1), rootCat=101)
    post = KeepaDealsAdapter._map_row(row, SRC, NOW, category_names={"101": "Home & Kitchen"})
    insert(db, deal(asin=row["asin"], category="kitchen"))
    parser, messages = parser_for()
    pairs, _ = parse_posts([post], parser, parser.cfg, NOW, {}, db=db)
    assert pairs[0][1].category == "kitchen" and messages.calls == []
    db.close()


def test_product_root_survives_row_conversion_and_existing_product_calls(tmp_path, monkeypatch):
    db = DB(tmp_path / "state.db")
    product = dict(asin="B000000001", title="Face Cream", rootCategory=101,
                   stats={"current": _wh_row(1)["current"]})
    row = _product_row(product)
    post = KeepaDealsAdapter._map_warehouse_row(row, SRC, NOW,
                                               category_names={"101": "Beauty & Personal Care"})
    assert post.structured["category"] == "beauty"
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(
        200, json={"products": [product]}, request=httpx.Request("GET", "https://api.keepa.com/product")))
    verify.keepa_products("offline", [product["asin"]], 1, KeepaBudget.load(db))
    assert cached_meta(db, ROOTS_META) == {product["asin"]: "101"}
    db.close()


class IndexedMessages:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        reply = next(self.replies)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(reply))],
                               stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1000, output_tokens=100))


def test_partial_answers_keep_valid_indexes_retry_only_missing():
    messages = IndexedMessages([
        [{"i": 2, "category": "auto"}, {"i": 0, "category": "fashion"},
         {"i": 1, "category": "bogus"}, {"i": True, "category": "home"},
         {"i": 50, "category": "home"}, "junk", {"i": 0, "category": "home"}],
        [{"i": 1, "category": "beauty"}],
    ])
    parser, _ = parser_for(messages)
    assert categorize_titles(parser, ["Luggage", "Face Cream", "Axle", "Mystery"]) == [
        "fashion", "beauty", "auto", "other"]
    assert json.loads(messages.calls[1]["messages"][0]["content"]) == [
        {"i": 1, "title": "Face Cream"}, {"i": 3, "title": "Mystery"}]
    assert parser.spend_by_kind["category"]["calls"] == 2
    assert CATEGORY_RULES in messages.calls[0]["system"]
    assert CATEGORY_RULES in SYSTEM_PROMPT
    for rule in ("Luggage", "sunglasses/eyewear", "car parts", "skin care", "oral care",
                 "hearing-aid batteries", "blinds", "pools", "binoculars"):
        assert rule in CATEGORY_RULES


def test_retry_cap_keeps_accepted_categories_in_parse():
    messages = IndexedMessages([[{"i": 0, "category": "beauty"}]])
    parser, _ = parser_for(messages, spend_cap_usd_per_run=.00001)
    posts = [KeepaDealsAdapter._map_row(_amazon_row(i), SRC, NOW) for i in range(2)]
    pairs, stats = parse_posts(posts, parser, parser.cfg, NOW, {})
    assert [d.category for _, d in pairs] == ["beauty", "other"]
    assert stats["aborted_spend_cap"] and len(messages.calls) == 1


def snapshot(db):
    return {row["dedupe_key"]: dict(row) for row in db.conn.execute("SELECT * FROM deals")}


def test_recategorization_resumes_cap_is_idempotent_and_changes_only_category(tmp_path, caplog):
    db = DB(tmp_path / "state.db")
    for i in range(62):
        insert(db, deal(f"item-{i:02}", item=f"Private item {i}", asin="", category="other",
                        canonical_url=f"https://store.example/item-{i}"))
    insert(db, deal("root", category="electronics"))
    insert(db, deal("dead", category="other", status="dead"))
    db.meta_set(NAMES_META, json.dumps({"101": "Health & Household"}))
    db.meta_set(ROOTS_META, json.dumps({"B000000001": "101"}))
    original = snapshot(db)
    messages = IndexedMessages([[{"i": 0, "category": "fashion"}]])
    parser, _ = parser_for(messages, spend_cap_usd_per_run=.00001)
    with caplog.at_level("INFO"):
        recategorize_live_deals(db, parser)
    assert db.meta_get("recategorized_v2") != "1"
    assert db.get_deal("item-00")["category"] == "fashion"
    assert db.get_deal("root")["category"] == "health"
    parser, messages = parser_for()
    recategorize_live_deals(db, parser)
    assert [len(json.loads(c["messages"][0]["content"])) for c in messages.calls] == [60, 1]
    assert all("Private item 0" != e["title"] for c in messages.calls
               for e in json.loads(c["messages"][0]["content"]))
    assert db.meta_get("recategorized_v2") == "1"
    finished = snapshot(db)
    for key, row in original.items():
        old_payload = json.loads(row.pop("payload"))
        new_row = dict(finished[key])
        new_payload = json.loads(new_row.pop("payload"))
        assert row == new_row
        if key == "dead":
            assert old_payload == new_payload
        else:
            old_payload.pop("category")
            new_payload.pop("category")
            assert old_payload == new_payload
    before_calls = len(messages.calls)
    recategorize_live_deals(db, parser)
    assert snapshot(db) == finished and len(messages.calls) == before_calls
    assert "before=" in caplog.text and "after=" in caplog.text
    assert "Private item" not in caplog.text
    db.close()


def test_recategorization_waits_for_parser_or_deadline(tmp_path):
    db = DB(tmp_path / "state.db")
    insert(db, deal(category="other"))
    original = snapshot(db)
    recategorize_live_deals(db, None)
    parser, messages = parser_for()
    recategorize_live_deals(db, parser, deadline=time.monotonic() - 1)
    assert db.meta_get("recategorized_v2") != "1"
    assert snapshot(db) == original and messages.calls == []
    db.close()


def test_recategorization_uses_cached_root_from_url_without_a_parser(tmp_path):
    db = DB(tmp_path / "state.db")
    insert(db, deal(asin="", category="kitchen"))
    db.meta_set(NAMES_META, json.dumps({"101": "Home & Kitchen"}))
    db.meta_set(ROOTS_META, json.dumps({"B000000001": "101"}))
    original = snapshot(db)
    recategorize_live_deals(db, None)
    assert snapshot(db) == original
    assert db.meta_get("recategorized_v2") == "1"
    db.close()


def focus_fetch(db, monkeypatch, *, tokens=500, categories=None, now=NOW, cached=False):
    cfg = load_config()
    cfg.keepa_sweep_enabled = False
    if categories is not None:
        cfg.keepa_focus_categories = categories
    if cached:
        db.meta_set(NAMES_META, json.dumps({"101": "Beauty & Personal Care", "102": "Health & Household"}))
        db.meta_set("keepa_root_categories_loaded", "1")
    if tokens is not None:
        budget = KeepaBudget(tokens_left=tokens, refill_rate=0, seen_at=now)
        budget.save(db)
    calls, sleeps = [], []
    remaining = tokens if tokens is not None else 500
    def handler(request):
        nonlocal remaining
        calls.append(request)
        if request.url.path == "/category":
            assert request.url.params["category"] == "0"
            remaining -= 1
            body = {"categories": {"101": {"catId": 101, "name": "Beauty & Personal Care", "parent": 0},
                                    "102": {"catId": 102, "name": "Health & Household", "parent": 0}}}
        else:
            assert request.url.path == "/deal", "no extra product queries"
            sel = json.loads(request.url.params["selection"])
            remaining -= 5
            if "includeCategories" in sel:
                assert remaining >= 300
                cid = sel["includeCategories"][0]
                body = {"deals": {"dr": [dict(_amazon_row(cid), rootCat=cid)]}}
            else:
                body = {"deals": {"dr": []}}
        body.update(tokensLeft=remaining, refillRate=0)
        return httpx.Response(200, json=body)
    monkeypatch.setenv("KEEPA_KEY", "offline")
    adapter = KeepaDealsAdapter(http_client=httpx.Client(transport=httpx.MockTransport(handler)),
                                sleep_fn=sleeps.append)
    posts = adapter.fetch_posts(SRC, None, db, cfg, now, None)
    assert sleeps == [cfg.min_host_interval_s] * (len(calls) - 1)
    return calls, posts


@pytest.mark.parametrize("tokens", [500, None])
def test_focus_pages_discover_roots_by_name_and_share_floors(tmp_path, monkeypatch, tokens):
    db = DB(tmp_path / "state.db")
    calls, posts = focus_fetch(db, monkeypatch, tokens=tokens)
    assert len([c for c in calls if c.url.path == "/category"]) == 1
    sels = [json.loads(c.url.params["selection"]) for c in calls if c.url.path == "/deal"]
    focus = [s for s in sels if "includeCategories" in s]
    general = next(s for s in sels if s["priceTypes"] == [0] and "includeCategories" not in s)
    assert [s["includeCategories"] for s in focus] == [[101], [102]]
    for sel in focus:
        assert {k: v for k, v in sel.items() if k != "includeCategories"} == general
    assert sorted(p.structured["category"] for p in posts) == ["beauty", "health"]
    assert len(cached_meta(db, ROOTS_META)) == 2
    second, _ = focus_fetch(db, monkeypatch, now=NOW + timedelta(minutes=10))
    assert all(c.url.path != "/category" for c in second)
    db.close()


@pytest.mark.parametrize("tokens,categories,cached", [
    (304, None, True), (500, [], False), (300, None, False),
])
def test_focus_skips_short_tokens_or_empty_config(tmp_path, monkeypatch, tokens, categories, cached):
    db = DB(tmp_path / "state.db")
    calls, posts = focus_fetch(db, monkeypatch, tokens=tokens, categories=categories, cached=cached)
    assert not posts
    assert all("includeCategories" not in json.loads(c.url.params["selection"])
               for c in calls if c.url.path == "/deal")
    assert all(c.url.path != "/category" for c in calls)
    db.close()


def test_focus_config_empty_and_named_defaults(tmp_path):
    assert load_config().keepa_focus_categories == ["Beauty & Personal Care", "Health & Household"]
    path = tmp_path / "config.yaml"
    path.write_text("keepa:\n  focus_categories: []\n")
    assert load_config(path).keepa_focus_categories == []


def test_cached_focus_roots_wait_for_first_token_reading(tmp_path, monkeypatch):
    db = DB(tmp_path / "state.db")
    calls, posts = focus_fetch(db, monkeypatch, tokens=None, cached=True)
    assert all(c.url.path != "/category" for c in calls)
    focus = [json.loads(c.url.params["selection"]) for c in calls
             if "includeCategories" in json.loads(c.url.params["selection"])]
    assert [s["includeCategories"] for s in focus] == [[101], [102]]
    assert sorted(p.structured["category"] for p in posts) == ["beauty", "health"]
    db.close()


def test_remembered_roots_stay_bounded():
    from dealradar import keepa_categories as kc

    class Meta:
        def __init__(self): self.v = {}
        def meta_get(self, k): return self.v.get(k)
        def meta_set(self, k, val): self.v[k] = val

    db = Meta()
    kc.remember_roots(db, [{"asin": f"B{i:09d}", "rootCat": 3760911} for i in range(kc.MAX_ROOTS + 50)])
    roots = kc.cached_meta(db, kc.ROOTS_META)
    assert len(roots) == kc.MAX_ROOTS and f"B{kc.MAX_ROOTS + 49:09d}" in roots and "B000000000" not in roots


def test_other_is_never_reused_for_a_resighted_asin(tmp_path):
    # A stored "other" must not file the same ASIN there again: it gets a title read.
    db = DB(tmp_path / "state.db")
    posts = [KeepaDealsAdapter._map_row(_amazon_row(0), SRC, NOW)]
    asin = posts[0].structured["asin"]
    insert(db, deal("seen", asin=asin, category="other"))
    messages = IndexedMessages([[{"i": 0, "category": "beauty"}]])
    parser, _ = parser_for(messages)
    pairs, _ = parse_posts(posts, parser, parser.cfg, NOW, {}, db=db)
    assert [d.category for _, d in pairs] == ["beauty"] and len(messages.calls) == 1
    db.close()


def test_recategorize_other_moves_live_other_deals_once_within_caps(tmp_path, caplog):
    from dealradar.parse.extractor import OTHER_TRIED_META, recategorize_other
    db = DB(tmp_path / "state.db")
    for i in range(3):
        insert(db, deal(f"o{i}", item=f"Title {i}", asin="", category="other",
                        canonical_url=f"https://store.example/{i}"))
    insert(db, deal("rooted", category="other"))           # cached Amazon root, no LLM
    insert(db, deal("kept", category="kitchen", asin=""))  # real category: untouched
    insert(db, deal("gone", category="other", status="dead", asin=""))
    db.meta_set(NAMES_META, json.dumps({"101": "Beauty & Personal Care"}))
    db.meta_set(ROOTS_META, json.dumps({"B000000001": "101"}))
    original = snapshot(db)
    # o0 -> fashion, o1 really is "other", o2 unanswered (retried next run).
    messages = IndexedMessages([[{"i": 0, "category": "fashion"}, {"i": 1, "category": "other"}], []])
    parser, _ = parser_for(messages)
    with caplog.at_level("INFO"):
        assert recategorize_other(db, parser, per_run=10) == 2
    cats = {k: db.get_deal(k)["category"] for k in ("o0", "o1", "o2", "rooted", "kept", "gone")}
    assert cats == {"o0": "fashion", "o1": "other", "o2": "other", "rooted": "beauty",
                    "kept": "kitchen", "gone": "other"}
    assert set(json.loads(db.meta_get(OTHER_TRIED_META))) == {"o1"}  # live "other" only
    asked = [e["title"] for c in messages.calls for e in json.loads(c["messages"][0]["content"])]
    assert "Title 0" in asked and "Title 1" in asked and "Title 2" in asked
    # Only category changed on the moved rows.
    after = snapshot(db)
    for key in ("o0", "rooted"):
        old, new = json.loads(original[key]["payload"]), json.loads(after[key]["payload"])
        old.pop("category"); new.pop("category")
        assert old == new
    assert snapshot(db)["gone"] == original["gone"] and "Title" not in caplog.text
    # Next run: only the unanswered title is asked again; o1 is never re-asked.
    messages = IndexedMessages([[{"i": 0, "category": "home"}]])
    parser, _ = parser_for(messages)
    assert recategorize_other(db, parser, per_run=10) == 1
    assert [e["title"] for c in messages.calls for e in json.loads(c["messages"][0]["content"])] == ["Title 2"]
    assert db.get_deal("o2")["category"] == "home"
    # Switch off, no parser, past deadline, or a spend cap: nothing asked.
    insert(db, deal("o9", item="Title 9", asin="", category="other", canonical_url="https://store.example/9"))
    for kwargs in ({"per_run": 0}, {"deadline": time.monotonic() - 1}):
        p, m = parser_for()
        recategorize_other(db, p, **kwargs)
        assert m.calls == []
    assert recategorize_other(db, None) == 0
    p, m = parser_for(IndexedMessages([[{"i": 0, "category": "home"}]]), spend_cap_usd_per_run=0.0)
    assert recategorize_other(db, p) == 0 and db.get_deal("o9")["category"] == "other"
    assert load_config().recategorize_other_per_run == 120
    db.close()
