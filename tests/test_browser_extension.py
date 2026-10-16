"""Extension packaging, Python/JS identity parity and privacy-only site changes."""

import hashlib
import json
import re
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from dealradar import build_extension, cli
from dealradar.config import AppConfig, load_config
from dealradar.publish import checker, site
from tests.test_publish_site import NOW as INDEX_NOW
from tests.test_pwa import baseline_config, sample_deals
from tests.test_support import NOW, render

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extension"
ROWS = json.loads((ROOT / "tests/fixtures/checker_aliases.json").read_text())


def node(code, payload=None):
    result = subprocess.run(["node", "-e", code], input=json.dumps(payload), text=True,
                            cwd=ROOT, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def identities():
    return node("""
const fs=require('fs'), w=require('./extension/lookup.js'), r=require('./extension/rules.json');
(async()=>{
 const results=[];
 for(const row of JSON.parse(fs.readFileSync(0,'utf8'))) {
   const aliases=w.aliases(row.url,r), shards=[];
   for(const n of [16,32,64,128,256,512,1024]) shards.push(await Promise.all(aliases.map(a=>w.shard(a,n))));
   results.push({aliases,shards});
 }
 console.log(JSON.stringify(results));
})();
""", ROWS)


@pytest.mark.parametrize("index,row", list(enumerate(ROWS)), ids=[r["url"][:70] for r in ROWS])
def test_content_and_popup_url_aliases_and_shards_equal_python(identities, index, row):
    expected = checker.aliases(row["url"])
    assert identities[index] == {"aliases": expected, "shards": [
        [checker.shard(alias, count) for alias in expected] for count in (16,32,64,128,256,512,1024)]}


def test_raw_url_paths_scheme_case_query_encoding_and_invalid_authority_equal_python():
    urls = ["https://home.woot.com/offers/café", "https://woot.com/offers/..",
            "HTTP://SHOP.EXAMPLE:80/product?x=%7e&a=*&blank=&utm_source=x",
            "https:///amazon.com/dp/B012345678", "https://target.com/p/A-123/../A-456",
            "https://bestbuy.com/site/widget.p?skuId=&skuId=123", "https://ebay.com/itm/123?var="]
    actual = node("""
const fs=require('fs'),w=require('./extension/lookup.js'),r=require('./extension/rules.json');
console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(url=>w.aliases(url,r))));
""", urls)
    assert actual == [checker.aliases(url) for url in urls]


def match_pattern(pattern, value):
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    scheme, rest = pattern.split("://")
    host, path = rest.split("/", 1)
    target = parsed.path + ("?" + parsed.query if parsed.query else "")
    return (parsed.scheme in ({"http", "https"} if scheme == "*" else {scheme})
            and (parsed.hostname == host.removeprefix("*.") or
                 (host.startswith("*.") and (parsed.hostname or "").endswith(host[1:])))
            and re.fullmatch(re.escape("/" + path).replace(r"\*", ".*"), target) is not None)


def test_manifest_is_narrow_mv3_and_cross_browser():
    manifest = json.loads((EXT / "manifest.json").read_text())
    listing = (EXT / "store/listing.md").read_text()
    assert listing.split("Short description:\n", 1)[1].split("\n", 1)[0] == manifest["description"]
    assert manifest["manifest_version"] == 3
    assert manifest["permissions"] == ["storage", "alarms"]
    assert manifest["host_permissions"] == ["https://wowpriced.com/*"]
    assert manifest["browser_specific_settings"]["gecko"] == {
        "id":"extension@wowpriced.com", "strict_min_version":"140.0", "data_collection_permissions":{"required":["none"]}}
    assert manifest["background"] == {"service_worker":"background.js", "scripts":["lookup.js","background.js"]}
    assert not ({"optional_permissions", "optional_host_permissions", "web_accessible_resources", "externally_connectable"} & manifest.keys())
    matches = manifest["content_scripts"][0]["matches"]
    assert "<all_urls>" not in matches
    for row in ROWS:
        if any(not a.startswith("url:") for a in row["aliases"]):
            assert any(match_pattern(pattern, row["url"]) for pattern in matches), row["url"]
        if row["url"].startswith(tuple("https://" + host + "/" for host in checker.SHORT_HOSTS)):
            assert not any(match_pattern(pattern, row["url"]) for pattern in matches), row["url"]
    for value in ["https://www.amazon.com/", "https://www.ebay.com/sch/i.html", "https://www.woot.com/plus/home", "https://amazon.com.evil.example/dp/B012345678", "https://bestbuy.com/site/searchpage.jsp?st=widget"]:
        assert not any(match_pattern(pattern, value) for pattern in matches)
    for path in EXT.rglob("*"):
        if path.suffix in {".js", ".cjs", ".html", ".json"}:
            text = path.read_text()
            assert not re.search(r"\beval\s*\(|\bnew\s+Function\b|<script\b[^>]*src=[\"']https?://", text)
    content = (EXT / "content.js").read_text()
    assert "location.href" in content
    assert not re.search(r"document\.(?:cookie|body|querySelector|querySelectorAll|links)|innerHTML|outerHTML|innerText", content)


def test_committed_rules_are_fresh_and_disabled_policies_stay_disabled():
    cfg = load_config()
    assert (EXT / "rules.json").read_bytes() == build_extension.rules_bytes(
        EXT, shards=cfg.checker["shards"], amazon_mode=cfg.extension["amazon_mode"])
    rules = json.loads((EXT / "rules.json").read_text())
    assert rules["AMAZON_MODE"] == "full"  # Amazon OK under amz378_998
    assert {k:v for k,v in rules.items() if k not in {"AMAZON_MODE", "version", "shards"}} == checker.js_rules()
    assert not load_config().checker["enabled"]
    assert load_config().amazon_watches == "off"


@pytest.mark.parametrize("settings", [{}, {"amazon_mode":"full"}, {"amazon_mode":"link"}])
def test_extension_config_validates_modes_and_build_reads_the_knob(tmp_path, monkeypatch, settings):
    config = tmp_path / "config.yaml"
    config.write_text(json.dumps({"extension": settings}))
    cfg = load_config(config)
    expected = settings.get("amazon_mode", "full")
    assert cfg.extension == {"amazon_mode": expected}
    assert AppConfig().extension == {"amazon_mode":"full"}
    shutil.copytree(EXT, tmp_path / "extension")
    monkeypatch.setattr(build_extension, "load_config", lambda: cfg)
    archive = build_extension.build(tmp_path)
    assert json.loads((tmp_path / "extension/rules.json").read_text())["AMAZON_MODE"] == expected
    with zipfile.ZipFile(archive) as package:
        assert json.loads(package.read("rules.json"))["AMAZON_MODE"] == expected


@pytest.mark.parametrize("settings", [None, [], "full", {"amazon_mode":None}, {"amazon_mode":True},
                                     {"amazon_mode":[]}, {"amazon_mode":"invalid"}, {"enabled":True}])
def test_extension_config_rejects_invalid_settings(tmp_path, settings):
    config = tmp_path / "config.yaml"
    config.write_text(json.dumps({"extension": settings}))
    with pytest.raises(ValueError, match="extension"):
        load_config(config)


def test_build_is_byte_identical_sorted_fixed_time_and_contains_runtime_only(tmp_path, monkeypatch, capsys):
    shutil.copytree(EXT, tmp_path / "extension")
    first = build_extension.build(tmp_path)
    data = first.read_bytes()
    for path in (tmp_path / "extension").rglob("*"):
        if path.is_file():
            path.touch()
    assert build_extension.build(tmp_path).read_bytes() == data
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        assert names == sorted(names) and "manifest.json" in names and "rules.json" in names
        assert all(not name.startswith("store/") for name in names)
        assert all(item.date_time == (1980,1,1,0,0,0) for item in archive.infolist())
        assert all((item.external_attr >> 16) == 0o100644 for item in archive.infolist())
    monkeypatch.setattr(build_extension, "build", lambda: first)
    assert cli.main(["build-extension"]) == 0
    assert str(first) in capsys.readouterr().out


@pytest.mark.parametrize("size", [16,32,48,128])
def test_brand_icons_have_required_png_dimensions(size):
    data = (EXT / "icons" / f"{size}.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == (size,size)


def test_node_runtime_card_popup_storage_network_and_amazon_guards():
    result = subprocess.run(["node", "tests/extension_logic.cjs"], cwd=ROOT,
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, (result.stdout + result.stderr)[-4500:]
    assert "# pass 22" in result.stdout and "# fail 0" in result.stdout


def test_python_compact_index_decodes_exactly_in_extension():
    entries = {
        "bestbuy:123": {"kind": "live", "store": "bestbuy", "item": "Café 🎧",
                        "price": 0, "credit": [{"name": "Source", "url": "https://example.com/é"}],
                        "take": {"text": "A checked fact", "amount": 1.25}, "deal_path": "/deal/widget/"},
        "amazon:B012345678": {"kind": "live", "store": "amazon", "item": "Other", "deal_path": "/deal/other/"},
    }
    actual = node("""
const fs=require('fs'), vm=require('vm');
const listener={addListener(){}};
const chrome={runtime:{getURL:n=>n,onInstalled:listener,onStartup:listener,onMessage:listener},
 storage:{local:{get(k,cb){cb({});}}},alarms:{get(n,cb){cb({});},onAlarm:listener}};
const context=vm.createContext({chrome,fetch:async()=>({json:async()=>({})}),console});
vm.runInContext(fs.readFileSync('extension/lookup.js','utf8'),context);
vm.runInContext(fs.readFileSync('extension/background.js','utf8'),context);
context.data=JSON.parse(fs.readFileSync(0,'utf8'));
console.log(JSON.stringify(vm.runInContext(`(() => {
 const index=unpack({downloaded_at:Date.now(),data});
 return Object.fromEntries(Object.entries(data.entries).map(([alias,row])=>[alias,
  Object.fromEntries(data.fields.flatMap((f,i)=>row[i]<0?[]:[[f,index.values[row[i]]]]))]));
})()`,context)));
""", json.loads(checker.compact_index(INDEX_NOW, entries)))
    assert actual == entries


def test_real_extension_offline_when_chromium_can_launch(tmp_path):
    result = subprocess.run(["node", "tests/extension_browser.cjs", str(tmp_path / "browser")], cwd=ROOT,
                            text=True, capture_output=True, timeout=60)
    if result.returncode == 77:
        pytest.skip("Chromium sandbox launch blocked; Node chrome-API fallback is tested separately")
    assert result.returncode == 0, (result.stdout + result.stderr)[-2000:]


@pytest.mark.parametrize("mode", ["membership", "open", "support", "shipped"])
def test_site_changes_only_privacy_page(tmp_path, monkeypatch, mode):
    cfg = baseline_config(mode)
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    (tmp_path / "after").mkdir()
    (tmp_path / "before").mkdir()
    out, before_payload, _, _ = render(tmp_path / "after", cfg, sample_deals())
    after = {p.relative_to(out).as_posix():p.read_bytes() for p in out.rglob("*") if p.is_file()}
    templates = tmp_path / "templates"
    shutil.copytree(site.TEMPLATES_DIR, templates)
    path = templates / "privacy.html"
    text = path.read_text()
    legacy = re.sub(r"<h2>Browser extension</h2>.*?(?=<h2>Service providers and sharing</h2>)", "", text, flags=re.S)
    assert legacy != text
    # Only the new section was inserted into the untouched pre-task template.
    assert hashlib.sha256(legacy.encode()).hexdigest() == "f412ebdb20bfa471c49b7edcf424e50226d6fc868b98fe4da49d24aa93958538"
    path.write_text(legacy)
    monkeypatch.setattr(site, "TEMPLATES_DIR", templates)
    out, after_payload, _, _ = render(tmp_path / "before", cfg, sample_deals())
    before = {p.relative_to(out).as_posix():p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert before.keys() == after.keys()
    assert [name for name in after if after[name] != before[name]] == ["privacy.html"]
    assert before_payload == after_payload
    assert b"collects nothing" in after["privacy.html"] and b"Browser extension" in after["privacy.html"]
    assert not any(name.startswith("check/") for name in after)
