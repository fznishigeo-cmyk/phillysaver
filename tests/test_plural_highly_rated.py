"""Operator (Nishi) 2026-10-24 ~04:00Z: "See highly rated vacuums on Amazon" (plural_highly_rated)."""
import json
import re
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import plural_highly_rated
from tests.history_pins import pre_social_profiles
from tests.test_tools_merge import build, files
from tests.test_word_links import PAGES, words_box

TEMPLATE = (Path(plural_highly_rated.__file__).with_name("templates") / "tool-name-panel.js").read_text()
# The operator's table, then the refinements: anything not plainly safe stays as typed.
TABLE = {"vacuum": "vacuums", "air fryer": "air fryers", "iphone 15 case": "iphone 15 cases", "dress": "dresses",
         "watch": "watches", "battery": "batteries", "wireless headphones": "wireless headphones",
         "toilet paper": "toilet paper", "gift for mom": "gift for mom", "AirPods": "AirPods", "tv": "tv",
         "cookware": "cookware", "knife": "knives"}
MORE = {"glass": "glasses", "brush": "brushes", "box": "boxes", "toy": "toys", "shelf": "shelves", "bookshelf": "bookshelves",
        "office chair": "office chairs", "earring": "earrings", "jeans": "jeans", "scarf": "scarf", "tomato": "tomato",
        "wireless": "wireless", "dog harness": "dog harness", "headphones black": "headphones black", "ipad air": "ipad air",
        "hair": "hair", "apple": "apple", "apple watch": "apple watches", "shoes women": "shoes women",
        "computer mouse": "computer mouse", "dog food": "dog food", "footwear": "footwear", "salt & pepper": "salt & pepper",
        "Lamp": "Lamp", "big floor lamp set": "big floor lamp set", "case for iphone": "case for iphone"}
RUN = r"""
const fs = require('node:fs'), vm = require('node:vm'), scope = {};
vm.createContext(scope);
vm.runInContext(fs.readFileSync(0, 'utf8'), scope);
console.log(JSON.stringify(Object.fromEntries(JSON.parse(process.argv[1]).map(words => [words, scope.ratedWords(words)]))));
"""


def version(out):
    return json.loads((out / "deals.json").read_text())["meta"]["site_version"].encode()


def rated(script, typed):
    function = re.search(r"^function ratedWords\(.*?^\}", script, re.S | re.M)[0]
    result = subprocess.run(["node", "-e", RUN, json.dumps(list(typed))], input=function, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def sites(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("plural-highly-rated")
    with pytest.MonkeyPatch.context() as monkeypatch:
        on, _ = build(tmp, monkeypatch, True, "on")
        off, _ = build(tmp, monkeypatch, True, "off", plural_highly_rated=False)
        # The same build with the feature removed altogether.
        monkeypatch.setattr(plural_highly_rated, "name_panel", lambda cfg: TEMPLATE)
        monkeypatch.setattr(plural_highly_rated, "site_version", lambda version, cfg: version)
        without, _ = build(tmp, monkeypatch, True, "without", plural_highly_rated=False)
    return {"on": on, "off": off, "without": without}


def test_switch_shipped_on_default_off_strict_and_pinned(tmp_path):
    assert load_config().plural_highly_rated is True  # operator 2026-10-24 ~04:00Z
    assert AppConfig().plural_highly_rated is False
    assert pre_social_profiles(AppConfig(plural_highly_rated=True)).plural_highly_rated is False
    (tmp_path / "missing.yaml").write_text("{}\n")
    assert load_config(tmp_path / "missing.yaml").plural_highly_rated is False
    for value in ("1", "'true'", "null", "[]"):
        path = tmp_path / "config.yaml"
        path.write_text(f"plural_highly_rated: {value}\n")
        with pytest.raises(ValueError, match="^plural_highly_rated must be a boolean$"):
            load_config(path)


def test_patched_panel_and_the_plural_table():
    assert plural_highly_rated.name_panel(AppConfig()) == TEMPLATE  # the template itself never changes
    patched = plural_highly_rated.name_panel(AppConfig(plural_highly_rated=True))
    # One function more and one changed line: the button's text. Its link keeps the typed words.
    assert patched.replace(plural_highly_rated.PLURAL, "", 1).replace("ratedWords(words)", "words") == TEMPLATE
    assert "button.href = highlyRatedUrl(words);" in patched and patched.count("ratedWords(") == 2
    assert rated(patched, TABLE) == TABLE
    assert rated(patched, MORE) == MORE


def test_off_and_missing_change_no_byte(sites):
    off = files(sites["off"])
    assert off == files(sites["without"])
    assert not any(b"ratedWords" in data for data in off.values())


def test_on_both_tools_name_the_plural_and_link_the_typed_words(sites):
    on, off = files(sites["on"]), files(sites["off"])
    # Only the two scripts change, and the pages that load them (their ?v=), with the publication's version stamp.
    old, new = version(sites["off"]), version(sites["on"])
    assert old != new and on.keys() == off.keys()
    assert {name for name in on if on[name] != off[name].replace(old, new)} == {
        name for script, pages in PAGES.items() for name in (script, *pages)}
    for name in PAGES:
        script = on[name].decode()
        result = subprocess.run(["node", "--check", str(sites["on"] / name)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        assert rated(script, TABLE) == TABLE, name
        found = words_box(script, "vacuum", "wireless headphones")
        yellow = {typed: box["links"][-1] for typed, box in found.items()}
        assert yellow["vacuum"][1] == "See highly rated vacuums on Amazon →" and "?k=vacuum&" in yellow["vacuum"][2]
        assert yellow["wireless headphones"][1] == "See highly rated wireless headphones on Amazon →"
        old = words_box(off[name].decode(), "vacuum")["vacuum"]["links"]
        assert old[-1][1] == "See highly rated vacuum on Amazon →" and found["vacuum"]["links"][:-1] == old[:-1]
        assert found["vacuum"]["links"][-1][2:] == old[-1][2:]  # same link, tab and rel
