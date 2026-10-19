"""Task 278: literal-safe home stripping, original byte pins and offline QA."""

import json
import socket
import subprocess
from pathlib import Path

import httpx
import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import comments, site
from scripts.publication_budget import analyze
from tests.phone_budget_fixture import Inline, NOW, publish_fixture, visible_offer_count
from tests.test_offscreen_cards import offers
from tests.test_publish_site import make_deal


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task278 network unavailable")
    monkeypatch.setattr(httpx.Client, "send", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(site, "util_now", lambda: NOW)


@pytest.mark.parametrize("value", [False, True])
def test_boolean_and_shipped_config(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"site": {"strip_comments": value}}))
    assert load_config(path).strip_comments is value
    path.write_text("{}")
    assert load_config(path).strip_comments is AppConfig().strip_comments is False
    assert load_config().strip_comments is True


@pytest.mark.parametrize("value", [0, 1, "true", "false", None, [], {}])
def test_invalid_config(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"site": {"strip_comments": value}}))
    with pytest.raises(ValueError, match=r"^site.strip_comments must be a boolean$"):
        load_config(path)


@pytest.mark.parametrize("literal", [
    r'"https://example.invalid/a/*keep*/"', r"'// keep'",
    r'/[/*`"\u0027]+\/\/example/', r'/https?:\/\/[^/]+\/\*keep\*\//',
    '`first\n\n\n// keep\n/* keep */\nlast`',
    '`outer ${`inner ${"}" /* keep */}`}\n// keep\n`',
    '`outer ${ /[}`"\u0027]/.test("}") ? `nested` : "// keep" }\n// keep\n`',
    '"continued\\\n// keep"',
])
def test_js_literals_are_exact(literal):
    source = "// remove\nconst value = " + literal + "; // trailing stays\n// remove\n"
    expected = "\nconst value = " + literal + "; // trailing stays\n\n"
    assert comments.javascript(source) == expected
    assert comments.strip("<script>" + source + "</script>").count(literal) == 1


def test_js_comments_division_control_regex_and_asi():
    source = '''// remove
const x = 12 / 3 / 2;
const a = x / 2; const s = "// stays";
const object = {} / 2; const template = `https://example.invalid/
// stays in template
`;
const fn = function () {} / 2; const t = `https://example.invalid/
// stays in template
`;
if (x) /[/*`'"{}]/.test(s);
while (false) /[/*`'"{}]/.test(s);
function f() { return /[/*`'"{}]/; }
// remove
/* remove
   remove */
const b = 1; /* trailing stays */
/* not alone */ const c = 2;
const d = /* inline stays */ 3;
function asi() { return
// remove
{x: 1}; }
'''
    result = comments.javascript(source)
    assert "remove" not in result
    assert result.count("// stays in template") == 2
    assert all(text in result for text in ("12 / 3 / 2", "trailing stays", "not alone", "inline stays"))
    script = 'const vm=require("node:vm"); const code=JSON.parse(process.argv[1]);'
    script += 'const a=vm.runInNewContext(code+"; [x,a,b,c,d,asi()]"); console.log(JSON.stringify(a));'
    run = subprocess.run(["node", "-e", script, json.dumps(result)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == [2, 1, 1, 2, 3, None]


@pytest.mark.parametrize("newline", ["\n", "\r", "\r\n", "\u2028", "\u2029"])
def test_js_line_terminators_never_remove_following_code(newline):
    source = ("// remove" + newline + "const value = 1; // trailing stays" + newline
              + "/* remove" + newline + "remove */" + newline + "const other = 2;")
    result = comments.javascript(source)
    assert "remove" not in result
    assert "const value = 1; // trailing stays" in result and "const other = 2;" in result
    assert result.count(newline) == source.count(newline)
    expected = ("<script>" + newline + "const value = 1; // trailing stays" + newline * 2
                + "const other = 2;</script>")
    assert comments.strip("<script>" + source + "</script>") == expected
    assert comments.strip(expected) == expected


def test_css_comments_literals_urls_escapes_and_token_boundaries():
    source = r'''/* remove */
.a/**/.b {content:"/* keep */ // keep"; --quoted:'/* keep */';
background:url(data:image/svg+xml;/*keep*/); --url:url("https://example.invalid/*keep*/");
--escaped:\/*keep*/; --separate:red/**/blue; color:/* remove */red;}
'''
    result = comments.css(source)
    assert "remove" not in result
    for text in ('.a.b', '"/* keep */ // keep"', "'/* keep */'", 'url(data:image/svg+xml;/*keep*/)',
                 'url("https://example.invalid/*keep*/")', r'\/*keep*/', 'red blue'):
        assert text in result
    assert comments.css(result) == result


def test_html_audited_markers_conditionals_attributes_and_preformatted_literals():
    markers = "".join("<!-- " + prefix + name + " -->" for name in sorted(comments.MARKERS)
                      for prefix in ("", "/"))
    literal = '<pre>first\n\n\nlast</pre><textarea><!--keep-->\n\n\nlast</textarea>'
    source = ('<!--remove-->\n\n\n' + markers + '<!--[if IE]>keep<![endif]--><!--<![endif]-->'
              '<div title="<!--attribute-->">x</div>' + literal
              + '<script type="application/json">{"text":"// keep <!--keep-->"}</script>'
              + '<script>const text = "<!--keep-->";\n// remove\n</script>')
    result = comments.strip(source)
    assert "remove" not in result
    assert markers in result and literal in result
    assert '<!--[if IE]>keep<![endif]--><!--<![endif]-->' in result
    assert 'title="<!--attribute-->"' in result
    assert '{"text":"// keep <!--keep-->"}' in result
    assert result.startswith("\n\n")
    assert comments.strip(result) == result


def test_only_home_changes_and_off_does_not_touch_bytes_or_mtime(tmp_path):
    before = '<!--remove-->\r\n<style>/* remove */ .a{color:red}</style>'
    home, other = tmp_path / "index.html", tmp_path / "other.html"
    home.write_bytes(before.encode())
    other.write_bytes(before.encode())
    mtime = home.stat().st_mtime_ns
    cfg = AppConfig()
    comments.publish(tmp_path, cfg)
    assert home.read_bytes() == before.encode() and home.stat().st_mtime_ns == mtime
    assert comments.site_version("old", cfg) == "old"
    cfg.strip_comments = True
    assert comments.site_version("old", cfg) != "old"
    comments.publish(tmp_path, cfg)
    assert "remove" not in home.read_text()
    assert other.read_bytes() == before.encode()
    mtime = home.stat().st_mtime_ns
    comments.publish(tmp_path, cfg)
    assert home.stat().st_mtime_ns == mtime


def files(out):
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}


def assert_parses(out):
    home = (out / "index.html").read_text()
    assert comments.strip(home) == home
    parser = Inline(out)
    parser.feed(home)
    code = '''const vm=require('node:vm');
for (const script of JSON.parse(require('node:fs').readFileSync(0,'utf8'))) new vm.Script(script);
console.log('PASS: every executable inline script parses');'''
    # Inline also collects own external scripts, giving them the same parse check.
    result = subprocess.run(["node", "-e", code], input=json.dumps([p.decode() for p in parser.parts["js"]]),
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for style in parser.parts["css"]:
        text, at, depth = style.decode(), 0, 0
        while at < len(text):
            if text[at] in "\"'":
                at = comments._quoted(text, at)
                continue
            if text.startswith("/*", at):
                at = text.index("*/", at + 2) + 2
                continue
            depth += (text[at] == "{") - (text[at] == "}")
            assert depth >= 0
            at += 1
        assert depth == 0


@pytest.mark.parametrize("mode", ["support", "open", "closed", "shipped"])
def test_final_page_parses_and_enabled_rerun_is_idempotent(tmp_path, mode):
    cfg = load_config() if mode == "shipped" else AppConfig(
        support_enabled=mode == "support", paywall_enabled=True,
        paywall_open_access=mode == "open", engagement={"saved_deals": True})
    cfg.strip_comments = False
    if mode == "closed":
        # Isolate stripping from the deliberate free-eight rotation between
        # direct renders; pipeline same-key idempotency is checked separately.
        cfg.paywall_teaser_pool = cfg.paywall_teaser_count
    db = DB(":memory:")
    out = tmp_path / "site"
    rows = offers()
    try:
        def render():
            return site.render_site(db, cfg, NOW, out_dir=out, deals=rows,
                                    last_run={"run_key": "task278-fixed"})
        old_version = render()["full_payload"]["meta"]["site_version"].encode()
        before = files(out)
        cfg.strip_comments = True
        new_version = render()["full_payload"]["meta"]["site_version"].encode()
        after = files(out)
        assert old_version != new_version and after.keys() == before.keys()
        for name, body in before.items():
            expected = body.replace(old_version, new_version)
            if name == "index.html":
                expected = comments.strip(expected.decode()).encode()
                assert len(expected) < len(body) - 5000
            assert after[name] == expected, name
        assert_parses(out)
        state = list(db.conn.iterdump())
        render()
        assert files(out) == after
        assert list(db.conn.iterdump()) == state
        cfg.strip_comments = False
        render()
        assert files(out) == before
    finally:
        db.close()


def test_enabled_shipped_phone_budget_with_headroom(tmp_path):
    cfg = load_config()
    cfg.phone_budget["enabled"] = True
    measurements = {}
    for enabled in (False, True):
        cfg.strip_comments = enabled
        def inspect(db, out):
            expected = visible_offer_count(cfg)
            report = analyze(out, cfg, expected_offers=expected)
            measurements[enabled] = report["metrics"]
            if enabled:
                assert report["ok"], report
                assert report["metrics"]["home_html"]["gzip"] < cfg.phone_budget["home_html_gzip_max_bytes"] - 1000
                assert_parses(out)
        publish_fixture(tmp_path / str(enabled), cfg, inspect=inspect)
    for metric in ("home_html", "home_js", "home_css", "deals_json"):
        print(f'{metric} OFF→ON gzip bytes: {measurements[False][metric]["gzip"]}→{measurements[True][metric]["gzip"]}')
    assert measurements[True]["home_html"]["gzip"] < measurements[False]["home_html"]["gzip"]


def test_chromium_home_on_matches_off(tmp_path):
    cfg = load_config()
    cfg.affiliate_amazon_tag = "example-20"
    cfg.tracking = {}  # Public tracking IDs have no transport in this offline smoke.
    # Exercise stripping against every other shipped switch, with real-shaped
    # qualified Buy Box facts instead of Unknown Amazon prices.
    rows = [make_deal(dedupe_key=f"task278-{i}", item=f'{"Kitchen kettle" if i % 2 else "Beauty serum"} {i}',
                      category="kitchen" if i % 2 else "beauty", codes=[], conditions=["new"],
                      store="Amazon", canonical_url=f"https://www.amazon.com/dp/B{i:09d}", asin=f"B{i:09d}",
                      price_rechecked_at=NOW.isoformat(), first_seen_at=NOW.isoformat(),
                      critic={"plausible": True, "confidence": .9, "reason": "Backed basis"},
                      click_price_check=dict(asin=f"B{i:09d}", final=10, buybox=10, at=NOW.isoformat()),
                      image_url="https://images.example.invalid/photo.png") for i in range(180)]
    for enabled in (False, True):
        cfg.strip_comments = enabled
        db = DB(":memory:")
        try:
            result = site.render_site(db, cfg, NOW, out_dir=tmp_path / str(enabled), deals=rows)
            assert len(result["public_deals"]) == 180
            assert not any(d.get("click_price_unknown") for d in result["public_deals"])
        finally:
            db.close()
    result = subprocess.run(["node", str(Path(__file__).with_name("home_comment_strip_browser.cjs")),
                             str(tmp_path / "False"), str(tmp_path / "True")],
                            capture_output=True, text=True, timeout=180)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout)
