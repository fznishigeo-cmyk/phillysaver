"""Ticker deals are links (operator, 2026-10-15: "the products should be
clickable"), led by the front page's top picks."""
import json

from dealradar.publish import seo
from tests.test_greyed_just_missed import js_function, run_js

ROWS = [
    {"key": "a", "item": "Lamp A", "pick": 2, "pct_at_checkout": 50, "final_price": 5,
     "store": "Target", "category": "home", "deal_path": "/deal/x/", "url": "https://example.com/a?x=1&y=2"},
    {"key": "b", "item": "Lamp B", "pct_at_checkout": 95, "final_price": 4, "store": "Woot",
     "category": "electronics"},
    {"key": "c", "item": "Lamp C", "pick": 1, "pct_at_checkout": 40, "final_price": 9,
     "store": "Amazon", "category": "kitchen", "deal_path": "/deal/lamp-c-abcdef12/",
     "url": "https://example.com/c"},
    {"key": "d", "item": "Lamp D", "pct_at_checkout": 80, "final_price": 3, "store": "Kohl's",
     "category": "home", "url": "https://example.com/d"},
] + [{"key": f"l{i}", "locked": True, "pct_at_checkout": 90, "store": "Walmart"} for i in range(3)]

HARNESS = '''
var state = {deals: JSON.parse(require("fs").readFileSync(0, "utf8")), shown: 0};
var TICKER_MAX = 16, TICKER_PX_S = 38, tickerSig = "", PAGE_CARDS = 24;
var num = x => x == null ? null : Number(x), offer = () => ({trial: false, days: 0});
var isLocked = d => d.locked === true, isMember = () => false, dealKey = d => d.key;
var esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
var events = [], steps = 0, handler = null;
var track = (name, params) => events.push({name: name, params: params});
var nextStep = () => { steps++; };
var cardEls = () => [], filtered = () => [], ordered = rows => rows, render = () => {}, reduceMotion = () => true;
var els = {}, el = id => els[id] = els[id] ||
  {classList: {remove() {}}, style: {setProperty() {}}, scrollWidth: 800, innerHTML: "", hidden: false};
var document = {getElementById: id => id === "tkband"
  ? {addEventListener: (type, fn) => { handler = fn; }} : el(id)};
function click(attrs) {
  var item = {getAttribute: n => n in attrs ? attrs[n] : null, hasAttribute: n => n in attrs};
  handler({target: {closest: () => item}});
}
'''


def _click_handler():
    source = (seo.TEMPLATES_DIR / "index.html").read_text()
    start = source.index('  document.getElementById("tkband").addEventListener("click", function (e) {')
    return source[start:source.index("\n  });", start) + 6]


def _run(tail):
    script = HARNESS + js_function("tickerItems") + "\n" + js_function("paintTickerNow") + "\n"
    return run_js(script + _click_handler() + "\n" + tail, ROWS)


def test_top_picks_lead_then_biggest_discounts():
    items = _run("console.log(JSON.stringify(tickerItems()));")
    assert [it["key"] for it in items] == ["c", "a", "b", "l2", "d"]
    links = {it["key"]: (it["href"], it["out"]) for it in items}
    assert links["c"] == ("/deal/lamp-c-abcdef12/", False)  # our deal page, like the card title
    assert links["a"] == ("https://example.com/a?x=1&y=2", True)  # malformed page: the deal link
    assert links["b"][0] == "" and links["l2"][0] == ""  # nothing to open; locked never links


def test_each_free_deal_renders_as_a_link_out_of_the_tab_order():
    html = _run("paintTickerNow(); console.log(JSON.stringify(el('tkstrip').innerHTML));")
    assert html.count('<a class="tkitem" data-k="c" href="/deal/lamp-c-abcdef12/" tabindex="-1">') == 2
    assert html.count('<a class="tkitem" data-k="a" href="https://example.com/a?x=1&amp;y=2" '
                      'tabindex="-1" target="_blank" rel="sponsored noopener">') == 2
    assert html.count('<span class="tkitem" data-k="b">') == 2
    assert html.count('<span class="tkitem" data-k="l2" data-locked="1">') == 2
    assert html.count("</a>") == 6 and "Walmart" in html


def test_clicks_open_track_or_take_the_next_step():
    result = _run('''
paintTickerNow();
click({"data-k": "a", href: "https://example.com/a", target: "_blank"});
click({"data-k": "c", href: "/deal/lamp-c-abcdef12/"});
click({"data-k": "l2", "data-locked": "1"});
click({"data-k": "b"});
console.log(JSON.stringify({events: events, steps: steps}));''')
    assert result == {"events": [{"name": "deal_click", "params": {
        "store": "Target", "category": "home", "method": "ticker"}}], "steps": 1}
    assert json.dumps(result).count("Lamp") == 0  # the item never reaches analytics
