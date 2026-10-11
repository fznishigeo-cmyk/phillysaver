"""Offline tests: Woot offer scoring and selection (the All feed holds
thousands of offers; the deep used/refurb/open-box discounts must map first
and the unpriced or above-list junk never)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dealradar.config import AppConfig, load_config
from dealradar.fetch.woot import WootAdapter
from dealradar.models import SourceConfig

NOW = datetime(2026, 10, 11, 4, 0, 0, tzinfo=timezone.utc)
SRC = SourceConfig(name="woot", method="api",
                   url="https://developer.woot.com/feed/All", adapter="woot")


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("WOOT_API_KEY", "wkey")


@pytest.fixture()
def cfg() -> AppConfig:
    return load_config()  # publish floors 40 (new) / 50 (used)


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
        transport=httpx.MockTransport(handler)))


def test_deep_used_discounts_map_first_and_junk_never(cfg, caplog):
    with caplog.at_level("INFO", logger="dealradar.fetch.woot"):
        posts = _adapter({"Items": MIXED}).fetch_posts(
            SRC, None, None, cfg, NOW, None)
    assert [p.uid for p in posts] == [
        "openbox-deeper", "refurb-deep", "used-grade",   # used, deepest first
        "new-deep", "new-mid",                           # then new
    ]
    assert ("woot: 14 raw offers -> 5 scored (3 used, 2 new) -> 5 mapped"
            in caplog.text)
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


@pytest.mark.parametrize("condition,used", [
    ("New", False), ("Brand New", False), ("", False),
    ("Refurbished", True), ("Manufacturer Reconditioned", True),
    ("Renewed", True), ("Used - Like New", True), ("Open Box", True),
    ("New (Open Box)", True), ("Pre-Owned", True), ("Grade A", True),
    ("Like New", True), ("Scratch & Dent", True),
])
def test_condition_classification(condition, used):
    from dealradar.fetch.woot import _is_used
    assert _is_used(condition) is used
