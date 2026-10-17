"""Render-local link decisions, compared with the pre-edit link decisions."""

import base64
import gzip
import json
from dataclasses import FrozenInstanceError
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import site
from dealradar.publish.credits import link_policy
from dealradar.publish.link_facts import LinkPolicy, derive_link_facts
from dealradar.rank import pick_score
from dealradar.util import iso
from tests.test_e2e_offline import env
from tests.test_publish_site import make_deal

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
EXPECTED = Path(__file__).with_name("fixtures") / "publish_link_facts.json"
LINK_FIELDS = ("url", "earns", "needs_steps", "credits")
MODES = ("support", "open", "closed", "unpaywalled")
TAG = "our-store-20"
KEY = "0123456789abcdef0123456789abcdef"
POST = "https://slickdeals.net/f/1"


def config(mode):
    cfg = load_config()
    cfg.keepa_click_price_rule = "off"  # link fixtures recorded before task 275
    cfg.front_page["used_tab_only"] = False  # recorded before task 274
    cfg.front_page.pop("demote_words", None)  # recorded before task 267b
    cfg.front_page["appeal"]["want"]["enabled"] = False  # recorded before task 273
    cfg.front_page["appeal"]["season"] = []
    cfg.support_enabled = mode == "support"
    cfg.paywall_enabled = mode != "unpaywalled"
    cfg.paywall_open_access = mode == "open"
    cfg.paywall_teaser_count = 8
    cfg.affiliate_amazon_tag = TAG
    cfg.affiliate_sovrn_key = KEY
    cfg.affiliate_sovrn_enabled = True
    cfg.affiliate_link_overrides = {"walmart.com/override": "https://sovrn.co/ours"}
    cfg.seo_indexnow_key = KEY
    cfg.pwa_enabled = mode in {"support", "open"}
    return cfg


def facts(n=36, *, unique=True):
    rows = []
    for i in range(n):
        ident = str(i) if unique else str(i % 12)
        variant = i % 12
        changes = [
            dict(canonical_url=f"https://amazon.com/dp/B{i:09d}", store="Amazon"),
            dict(canonical_url=f"https://target.com/p/{ident}?size=XL&utm_source=partner"),
            dict(canonical_url=""),
            dict(canonical_url=f"https://walmart.com/ip/{ident}", codes=["SAVE10"],
                 stack_steps=["Use code SAVE10"]),
            dict(canonical_url=f"https://walmart.com/ip/{ident}", codes=["SAVE10"],
                 stack_steps=["Clip the coupon", "Use code SAVE10"]),
            dict(canonical_url=f"https://amazon.com/dp/B{i:09d}", store="Elsewhere",
                 codes=["SAVE10"], stack_steps=["Clip the coupon", "Use code SAVE10"]),
            dict(canonical_url=f"https://ebay.com/itm/{ident}?var=55&campid=1111111111", store="eBay"),
            dict(canonical_url=f"https://walmart.com/ip/{ident}",
                 link_as_posted=f"https://cc.dealsofamerica.com/click/{ident}"),
            dict(canonical_url="https://walmart.com/ip/override", codes=["SAVE10"]),
            dict(canonical_url=f"https://amzn.to/{ident}"),
            dict(canonical_url=f"http://target.com/p/{ident}"),
            dict(canonical_url=f"https://macys.com/p/{ident}", conditions=["used"]),
        ][variant]
        rows.append(make_deal(**{
            "dedupe_key": f"link-facts-{i}", "item": f"Acme kitchen kettle {i}",
            "brand": f"Brand {i % 9}", "category": ("home", "kitchen", "beauty")[i % 3],
            "image_url": "https://img.example/kettle.jpg" if i % 17 else "",
            "codes": [], "conditions": [], "first_seen_at": iso(NOW - timedelta(hours=2)),
            "price_rechecked_at": iso(NOW), "price_published_at": iso(NOW),
            **changes,
        }))
    return rows


def seed(db, rows):
    for row in rows:
        db.insert_deal(dedupe_key=row["dedupe_key"], store=row["store"], item=row["item"],
                       normalized_item=row["item"].lower(), canonical_url=row["canonical_url"],
                       payload=row, final_price=row["final_price"],
                       pct_at_checkout=row["pct_at_checkout"], abs_savings=row["abs_savings"],
                       online_only="true", expires_at="", now=NOW)


def capture(root, cfg, rows):
    with_db = DB(root / "state.db")
    try:
        seed(with_db, rows)
        result = site.render_site(with_db, cfg, NOW, out_dir=root / "site", deals=deepcopy(rows),
                                  last_run={"run_key": "task225"})
        files = {str(p.relative_to(root)): base64.b64encode(p.read_bytes()).decode()
                 for p in sorted(root.rglob("*")) if p.is_file() and not p.name.startswith("state.db")}
        return {"files": files, "payload": json.dumps(result["full_payload"], separators=(",", ":")),
                "link_mix": result["link_mix"],
                "selected": {key: with_db.meta_get(key) for key in (site.PICKS_META, site.WALL_META)},
                "sizes": {name: [len(data := base64.b64decode(value)),
                                  len(gzip.compress(data, mtime=0))] for name, value in files.items()}}
    finally:
        with_db.close()


def links(deals):
    return {d.get("dedupe_key") or d.get("key"): [d.get(f) for f in LINK_FIELDS] for d in deals}


@pytest.mark.parametrize("mode", MODES)
def test_link_decisions_match_pre_edit_and_reuse_changes_no_byte(tmp_path, monkeypatch, mode):
    # Link decisions captured on 755371a280d8 before the extraction: Get-deal URL,
    # earns, needs-steps and credits per deal, the link mix and the selected order.
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    expected = json.loads(EXPECTED.read_text())[mode]
    captured = {}
    for enabled in (False, True):
        cfg = config(mode)
        cfg.reuse_link_facts_enabled = enabled
        root = tmp_path / ("on" if enabled else "off")
        root.mkdir()
        captured[enabled] = capture(root, cfg, facts())
    off, on = captured[False], captured[True]
    public = json.loads(base64.b64decode(off["files"]["site/deals.json"]))
    assert links(public["deals"]) == expected["public"]
    assert links(json.loads(off["payload"])["deals"]) == expected["member"]
    assert off["link_mix"] == expected["link_mix"] and off["selected"] == expected["selected"]
    # Reuse is an optimisation only: every published byte is the same with it on.
    assert on == off


@pytest.mark.parametrize("text,expected", [("{}", False),
    ("publish: {reuse_link_facts: {}}", False),
    ("publish: {reuse_link_facts: {enabled: false}}", False),
    ("publish: {reuse_link_facts: {enabled: true}}", True)])
def test_config_is_explicit_and_ships_off(tmp_path, text, expected):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    assert load_config(path).reuse_link_facts_enabled is expected
    assert not AppConfig().reuse_link_facts_enabled
    assert not load_config().reuse_link_facts_enabled


@pytest.mark.parametrize("value", ['"false"', "null", "1", "[]"])
def test_config_rejects_non_boolean_enabled(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(f"publish: {{reuse_link_facts: {{enabled: {value}}}}}")
    with pytest.raises(ValueError, match=r"publish.reuse_link_facts.enabled"):
        load_config(path)


@pytest.mark.parametrize("value", ["false", "null", "[]"])
def test_config_rejects_non_mapping_policy(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(f"publish: {{reuse_link_facts: {value}}}")
    with pytest.raises(ValueError, match=r"publish.reuse_link_facts"):
        load_config(path)


DIRECT = "https://walmart.com/ip/12345"
AMAZON = "https://amazon.com/dp/B012345678"
TRACKER = "https://cc.dealsofamerica.com/click"
CAMP = "5339221823"
EBAY = ("https://ebay.com/itm/9?var=55&mkcid=1&mkrid=711-53200-19255-0&siteid=0"
        "&campid=5339221823&customid=fdaabef399c8fede&toolid=10001&mkevt=1")


@pytest.mark.parametrize("changes,options,url,earns", [
    ({}, {}, DIRECT, False),
    ({"canonical_url": ""}, {}, POST, False),
    ({"canonical_url": "", "sightings": []}, {}, "", False),
    ({"canonical_url": "", "sightings": [{"source_name": "old", "source_url": "relative"}]},
     {}, "relative", False),
    ({"codes": ["SAVE10"]}, {}, POST, False),
    ({"codes": ["SAVE10"]}, {"overrides": {"walmart.com/12345": "https://sovrn.co/ours"}},
     "https://sovrn.co/ours", True),
    ({"codes": ["SAVE10"], "stack_steps": ["Clip the coupon", "Use code SAVE10"]},
     {"overrides": {"walmart.com/12345": "https://sovrn.co/ours"}}, POST, False),
    ({"canonical_url": AMAZON, "codes": ["SAVE10"], "conditions": ["in_cart"]},
     {}, AMAZON + "?tag=" + TAG, True),
    ({"canonical_url": AMAZON, "store": "Not Amazon"}, {}, AMAZON + "?tag=" + TAG, True),
    ({"canonical_url": AMAZON, "codes": ["SAVE10"]}, {"amazon_tag": ""}, POST, False),
    ({"store": "Amazon"}, {}, DIRECT, False),
    ({"canonical_url": "https://amazon.com.evil/p/1"}, {}, "https://amazon.com.evil/p/1", False),
    ({"canonical_url": TRACKER}, {"overrides": {"cc.dealsofamerica.com/click": "https://sovrn.co/ours"}},
     TRACKER, False),
    ({"link_as_posted": TRACKER}, {"overrides": {"walmart.com/12345": "https://sovrn.co/ours"}},
     TRACKER, False),
    ({}, {"sovrn_enabled": True, "sovrn_key": KEY,
          "overrides": {"walmart.com/12345": "https://sovrn.co/override"}}, "https://sovrn.co/override", True),
    ({"canonical_url": "https://ebay.com/itm/9?var=55&campid=1111111111"},
     {"ebay_campid": CAMP}, EBAY, True),
    ({"canonical_url": "/relative"}, {}, "/relative", False),
    ({"canonical_url": "https://[invalid"}, {}, "https://[invalid", False),
    ({"canonical_url": "http://walmart.com/ip/12345"}, {}, "http://walmart.com/ip/12345", False),
])
def test_independent_final_routes(changes, options, url, earns):
    row = make_deal(**{"codes": [], "conditions": [], "canonical_url": DIRECT, **changes})
    args = {"amazon_tag": TAG, "competitor_tracker_hosts": ["cc.dealsofamerica.com"], **options}
    frozen = derive_link_facts(row, link_policy(**args))
    before = deepcopy(row)
    for links in (None, frozen):
        actual = site._serialize_deal(row, NOW, **args, links=links)
        assert (actual["url"], actual["earns"]) == (url, earns)
    assert row == before


def test_credits_are_ordered_deduplicated_and_cannot_alias():
    row = make_deal(codes=[], canonical_url="", sightings=[
        {"source_name": "keepa", "source_url": "https://keepa.com/1"},
        {"source_name": "hip2save", "source_url": "https://hip2save.com/first"},
        {"source_name": "hip2save", "source_url": "https://hip2save.com/email"},
        {"source_name": "hip2save_amazon", "source_url": "https://hip2save.com/other-feed"},
        {"source_name": "retired", "source_url": "https://retired.example/post"},
    ])
    links = derive_link_facts(row, link_policy())
    first = site._serialize_deal(row, NOW, links=links)
    assert first["credits"] == [dict(name="Amazon price drop", url=""),
                                dict(name="Hip2Save", url="https://hip2save.com/first"),
                                dict(name="retired", url="https://retired.example/post")]
    # Without a product URL, the original fallback is the first credit, even
    # when that credit has an empty URL (not the later usable post).
    assert first["url"] == ""
    first["credits"][1]["url"] = "https://mutated.example/"
    assert site._serialize_deal(row, NOW, links=links)["credits"][1]["url"] == "https://hip2save.com/first"
    with pytest.raises(FrozenInstanceError):
        links.url = "changed"
    with pytest.raises(TypeError):
        links.credits[0] = ("changed", "changed")


def test_resolved_policy_owns_immutable_copies_and_derivation_has_no_io(monkeypatch):
    overrides = {"walmart.com/12345": "https://sovrn.co/ours"}
    names = {"hip2save": "Partner"}
    trackers = ["cc.dealsofamerica.com"]
    policy = LinkPolicy(amazon_tag=TAG, overrides=overrides, source_names=names,
                        competitor_tracker_hosts=trackers)
    overrides.clear()
    names.clear()
    trackers.clear()
    with pytest.raises(TypeError):
        policy.overrides["new"] = "new"
    row = make_deal(codes=[], canonical_url=DIRECT)

    def forbidden(*args, **kwargs):
        raise AssertionError("pure link derivation must not read files")

    monkeypatch.setattr(Path, "read_text", forbidden)
    links = derive_link_facts(row, policy)
    assert (links.url, links.earns) == ("https://sovrn.co/ours", True)
    assert links.credits[-1][0] == "Partner"


@pytest.mark.parametrize("changes,expected", [
    ({"canonical_url": AMAZON}, AMAZON + "?tag=" + TAG),
    ({"link_as_posted": TRACKER}, TRACKER),
    ({"codes": ["NEW"]}, POST),
    ({"stack_steps": ["Clip the coupon"]}, POST),
    ({"conditions": ["in_cart"]}, POST),
    ({"rebates": ["$5 mail-in rebate"]}, POST),
    ({"sightings": [{"source_name": "custom", "source_url": "https://custom.example/post"}],
      "canonical_url": ""}, "https://custom.example/post"),
])
def test_changed_routing_input_with_same_key_is_derived_again(tmp_path, monkeypatch, changes, expected):
    cfg = config("open")
    cfg.reuse_link_facts_enabled = True
    cfg.affiliate_sovrn_enabled = False
    row = facts(1)[0]
    row.update(canonical_url=DIRECT, image_url="https://img.example/1.jpg")
    original = site.pick_score
    mutated = False

    def score_then_mutate(deal, *args, **kwargs):
        nonlocal mutated
        result = original(deal, *args, **kwargs)
        if not mutated:
            deal.update(changes)
            mutated = True
        return result

    monkeypatch.setattr(site, "pick_score", score_then_mutate)
    db = DB(":memory:")
    try:
        result = site.render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=[row])
        assert result["full_payload"]["deals"][0]["url"] == expected
    finally:
        db.close()


@pytest.mark.parametrize("changes", [
    {"flags": ["rebate_dependent"]}, {"codes": ["OTHER"]},
    {"stack_steps": ["Use code SAVE10", "Clip the coupon"]},
    {"conditions": ["multi_buy"]}, {"rebates": ["$5 mail-in rebate"]},
])
def test_simple_code_exception_is_rechecked_after_mutation(tmp_path, monkeypatch, changes):
    cfg = config("open")
    cfg.reuse_link_facts_enabled = True
    row = facts(2)[1]
    row.update(canonical_url=DIRECT, codes=["SAVE10"], stack_steps=["Use code SAVE10"])
    score = site.pick_score

    def changed(deal, *args, **kwargs):
        result = score(deal, *args, **kwargs)
        deal.update(changes)
        return result

    monkeypatch.setattr(site, "pick_score", changed)
    db = DB(":memory:")
    try:
        result = site.render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=[row])
        actual = result["full_payload"]["deals"][0]
        assert actual["url"] == POST and not actual["earns"]
    finally:
        db.close()


def test_credit_only_mutation_and_current_checkout_are_not_stale(tmp_path, monkeypatch):
    cfg = config("open")
    cfg.reuse_link_facts_enabled = True
    row = facts(2)[1]
    row["canonical_url"] = ""
    score = site.pick_score

    def changed(deal, *args, **kwargs):
        result = score(deal, *args, **kwargs)
        deal.update(sightings=[dict(source_name="New partner", source_url="https://new.example/post")],
                    final_price=12.0, first_seen_at=iso(NOW))
        return result

    monkeypatch.setattr(site, "pick_score", changed)
    db = DB(":memory:")
    try:
        result = site.render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=[row])
        actual = result["full_payload"]["deals"][0]
        assert actual["url"] == "https://new.example/post"
        assert actual["credits"] == [dict(name="New partner", url="https://new.example/post")]
        assert actual["age_hours"] == 0 and actual["final_price"] == 12.0
        assert actual["checkout"]["now"].startswith("Pay now: $12.00")
    finally:
        db.close()


@pytest.mark.parametrize("options,url", [
    ({"affiliate_amazon_tag": "second-20"}, AMAZON + "?tag=second-20"),
    ({"affiliate_sovrn_enabled": False}, DIRECT),
    ({"affiliate_sovrn_key": "a" * 32}, "https://redirect.viglink.com?key=" + "a" * 32),
    ({"affiliate_link_overrides": {"walmart.com/12345": "https://sovrn.co/new"}}, "https://sovrn.co/new"),
    ({"affiliate_competitor_tracker_hosts": ["walmart.com"]}, DIRECT),
    ({"affiliate_ebay_campaign_id": "1234567890"}, "campid=1234567890"),
])
def test_changed_policy_on_next_render_is_never_reused(tmp_path, options, url):
    cfg = config("open")
    cfg.reuse_link_facts_enabled = True
    row = facts(2)[1]
    row["canonical_url"] = (AMAZON if "affiliate_amazon_tag" in options else
                            "https://ebay.com/itm/9" if "affiliate_ebay_campaign_id" in options else DIRECT)
    db = DB(":memory:")
    try:
        first = site.render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=[row])
        for name, value in options.items():
            setattr(cfg, name, value)
        second = site.render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=[row])
        before = first["full_payload"]["deals"][0]["url"]
        after = second["full_payload"]["deals"][0]["url"]
        assert after != before and url in after
    finally:
        db.close()


def test_rank_outside_render_uses_current_policy_without_full_projection(monkeypatch):
    cfg = config("open")
    row = facts(2)[1]
    row["canonical_url"] = AMAZON
    baseline = pick_score(row, cfg, NOW)
    monkeypatch.setattr(site, "_serialize_deal", lambda *a, **k: pytest.fail("rank projected public JSON"))
    cfg.affiliate_amazon_tag = ""
    assert pick_score(row, cfg, NOW) == pytest.approx(baseline * cfg.front_page["top_picks"]["no_earn_factor"])
    cfg.affiliate_amazon_tag = TAG
    assert pick_score(row, cfg, NOW) == baseline


@pytest.mark.parametrize("enabled", [False, True])
def test_render_reuses_only_link_facts_and_keeps_both_score_passes(tmp_path, monkeypatch, enabled):
    from dealradar.publish import link_facts

    cfg = config("open")
    cfg.reuse_link_facts_enabled = enabled
    rows = facts(4)
    calls, projections, passes = [], [], []
    derive, project, score = link_facts.derive_link_facts, site._serialize_deal, site.pick_score

    def counted(deal, policy):
        calls.append(deal["dedupe_key"])
        return derive(deal, policy)

    def projected(deal, *args, **kwargs):
        projections.append(deal["dedupe_key"])
        return project(deal, *args, **kwargs)

    def scored(deal, *args, **kwargs):
        passes.append("earns" in deal)
        return score(deal, *args, **kwargs)

    monkeypatch.setattr(link_facts, "derive_link_facts", counted)
    # rank imports the helper at each call, so the same counter observes it.
    monkeypatch.setattr(site, "_serialize_deal", projected)
    monkeypatch.setattr(site, "pick_score", scored)
    db = DB(":memory:")
    try:
        result = site.render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=rows)
        assert len(projections) == len(rows)
        assert passes == [False] * len(rows) + [True] * len(rows)
        assert len(calls) == (len(rows) if enabled else 7)
        assert set(calls) == {d["dedupe_key"] for d in rows}
        assert not any(k.startswith("link_") or k in {"simple_code", "source_names"}
                       for row in result["full_payload"]["deals"] for k in row)
    finally:
        db.close()


def test_same_links_do_not_cache_confirmation_freshness_or_paywall_gates(tmp_path, monkeypatch):
    from dealradar.publish.paywall import STUB_KEYS

    cfg = config("closed")
    cfg.reuse_link_facts_enabled = True
    cfg.paywall_teaser_count = 1
    cfg.front_page["checkout_price_deals"]["enabled"] = False
    cfg.unconfirmed_label_enabled = False
    row = facts(2)[1]
    row.update(store="Amazon", canonical_url=AMAZON,
               price_rechecked_at=iso(NOW - timedelta(hours=8)),
               price_published_at=iso(NOW - timedelta(hours=8)))
    links = derive_link_facts(row, link_policy(TAG))
    assert pick_score(row, cfg, NOW, links=links) is None
    assert pick_score(row, cfg, NOW, require_confirmation=False, links=links) is not None
    assert pick_score(dict(row, price_unconfirmed=True), cfg, NOW,
                      require_confirmation=False, links=links) is None
    fresh = dict(row, dedupe_key="fresh", price_rechecked_at=iso(NOW))
    # Two near-identical titles with distinct URLs/conditions retain identity.
    other = dict(fresh, dedupe_key="other", canonical_url="https://ebay.com/itm/9",
                 store="eBay", conditions=["used"])
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    db = DB(":memory:")
    try:
        result = site.render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=[fresh, row, other])
        assert len(result["full_payload"]["deals"]) == 3
        public = json.loads((tmp_path / "site/deals.json").read_text())["deals"]
        assert sum(bool(d.get("locked")) for d in public) == 2
        for stub in (d for d in public if d.get("locked")):
            assert set(stub) == set(STUB_KEYS) | {"locked"}
        member = {d["dedupe_key"]: d for d in result["full_payload"]["deals"]}
        assert member["other"]["url"] != member["fresh"]["url"]
        assert member["other"]["conditions"] == ["used"]
        assert "ps" not in member["other"]
    finally:
        db.close()


@pytest.mark.parametrize("price_only", [False, True])
def test_label_copies_and_spotlight_share_only_pure_routes(tmp_path, monkeypatch, price_only):
    from dealradar.publish import link_facts

    cfg = config("open")
    cfg.reuse_link_facts_enabled = True
    cfg.critic_tiering["enabled"] = False  # synthetic source facts, no model calls
    stale = facts(2)[1]
    stale.update(dedupe_key="stale", store="Elsewhere", canonical_url=AMAZON,
                 first_seen_at=iso(NOW - timedelta(hours=30)),
                 price_rechecked_at=iso(NOW - timedelta(hours=30)),
                 price_published_at=iso(NOW - timedelta(hours=30)))
    daily = facts(2)[1]
    daily.update(dedupe_key="daily", store="MorningSave", canonical_url="https://morningsave.com/p/1",
                 sightings=[dict(source_name="morningsave", source_url="https://morningsave.com/p/1",
                                 seen_at=iso(NOW), final_price=10.0)])
    if price_only:
        daily.update(pct_at_checkout=None, regular_price=None, abs_savings=None)
    calls = []
    derive = link_facts.derive_link_facts

    def counted(deal, policy):
        calls.append(deal["dedupe_key"])
        return derive(deal, policy)

    monkeypatch.setattr(link_facts, "derive_link_facts", counted)
    db = DB(":memory:")
    try:
        result = site.render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=[stale, daily])
        member = result["full_payload"]
        labeled = next(d for d in member["deals"] if d["dedupe_key"] == "stale")
        assert labeled["price_unconfirmed"] and "ps" not in labeled and "pick" not in labeled
        assert labeled["amazon"] and "amazon_as_of" in labeled
        assert "stale" in json.loads(db.meta_get(site.PICKS_META))
        assert member["spotlight"][0]["dedupe_key"] == "daily"
        assert sum(d["dedupe_key"] == "daily" for d in member["deals"]) == int(not price_only)
        assert sorted(calls) == ["daily", "stale"]
    finally:
        db.close()


@pytest.mark.parametrize("mode", MODES)
def test_repeat_publish_reopen_and_changed_policy_have_no_stale_state(tmp_path, monkeypatch, mode):
    cfg = config(mode)
    cfg.reuse_link_facts_enabled = True
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    rows = facts(4)
    path = tmp_path / "state.db"
    db = DB(path)
    seed(db, rows)
    first = site.render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=rows,
                             last_run={"run_key": "task225"})
    state = list(db.conn.iterdump())
    db.close()
    db = DB(path)
    try:
        again = site.render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=rows,
                                 last_run={"run_key": "task225"})
        assert again == first
        assert list(db.conn.iterdump()) == state
        cfg.affiliate_amazon_tag = ""
        cfg.affiliate_sovrn_enabled = False
        changed = site.render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=rows)
        assert all(not d["earns"] for d in changed["full_payload"]["deals"])
        assert any(d["earns"] for d in first["full_payload"]["deals"])
    finally:
        db.close()


@pytest.mark.parametrize("open_access", [False, True])
def test_enabled_pipeline_rerun_idempotency(env, capsys, open_access):
    from tests.test_e2e_offline import test_rerun_is_idempotent as check_rerun

    path = Path(env["cfg"])
    text = path.read_text().replace("reuse_link_facts:\n    enabled: false",
                                    "reuse_link_facts:\n    enabled: true")
    if not open_access:
        text = text.replace("open_access: true", "open_access: false")
        text = text.replace("support:\n  enabled: true", "support:\n  enabled: false")
        # This existing regression expects two public deals and one dry send.
        text = text.replace("teaser_count: 8", "teaser_count: 2")
    path.write_text(text)
    actual = load_config(path)
    assert actual.reuse_link_facts_enabled
    assert site.is_open(actual) is open_access
    check_rerun(env, capsys, "shadow")
