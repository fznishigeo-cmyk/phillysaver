"""Operator 2026-10-24: Best deals today's own 30; the homepage keeps its mix."""
import copy
import json
import re
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from html import unescape

import pytest

from dealradar import best_today_mix, front_mix
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.publish import site
from tests.history_pins import pre_best_today_mix, pre_social_profiles, pre_social_profiles_raw
from tests.test_publish_site import make_deal

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
TARGET = {"sexy": 3, "wow": 2, "seasonal": 4, "pets": 3, "kids": 1, "health": 3,
          "essentials": 5, "beauty": 5, "home_kitchen": 4, "rest": 0}
TITLES = {
    "sexy": ("Women's lace lingerie set", "fashion"),
    "wow": ("Shark Navigator vacuum", "home"),
    "seasonal": ("Halloween skull decoration", "home"),
    "pets": ("Bully sticks for dogs", "pets"),
    "kids": ("Toddler building blocks", "toys_games"),
    "health": ("Magnesium supplement", "health"),
    "essentials": ("Puffs Plus Lotion facial tissues", "health"),
    "beauty": ("Hydrating face serum", "beauty"),
    "home_kitchen": ("Ceramic dinnerware set", "home"),
    "rest": ("Adjustable wrench", "tools"),
}


def best_fp(cfg=None, now=NOW):
    fp = (cfg or load_config()).front_page
    return best_today_mix.front_page(fp, now)


def test_shipped_mix_and_history_pins():
    cfg = load_config()
    assert list(front_mix.shares(best_fp(cfg)).items()) == list(TARGET.items())
    assert "checkout_spacing_exempt" not in best_fp(cfg)["mix"]
    assert front_mix.shares(cfg.front_page) == {
        "sexy_for_her": 7, "beauty": 7, "essentials": 4, "kitchen_dining": 4, "electronics": 3, "rest": 5}
    assert "best_today_mix" not in pre_social_profiles(copy.deepcopy(cfg)).front_page
    assert "best_today_mix" not in pre_social_profiles_raw(copy.deepcopy(cfg.raw))["publish"]["front_page"]


@pytest.mark.parametrize("change,message", [
    ({"enabled": "true"}, "enabled must be a boolean"),
    ({"per": 29}, "shares must add up to per"),
    ({"per": True}, "per must be a positive integer"),
    ({"checkout_spacing_exempt": []}, "supported settings only"),
    ({"buckets": {"rest": {"share": 30}, "seasonal": {"share": 0, "season": True}}}, "positive integer"),
    ({"buckets": {"rest": {"share": -1}, "seasonal": {"share": 31, "season": True}}}, "integer"),
    ({"buckets": {"rest": {"share": False}, "seasonal": {"share": 30, "season": True}}}, "integer"),
    ({"buckets": {"rest": {"share": 0}, "seasonal": {"share": 30, "season": "true"}}}, "season must be a boolean"),
    ({"buckets": {"rest": {"share": 0}, "seasonal": {"share": 30, "season": 1}}}, "season must be a boolean"),
    ({"buckets": {"rest": {"share": 0}, "seasonal": {"share": 30, "season": False}}}, "needs words"),
    ({"buckets": {"rest": {"share": 30, "season": True}}}, "takes a share only"),
    ({"buckets": {"rest": {"share": 0}, "kids": {"share": 30, "groups": ["children"]}}}, "want groups"),
])
def test_validation(tmp_path, change, message):
    fp = copy.deepcopy(load_config().front_page)
    fp["best_today_mix"].update(change)
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"publish": {"front_page": fp}}))
    with pytest.raises(ValueError, match="best_today_mix.*" + message):
        load_config(path)


def test_rest_zero_and_season_only_are_valid(tmp_path):
    mix = {"enabled": True, "per": 30, "buckets": {
        "seasonal": {"share": 30, "season": True}, "rest": {"share": 0}}}
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"publish": {"front_page": {"best_today_mix": mix}}}))
    assert load_config(path).front_page["best_today_mix"] == mix


@pytest.mark.parametrize("item,category,expected", [
    *[(title, category, name if name != "rest" else None) for name, (title, category) in TITLES.items()],
    ("iPhone case compatible with Apple Watch", "electronics", None),
    ("Replacement filter for Shark Navigator vacuum", "home", "home_kitchen"),
    ("Keurig K Cup coffee pods", "grocery", "essentials"),
    ("LEGO toddler building toy", "toys_games", "wow"),
    ("Dog toy for puppies", "toys_games", "pets"),
    ("Hot dog buns", "grocery", "essentials"),
    ("Cat eye sunglasses", "fashion", None),
    ("Calvin Klein Men's Cologne", "beauty", "beauty"),
    ("Vitamin supplement detox weight loss", "health", "essentials"),
    ("Portable air conditioner", "home", "home_kitchen"),
    ("Nail gun kit", "tools", None),
    ("Puffs Plus Lotion facial tissues", "beauty", "essentials"),
    ("Rose toy vibrator lingerie", "beauty", None),
    ("Adult Halloween massager", "health", None),
])
def test_bucket_labels(item, category, expected):
    assert front_mix.bucket(dict(item=item, category=category), best_fp()) == expected


@pytest.mark.parametrize("stamp,title,expected", [
    ("2026-10-24T12:00:00+00:00", "Halloween skull", "seasonal"),
    ("2026-12-28T12:00:00+00:00", "Halloween skull", None),
    ("2026-12-28T12:00:00+00:00", "Halloween skull hand warmer", "seasonal"),
    ("2026-11-01T12:00:00+00:00", "Holiday gift set", "seasonal"),
    ("2026-12-24T12:00:00+00:00", "Holiday gift set", "seasonal"),
    ("2026-12-25T12:00:00+00:00", "Holiday gift set", None),
    ("2026-11-10T12:00:00+00:00", "Holiday gift set", "seasonal"),
    ("2026-11-10T12:00:00+00:00", "Roasting pan", "seasonal"),
    ("2026-11-27T12:00:00+00:00", "Roasting pan", "seasonal"),
    ("2026-11-28T12:00:00+00:00", "Roasting pan", None),
    ("2026-11-01T00:30:00+00:00", "Halloween skull", "seasonal"),
    ("2026-11-01T00:30:00+00:00", "Holiday gift set", None),
])
def test_season_windows(stamp, title, expected):
    assert front_mix.bucket({"item": title}, best_fp(now=datetime.fromisoformat(stamp))) == expected


def test_wrapped_season_and_no_implicit_clock():
    cfg = load_config()
    cfg.front_page["appeal"]["season"] = [{"from": "12-20", "to": "01-05", "words": ["snowflake"]}]
    for month, day in ((12, 25), (1, 3)):
        fp = best_fp(cfg, datetime(2026, month, day, 12, tzinfo=timezone.utc))
        assert front_mix.bucket({"item": "Snowflake lights"}, fp) == "seasonal"
    assert front_mix.bucket({"item": "Snowflake lights"}, best_fp(cfg, None)) is None
    assert front_mix.bucket({"item": "Electric blanket"}, best_fp(cfg, None)) == "seasonal"


def inventory(counts=None):
    counts = counts or dict.fromkeys(TARGET, 10)
    return [make_deal(dedupe_key=f"best-mix|{name}|{i}", item=f"{title} model {i}",
                      category=category, brand=f"Brand-{name}-{i}", codes=[], conditions=[],
                      canonical_url=f"https://www.walmart.com/ip/{name}-{i}",
                      image_url=f"https://images.example.invalid/{name}-{i}.jpg",
                      first_seen_at=NOW.isoformat(), last_seen_at=NOW.isoformat(),
                      price_published_at=NOW.isoformat(), price_rechecked_at=NOW.isoformat(),
                      critic={"plausible": True, "confidence": .9, "reason": "Backed basis"})
            for name, (title, category) in TITLES.items() for i in range(counts.get(name, 0))]


def build(root, cfg, deals, monkeypatch):
    root.mkdir()
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    db = DB(root / "state.db")
    try:
        site.render_site(db, cfg, NOW, out_dir=root / "site", deals=deals)
    finally:
        db.close()
    return {str(p.relative_to(root / "site")): p.read_bytes()
            for p in (root / "site").rglob("*") if p.is_file()}


def best_titles(files):
    return [unescape(re.sub(r"<[^>]+>", "", t)) for t in re.findall(
        r'<h3 class="title">(.*?)</h3>', files["best-deals-today/index.html"].decode())]


@pytest.mark.parametrize("supply", ["ample", "short", "rest", "one_bucket"])
def test_published_top_30(tmp_path, monkeypatch, supply):
    cfg = load_config()
    counts = dict.fromkeys(TARGET, 10)
    if supply == "short":
        counts["pets"] = 1
    elif supply == "rest":
        counts = dict(TARGET, pets=1, rest=10)
    elif supply == "one_bucket":
        counts = {"beauty": 35, "rest": 35}
    deals = inventory(counts)
    files = build(tmp_path / "on", cfg, deals, monkeypatch)
    titles = best_titles(files)
    assert len(titles) == len(set(titles)) == 30
    labels = {d["item"]: front_mix.bucket(d, best_fp(cfg)) or "rest" for d in deals}
    actual = Counter(labels[t] for t in titles)
    if supply == "ample":
        assert actual == Counter(TARGET)
    elif supply == "short":
        assert actual["pets"] == 1 and actual["rest"] == 0
        assert all(actual[name] >= share for name, share in TARGET.items() if name != "pets")
    elif supply == "rest":
        assert actual == Counter(dict(TARGET, pets=1, rest=2))
    else:
        assert actual == {"beauty": 30}
    doc = json.loads(files["deals.json"])
    assert "best_today_mix" not in doc["meta"]["front_page"]
    assert all(d.get("mx") == front_mix.bucket(d, cfg.front_page) for d in doc["deals"])


def test_off_absent_and_homepage_bytes(tmp_path, monkeypatch):
    cfg, deals = load_config(), inventory()
    on = build(tmp_path / "on", cfg, deals, monkeypatch)
    cfg.front_page["best_today_mix"]["enabled"] = False
    off = build(tmp_path / "off", cfg, deals, monkeypatch)
    missing = build(tmp_path / "missing", pre_best_today_mix(cfg), deals, monkeypatch)
    assert off == missing
    # Recorded from the original checkout: every published file, version stamps included.
    recorded = [(name, sha256(data).hexdigest()) for name, data in sorted(off.items())]
    assert sha256(repr(recorded).encode()).hexdigest() == "246d7d9a3cc49a48aea8ff138f33d9c6e137963357dbf07c5b54e680a3957746"
    assert on.keys() == off.keys()
    assert {name for name in on if on[name] != off[name]} == {"best-deals-today/index.html"}
    # Original selection path: only precomputed homepage mx labels are read.
    real = front_mix.lead
    def legacy(rows, fp, extra=(), n=30):
        assert "best_today_mix" not in fp
        return real(rows, fp, extra, n)
    monkeypatch.setattr(front_mix, "lead", legacy)
    assert build(tmp_path / "legacy", cfg, deals, monkeypatch) == off


def test_labels_do_not_mutate_rows_and_extra_rank_is_preserved():
    fp = best_fp()
    deals = inventory()
    for i, d in enumerate(deals):
        d["mx"] = "beauty"  # Deliberately wrong homepage labels.
        if i % 2:
            d["pick"] = i
    before = copy.deepcopy(deals)
    extra = [d["dedupe_key"] for d in reversed(deals) if "pick" not in d]
    label = lambda d: front_mix.bucket(d, fp) or "rest"
    out = best_today_mix.lead(deals, fp, extra)
    assert Counter(label(d) for d in out[:30]) == Counter(TARGET)
    for name in TARGET:
        selected = [d for d in out[:30] if label(d) == name]
        expected = [d for d in deals if label(d) == name and "pick" in d]
        expected += [d for d in reversed(deals) if label(d) == name and "pick" not in d]
        assert selected == expected[:len(selected)]
    assert deals == before and len({id(d) for d in out}) == len(deals)


def test_best_today_extras_work_with_homepage_mix_off():
    from dataclasses import replace
    cfg = load_config()
    cfg.front_page["mix"]["enabled"] = False
    deal = inventory({"pets": 1})[0]
    assert front_mix.extras([(deal, 5.0)], set(), replace(cfg, front_page=best_fp(cfg)), NOW) == [deal["dedupe_key"]]
