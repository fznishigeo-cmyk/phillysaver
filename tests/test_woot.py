"""Offline tests: Woot offer scoring and selection (the All feed holds
thousands of offers; the deep used/refurb/open-box discounts must map first
and the unpriced or above-list junk never)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dealradar.config import AppConfig, load_config
from tests.http_helpers import api_transport
from dealradar.db import DB
from dealradar.fetch.woot import USED_MARKER, WootAdapter
from dealradar.models import SourceConfig

NOW = datetime(2026, 10, 11, 4, 0, 0, tzinfo=timezone.utc)
SRC = SourceConfig(name="woot", method="api",
                   url="https://developer.woot.com/feed/All", adapter="woot")


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("WOOT_API_KEY", "wkey")


@pytest.fixture()
def cfg() -> AppConfig:
    cfg = load_config()  # publish floors 40 (new) / 50 (used)
    cfg.woot_named_feeds = []  # single-feed cases; rotation has its own tests
    return cfg


def _offer(oid, sale, list_price=None, condition="New", **kw):
    item = {"OfferId": oid, "Title": f"Offer {oid}", "Site": "Electronics",
            "Url": f"https://electronics.woot.com/offers/{oid}",
            "SalePrice": sale, "Condition": condition, "IsSoldOut": False,
            "StartDate": "2026-10-10T05:00:00Z",
            "EndDate": "2026-10-12T04:59:59Z"}
    if list_price is not None:
        item["ListPrice"] = list_price
    item.update(kw)
    return item


#: A mixed feed: the deep used discounts sit at the END, behind junk.
MIXED = [
    _offer("new-shallow", 79.99, 99.99),                       # 20%: skip
    _offer("refurb-above-list", 449.99, 399.99, "Refurbished"),  # skip
    _offer("refurb-at-list", 299.99, 299.99, "Refurbished"),     # skip
    _offer("refurb-shallow", 89.99, 129.99, "Refurbished"),      # 31%: skip
    _offer("refurb-no-list", 19.99, None, "Refurbished"),        # skip
    _offer("refurb-zero-list", 19.99, 0, "Refurbished"),         # skip
    _offer("new-wide-range", {"Minimum": 9.99, "Maximum": 49.99},
           99.99),                                              # skip
    _offer("refurb-sold-out", 9.99, 199.99, "Refurbished",
           IsSoldOut=True),                                     # skip
    _offer("refurb-ended", 9.99, 199.99, "Refurbished",
           EndDate="2026-10-10T04:59:59Z"),                     # skip
    # Between the two cut-offs (new 35, used 45): a refurb at 40% is held
    # to the used floor although "new-mid" at the same 40% maps.
    _offer("refurb-mid", 59.99, 99.99, "Refurbished"),          # skip
    # Near the used cut-off, the conservative ends of ranges decide: the
    # HIGH sale end ($56: 44%) and the LOW list end ($100: 44%).
    _offer("refurb-sale-range", {"Minimum": 53, "Maximum": 56}, 100,
           "Refurbished"),                                      # skip
    _offer("refurb-list-range", 56, [100, 108], "Refurbished"),  # skip
    _offer("new-mid", 59.99, 99.99),                            # 40% new
    _offer("new-deep", 24.99, 99.99, "Brand New"),              # 75% new
    _offer("used-grade", [30.0, 31.0], [100, 100],
           "Used - Grade B"),                                   # 69% used
    _offer("refurb-deep", 39.99, 199.99,
           "Manufacturer Refurbished"),                         # 80% used
    _offer("openbox-deeper", {"Minimum": 20, "Maximum": 21},
           "$150.00", "New - Open Box"),                        # 86% used
]


def _adapter(payload, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        assert request.url.path == "/feed/All"
        return httpx.Response(200, json=payload)

    return WootAdapter(http_client=httpx.Client(
        transport=api_transport(handler)), sleep_fn=lambda _: None)


def test_deep_used_discounts_map_first_and_junk_never(cfg, caplog):
    with caplog.at_level("INFO", logger="dealradar.fetch.woot"):
        posts = _adapter({"Items": MIXED}).fetch_posts(
            SRC, None, None, cfg, NOW, None)
    assert [p.uid for p in posts] == [
        "openbox-deeper", "refurb-deep", "used-grade",   # used, deepest first
        "new-deep", "new-mid",                           # then new
    ]
    assert ("woot: 17 raw offers -> 5 scored (3 used, 2 new) -> 5 mapped "
            "(5 unseen, 0 more held for later runs)" in caplog.text)
    # Prices stay verbatim for the evidence check, whatever their shape.
    openbox = posts[0]
    assert "Sale price: $20.00 - $21.00" in openbox.text
    assert "List price: $150.00" in openbox.text
    assert "Condition: New - Open Box." in openbox.text
    assert "Sale price: $30.00 - $31.00" in posts[2].text


def test_floors_come_from_config(cfg):
    """Selection reads the publish floors (5 points of slack), never a
    hardcoded number: raise them and the near-misses drop out."""
    cfg.used_min_pct, cfg.min_pct = 85.0, 79.0   # ingest from 80 / 74
    posts = _adapter({"Items": MIXED}).fetch_posts(SRC, None, None, cfg,
                                                   NOW, None)
    assert [p.uid for p in posts] == ["openbox-deeper", "refurb-deep",
                                      "new-deep"]


def test_cap_is_a_config_knob_and_used_fill_it_first(cfg):
    assert cfg.woot_max_items_per_run == 300
    cfg.woot_max_items_per_run = 2
    posts = _adapter({"Items": MIXED}).fetch_posts(SRC, None, None, cfg,
                                                   NOW, None)
    assert [p.uid for p in posts] == ["openbox-deeper", "refurb-deep"]


def test_deep_offers_behind_thousands_of_shallow_ones_are_found(cfg):
    """Live wart: the adapter mapped the FIRST 150 offers in feed order and
    Woot's real deep discounts were never selected."""
    feed = [_offer(f"meh-{i}", 89.99, 99.99) for i in range(4990)]
    feed += [_offer(f"refurb-{i}", 29.99 + i, 199.99, "Refurbished")
             for i in range(10)]
    calls: list[str] = []
    posts = _adapter({"Items": feed}, calls).fetch_posts(SRC, None, None, cfg,
                                                         NOW, None)
    assert [p.uid for p in posts] == [f"refurb-{i}" for i in range(10)]
    assert len(calls) == 1  # one /feed/All GET per run, whatever its size


def test_one_get_per_run(cfg):
    calls: list[str] = []
    adapter = _adapter({"Items": MIXED}, calls)
    for n in range(3):
        adapter.fetch_posts(SRC, None, None, cfg, NOW + timedelta(hours=n),
                            None)
    assert len(calls) == 3


@pytest.mark.parametrize("fields,expected", [
    ({"Photo": "https://img.woot.com/feed.jpg"}, "feed.jpg"),
    ({"Photo": {"Url": "https://img.woot.com/photo.jpg"}}, "photo.jpg"),
    ({"Photos": [{"Url": "https://img.woot.com/offer.jpg",
                  "Width": 1000, "Height": 1000}]}, "offer.jpg"),
    ({"Photo": None, "Photos": [None, {}, {"Url": "bad"},
                                 {"Url": "https://img.woot.com/valid.jpg"}]},
     "valid.jpg"),
    ({"Photos": ["https://img.woot.com/string.jpg"]}, "string.jpg"),
    ({"Items": [{"Photos": []},
                {"Photos": [{"Url": "https://img.woot.com/variant.jpg"}]}]},
     "variant.jpg"),
    ({"Photo": "https://img.woot.com/feed.jpg",
      "Photos": [{"Url": "https://img.woot.com/offer.jpg"}]}, "feed.jpg"),
    ({"Photos": [{"Url": "https://img.woot.com/offer.jpg"}],
      "Items": [{"Photos": [{"Url": "https://img.woot.com/variant.jpg"}]}]},
     "offer.jpg"),
    ({"Photo": "javascript:bad", "Photos": [None, {}], "Items": [None]}, ""),
])
def test_feed_and_offer_photo_shapes(cfg, fields, expected):
    # API schema: https://developer.woot.com/#schemaoffer (Photos[].Url,
    # including OfferItem photos); the feed itself uses Photo.
    posts = _adapter([_offer("photo", 20, 100, **fields)]).fetch_posts(
        SRC, None, None, cfg, NOW, None)
    assert len(posts) == 1
    assert posts[0].image_url == ("https://img.woot.com/" + expected if expected else "")


def test_unchanged_offer_hashes_identically_across_runs(cfg):
    adapter = _adapter({"Items": MIXED})
    first = {p.uid: p.content_hash
             for p in adapter.fetch_posts(SRC, None, None, cfg, NOW, None)}
    later = {p.uid: p.content_hash
             for p in adapter.fetch_posts(SRC, None, None, cfg,
                                          NOW + timedelta(minutes=10), None)}
    assert first and first == later
    # A real change (a new sale price) is a new post.
    changed = [dict(o) for o in MIXED]
    changed[-1]["SalePrice"] = 19.0
    again = {p.uid: p.content_hash
             for p in _adapter({"Items": changed}).fetch_posts(
                 SRC, None, None, cfg, NOW, None)}
    assert again["openbox-deeper"] != first["openbox-deeper"]
    assert again["refurb-deep"] == first["refurb-deep"]


def test_unseen_posts_are_capped_per_run_and_the_rest_arrive_later(
        cfg, tmp_path):
    """The first-run burst bound: at most max_new_items_per_run unseen
    posts per run, deepest first; posts the pipeline already handled ride
    free, and the rest of the selection arrives over the next runs."""
    cfg.woot_max_new_items_per_run = 20
    db = DB(tmp_path / "state.db")
    feed = [_offer(f"refurb-{i:02d}", 20.0 + i, 199.99, "Refurbished")
            for i in range(50)]
    adapter = _adapter({"Items": feed})
    sizes, unseen = [], []
    for n in range(4):
        posts = adapter.fetch_posts(SRC, None, db, cfg,
                                    NOW + timedelta(minutes=10 * n), None)
        new = [p for p in posts if not db.post_is_known(p.content_hash)]
        sizes.append(len(posts))
        unseen.append([p.uid for p in new])
        for p in new:  # what the pipeline records once it parsed them
            db.record_post(p, NOW)
    assert sizes == [20, 40, 50, 50]
    assert unseen == [[f"refurb-{i:02d}" for i in range(a, b)]
                      for a, b in ((0, 20), (20, 40), (40, 50), (50, 50))]
    db.close()


def test_used_posts_say_so_in_words_the_extractor_tags(cfg):
    """Woot's own labels ("Grade B", "New - Floor Model", none at all) may
    not read as used to the extractor; an untagged post would publish
    against the lower new floor."""
    feed = [_offer("grade-b", 54.0, 100.0, "Grade B"),
            _offer("unlabeled", 40.0, 100.0, ""),
            _offer("floor-model", 40.0, 100.0, "New - Floor Model"),
            _offer("plain-new", 40.0, 100.0, "New")]
    posts = {p.uid: p for p in _adapter({"Items": feed}).fetch_posts(
        SRC, None, None, cfg, NOW, None)}
    assert set(posts) == {"grade-b", "unlabeled", "floor-model", "plain-new"}
    assert "Condition: Grade B." in posts["grade-b"].text
    for uid in ("grade-b", "unlabeled", "floor-model"):
        assert USED_MARKER in posts[uid].text
    assert "used" in USED_MARKER and "refurbished" in USED_MARKER
    assert USED_MARKER not in posts["plain-new"].text
    # The unlabeled offer was selected against the used floor (45).
    cheap = _offer("unlabeled-42", 58.0, 100.0, "")
    assert _adapter({"Items": [cheap]}).fetch_posts(
        SRC, None, None, cfg, NOW, None) == []


@pytest.mark.parametrize("condition,used", [
    ("New", False), ("Brand New", False), ("", True),
    ("New - Floor Model", True), ("New (Display Model)", True),
    ("New - Damaged Box", True),
    ("Refurbished", True), ("Manufacturer Reconditioned", True),
    ("Renewed", True), ("Used - Like New", True), ("Open Box", True),
    ("New (Open Box)", True), ("Pre-Owned", True), ("Grade A", True),
    ("Like New", True), ("Scratch & Dent", True),
])
def test_condition_classification(condition, used):
    from dealradar.fetch.woot import _is_used
    assert _is_used(condition) is used
