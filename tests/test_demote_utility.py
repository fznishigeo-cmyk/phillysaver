"""Task 267b: reuse niche ordering, including the actual published JavaScript."""

import copy
import json
import re
import socket
import subprocess
from pathlib import Path

import httpx
import pytest

from dealradar.config import load_config
from dealradar.db import DB
from dealradar.publish import seo, site
from dealradar.rank import front_page_mix, is_niche, pick_order, pick_score
from tests.test_front_page import pick_deal
from tests.test_publish_site import NOW, make_deal

ROOT = Path(__file__).resolve().parents[1]
PHONE = "iPhone 16 Pro Max Symmetry Series Case"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task267b network unavailable")
    monkeypatch.setattr(httpx.Client, "send", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(site, "util_now", lambda: NOW)


@pytest.fixture
def cfg():
    cfg = load_config()
    cfg.custom_domain = "wowpriced.test"
    cfg.tracking = {}
    cfg.paywall_google_client_id = ""
    return cfg


@pytest.mark.parametrize("value", [[], ["custom phrase", "fuse"]])
def test_config_list_and_missing_default(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"publish": {"front_page": {"demote_words": value}}}))
    assert load_config(path).front_page["demote_words"] == value
    path.write_text("{}")
    assert not load_config(path).front_page.get("demote_words")


@pytest.mark.parametrize("value", [None, False, "case", {}, [1], [""], ["  "]])
def test_invalid_config_rejected(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"publish": {"front_page": {"demote_words": value}}}))
    with pytest.raises(ValueError, match=r"publish.front_page.demote_words"):
        load_config(path)


@pytest.mark.parametrize("title", [
    PHONE, "OtterBox Samsung Galaxy S24 FE React Series Case",
    "OtterBox Profile Series Case for Samsung Galaxy S25 Edge",
    "Phone Case", "iPhone Case", "Galaxy Case", "Pixel Case", "SPIGEN armor",
    "Screen Protector", "Tempered Glass", "Replacement", "Replacement Part",
    "Toner", "Ink Cartridge", "Filter Cartridge", "Adapter Plate", "Mounting Bracket",
    "Fuse", "Gasket", "O-Ring", "Hose Clamp", "Case for Google Pixel 9 Pro",
    "iPhone 16 smartphone with case",
])
def test_shipped_words_and_model_phrases_are_niche(cfg, title):
    assert cfg.front_page["demote_words"]
    assert is_niche({"item": title, "category": "electronics"}, cfg.front_page)


@pytest.mark.parametrize("title", [
    "Briefcase", "Pillowcase", "Case of 24 water bottles", "Showcase cabinet",
    "Fusebox organizer", "Otterboxx carry bag", "Replacementless accessory",
    "Ink drawing cartridge organizer", "Screen cleaning protector cloth",
    "Owala water bottle", "Universal phone stand",
])
def test_whole_words_and_literal_utility_phrases(cfg, title):
    assert not is_niche({"item": title, "category": "home"}, cfg.front_page)


@pytest.mark.parametrize("title,niche", [
    ("Clear Case for Apple iPhone SE (3rd Generation)", True),
    ("Clear CASES for SAMSUNG", True),
    ("Motorola edge protective case", True),
    ("Case for Moto G Power", True),
    ("OnePlus Nord clear case", True),
    ("Case for Google Pixel", True),
    ("Galaxy clear cases", True),
    ("Case for iPhone 16 with filter and spare cartridge storage", True),
    ("iPhone SE cover", False),
    ("Samsung briefcase", False),
    ("Pixel pillowcase", False),
    ("Motorola showcase", False),
    ("Case of 24 water bottles", False),
    ("Case for iphones", False),
    ("Case for samsungish", False),
    ("Case for motog", False),
    ("Case for oneplusx", False),
])
def test_phone_case_rule_uses_whole_words(title, niche):
    # Phone cases have their own rule whenever title demotion is enabled.
    assert is_niche({"item": title, "category": "home"},
                    {"demote_words": ["filter cartridge"]}) is niche


def test_config_controls_matching_and_off_preserves_category_rules(cfg):
    deal = {"item": "Custom Phrase", "category": "home"}
    assert is_niche(deal, {"demote_words": ["custom phrase"]})
    assert not is_niche(dict(deal, item="Custom travel Phrase"),
                        {"demote_words": ["custom phrase"]})
    assert is_niche(dict(deal, item=PHONE), {"demote_words": ["custom phrase"]})
    for words in ({}, {"demote_words": []}):
        assert not is_niche(dict(deal, item=PHONE), words)
        assert is_niche(dict(deal, category="auto"), dict(words, niche_categories=["auto"]))


def offers():
    titles = [PHONE, "Replacement gasket", "Laser toner"] + [f"Everyday offer {i}" for i in range(40)]
    return [make_deal(dedupe_key=f"task267b-{i}", item=title,
                     category="electronics" if i % 2 else "home", store="Walmart",
                     canonical_url=f"https://www.walmart.com/ip/{100000000 + i}",
                     brand=f"Brand {i}", codes=[], conditions=["new"],
                     image_url=f"https://{'utility' if i < 3 else 'ordinary'}.example.invalid/{i}.jpg",
                     first_seen_at=NOW.isoformat(), price_rechecked_at=NOW.isoformat())
            for i, title in enumerate(titles)]


@pytest.fixture
def published(tmp_path, cfg):
    source = offers()
    before = copy.deepcopy(source)
    db = DB(":memory:")
    try:
        result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=source)
        assert source == before
        yield tmp_path, result
    finally:
        db.close()


def js_order(out, cases):
    path = out / "cases.json"
    path.write_text(json.dumps(cases))
    result = subprocess.run(["node", str(ROOT / "tests/front_page_logic.cjs"),
                             str(out / "index.html"), str(path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("title,words,niche", [
    pytest.param("Clear Case for Apple iPhone SE (3rd Generation)", None, True,
                 id="iphone-se"),
    # Not a named-device accessory: those are niche by title since task 282's ordering.
    pytest.param("Countertop caddy with filter and spare cartridge storage",
                 ["filter cartridge"], False, id="utility-gap"),
    pytest.param("Countertop caddy with FILTER CARTRIDGE storage",
                 ["filter cartridge"], True, id="utility-contiguous"),
])
def test_review_rendered_order(tmp_path, cfg, title, words, niche):
    if words is not None:
        cfg.front_page["demote_words"] = words
    source = offers()
    # A strong candidate can reach the first screen unless demoted.
    source = [dict(source[0], item=title, pct_at_checkout=99.0, abs_savings=990.0,
                   regular_price=1000.0, score=999.0)] + source[3:]
    db = DB(":memory:")
    try:
        result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=source)
    finally:
        db.close()
    doc = json.loads((tmp_path / "deals.json").read_text())
    public = doc["deals"]
    candidate = next(d for d in public if d["item"] == title)
    assert candidate.get("niche", False) is niche
    assert ("pick" in candidate) is not niche
    assert result["full_payload"]["deals"] == public
    expected = seo.home_order(public, cfg.front_page)
    position = [d["key"] for d in expected].index(candidate["key"])
    assert position == 40 if niche else position < 8
    home = (tmp_path / "index.html").read_text()
    wall = home.split('<div id="content"')[1].split('<div id="paywall"')[0]
    assert re.findall(r'<h3 class="title"><a href="[^"]+">([^<]+)</a>', wall) == [
        d["item"] for d in expected[:seo._STATIC_WALL_MAX]]
    cases = [dict(rows=public, options=doc["meta"]["front_page"], state={"sortKey": sort})
             for sort in ("top", "fresh")]
    for case in js_order(tmp_path, cases):
        for field in ("ordered", "rendered"):
            assert (case[field].index(candidate["key"]) >= 40) is niche


def test_server_static_jsonld_preconnect_and_real_js_agree(published, cfg):
    out, result = published
    doc = json.loads((out / "deals.json").read_text())
    public = doc["deals"]
    assert "demote_words" not in doc["meta"]["front_page"]
    assert all(d.get("niche") is True for d in public if d["dedupe_key"] in {f"task267b-{i}" for i in range(3)})
    assert all("niche" not in d for d in public if d["dedupe_key"] not in {f"task267b-{i}" for i in range(3)})
    assert result["full_payload"]["deals"] == public
    expected = seo.home_order(public, cfg.front_page)
    assert all(not d.get("niche") for d in expected[:40])
    assert {d["dedupe_key"] for d in expected} == {d["dedupe_key"] for d in public}
    # Treating the same titles as category-niche produces the identical pick order.
    equivalent = [dict(d, category="auto") if d.get("niche") else d for d in public]
    assert [d["key"] for d in pick_order(equivalent, cfg.front_page)] == [d["key"] for d in pick_order(public, cfg.front_page)]
    home = (out / "index.html").read_text()
    wall = home.split('<div id="content"')[1].split('<div id="paywall"')[0]
    assert re.findall(r'<h3 class="title"><a href="[^"]+">([^<]+)</a>', wall) == [d["item"] for d in expected[:seo._STATIC_WALL_MAX]]
    best = (out / "best-deals-today/index.html").read_text()
    lists = [json.loads(body) for body in re.findall(r'<script type="application/ld\+json">(.*?)</script>', best, re.S)]
    listing = next(data for data in lists if data.get("@type") == "ItemList")
    assert [row["url"] for row in listing["itemListElement"]] == ["https://wowpriced.test" + d["deal_path"] for d in expected[:30]]
    assert 'rel="preconnect" href="https://ordinary.example.invalid"' in home
    assert 'rel="preconnect" href="https://utility.example.invalid"' not in home
    cases = [dict(rows=public, options=doc["meta"]["front_page"], state={"sortKey": sort})
             for sort in ("top", "fresh")]
    niche_keys = {d["key"] for d in public if d.get("niche")}
    for case in js_order(out, cases):
        for field in ("ordered", "rendered"):
            assert not niche_keys.intersection(case[field][:40])
            assert niche_keys.issubset(case[field])
    # Search and department ordering ignore the niche flag.
    for state, controls in (({"catTab": 3}, {}), ({"catTab": 1}, {}),
                            ({"catTab": 0, "sortKey": "pct"}, {}),
                            ({"catTab": 0}, {"q": "case"})):
        inputs = [dict(rows=[{k: v for k, v in d.items() if keep or k != "niche"} for d in public],
                       options=doc["meta"]["front_page"], state=state, controls=controls)
                  for keep in (True, False)]
        actual = js_order(out, inputs)
        assert actual[0]["ordered"] == actual[1]["ordered"]


def test_existing_mix_cap_includes_demoted_titles(published, cfg):
    out, _ = published
    raw = offers()
    # Enough ordinary supply: existing mix caps require no sparse relaxation.
    source = raw[:3] * 3 + raw[3:]
    source = [dict(d, key=str(i)) for i, d in enumerate(source)]
    options = dict(cfg.front_page, niche_last=False, max_niche_top=2,
                   max_same_category_run=99, max_same_store_top=99)
    mixed = front_page_mix(source, options)
    assert sum(is_niche(d, options) for d in mixed[:24]) == 2
    assert not any(is_niche(d, options) for d in mixed[:8])
    flagged = [dict(d, niche=True) if is_niche(d, options) else d for d in source]
    actual = js_order(out, [dict(rows=flagged, options=options, state={"sortKey": "fresh"})])[0]
    assert actual["mixed"] == [d["key"] for d in mixed]
    # The existing pick gate excludes these exactly as it excludes categories.
    for title in (PHONE, "Laser toner"):
        deal = pick_deal(item=title, category="electronics")
        assert pick_score(deal, cfg, NOW) is None
        cfg.front_page["demote_words"] = []
        assert pick_score(deal, cfg, NOW) is not None
        cfg.front_page["demote_words"] = load_config().front_page["demote_words"]


@pytest.mark.parametrize("mode", ["support", "open", "closed", "shipped"])
def test_missing_and_empty_restore_all_bytes_and_member_payload(tmp_path, mode):
    from tests.test_amazon_as_of import config
    cfg = config(mode)
    cfg.front_page.pop("demote_words", None)
    outputs = []
    for setting in (None, [], ["iphone case", "toner", "gasket"], []):
        if setting is not None:
            cfg.front_page["demote_words"] = setting
        db = DB(":memory:")
        try:
            result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=offers())
            outputs.append(({p.relative_to(tmp_path).as_posix(): p.read_bytes()
                             for p in tmp_path.rglob("*") if p.is_file()}, result["full_payload"]))
        finally:
            db.close()
    assert outputs[0] == outputs[1] == outputs[3]
    assert outputs[2] != outputs[0]
    assert all("niche" not in d for d in outputs[0][1]["deals"])


def test_enabled_publish_rerun_has_identical_bytes_and_state(tmp_path, cfg):
    db = DB(":memory:")
    try:
        snapshots = []
        for _ in range(2):
            result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=offers(),
                                      last_run={"run_key": "task267b-fixed"})
            snapshots.append(({p.relative_to(tmp_path).as_posix(): p.read_bytes()
                               for p in tmp_path.rglob("*") if p.is_file()},
                              result["full_payload"], list(db.conn.iterdump())))
        assert snapshots[0] == snapshots[1]
        assert any(d.get("niche") for d in result["public_deals"])
    finally:
        db.close()


def test_chromium_home_order_offline(published):
    out, _ = published
    result = subprocess.run(["node", str(Path(__file__).with_name("demote_utility_browser.cjs")), str(out)],
                            capture_output=True, text=True, timeout=120)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
