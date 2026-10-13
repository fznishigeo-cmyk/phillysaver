"""Home-screen app publishing and install behavior, entirely offline."""

import hashlib
import json
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import pwa, site
from tests.test_publish_site import make_deal
from tests.test_support import NOW, render


def baseline_config(mode):
    if mode == "shipped":
        cfg = load_config()
    else:
        cfg = AppConfig(custom_domain="wowpriced.com", seo_store_page_min_deals=1)
        if mode == "open":
            cfg.paywall_open_access = True
            cfg.paywall_yearly_price_usd = 49
        elif mode == "support":
            cfg.support_enabled = True
            cfg.engagement = {"saved_deals": True, "surprise_me": True}
            cfg.tracking = {"ga4_measurement_id": "G-ABC123XYZ9"}
    cfg.pwa_enabled = False
    return cfg


def sample_deals():
    return [make_deal(dedupe_key=f"walmart|item{i}", category="electronics", codes=[], conditions=[])
            for i in range(15)]


def tree_hash(out):
    files = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(out.rglob("*")) if p.is_file()}
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("mode,expected", [
    ("membership", "09faa24f0ac60185f05262553dc7e80240df5d66e3d05f7e82ab5958a882b560"),
    ("open", "37af119632dba4ad624b0b0d763c642576d0195cb9d95c2660a27c41c71e8f6e"),
    ("support", "9c91d584767cbf39211b77c4d55289c28f069a5db8ca0a94f9d107103c25be31"),
    ("shipped", "331e0ed467459b0a7f3ea31e4eba76d6d3ecd857f1398f71245571d191d38ff6"),
])
def test_pwa_disabled_matches_every_baseline_published_byte(tmp_path, monkeypatch, mode, expected):
    # Task 076 updates all four trees: shared phone target CSS changes
    # every footer page and the template version, without any PWA output.
    # Only shipped has support links and task 075's post-join ask.
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = baseline_config(mode)
    out, _, _, _ = render(tmp_path, cfg, sample_deals())
    assert tree_hash(out) == expected
    assert not (out / "manifest.webmanifest").exists()
    assert not (out / "pwa-icons").exists()
    for path in out.rglob("*.html"):
        assert "pwa-" not in path.read_text()


def test_pwa_config_defaults_shipped_names_and_switch(tmp_path):
    assert not AppConfig().pwa_enabled
    cfg = load_config()
    assert cfg.pwa_enabled and cfg.pwa_name == cfg.pwa_short_name == "wowpriced"
    path = tmp_path / "config.yaml"
    path.write_text("{}")
    assert not load_config(path).pwa_enabled
    path.write_text('pwa:\n  enabled: false\n  name: "My app"\n  short_name: "My app"\n')
    cfg = load_config(path)
    assert not cfg.pwa_enabled and cfg.pwa_name == cfg.pwa_short_name == "My app"


def test_manifest_icons_heads_headers_and_footer_on_every_page(tmp_path):
    cfg = baseline_config("support")
    cfg.pwa_enabled = True
    out, _, _, _ = render(tmp_path, cfg, sample_deals())
    manifest = json.loads((out / "manifest.webmanifest").read_text())
    assert manifest == {
        "name": "wowpriced", "short_name": "wowpriced", "theme_color": "#4fd8c6",
        "background_color": "#f3f5f7", "display": "standalone", "start_url": "/", "id": "/", "scope": "/",
        "icons": [
            {"src": "/pwa-icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/pwa-icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/pwa-icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]}
    for name, size in pwa.ICON_SIZES.items():
        png = (out / "pwa-icons" / name).read_bytes()
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR"
        assert struct.unpack(">II", png[16:24]) == (size, size)
        assert len(png) < 64_000  # truecolor: a palette bands the gradient; fetched only on install
    pages = {str(p.relative_to(out)): p.read_text() for p in out.rglob("*.html")}
    assert {"index.html", "join.html", "404.html", "welcome.html", "deals/electronics/index.html",
            "stores/walmart/index.html", "deals/under-25/index.html", "best-deals-today/index.html"} <= pages.keys()
    assert any(path.startswith("deal/") for path in pages)
    for name, page in pages.items():
        head = page.split("</head>")[0]
        assert head.count('<link rel="manifest" href="/manifest.webmanifest">') == 1, name
        assert head.count('rel="apple-touch-icon" sizes="180x180"') == 1, name
        assert head.count('name="theme-color"') == 2, name
        light, dark = (("#ffffff", "#161c23") if name in
                       {"404.html", "careers.html", "contact.html", "hacks.html"} else ("#4fd8c6", "#0f1b22"))
        for color, theme in ((light, "light"), (dark, "dark")):
            assert f'content="{color}" media="(prefers-color-scheme: {theme})"' in head
        assert 'name="apple-mobile-web-app-status-bar-style" content="default"' in head
        assert '{{PWA_' not in head
        assert "serviceWorker" not in page and "caches.open" not in page
        assert '<button id="pwa-install" class="pwa-link" type="button" hidden>Install app</button>' in page
        assert '<a id="pwa-ios" class="pwa-link" href="#pwa-help" hidden>Add to Home Screen</a>' in page
        assert page.index('id="pwa-actions"') > page.index("<footer>")
        assert page.index('id="pwa-help"') < page.index("</footer>")
    headers = (out / "_headers").read_text()
    assert "/manifest.webmanifest\n  Content-Type: application/manifest+json\n" in headers
    assert "Cache-Control: public, max-age=3600, must-revalidate" in headers
    assert "/deals.json\n  X-Robots-Tag: noindex\n  Cache-Control: no-store" in headers
    assert not list(out.rglob("*sw*.js"))
    original = tree_hash(out)
    pwa.publish(out, cfg)
    assert tree_hash(out) == original  # repeat the post-pass, including the headers


def test_pwa_preserves_every_outbound_link_and_the_home_before_footer(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = baseline_config("shipped")
    out, before, _, _ = render(tmp_path, cfg, sample_deals())
    pages = {str(p.relative_to(out)): p.read_text() for p in out.rglob("*.html")}
    cfg.pwa_enabled = True
    out, after, _, _ = render(tmp_path, cfg, sample_deals())
    link_pattern = r'<a\b[^>]*class="[^"]*getdeal\b[^>]*>'
    for name, page in pages.items():
        assert re.findall(link_pattern, page) == re.findall(link_pattern, (out / name).read_text()), name
    home = (out / "index.html").read_text()
    assert home.split("<body>", 1)[1].split("<footer>", 1)[0] == pages["index.html"].split("<body>", 1)[1].split("<footer>", 1)[0]
    assert before["deals"] == after["deals"]
    assert before["meta"]["site_version"] != after["meta"]["site_version"]
    version = after["meta"]["site_version"]
    assert f'var SITE_VERSION = "{version}";' in home
    assert "max-age=86400" in (out / "_headers").read_text()
    _, repeated, _, _ = render(tmp_path, cfg, sample_deals())
    assert repeated["meta"]["site_version"] == version
    cfg.pwa_short_name = "Wow"
    _, renamed, _, _ = render(tmp_path, cfg, sample_deals())
    assert renamed["meta"]["site_version"] != version


def test_switch_off_removes_assets_and_restores_all_original_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = baseline_config("support")
    out, _, _, _ = render(tmp_path, cfg, sample_deals())
    original = tree_hash(out)
    cfg.pwa_enabled = True
    out, _, _, _ = render(tmp_path, cfg, sample_deals())
    assert (out / "manifest.webmanifest").is_file()
    cfg.pwa_enabled = False
    out, _, _, _ = render(tmp_path, cfg, sample_deals())
    assert tree_hash(out) == "9c91d584767cbf39211b77c4d55289c28f069a5db8ca0a94f9d107103c25be31"
    assert not (out / "manifest.webmanifest").exists() and not (out / "pwa-icons").exists()


def test_pwa_browser_logic_offline(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    out, _, _, _ = render(tmp_path, AppConfig(pwa_enabled=True, support_enabled=True))
    html = (out / "index.html").read_text()
    script = next(s for s in re.findall(r"<script>(.*?)</script>", html, re.S) if "beforeinstallprompt" in s)
    target = tmp_path / "pwa.js"
    target.write_text(script)
    subprocess.run(["node", str(Path(__file__).with_name("pwa_logic.cjs")), str(target)],
                   check=True, capture_output=True, text=True, timeout=30)
