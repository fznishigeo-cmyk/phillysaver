"""keep_first_cards (quality loop 2026-10-23: phone LCP / taps), entirely offline."""
import hashlib
import json
import re
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import keep_first_cards as feature, site
from tests.history_pins import pre_keep_first_cards, pre_social_profiles
from tests.phone_budget_fixture import NOW, fixture_offers
from tests.test_search_amazon import run_node
from tests.test_support import NOW as TEST_NOW, render

PROBE = Path(__file__).with_name("phone_lcp_browser.cjs")


def build(root, on, rows=300):
    """The phone-budget offers (every fourth coded, so How boxes on the first screen); on=None: missing."""
    cfg = load_config()
    cfg.tracking = {}
    cfg.hide_deal_site_links = False  # keep the coded partner offers, as the budget fixture does
    if on is None:
        del cfg.keep_first_cards
    else:
        cfg.keep_first_cards = on
    db = DB(":memory:")
    try:
        with patch.object(site, "util_now", lambda: NOW):
            site.render_site(db, cfg, NOW, out_dir=root, deals=fixture_offers()[:rows],
                             last_run={"run_key": "keep-first-cards"})
    finally:
        db.close()
    return root


def files(out):
    return {str(p.relative_to(out)): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


def wall(out):
    return re.sub(r"<script\b.*?</script>", "", (out / "index.html").read_text(), flags=re.S)


def test_switch_strict_default_shipped_and_pinned(tmp_path):
    assert load_config().keep_first_cards is True  # quality loop 2026-10-23
    assert AppConfig().keep_first_cards is False
    assert pre_keep_first_cards(load_config()).keep_first_cards is False
    assert pre_social_profiles(load_config()).keep_first_cards is False  # every older recorded fixture
    for value in (1, "true", None, [], {}):
        path = tmp_path / "config.yaml"
        path.write_text(json.dumps({"keep_first_cards": value}))
        with pytest.raises(ValueError, match="^keep_first_cards must be a boolean$"):
            load_config(path)
    cfg = load_config()
    cfg.drop_fresh = False
    assert not feature.enabled(cfg)  # it patches the drop module, nothing else
    assert not feature.enabled(type("Config", (), {"keep_first_cards": 1, "drop_fresh": True, "drop_shuffle": True})())


def test_off_missing_rerun_back_changes_no_byte(tmp_path, monkeypatch):
    off = files(build(tmp_path / "off", False, rows=120))
    with monkeypatch.context() as m:  # the same build without the feature at all
        m.setattr(feature, "enabled", lambda cfg: False)
        m.setattr(feature, "site_version", lambda version, cfg: version)
        m.setattr(feature, "publish", lambda out, cfg: None)
        assert files(build(tmp_path / "without", False, rows=120)) == off
    assert files(build(tmp_path / "missing", None, rows=120)) == off
    for name, data in off.items():
        for marker in (b"freshKeep", b"freshUI.keep(", b"freshHold", b"freshClock"):
            assert marker not in data, (name, marker)
    on = files(build(tmp_path / "on", True, rows=120))
    assert on != off and {k for k in on if on[k] != off.get(k)} == {"index.html", "deals.json", "drop-fresh.js"}
    assert files(build(tmp_path / "on", True, rows=120)) == on  # a rerun is byte-stable
    assert files(build(tmp_path / "on", False, rows=120)) == off  # and back


def test_static_wall_draws_the_script_how_box(tmp_path):
    out = build(tmp_path / "on", True, rows=120)
    cards = re.findall(r"<article class=\"card.*?</article>", wall(out), flags=re.S)
    assert len(cards) > 8 and sum("<div class=\"how\"" in card for card in cards[:8]) >= 1
    assert "<details class=\"how\"" not in wall(out)
    assert "<details class=\"how\"" in (out / "stores" / "woot" / "index.html").read_text()  # other pages keep theirs
    home = (out / "index.html").read_text()
    assert feature.KEEP in home and feature.RENDER not in home
    module = (out / "drop-fresh.js").read_text()
    version = re.search(r"/drop-fresh\.js\?v=([0-9a-f]{12})", home)[1]
    assert hashlib.sha256(module.encode()).hexdigest()[:12] == version
    assert "keep: freshKeep" in module and "var freshClock = host.dropPickIndex;" in module
    off = build(tmp_path / "off", False, rows=120)
    assert "<div class=\"how\"" not in wall(off) and "<details class=\"how\"" in wall(off)


@pytest.mark.parametrize("tidy", [True, False])
def test_static_how_box_matches_the_script(tmp_path, tidy):
    from tests.test_publish_site import make_deal
    cfg = load_config()
    cfg.card_tidy = tidy
    cfg.non_amazon_freshness_enabled = False
    out, doc, *_ = render(tmp_path, cfg, [make_deal(dedupe_key=f"how-{i}", item=f"Product{i} kitchen set",
                                                    category="kitchen", store="Walmart", codes=[], conditions=[],
                                                    image_url="https://images.example.invalid/card.png",
                                                    first_seen_at=TEST_NOW.isoformat()) for i in range(4)])
    base = dict(doc["deals"][0], status="alive")
    rows = [
        dict(base, key="code", needs_steps=True, how=["Enter code SAVE5 at checkout"], codes=["SAVE5"]),
        dict(base, key="steps", needs_steps=True, codes=["A&B'\"<1"],
             how=["Clip the coupon", "clip  the coupon!", "Add 2 to cart", "Use code A&B", "Check out <now>"]),
        dict(base, key="extra", needs_steps=False, how=[], codes=[],
             checkout=dict(base.get("checkout") or {}, card="Ships after Nov 1 & <soon>")),
        dict(base, key="extra-steps", needs_steps=True, how=["Subscribe & Save"], codes=["X1", "Y2"],
             checkout=dict(base.get("checkout") or {}, card="Prime only")),
        dict(base, key="bare", needs_steps=True, how=[], codes=[]),
        dict(base, key="plain", needs_steps=False, how=["Ignored"], codes=["NOPE"]),
        dict(base, key="dead", status="dead", needs_steps=True, how=["Gone"], codes=["OLD"]),
    ]
    cases = [dict(row=row, how=feature.how_html(row, cfg)) for row in rows]
    assert [bool(c["how"]) for c in cases] == [True, True, True, True, True, False, False]
    assert ('class="how-more"' in cases[1]["how"]) is tidy
    path = tmp_path / "parity.json"
    path.write_text(json.dumps(cases))
    result = run_node("drop_card_parity.cjs", out / "index.html", out / "deals.json", path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope="module")
def sites(tmp_path_factory):
    root = tmp_path_factory.mktemp("keep-first-cards")
    return {on: build(root / ("on" if on else "off"), on) for on in (True, False)}


def probe(out, now, delay=0, preserved=False):
    args = ["node", str(PROBE), str(out), "/opt/pw-browsers/chromium", "--now=" + now]
    args += [f"--deals-delay={delay}"] if delay else []
    args += ["--assert-preserved"] if preserved else []
    result = subprocess.run(args, capture_output=True, text=True, timeout=180, cwd=PROBE.parent.parent)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    return result, json.loads(result.stdout.splitlines()[0])


@pytest.mark.parametrize("minutes,delay", [(5, 0), (20, 0), (5, 1200)])
def test_browser_phone_first_screen_is_kept(sites, minutes, delay):
    """Real Chromium, 390x844, 4x CPU. Off: the module's first render replaces every server
    card (20 minutes on, a mark later, with a different drop) and paints the LCP anew.
    On: the same nodes stay, the first eight in place, and the wall ends as the same markup."""
    now = (NOW + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
    off_run, off = probe(sites[False], now, delay)
    assert off_run.returncode == 0, off_run.stderr[-3000:]
    assert off["errors"] == [] and off["blocked"] == []
    assert off["lostOriginal"] == len(off["originals"]) >= 60
    assert not any(item["connected"] for item in off["firstEight"])
    assert off["lcpScripted"]  # the largest text was painted again by the script
    on_run, on = probe(sites[True], now, delay, preserved=True)
    assert on_run.returncode == 0, on_run.stdout[-2000:] + on_run.stderr[-3000:]
    assert on["errors"] == [] and on["blocked"] == [] and on["hydratedAt"] is not None
    assert on["lostOriginal"] == 0 and on["removedFirstScreen"] == 0
    assert all(item["connected"] and item["sameParent"] for item in on["firstEight"])
    assert on["lcpScripted"] == []
    # The cards the visitor saw stay, in order.
    assert on["finalKeys"] == [item["key"] for item in on["originals"]]
    if minutes < 15:
        assert on["contentSha256"] == off["contentSha256"]  # no visible change: the same markup
    else:
        assert off["finalKeys"] != on["finalKeys"]  # off swapped in the clock's newer drop
