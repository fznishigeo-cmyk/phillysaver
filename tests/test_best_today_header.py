"""Operator (Nishi) 2026-10-24 ~02:00Z: "best deals today removes the search bar... please fix and
push it through" -> /best-deals-today/ wears the home header."""
import re
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import (best_today_header, best_today_tab, hide_timer_ticker, search_focus_ring, site,
                                tool_buttons_outside)
from tests.history_pins import pre_social_profiles
from tests.test_publish_site import make_deal
from tests.test_support import NOW, render

BEST = "best-deals-today/index.html"
HEADER = re.compile(r"<header>.*?</header>\n", re.S)


def rows():
    return [make_deal(dedupe_key=f"best-header-{i}", item=f"Electric kettle 1.7L model {i}", store="Amazon",
                      category="kitchen", verify_tier="verified", codes=[], conditions=["new"],
                      final_price=24.99 + i, regular_price=49.99, asin=f"B{i:09d}",
                      canonical_url=f"https://amazon.com/dp/B{i:09d}", first_seen_at=NOW.isoformat(),
                      price_rechecked_at=NOW.isoformat(),
                      critic={"plausible": True, "confidence": .9, "reason": "Backed basis"},
                      image_url="https://m.media-amazon.com/images/I/photo.jpg") for i in range(6)]


def build(tmp_path, monkeypatch, on, name="site"):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = load_config()
    if on is None:
        del cfg.best_today_header
    else:
        cfg.best_today_header = on
    (tmp_path / name).mkdir()
    return render(tmp_path / name, cfg, rows())[0]


def files(out):
    return {str(p.relative_to(out)): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


def test_shipped_on_default_off_strict(tmp_path):
    assert load_config().best_today_header is True  # operator 2026-10-24 ~02:00Z
    assert AppConfig().best_today_header is False
    assert pre_social_profiles(AppConfig(best_today_header=True)).best_today_header is False
    for value in ("1", "'true'", "null", "[]"):
        path = tmp_path / "config.yaml"
        path.write_text(f"best_today_header: {value}\n")
        with pytest.raises(ValueError, match="^best_today_header must be a boolean$"):
            load_config(path)



def test_needs_the_home_passes_it_copies():
    """Codex review 10-24: without header_polish, tools_merge or page_polish the copy would fail
    (tabs outside <header>) or misroute (old tool paths, no ?analyze= handler); the page keeps
    its own header instead."""
    cfg = load_config()
    assert best_today_header.enabled(cfg)
    for name in ("header_polish", "tools_merge", "page_polish"):
        off = load_config()
        setattr(off, name, False)
        assert not best_today_header.enabled(off), name

def test_off_and_missing_change_no_byte(tmp_path, monkeypatch):
    off = files(build(tmp_path, monkeypatch, False, "off"))
    assert files(build(tmp_path, monkeypatch, None, "missing")) == off
    # The same build with the pass removed altogether.
    monkeypatch.setattr(best_today_header, "publish", lambda out, cfg, now: None)
    assert files(build(tmp_path, monkeypatch, False, "without")) == off
    assert b"refreshed every 15 minutes" in off[BEST] and b'id="q"' not in off[BEST]


def test_on_gives_the_page_the_home_header_and_nothing_else_moves(tmp_path, monkeypatch):
    before = files(build(tmp_path, monkeypatch, False, "off"))
    on = build(tmp_path, monkeypatch, True, "on")
    after = files(on)
    # The home page, deals.json and every other page keep their bytes.
    assert after.keys() == before.keys()
    assert {k for k in after if after[k] != before[k]} == {BEST}
    home, best, old = after["index.html"].decode(), after[BEST].decode(), before[BEST].decode()
    header = HEADER.search(best)[0]
    # The home's own header, but for the logo (a paragraph, linking home), the current pill,
    # the live-count line the home's script draws and the (hidden) drop timer it runs.
    tagline = best_today_header.tagline(6, True)
    assert tagline in header and ">6 live deals<" in tagline and ">6 verified deals<" in tagline
    copied = (header.replace('<p class="brand"><a href="/"', '<h1 class="brand"><a href="./"')
              .replace('</a></p>', '</a></h1>', 1)
              .replace('<a class="cattab" href="/" data-i="0">', '<a class="cattab active" href="/" data-i="0">')
              .replace('<a class="cattab active" aria-current="page">Best today</a>', best_today_tab.PILL)
              .replace(tagline, '<span id="tagline" class="tag">verified deals</span>'))
    timer = re.search(r'\n *<div id="drop"[^>]*>.*?</div>', home, re.S)[0]
    assert 'id="drop"' not in best and timer in home and "#drop" in home  # hidden there: hide_timer_ticker
    assert copied == HEADER.search(home)[0].replace(timer, "")
    for part in ('<input id="q" type="search"', 'data-tool-link="deal-spotter"', 'data-tool-link="review-checker"',
                 '>Join free<', '<nav id="cattabs"', '<a class="cattab active" aria-current="page">Best today</a>'):
        assert header.count(part) == 1, part
    assert 'href="/best-deals-today/"' not in header and "refreshed every" not in best
    assert best.count("<h1") == 1 and "<h1>Best deals today</h1>" in best
    # The page below the header is the same markup; scripts are added at its end.
    body = best[best.index(header) + len(header):]
    assert body.startswith(old[old.index(HEADER.search(old)[0]) + len(HEADER.search(old)[0]):old.index("</body>")])
    assert body.count(best_today_header.SCRIPT) == 1 and 'src="/join-loader.js?v=' in body
    assert best.count('<style id="home-header">') == 1
    # The passes that add to every page with a search box or a timer find nothing left to add.
    for again in (search_focus_ring, hide_timer_ticker, tool_buttons_outside):
        again.publish(on, load_config())
    assert files(on) == after
    # The same build again gives the same bytes.
    assert files(build(tmp_path, monkeypatch, True, "again")) == after


def test_tagline_matches_the_home_script():
    assert best_today_header.tagline(1, True) == (
        '<span id="tagline" class="tag"><span class="tag-full tag-lead">verified deals · </span>'
        '<span class="live-dot" aria-hidden="true"></span><span class="tag-full tag-count">1 live deal</span>'
        '<span class="tag-short">1 verified deal</span><span id="tagsince"></span></span>')
    assert ">2,300+ live deals<" not in best_today_header.tagline(2345, True)
    assert ">1,000+ live deals<" in best_today_header.tagline(2345, True)
    assert ">400+ verified deals<" in best_today_header.tagline(456, True)
    assert best_today_header.tagline(99, False) == (
        '<span id="tagline" class="tag">verified deals · <span class="live-dot" aria-hidden="true"></span>'
        '99 live deals<span id="tagsince"></span></span>')
    assert "Offers checked before publication" in best_today_header.tagline(0, True)


HEAD = ('<header><div class="hrow"><div class="search"><input id="q"><a class="tool"></a></div>'
        '<a class="getdeal" id="subscribe"></a><span id="tagline" class="tag"></span></div></header>\n')
PAGE = '<div class="wrap"><h1>T</h1><article class="card"><a class="getdeal"></a></article></div>'


def test_home_rules_kept_are_the_ones_that_reach_the_header():
    css = (":root{color-scheme:light dark;--a:1}@media (prefers-color-scheme:dark){:root{--a:2}.card{x:0}}"
           "body{font:15px/1.5 x;margin:0}*{box-sizing:border-box}header{color:red}.hrow{gap:1px}"
           ".card{x:1}.getdeal{y:1}.wrap,.hrow{z:1}:is(.search,#event-search) .tool{w:1}#controls,#q{v:1}"
           "@media(max-width:899px){.card .getdeal{v:1}.hrow a{u:1}}@keyframes k{to{opacity:0}}"
           "a::before{content:''}.hrow:has(.trialcta) #q{t:1}html:not([data-kbd]) #q:focus-visible{outline:0}")
    early, late = best_today_header.header_rules(css, HEAD, PAGE)
    assert early == ":root{color-scheme:light dark;--a:1}@media (prefers-color-scheme:dark){:root{--a:2}}"
    assert late == (":where(body) :where(header){font:15px/1.5 x;margin:0}*:where(header,header *){box-sizing:border-box}"
                    "header{color:red}.hrow{gap:1px}.getdeal:where(header,header *){y:1}.hrow{z:1}"
                    ":is(.search,#event-search) .tool{w:1}#q{v:1}@media(max-width:899px){.hrow a{u:1}}"
                    "@keyframes k{to{opacity:0}}a:where(header,header *)::before{content:''}"
                    ".hrow:has(.trialcta) #q{t:1}html:not([data-kbd]) #q:focus-visible{outline:0}")


def test_page_rules_stop_at_the_header():
    css = (":root{--a:1}body{margin:0;color:var(--fg);overflow-wrap:anywhere}header{background:#4fd8c6}"
           ".hrow{gap:4px}.wrap,.hrow{max-width:1360px}@media (max-width:640px){.tag{display:none}h1{x:1}}"
           "a{color:var(--link)}.getdeal{y:1}.getdeal:hover{z:1}.card .getdeal{w:1}p:not(.brand) a{u:1}")
    rules, reset = best_today_header.page_rules(css, HEAD, PAGE)
    assert rules == (":root{--a:1}body{margin:0;color:var(--fg);overflow-wrap:anywhere}.wrap{max-width:1360px}"
                     "@media (max-width:640px){h1:where(:not(header,header *)){x:1}}"
                     "a:where(:not(header,header *)){color:var(--link)}.getdeal:where(:not(header,header *)){y:1}"
                     ".getdeal:hover:where(:not(header,header *)){z:1}.card .getdeal{w:1}"
                     "p:not(.brand) a:where(:not(header,header *)){u:1}")
    assert reset == ":where(header){margin:initial;color:initial;overflow-wrap:initial}"


def test_in_real_chromium(tmp_path):
    result = subprocess.run(["node", str(Path(__file__).with_name("best_today_header_browser.cjs")), str(tmp_path)],
                            capture_output=True, text=True, timeout=600, cwd=Path(__file__).parent.parent)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
    for line in ("phone light: home header", "phone dark: home header", "laptop light: home header",
                 "laptop dark: home header", "phone: typing then Enter matches the home",
                 "laptop: typing then Enter matches the home"):
        assert line in result.stdout, result.stdout
