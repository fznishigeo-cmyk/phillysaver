"""Operator 2026-10-23 (A51, A52): the typed-words box's two Amazon links, one switch per tool."""
import json
import re
import shutil
import subprocess

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import word_links
from dealradar.publish.deal_spotter import shared_functions
from tests.test_tools_merge import build, files

SWITCHES = ("review_checker_word_links", "deal_spotter_word_links")
RC_BLUE = "https://www.amazon.com/s?k=vacuum&rh=p_n_deal_type%3A210906366011&tag=example-20"
RC_YELLOW = "https://www.amazon.com/s?k=vacuum&rh=p_n_deal_type%3A23566065011&s=exact-aware-popularity-rank&tag=example-20"
SPOTTER_BLUE = "https://www.amazon.com/s?k=vacuum&rh=p_n_deal_type%3A23566064011&tag=example-20"
SPOTTER_YELLOW = "https://www.amazon.com/s?k=vacuum&rh=p_n_deal_type%3A210906366011&s=exact-aware-popularity-rank&tag=example-20"
# Both tools before (and with the switches off): Featured + coupons under the $50 cap; Amazon's review sort.
OLD_BLUE = "https://www.amazon.com/s?k=vacuum&rh=p_n_deal_type%3A210906366011%2Cp_36%3A-5000&tag=example-20"
OLD_YELLOW = "https://www.amazon.com/s?k=vacuum&s=review-rank&tag=example-20"
PAGES = {"review-checker.js": ("review-checker/index.html", "reviewmeta-alternative/index.html"),
         "deal-spotter.js": ("deal-spotter/index.html", "fakespot-alternative/index.html",
                             "amazon-deal-checker/index.html", "amazon-discount-finder/index.html")}
# The published words box (namePanel) on a minimal DOM: each typed text's links as a browser builds them.
WORDS_BOX = r"""
const fs = require('node:fs'), vm = require('node:vm');
class Node {
  constructor(tag) { Object.assign(this, {tag, id:'', className:'', textContent:'', hidden:false, dataset:{}, children:[]}); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute() {}
}
const text = n => typeof n === 'string' ? n : n.children.length ? n.children.map(text).join('') : n.textContent;
const links = n => typeof n === 'string' ? [] : (n.tag === 'a' ? [n] : []).concat(...n.children.map(links));
const scope = {URL, document:{createElement:tag => new Node(tag)}};
vm.createContext(scope);
vm.runInContext(fs.readFileSync(0, 'utf8') + '\nfunction wordsDeals() {}', scope);
const panel = new Node('div'), result = {};
panel.id = 'name-results';
for (const raw of JSON.parse(process.argv[1])) {
  scope.namePanel(panel, raw);
  result[raw] = {hidden:panel.hidden, links:links(panel).map(a => [a.className, text(a), a.href, a.target, a.rel])};
}
console.log(JSON.stringify(result));
"""


def words_box(script, *typed):
    lines = [re.search(r"^var " + name + r" ?=.*$", script, re.M)[0] for name in ("toolSettings", "analyzeExclude", "nameHint")]
    code = "\n".join(lines + shared_functions(script, ("analyzeInput", "analyzeAdult", "searchAmazonQuery", "toolQuery",
                                                        "discountsLink", "toolDiscount", "highlyRatedUrl",
                                                        *(("ratedWords",) if "function ratedWords(" in script else ()),
                                                        "namePanelRated", "namePanel")))
    result = subprocess.run(["node", "-e", WORDS_BOX, json.dumps(typed)], input=code, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def box(blue, yellow):
    # The yellow one names the plural (plural_highly_rated, operator 2026-10-24); its link keeps "vacuum".
    return {"hidden": False, "links": [
        ["words-discounts", "See vacuum discounts on Amazon →", blue, "_blank", "sponsored noopener"],
        ["name-button", "See highly rated vacuums on Amazon →", yellow, "_blank", "sponsored noopener"]]}


@pytest.fixture(scope="module")
def sites(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("word-links")
    with pytest.MonkeyPatch.context() as monkeypatch:
        on, _ = build(tmp, monkeypatch, True, "on")
        off, _ = build(tmp, monkeypatch, True, "off", **dict.fromkeys(SWITCHES, False))
        # The same build with the feature removed altogether.
        monkeypatch.setattr(word_links, "publish", lambda out, cfg: None)
        without, _ = build(tmp, monkeypatch, True, "without")
    return {"on": on, "off": off, "without": without}


def test_switches_shipped_on_default_off_strict(tmp_path):
    cfg = load_config()
    assert cfg.review_checker_word_links is True and cfg.deal_spotter_word_links is True  # operator 2026-10-23 A51/A52
    assert all(word_links.enabled(cfg, name) for name in word_links.TOOLS)
    (tmp_path / "missing.yaml").write_text("{}\n")
    missing = load_config(tmp_path / "missing.yaml")
    for key in SWITCHES:
        assert getattr(AppConfig(), key) is False and getattr(missing, key) is False
        path = tmp_path / f"{key}.yaml"
        path.write_text(f"{key}: 1\n")
        with pytest.raises(ValueError, match=f"^{key} must be a boolean$"):
            load_config(path)
    # The words box exists only in the merged tools.
    alone = AppConfig(**dict.fromkeys(SWITCHES, True))
    assert not any(word_links.enabled(alone, name) for name in word_links.TOOLS)


def test_review_checker_words_box(sites):
    found = words_box((sites["on"] / "review-checker.js").read_text(), "vacuum", "vibrator")
    assert found["vacuum"] == box(RC_BLUE, RC_YELLOW)
    assert found["vibrator"] == {"hidden": True, "links": []}  # adult words: still no box


def test_spotter_and_keyword_pages_words_box(sites):
    found = words_box((sites["on"] / "deal-spotter.js").read_text(), "vacuum", "vibrator")
    assert found["vacuum"] == box(SPOTTER_BLUE, SPOTTER_YELLOW)
    assert found["vibrator"] == {"hidden": True, "links": []}


def test_pages_load_the_new_scripts_and_other_exits_keep_the_cap(sites):
    on, off = sites["on"], sites["off"]
    for name, pages in PAGES.items():
        new, old = (on / name).read_text(), (off / name).read_text()
        src = '<script defer src="/' + name + '?v='
        for page in pages:
            html = (on / page).read_text()
            assert src + word_links._version(new) + '"' in html, page
            assert html == (off / page).read_text().replace(src + word_links._version(old), src + word_links._version(new)), page
        # Only the words box's two URL builders change; the cap stays on every other exit.
        changed = [line for line in new.splitlines() if line not in old.splitlines()]
        assert len(changed) == 2 and "p_36" not in "".join(changed) and "p_36" in old
    spotter = (on / "deal-spotter.js").read_text()
    assert "p_n_deal_type%3A23566065011%2Cp_36%3A-5000&" in re.search(r"^function toolSimilar\(.*?^\}", spotter, re.S | re.M)[0]
    for page in ("index.html", "black-friday/index.html"):
        html = (on / page).read_text()
        hits = re.findall(r'p_n_deal_type%3A\d+[^"\'&]*&', html)
        assert hits and all("%2Cp_36%3A-5000&" in hit for hit in hits) and html == (off / page).read_text()


def test_switches_off_keep_every_byte(sites):
    off = files(sites["off"])
    assert off == files(sites["without"])
    for name in PAGES:
        assert words_box(off[name].decode(), "vacuum")["vacuum"] == box(OLD_BLUE, OLD_YELLOW), name
    on = files(sites["on"])
    assert set(on) == set(off)
    changed = {name for name in on if on[name] != off[name]}
    assert changed == {name for script, pages in PAGES.items() for name in (script, *pages)}


def test_each_switch_alone_changes_only_its_tool(sites, tmp_path):
    on, off = files(sites["on"]), files(sites["off"])
    for switches, scripts in (((True, False), ("review-checker.js",)), ((False, True), ("deal-spotter.js",)),
                              ((True, True), tuple(PAGES)), ((False, False), ())):
        tree = tmp_path / "-".join(map(str, switches))
        shutil.copytree(sites["off"], tree)
        cfg = load_config()
        for key, value in zip(SWITCHES, switches):
            setattr(cfg, key, value)
        word_links.publish(tree, cfg)
        expected = {name for script in scripts for name in (script, *PAGES[script])}
        found = files(tree)
        assert {name for name in found if found[name] != off[name]} == expected, switches
        assert all(found[name] == on[name] for name in expected), switches
