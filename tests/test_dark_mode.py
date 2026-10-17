"""Dark-only, additive publication and offline phone/browser checks (task 254)."""

import colorsys
import json
import re
import shutil
import socket
import subprocess
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from dealradar.config import AppConfig, load_config, validated_checker
from dealradar.db import DB
from dealradar.publish import dark, product_pages, site
from dealradar.util import iso
from scripts.publication_budget import analyze
from tests.phone_budget_fixture import fixture_config, publish_fixture
from tests.test_offscreen_cards import offers
from tests.test_product_entities import insert
from tests.test_pwa import baseline_config, tree_hash
from tests.test_support import NOW, render

SHOTS = Path("/tmp/task254-shots")
TEMPLATES = Path(__file__).resolve().parents[1] / "dealradar/publish/templates"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task254 network unavailable")
    monkeypatch.setattr(httpx.Client, "send", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(site, "util_now", lambda: NOW)


@pytest.mark.parametrize("value", [False, True])
def test_boolean_config(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"site": {"dark_polish": value}}))
    assert load_config(path).dark_polish is value
    path.write_text("{}")
    assert load_config(path).dark_polish is False
    assert AppConfig().dark_polish is False
    assert load_config().dark_polish is True


@pytest.mark.parametrize("value", [0, 1, "false", "true", None, [], {}])
def test_non_boolean_rejected(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"site": {"dark_polish": value}}))
    with pytest.raises(ValueError, match=r"^site.dark_polish must be a boolean$"):
        load_config(path)


def files(out):
    return {p.relative_to(out).as_posix(): p.read_bytes()
            for p in sorted(out.rglob("*")) if p.is_file()}


def assert_additive(before, after, old_version, new_version):
    assert before.keys() == after.keys()
    assert old_version != new_version
    for name, body in after.items():
        if name.endswith(".html"):
            html = body.decode()
            assert html.count(dark.META) == html.count(dark.STYLE) == 1, name
            anchor = dark._VIEWPORT.search(html) or dark._HEAD.search(html)
            assert html[anchor.end():].startswith(dark.META), name
            # The prescribed tracking/click passes run after dark.publish and
            # own the last head slots. Strip only those blocks for placement.
            head = re.sub(r"<!-- wp-(tracking|click-signal) -->.*?<!-- /wp-\1 -->\n?", "", html, flags=re.S)
            assert dark.STYLE + "</head>" in head, name
            body = body.replace(dark.META.encode(), b"").replace(dark.STYLE.encode(), b"")
        assert body.replace(new_version.encode(), old_version.encode()) == before[name], name


@pytest.mark.parametrize("mode,expected", [
    ("membership", "489d1d02f241e1ebe54e84f2a4ae5c36daadbade57a7784b78263ea03f0477c1"),
    ("open", "b26de959ff433cb2687af92e517ca60100df9bc0113e4acbf0df09aea68e936a"),
    ("support", "91c77988b99c3ff7ba842caeb623e67d0a981f996fbec09cb0bc6b4ab972c230"),
    ("shipped", "145e417e40d46eda3289bcf930eb694149ace00c9c739d30eb838eba2a08686e"),
])
@pytest.mark.usefixtures("pre_extension_privacy")
def test_off_bytes_and_on_only_css(tmp_path, mode, expected):
    cfg = baseline_config(mode)
    cfg.offscreen_cards = cfg.dark_polish = False
    out, before, full_before, _ = render(tmp_path, cfg, offers())
    original = files(out)
    assert tree_hash(out) == expected
    cfg.dark_polish = True
    out, after, full_after, _ = render(tmp_path, cfg, offers())
    enabled = files(out)
    assert_additive(original, enabled, before["meta"]["site_version"], after["meta"]["site_version"])
    full_after["meta"]["site_version"] = full_before["meta"]["site_version"]
    assert full_after == full_before
    render(tmp_path, cfg, offers())
    assert files(out) == enabled
    cfg.dark_polish = False
    render(tmp_path, cfg, offers())
    assert files(out) == original


def blocks(css):
    """Read balanced CSS blocks, retaining everything outside their braces."""
    while css.strip():
        start = css.index("{")
        depth = 1
        end = start + 1
        while depth:
            depth += (css[end] == "{") - (css[end] == "}")
            end += 1
        yield css[:start].strip(), css[start + 1:end - 1]
        css = css[end:]


def tokens(css):
    return dict(re.findall(r"--([\w-]+)\s*:\s*([^;}]+)", css))


def test_css_is_dark_only_and_never_fetches():
    wrappers = list(blocks(dark.CSS))
    assert [selector for selector, _ in wrappers] == [
        "@media (prefers-color-scheme:dark)",
        "@media (prefers-color-scheme:dark) and (max-width:640px)"]
    assert not re.search(r"url\s*\(|@import|@font-face", dark.CSS, re.I)
    assert dark.CSS.count("!important") == 1
    assert ':root .joincard .pwbtn:disabled{color:var(--muted)!important;opacity:1}' in dark.CSS


def test_postpass_exact_removal_fallback_skips_and_unchanged_mtimes(tmp_path):
    pages = {"index.html": '<head>\n<meta name="viewport" content="width=device-width">\n</head>\n',
             "nested/a.html": '<head>\n<meta charset="utf-8">\n</head>\n',
             "broken.html": '<head>missing close\r\n' + dark.META + dark.STYLE,
             "crlf.html": '<head>\r\n</head>\r\n'}
    for name, body in pages.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body.encode())
    original = files(tmp_path)
    cfg = AppConfig()
    mtimes = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*.html")}
    assert dark.publish(tmp_path, cfg) == {"pages": 3, "skipped": 1}
    assert files(tmp_path) == original
    assert {p: p.stat().st_mtime_ns for p in mtimes} == mtimes
    cfg.dark_polish = True
    assert dark.publish(tmp_path, cfg) == {"pages": 3, "skipped": 1}
    assert (tmp_path / "nested/a.html").read_text().startswith('<head>\n' + dark.META)
    assert (tmp_path / "broken.html").read_bytes() == original["broken.html"]
    enabled = files(tmp_path)
    mtimes = {p: p.stat().st_mtime_ns for p in mtimes}
    dark.publish(tmp_path, cfg)
    assert files(tmp_path) == enabled
    assert {p: p.stat().st_mtime_ns for p in mtimes} == mtimes
    cfg.dark_polish = False
    dark.publish(tmp_path, cfg)
    assert files(tmp_path) == original


def test_one_shared_dark_token_set():
    shared = tokens(next(body for selector, body in blocks(list(blocks(dark.CSS))[0][1])
                         if selector == ":root"))
    sources = {p.name: p.read_text() for p in TEMPLATES.glob("*.html") if p.name != "android-offline.html"}
    sources["READER_STYLE"] = product_pages.READER_STYLE
    checked = 0
    for name, source in sources.items():
        for media in re.finditer(r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{", source):
            _, body = next(blocks(source[media.start():]))
            for root in re.findall(r":root\s*\{([^}]+)\}", body):
                for key, value in tokens(root).items():
                    if key in shared:
                        assert value.strip() == shared[key].strip(), (name, key)
                        checked += 1
    assert checked > 100


def rgb(value):
    value = value.removeprefix("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


def luminance(color):
    return sum(weight * (v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4)
               for v, weight in zip(color, (.2126, .7152, .0722)))


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + .05) / (lo + .05)


def mix(a, b, alpha):
    return tuple(x * alpha + y * (1 - alpha) for x, y in zip(a, b))


def test_wcag_contrast_table():
    raw = tokens(dark.CSS)
    color = {k: rgb(v) for k, v in raw.items() if v.startswith("#")}
    home = (TEMPLATES / "index.html").read_text()
    light_root = tokens(re.search(r":root\s*\{([^}]+)\}", home)[1])
    color.update({key: rgb(light_root[key]) for key in ("flag", "flag-fg")})
    text_pairs = [(fg, bg) for fg in ("fg", "muted", "link", "accent")
                  for bg in ("bg", "card", "chip", "card-raised")]
    text_pairs += [("accent-fg", "accent"), ("cond-fg", "cond"), ("dead-fg", "dead"),
                   ("flag-fg", "flag"), *((fg, "card") for fg in ("good", "hist", "unv"))]
    for fg, bg in text_pairs:
        assert contrast(color[fg], color[bg]) >= 4.5, (fg, bg)
    headers = re.findall(r"\bheader\s*\{([^}]+)\}", home)
    header = tokens(headers[1])
    for fg in ("fg", "muted", "link", "accent"):
        assert contrast(rgb(header[fg]), rgb("#0f1b22")) >= 4.5, fg
    dim = tuple(float(v) for v in re.fullmatch(r"rgba\(([^)]+)\)", raw["ph-dim"])[1].split(","))
    for hue in range(360):
        tile = colorsys.hls_to_rgb(hue / 360, .62, .42)
        assert contrast(color["tile-fg"], mix(tuple(v / 255 for v in dim[:3]), tile, dim[3])) >= 4.5, hue
    for bg in ("card", "card-raised"):
        assert contrast(color["muted"], mix(color["accent"], color[bg], .2)) >= 4.5, bg
    for bg in ("card", "card-raised", "bg"):
        assert contrast(color["field-line"], color[bg]) >= 3, bg
    for stroke in ("hist", "muted"):
        assert contrast(color[stroke], color["card"]) >= 3, stroke
    assert contrast(rgb("#0e7569"), rgb("#f1f3f5")) >= 3


def test_shipped_phone_budget_off_on(tmp_path):
    numbers = {}
    for enabled in (False, True):
        cfg = fixture_config("shipped")
        cfg.dark_polish = enabled
        root = tmp_path / ("on" if enabled else "off")
        publish_fixture(root, cfg)
        report = analyze(root / "site", cfg, expected_offers=1400)
        numbers["ON" if enabled else "OFF"] = {
            key: report["metrics"][key]["gzip"] for key in ("home_css", "home_html")}
    print("task254 gzip bytes: " + json.dumps(numbers, sort_keys=True))
    SHOTS.mkdir(parents=True, exist_ok=True)
    (SHOTS / "budget.json").write_text(json.dumps(numbers, indent=2) + "\n")
    assert numbers["ON"]["home_css"] <= 14913
    assert numbers["ON"]["home_html"] <= 77360


@pytest.fixture
def dark_trees(tmp_path):
    cfg = load_config()
    cfg.tracking = {}
    cfg.paywall_google_client_id = ""
    cfg.join_prompt = False  # browser opens the popup directly, as support.js does
    cfg.dark_polish = False
    cfg.seo_product_pages_enabled = True
    cfg.seo_hub_min_items = cfg.seo_store_page_min_deals = 1
    cfg.checker = validated_checker({"enabled": True, "stores": {"amazon": "history", "walmart": "history"}})
    stores = [("Amazon", f"https://www.amazon.com/dp/B{i:09d}") for i in range(12)]
    for i in range(20):
        store, url = [("Walmart", f"https://www.walmart.com/ip/widget/{123456789 + i}"),
                      ("Target", f"https://www.target.com/p/widget/-/A-{12345678 + i}"),
                      ("Best Buy", f"https://www.bestbuy.com/site/widget/{1234567 + i}.p"),
                      ("Woot", f"https://www.woot.com/offers/widget-{i}")][i % 4]
        stores.append((store, url))
    db = DB(":memory:")
    try:
        for i, (store, url) in enumerate(stores):
            key = f"task254-{i}"
            photo = "" if i == 12 else f"https://wowpriced.test/photos/{'transparent' if i == 13 else 'pale' if i == 14 else i}.svg"
            insert(db, key, now=NOW, store=store, item=f"Kitchen storage set {i}", category="kitchen",
                   canonical_url=url, asin=f"B{i:09d}" if store == "Amazon" else "",
                   image_url=photo, conditions=["new"], price_rechecked_at=iso(NOW),
                   first_seen_at=iso(NOW - timedelta(days=12)), last_seen_at=iso(NOW))
            for check in range(10):
                db.record_price(key, store, 10 + check, NOW - timedelta(days=check), f"check:task254-{check}")
            if i == 15:
                row = db.get_deal(key)
                row.update(status="dead", death="confirmed", died_at=iso(NOW))
                db.update_deal(key, status="dead", payload=row)
        rows = db.all_deals()
        out_off, out_on = tmp_path / "off", tmp_path / "on"
        before = site.render_site(db, cfg, NOW, out_dir=out_off, deals=rows)
        cfg.dark_polish = True
        after = site.render_site(db, cfg, NOW, out_dir=out_on, deals=rows)
        assert len(before["full_payload"]["deals"]) >= 24
        assert list((out_on / "p").glob("*/index.html"))
        assert 'class="price-history"' in next((out_on / "p").glob("*/index.html")).read_text()
        assert (out_on / "check/index.html").is_file()
        yield out_off, out_on, before, after
    finally:
        db.close()


def test_product_pages_and_checker_are_additive(dark_trees):
    off, on, before, after = dark_trees
    assert_additive(files(off), files(on), before["full_payload"]["meta"]["site_version"],
                    after["full_payload"]["meta"]["site_version"])


def test_chromium_dark_mode_offline(dark_trees):
    if not shutil.which("node"):
        pytest.skip("Node unavailable")
    off, on, *_ = dark_trees
    result = subprocess.run(["node", str(Path(__file__).with_name("dark_mode_browser.cjs")),
                             str(off), str(on), str(SHOTS)],
                            capture_output=True, text=True, timeout=300)
    print(result.stdout)
    if result.returncode == 77:
        for name, root in (("off", off), ("on", on)):
            shutil.copytree(root, SHOTS / "fixtures" / name, dirs_exist_ok=True)
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, (result.stdout + result.stderr)[-10000:]
