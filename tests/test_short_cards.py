"""short_cards and best_today_phone_cards (operator 2026-10-24: "can you shorten the card
slightly.. the other websites are less wordy"; the home phone cards on /best-deals-today/)."""
import json
import re
import shutil
import subprocess
from html import escape
from pathlib import Path
from unittest.mock import patch

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import best_today_phone_cards as phone, keep_first_cards, seo, short_cards as short, site
from scripts.publication_budget import analyze
from tests.history_pins import (pre_best_today_phone_cards, pre_best_today_tab, pre_front_mix, pre_short_cards,
                                pre_social_profiles)
from tests.phone_budget_fixture import NOW, fixture_offers, publish_fixture, visible_offer_count
from tests.test_keep_first_cards import files
from tests.test_search_amazon import run_node

BROWSER = Path(__file__).with_name("short_cards_browser.cjs")
SWITCHES = ("short_cards", "best_today_phone_cards")
LONG = (", 6 Piece Cooking Utensils With Wood Handle, Heat Resistant Kitchen Tool Set With Spatula, "
        "Spoon, Ladle, Skimmer, Slotted Spoon, Pasta Server")
CARD = '<article class="card'
REVIEW_LONG = ["Clip the 20% coupon on the product page, below the price (it can take a moment to show up)",
               "Add two of them to the cart before you check out", "Choose Subscribe & Save for the lowest price"]


def offers(rows):
    """The phone-budget offers (every fourth coded); every fifth named like a long store title, every
    third from one source; in every 15 a clip-coupon deal, a two-code deal, a rebate, a per-unit
    price, a price range, long required steps and a stated expiry."""
    out = fixture_offers()[:rows]
    for i, row in enumerate(out):
        if i % 5 == 1:
            row["item"] += LONG
        if i % 3 == 2:
            row["verify_tier"] = "unverified"
        if i % 15 == 3:
            row.update(stack_steps=["Clip the 20% coupon", "Choose Subscribe & Save"], conditions=["subscribe_save"])
        if i % 15 == 8:
            row.update(codes=["SAVE5", "EXTRA10"], stack_steps=["Clip the coupon and use code EXTRA10",
                                                                "Enter code SAVE5 at checkout"])
        if i % 15 == 13:
            row.update(rebates=["$10 mail-in rebate"], flags=["rebate_dependent"])
        if i % 15 == 5:
            row.update(flags=["per_unit"], quantity_or_unit="each, buy 6")
        if i % 15 == 10:
            row.update(flags=["range"])
        if i % 15 == 11:
            row.update(stack_steps=REVIEW_LONG, conditions=["subscribe_save"])
        if i % 15 == 6:
            row.update(expires_at="2026-10-25T23:00:00+00:00")
    return out


def build(root, short_on, phone_on, rows=120):
    """None: the switch is missing from the config."""
    cfg = load_config()
    cfg.tracking = {}
    cfg.hide_deal_site_links = False  # keep the coded partner offers, as the budget fixture does
    for name, on in zip(SWITCHES, (short_on, phone_on)):
        if on is None:
            delattr(cfg, name)
        else:
            setattr(cfg, name, on)
    db = DB(":memory:")
    try:
        with patch.object(site, "util_now", lambda: NOW):
            site.render_site(db, cfg, NOW, out_dir=root, deals=offers(rows), last_run={"run_key": "short-cards"})
    finally:
        db.close()
    return root


@pytest.fixture(scope="module")
def sites(tmp_path_factory):
    root = tmp_path_factory.mktemp("short-cards")
    return {name: build(root / name, *flags) for name, flags in
            {"off": (False, False), "short": (True, False), "phone": (False, True), "both": (True, True)}.items()}


def version(out):
    return json.loads((out / "deals.json").read_text())["meta"]["site_version"]


def test_switches_strict_default_shipped_and_pinned(tmp_path):
    shipped = load_config()
    assert shipped.short_cards is True  # approved 2026-10-24 ~03:30Z (A58)
    assert shipped.best_today_phone_cards is True  # operator approved 2026-10-24
    for name, pin, feature in zip(SWITCHES, (pre_short_cards, pre_best_today_phone_cards), (short, phone)):
        assert getattr(AppConfig(), name) is False
        on = AppConfig(**{name: True})
        assert feature.enabled(on)
        assert getattr(pin(on), name) is False
        assert getattr(pre_social_profiles(AppConfig(**{name: True})), name) is False  # every older recorded fixture
        assert not feature.enabled(type("Config", (), {name: 1})()) and not feature.enabled(object())
        for value in (1, "true", None, [], {}):
            path = tmp_path / "config.yaml"
            path.write_text(json.dumps({name: value}))
            with pytest.raises(ValueError, match=f"^{name} must be a boolean$"):
                load_config(path)
        path.write_text("{}")
        assert getattr(load_config(path), name) is False  # missing = off


def test_off_missing_rerun_back_changes_no_byte(tmp_path, monkeypatch, sites):
    off = files(sites["off"])
    with monkeypatch.context() as m:  # the same build without either feature at all
        for feature in (short, phone):
            m.setattr(feature, "enabled", lambda cfg: False)
            m.setattr(feature, "site_version", lambda version, cfg: version)
            m.setattr(feature, "publish", lambda out, cfg: None)
        assert files(build(tmp_path / "without", True, True)) == off
    assert files(build(tmp_path / "missing", None, None)) == off
    both = files(sites["both"])
    shutil.copytree(sites["both"], tmp_path / "again")
    assert files(build(tmp_path / "again", True, True)) == both  # a rerun over its own output is byte-stable
    assert files(build(tmp_path / "again", False, False)) == off  # and back
    assert both.keys() == off.keys()


def test_short_cards_on_every_card_page_only(sites):
    off, on = sites["off"], sites["short"]
    old = files(off)
    changed = {k for k, v in files(on).items() if old[k] != v}
    cards = {str(p.relative_to(off)) for p in off.rglob("*.html") if CARD in p.read_text()}
    assert {"index.html", "best-deals-today/index.html", "deals/home-kitchen/index.html",
            "stores/walmart/index.html", "black-friday/index.html"} <= cards
    assert any(k.startswith("deal/") for k in cards)  # related deals on deal pages
    assert changed == cards | {"deals.json"}
    assert version(on) != version(off)
    doc = json.loads((on / "deals.json").read_text())
    assert doc["deals"] == json.loads((off / "deals.json").read_text())["deals"]  # the data keeps every headline
    for name in cards:
        before, after = (off / name).read_text(), (on / name).read_text()
        css = short.CSS if name == "index.html" else short.HUB_CSS
        assert after.count(css) == 1 and after.count(short.ANCHOR + css) == 1, name
        assert '<div class="headline">' in before and '<div class="headline">' not in after, name
        if name.startswith("deal/"):  # the deal's own page content is the same; only its related cards change
            assert after.replace(css, "", 1).split("<h2>Related live deals</h2>")[0] == \
                before.split("<h2>Related live deals</h2>")[0], name
    best = (on / "best-deals-today/index.html").read_text()
    codes = [d for d in doc["deals"] if d.get("codes") and d.get("deal_path") and d["deal_path"] in best]
    assert codes and any(len(d["codes"]) > 1 for d in codes)
    for d in codes:  # "Enter Code: X at checkout", every code once in its card
        card = re.search(r'<article class="card[^"]*">(?:(?!</article>).)*href="' + re.escape(d["deal_path"]) +
                         r'".*?</article>', best, re.S)[0]
        line = ", ".join(f'<code class="code">{c}</code>' for c in d["codes"])
        assert f'<div class="code-line">Enter Code: {line} at checkout</div>' in card
        assert all(card.count(c) == 1 for c in d["codes"]), card
    # A step that only enters the code goes; a code inside another step reads "the code".
    assert "Clip the coupon and use the code" in best and "Enter code SAVE5" not in best
    assert '<div class="how-line">Clip the 20% coupon &rarr; Choose Subscribe &amp; Save</div>' in best
    home = (on / "index.html").read_text()
    for token, new in short.SLOTS:
        assert (token not in home or token in new) and new in home
    # No past-price wording, ever (2026-10-17).
    added = re.sub(r"<[^>]+>", " ", short.HUB_CSS + short.LINES + short.SHORT_HOW + phone.CSS)
    assert not re.search(r"\b(was|history|lowest|before)\b", added, re.I)


@pytest.mark.parametrize("tidy", [True, False])
def test_static_lines_match_the_script(tmp_path, tidy):
    """keep_first_cards: the home wall's static cards draw the script's lines and How box (drop_card_parity.cjs)."""
    out = tmp_path / "on"
    cfg = load_config()
    cfg.tracking, cfg.hide_deal_site_links, cfg.card_tidy = {}, False, tidy
    db = DB(":memory:")
    try:
        with patch.object(site, "util_now", lambda: NOW):
            site.render_site(db, cfg, NOW, out_dir=out, deals=offers(40), last_run={"run_key": "short-cards"})
    finally:
        db.close()
    assert short.enabled(cfg) and keep_first_cards.enabled(cfg)
    doc = json.loads((out / "deals.json").read_text())
    base = dict(next(d for d in doc["deals"] if d.get("deal_path")), status="alive")
    rows = [dict(base, key="code", needs_steps=True, how=["Enter code SAVE5 at checkout"], codes=["SAVE5"]),
            dict(base, key="codes", needs_steps=True, how=["Clip the coupon and use code EXTRA10", "Apply code TAKE5."],
                 codes=["TAKE5", "EXTRA10"]),
            dict(base, key="optional", needs_steps=False, how=["Use code TAKE10"], codes=["TAKE10"]),
            dict(base, key="clip", needs_steps=True, how=["Clip the 20% coupon", "Choose Subscribe & Save"], codes=[]),
            dict(base, key="rebate", needs_steps=True, how=["After purchase: $10 rebate"], codes=["REBATE1"],
                 checkout=dict(base.get("checkout") or {}, card="Rebate after purchase")),
            dict(base, key="escape", needs_steps=True, how=["Clip the coupon", "Use code 'A&B\"<1'", "Check out <now>"],
                 codes=["A&B\"<1"]),
            dict(base, key="bare", needs_steps=True, how=[], codes=[]),
            dict(base, key="plain", needs_steps=False, how=[], codes=[]),
            dict(base, key="dead", status="dead", needs_steps=True, how=["Gone"], codes=["GONE5"])]
    cases = []
    for row in rows:
        card = seo.card_html(row, {row["key"][:8]: row["deal_path"]}, NOW, fresh_config=cfg, client_how=True, short=True)
        part = lambda pattern: (re.search(pattern, card, re.S) or [""])[0]
        cases.append(dict(row=row, how=part(r'<div class="how".*?</div>'), steps=part(r'<a class="deal-steps".*?</a>'),
                          lines=part(r'<div class="how-line">.*?</div>') + part(r'<div class="code-line">.*?</div>')))
    assert [bool(c["lines"]) for c in cases] == [True, True, True, True, True, True, False, False, False]
    assert [bool(c["how"]) for c in cases] == [False, False, False, False, True, False, True, False, False]
    assert "Clip the coupon &rarr; Check out &lt;now&gt;</div>" in cases[5]["lines"]
    assert ('Enter Code: <button type="button" class="code" data-code="TAKE5" aria-label="Copy code TAKE5">TAKE5</button>, '
            in cases[1]["lines"])
    assert "Clip the coupon and use the code" in cases[1]["lines"] and "Apply" not in cases[1]["lines"]
    assert "the steps are in the deal post" in cases[6]["how"] and "Rebate after purchase" in cases[4]["how"]
    path = tmp_path / "parity.json"
    path.write_text(json.dumps(cases))
    result = run_node("drop_card_parity.cjs", out / "index.html", out / "deals.json", path)
    assert result.returncode == 0, result.stdout + result.stderr


# Codex review of bd8583e: a code masked inside other words and numbers, per-unit / range /
# quantity notes lost, required steps clipped, the expiry date lost with the headline.
REVIEW = {  # row overrides -> the card's steps line, as both renderers draw it ("" = none)
    "unit": (dict(store="Walmart", codes=[], flags=["per_unit"], quantity_or_unit="each, buy 6", conditions=["new"]),
             "Price is per unit; the checkout total depends on quantity. &rarr; Quantity: each, buy 6"),
    "range": (dict(store="Target", codes=[], flags=["range"], conditions=["new"]),
              "Price varies by option; the displayed price is the verified upper bound."),
    "ten": (dict(codes=["10"], stack_steps=["Spend $100 and use code 10"]), "Spend $100 and use the code"),
    "app": (dict(codes=["APP"], stack_steps=["Buy through the APP only", "Enter code APP at checkout"]),
            "Buy through the APP only"),
    "overlap": (dict(codes=["SAVE", "SAVE10"], stack_steps=["Use code SAVE10 on the first one and code SAVE on the second"]),
                "Use the code on the first one and the code on the second"),
    "list": (dict(codes=["A1", "B2"], stack_steps=["Use code A1, B2 together"]), "Use code A1, B2 together"),
    "long": (dict(codes=[], stack_steps=REVIEW_LONG, conditions=["subscribe_save"]),
             " &rarr; ".join(escape(step) for step in REVIEW_LONG + ["Choose Subscribe & Save"])),  # nothing clipped
    "ends": (dict(codes=[], expires_at="2026-10-25T23:00:00+00:00"), ""),
}


def test_review_fixes_static_and_script(tmp_path):
    from tests.test_publish_site import make_deal
    from tests.test_support import NOW as TEST_NOW, render
    cfg = load_config()
    cfg.tracking, cfg.hide_deal_site_links, cfg.non_amazon_freshness_enabled = {}, False, False
    deals = [make_deal(**{"dedupe_key": f"review-{key}", "item": f"Product {key} kitchen set", "category": "kitchen",
                          "image_url": "https://images.example.invalid/card.png",
                          "first_seen_at": TEST_NOW.isoformat(), **row}) for key, (row, _) in REVIEW.items()]
    out, doc, *_ = render(tmp_path, cfg, deals)
    rows = {d["item"].split()[1]: d for d in doc["deals"]}
    best = (out / "best-deals-today/index.html").read_text()
    assert set(rows) == set(REVIEW)
    assert rows["unit"]["needs_steps"] is False and rows["unit"]["checkout"]["card"] == ""
    assert rows["ends"]["headline"].endswith(" Expires Oct 25.")
    cases = []
    for key, (_, line) in REVIEW.items():
        d = rows[key]
        hub = re.search(r'<article class="card[^"]*">(?:(?!</article>).)*Product ' + key + r' kitchen set.*?</article>',
                        best, re.S)[0]
        home = seo.card_html(d, {d["key"][:8]: d["deal_path"]}, TEST_NOW, fresh_config=cfg, client_how=True, short=True)
        for card in (hub, home):
            steps = re.search(r'<div class="how-line">(.*?)</div>', card, re.S)
            assert (steps[1] if steps else "") == line, (key, card)
            # A step that only enters the code goes; the code line names every code.
            assert ('class="code-line">Enter Code: ' in card) is bool(d["codes"]), key
            assert ("<span class=\"chip\">ends Oct 25</span>" in card) is (key == "ends"), key
            assert "<div class=\"headline\">" not in card
        part = lambda pattern: (re.search(pattern, home, re.S) or [""])[0]
        cases.append(dict(row=d, lines=part(r'<div class="how-line">.*?</div>') + part(r'<div class="code-line">.*?</div>'),
                          ends=part(r'<span class="chip">ends [^<]*</span>')))
    # Required steps are never clipped: the steps line has no line clamp.
    assert "line-clamp" not in re.search(r"\.card \.how-line\{[^}]*\}", short.CSS)[0]
    path = tmp_path / "parity.json"
    path.write_text(json.dumps(cases))
    result = run_node("drop_card_parity.cjs", out / "index.html", out / "deals.json", path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_best_today_phone_cards_on_that_page_only(sites):
    off, on = sites["off"], sites["phone"]
    old = files(off)
    changed = {k for k, v in files(on).items() if old[k] != v}
    assert changed == {"best-deals-today/index.html", "index.html", "deals.json"}
    page = (on / phone.PAGE).read_text()
    assert page.count(phone.CSS) == 1 and page.count(phone.CSS + "</head>") == 1
    assert page.replace(phone.CSS, "", 1) == (off / phone.PAGE).read_text()
    assert (on / "index.html").read_text().replace(version(on), version(off)) == (off / "index.html").read_text()
    # Phones only, and only the card grid: no header, tab or page-heading rules.
    assert phone.CSS.startswith("<style>@media(max-width:640px){") and phone.CSS.endswith("}}</style>")
    assert phone.CSS.count("@media") == 1
    rules = re.findall(r"([^{}]+)\{[^{}]*\}", phone.CSS.removeprefix("<style>@media(max-width:640px){"))
    assert rules and all(sel.startswith(".grid") for rule in rules for sel in rule.split(","))
    both = (sites["both"] / phone.PAGE).read_text()
    assert both.count(phone.CSS) == 1 and both.count(short.HUB_CSS) == 1


@pytest.mark.parametrize("first_screen", [True, False])
def test_home_phone_budget_with_short_cards(tmp_path, first_screen):
    """The phone budgets and the 1,000-byte HTML reserve hold (test_phone_first_screen's fixture): the cards'
    headline sentences leave the home HTML, the lines, How box and CSS come in; with phone_first_screen
    off too, the hidden ticker's styles make the CSS room."""
    cfg = pre_best_today_tab(pre_front_mix(load_config()))  # the pill's bytes: test_best_today_tab
    cfg.short_cards, cfg.phone_first_screen = True, first_screen
    cfg.phone_budget["enabled"] = True

    def inspect(db, out):
        report = analyze(out, cfg, expected_offers=visible_offer_count(cfg))
        assert report["ok"], report
        reserve = cfg.phone_budget["home_html_gzip_max_bytes"] - report["metrics"]["home_html"]["gzip"]
        print("short_cards home gzip:", json.dumps({k: v["gzip"] for k, v in report["metrics"].items()}), "reserve", reserve)
        assert reserve >= 1000
        home = (out / "index.html").read_text()
        assert home.count(short.ANCHOR + short.CSS) == 1 and ".tk{--tk-teal" not in home
        assert "//" not in short.LINES  # the shipped home script carries no comments (test_home_comment_strip)
    publish_fixture(tmp_path, cfg, inspect=inspect)


def test_browser_short_cards_and_best_today_phone_cards(sites):
    """Real Chromium: no headline, two-line titles, the steps and code lines and the smaller Get deal on every
    card (home, best today; 1440x900, 390x844), the first-screen counts of /best-deals-today/ on phones, taps
    and 44px targets kept, best_today_phone_cards leaves laptops unchanged."""
    result = subprocess.run(["node", str(BROWSER), *(str(sites[k]) for k in ("off", "short", "phone", "both"))],
                            capture_output=True, text=True, timeout=600, cwd=BROWSER.parent.parent)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    report = json.loads(result.stdout.splitlines()[-1])
    print(json.dumps(report))
    assert report["first_screen"]["390x844"]["plain"] >= 3  # the operator's goal when none is a step deal
