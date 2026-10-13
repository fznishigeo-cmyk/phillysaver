"""Earning Get deal destinations, pick scores and shared social gating, offline."""

import hashlib
from copy import deepcopy

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import meta, pinterest, site, social, telegram_pub
from dealradar.publish.site import _serialize_deal, earning_link, link_mix, render_site
from dealradar.rank import pick_score, top_picks
from tests.test_front_page import pick_deal
from tests.test_pinterest import candidate, published
from tests.test_publish_site import NOW, make_deal
from tests.test_sovrn_links import KEY

TAG = "our-store-20"
TRACKERS = ["cc.dealsofamerica.com", "go.dealmoon.com", "slickdeals.net",
            "links.southernsavers.com", "custom-tracker.example"]


@pytest.fixture
def db(tmp_path):
    database = DB(tmp_path / "state.db")
    yield database
    database.close()


@pytest.mark.parametrize("url,bucket,earns", [
    (f"https://{host}/dp/B012345678?tag={TAG}", "amazon_tagged", True)
    for host in ("amazon.com", "www.amazon.com", "smile.amazon.com")
] + [
    ("https://amazon.com/dp/B012345678", "amazon_untagged", False),
    ("https://amazon.com/dp/B012345678?tag=someone-20", "amazon_untagged", False),
    (f"http://amazon.com/dp/B012345678?tag={TAG}", "amazon_untagged", False),
    *[(f"https://{host}/short{query}", "amazon_short", earns)
      for host in ("amzn.to", "a.co", "link.amazon")
      for query, earns in (("", False), ("?tag=someone-20", False), (f"?tag={TAG}", True))],
    ("https://redirect.viglink.com?key=test&u=test", "sovrn", True),
    ("https://sovrn.co/ours", "sovrn", True),
    ("http://sovrn.co/ours", "sovrn", False),
    ("https://walmart.com/ip/123", "other_store", False),
    (f"https://amazon.com.example/product?tag={TAG}", "other_store", False),
    *[(f"https://{host}/click", "other_store", False)
      for host in TRACKERS + ["www.cc.dealsofamerica.com", "go.slickdeals.net"]],
    ("", "none", False), ("/relative", "none", False), ("https://[invalid", "none", False),
])
def test_shared_bucket_earning_definition(url, bucket, earns):
    deal = {"status": "alive", "url": url}
    assert earning_link(deal, TAG, competitor_tracker_hosts=TRACKERS) is earns
    mix = link_mix([deal], TAG, competitor_tracker_hosts=TRACKERS)
    assert mix[bucket] == 1
    assert mix["earning"] == int(earns) and mix["not_earning"] == int(not earns)


def test_source_posts_and_totals_ignore_locked_and_ended():
    post = "https://source.example/post"
    deals = [{"status": "alive", "url": post, "credits": [{"url": post}]},
             {"status": "alive", "url": "https://sovrn.co/ours"},
             {"status": "alive", "url": "https://sovrn.co/ours", "locked": True},
             {"status": "dead", "url": "https://sovrn.co/ours"},
             {"status": "alive", "url": ""}]
    before = deepcopy(deals)
    assert not earning_link(deals[0], TAG)
    mix = link_mix(deals, TAG)
    assert mix["source_post"] == mix["earning"] == 1
    assert mix["not_earning"] == 2
    assert deals == before


@pytest.mark.parametrize("changes,options,earns", [
    ({"canonical_url": "https://amazon.com/dp/B012345678"}, {}, True),
    ({"canonical_url": "https://amazon.com/dp/B012345678"}, {"amazon_tag": ""}, False),
    ({"codes": []}, {"sovrn_enabled": True, "sovrn_key": KEY}, True),
    ({}, {"sovrn_enabled": True, "sovrn_key": KEY}, True),
    ({"stack_steps": ["Clip the coupon", "Use code SAVE10"]},
     {"sovrn_enabled": True, "sovrn_key": KEY}, False),
    ({"canonical_url": ""}, {}, False),
    ({"codes": []}, {}, False),
    ({"codes": []}, {"overrides": {"walmart.com/12345": "https://sovrn.co/ours"}}, True),
    *[({"canonical_url": f"https://{host}/click", "codes": []},
       {"sovrn_enabled": True, "sovrn_key": KEY}, False) for host in TRACKERS],
])
def test_serialized_earns_uses_final_get_deal_url(changes, options, earns):
    args = {"amazon_tag": TAG, "competitor_tracker_hosts": TRACKERS, **options}
    deal = make_deal(**changes)
    before = deepcopy(deal)
    out = _serialize_deal(deal, NOW, **args)
    assert out["earns"] is earns
    assert earning_link(out, args["amazon_tag"], competitor_tracker_hosts=TRACKERS) is earns
    assert deal == before
    if any(host in out["url"] for host in TRACKERS):
        assert not out["url"].startswith("https://redirect.viglink.com")


@pytest.mark.parametrize("factor", [0.5, 0.2, 1.0])
def test_equal_earning_pick_ranks_first_and_both_stay_on_wall(tmp_path, db, factor):
    cfg = load_config()
    cfg.front_page["top_picks"]["no_earn_factor"] = factor
    earning = pick_deal(dedupe_key="earning", canonical_url="https://amazon.com/dp/B012345678")
    other = dict(earning, dedupe_key="other", canonical_url="https://amazon.com/dp/B012345679")
    cfg.affiliate_amazon_tag = ""
    base = pick_score(other, cfg, NOW)
    cfg.affiliate_amazon_tag = TAG
    # An untagged short link stays non-earning even with a configured Store ID.
    other["canonical_url"] = "https://amzn.to/short"
    assert pick_score(other, cfg, NOW) == pytest.approx(base)
    assert pick_score(other, cfg, NOW) == pytest.approx(pick_score(earning, cfg, NOW) * factor)
    scores = [(d, pick_score(d, cfg, NOW)) for d in (other, earning)]
    picks = top_picks(scores, cfg.front_page)
    assert [d["dedupe_key"] for d in picks] == (["other", "earning"] if factor == 1 else ["earning", "other"])
    result = render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=[other, earning])
    rows = result["full_payload"]["deals"]
    assert len(rows) == 2 and [d["earns"] for d in rows] == [False, True]
    assert rows[0]["ps"] == round(pick_score(other, cfg, NOW), 1)
    assert result["link_mix"]["earning"] == result["link_mix"]["not_earning"] == 1


def test_config_defaults_and_tracker_hosts(tmp_path):
    assert AppConfig().social_require_earning_link is True
    cfg = load_config()
    assert cfg.social_require_earning_link is True
    assert cfg.affiliate_competitor_tracker_hosts == TRACKERS[:-1]
    path = tmp_path / "config.yaml"
    path.write_text("social:\n  require_earning_link: false\naffiliate:\n"
                    "  competitor_tracker_hosts: ['WWW.Custom-Tracker.Example']\n")
    cfg = load_config(path)
    assert cfg.social_require_earning_link is False
    assert cfg.affiliate_competitor_tracker_hosts == ["custom-tracker.example"]


@pytest.mark.parametrize("require", [True, False])
def test_all_social_channels_share_earning_gate(tmp_path, db, require):
    cfg = load_config()
    cfg.site_dir = str(tmp_path / "site")
    cfg.social_require_earning_link = require
    rows = [candidate("non-earning", 1, earns=False), candidate("earning", 2),
            candidate("missing", 3)]
    rows[-1].pop("earns")
    published(db, cfg, rows)
    for module in (pinterest, meta, telegram_pub):
        assert module._candidates is social._candidates
        actual = [d["dedupe_key"] for d, _path in module._candidates(db, cfg, {})]
        assert actual == (["earning"] if require else ["non-earning", "earning", "missing"])


@pytest.mark.parametrize("open_access,expected", [
    (False, "9181587e34af8a96966758c968e4bcf2311e853b0152e550f40f0fe74784aa15"),
    (True, "7a0dc4d5209943b5cd787e986b97b9a7e88f27f7d096089d52bf6adfe770e0d3"),
])
def test_deals_json_task049_byte_hash_baseline(tmp_path, db, monkeypatch, open_access, expected):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    monkeypatch.setattr(site, "site_template_version", lambda: "baseline")
    cfg = AppConfig(paywall_enabled=True, paywall_open_access=open_access,
                    paywall_teaser_count=1, affiliate_amazon_tag=TAG,
                    affiliate_competitor_tracker_hosts=TRACKERS)
    deals = [make_deal(dedupe_key="earning", canonical_url="https://amazon.com/dp/B012345678"),
             make_deal(dedupe_key="non-earning", canonical_url="https://cc.dealsofamerica.com/click")]
    render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=deals)
    assert hashlib.sha256((tmp_path / "site/deals.json").read_bytes()).hexdigest() == expected


@pytest.mark.parametrize("condition", ["used", "open_box", "refurbished"])
def test_social_channels_never_post_used_items(tmp_path, db, condition):
    """A one-unit used item is gone after the first click (operator, 2026-10-13)."""
    cfg = load_config()
    cfg.site_dir = str(tmp_path / "site")
    published(db, cfg, [candidate("used-one", 1, conditions=[condition]),
                        candidate("new-one", 2, conditions=["online_only"])])
    for module in (pinterest, meta, telegram_pub):
        assert [d["dedupe_key"] for d, _ in module._candidates(db, cfg, {})] == ["new-one"]
    cfg.social_exclude_conditions = []
    assert [d["dedupe_key"] for d, _ in social._candidates(db, cfg, {})] == ["used-one", "new-one"]
