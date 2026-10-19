"""Reproduce audit join misses using the publisher and its emitted card renderer."""

import html
import json
import re
import subprocess
from html.parser import HTMLParser

from dealradar.publish.site import render_site
from tests.test_front_page import daily_deal, options, pick_deal
from tests.test_publish_site import NOW
from tests.test_seo import ROOT, cfg, db


class Text(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.feed(markup)

    def handle_data(self, data):
        self.parts.append(data)


def test_rendered_home_identifies_gone_and_price_only_spotlight(tmp_path, cfg, db, options):
    cfg.front_page = options
    cfg.front_page["top_picks"]["count"] = 12
    cfg.paywall_open_access = True
    deals = [pick_deal(dedupe_key=f"grid-{i}", item=f'Lamp & shade "{i}"',
                       first_seen_at="2026-10-09T12:00:00Z",
                       store=("Target", "Walmart", "Woot", "eBay")[i % 4]) for i in range(60)]
    deals += [pick_deal(dedupe_key="gone", item="Robot vacuum & mop", status="dead",
                       first_seen_at="2026-10-09T13:00:00Z",
                       death="confirmed", died_at="2026-10-09T13:30:00Z"),
              daily_deal(item="Today's desktop speaker", pct_at_checkout=None,
                         regular_price=None, abs_savings=None)]
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=deals)
    doc = json.loads((out / "deals.json").read_text())
    home = (out / "index.html").read_text()
    static_wall = home.split('<div id="content" data-clarity-unmask="true">')[1].split('<div id="paywall"')[0]
    assert "desktop speaker" in static_wall
    assert "data-key=" not in static_wall  # The keyed audit only sees browser-rendered cards.
    rendered = subprocess.run(
        ["node", str(ROOT / "tests/spotlight_logic.cjs"), str(out / "index.html"),
         str(out / "deals.json"), "--render-home"], capture_output=True, text=True, check=True).stdout
    cards = []
    for attrs, body in re.findall(r"<article\b([^>]*)>(.*?)</article>", rendered, re.S):
        key = re.search(r'data-key="([^"]*)"', attrs)
        if not key:
            continue
        title = re.search(r'<h3 class="title">(.*?)</h3>', body, re.S)[1]
        store = re.search(r'<span class="chip storechip">(.*?)</span>', body, re.S)[1]
        category = re.search(r'data-category="([^"]*)"', attrs + body)
        cards.append(dict(key=html.unescape(key[1]), item="".join(Text(title).parts).strip(),
                          store="".join(Text(store).parts).strip(),
                          category=html.unescape(category[1]) if category else ""))
    assert len(cards) >= 48
    cards = cards[:48]
    old_map = {d["key"]: d for d in doc["deals"] if d["status"] == "alive" and not d.get("locked")}
    missed = [c for c in cards if c["key"] not in old_map]
    assert {c["item"] for c in missed} == {"Robot vacuum & mop", "Today's desktop speaker"}
    assert any(c["item"] == "Robot vacuum & mop" for c in cards[:24])
    all_rows = {d["key"]: d for d in doc["deals"] + doc["spotlight"]}
    assert all(c["key"] in all_rows for c in cards)  # No missing product payload.
    assert any('& shade "' in c["item"] for c in cards)  # DOM text is decoded once.

    # Use the same resolution/check/report functions as the live walk, with the
    # actual JSON consumed by the renderer, including dead and spotlight rows.
    run = subprocess.run(["node", "-e", """
const fs = require('node:fs');
const {identifyFrontCards, frontPageFlags, frontPageFlagLine} = require('./scripts/site_check_logic.cjs');
const {cards, doc} = JSON.parse(fs.readFileSync(0, 'utf8'));
const check = publishes => frontPageFlags(identifyFrontCards(cards, publishes), {niche:['auto','other']});
const result = check([doc]);
const old = check([{deals:doc.deals.filter(d => d.status === 'alive')}]);
process.stdout.write(JSON.stringify({result, old, lines:old.flags.map(frontPageFlagLine)}));
"""], input=json.dumps(dict(cards=cards, doc=doc)), cwd=ROOT,
        capture_output=True, text=True, check=True)
    result = json.loads(run.stdout)
    assert result["result"] == {"ok": True, "flags": []}
    assert result["old"]["ok"] is False
    assert len(result["lines"]) == 2
    assert all(line.startswith("unidentified card #") for line in result["lines"])
