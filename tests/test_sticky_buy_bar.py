"""Operator 2026-10-22: a phone-only sticky Get deal bar on deal pages (approved, shipped on)."""
import re
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import site, sticky_buy_bar
from tests.history_pins import pre_social_profiles
from tests.test_publish_site import make_deal
from tests.test_support import NOW, render


def rows():
    return [make_deal(dedupe_key=f"sticky-{i}", item=f"Electric kettle 1.7L model {i}", store="Amazon",
                      category="kitchen", verify_tier="verified", codes=[], conditions=["new"],
                      final_price=24.99 + i, regular_price=49.99, asin=f"B{i:09d}",
                      canonical_url=f"https://amazon.com/dp/B{i:09d}", first_seen_at=NOW.isoformat(),
                      price_rechecked_at=NOW.isoformat(),
                      critic={"plausible": True, "confidence": .9, "reason": "Backed basis"},
                      image_url="https://m.media-amazon.com/images/I/photo.jpg") for i in range(6)]


def build(tmp_path, monkeypatch, on, name="site"):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = load_config(); cfg.sticky_buy_bar = on; cfg.affiliate_amazon_tag = "example-20"
    (tmp_path / name).mkdir()
    out, *_ = render(tmp_path / name, cfg, rows())
    return out, cfg


def files(out):
    return {str(p.relative_to(out)): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


def test_shipped_on_default_off_strict(tmp_path):
    assert load_config().sticky_buy_bar is True  # operator approved 2026-10-22
    assert AppConfig().sticky_buy_bar is False
    assert pre_social_profiles(AppConfig(sticky_buy_bar=True)).sticky_buy_bar is False
    path = tmp_path / "config.yaml"
    path.write_text("sticky_buy_bar: 1\n")
    with pytest.raises(ValueError, match="sticky_buy_bar"):
        load_config(path)


def test_off_changes_no_byte(tmp_path, monkeypatch):
    off, _ = build(tmp_path, monkeypatch, False, "off")
    # The same build with the feature removed altogether.
    monkeypatch.setattr(sticky_buy_bar, "publish", lambda out, cfg: None)
    monkeypatch.setattr(sticky_buy_bar, "site_version", lambda version, cfg: version)
    without, _ = build(tmp_path, monkeypatch, False, "without")
    assert files(off) == files(without)
    assert not any(b"buybar" in data for data in files(off).values())


def test_on_deal_pages_only_with_the_main_buttons_link(tmp_path, monkeypatch):
    out, cfg = build(tmp_path, monkeypatch, True)
    deals = sorted((out / "deal").glob("*/index.html"))
    assert deals
    for path in out.rglob("*.html"):
        html = path.read_text()
        if path not in deals:  # home, hubs, tool pages
            assert "buybar" not in html, path
            continue
        assert html.count("data-buybar") == 1 and html.count(sticky_buy_bar.STYLE) == 1
        main = sticky_buy_bar.MAIN.search(html)
        bar = re.search(r'<div class="buybar" data-buybar><strong>([^<]+)</strong><a class="getdeal" ([^>]*)>([^<]+)</a></div>', html)
        assert bar[2] == main[1] and bar[3] == main[2] == "Get deal"
        for attr in ('href="https://amazon.com/dp/', 'target="_blank"', 'rel="sponsored noopener"',
                     'data-deal-outbound', 'data-store="Amazon"', 'data-category="kitchen"', 'data-click-key="'):
            assert attr in bar[2]
        assert f"<strong>{bar[1]}</strong> USD</p>" in html  # the page's own price
        assert html.index("data-buybar") < html.index("</main>")  # inside deal-actions.js's scope
    for name in ("index.html", "amazon-deal-checker/index.html", "deal-spotter/index.html"):
        assert "buybar" not in (out / name).read_text()
    # Laptops: hidden; only phones (<= 640px) ever show it.
    assert sticky_buy_bar.STYLE.startswith("<style>.buybar{display:none}@media (max-width:640px){")
    assert "env(safe-area-inset-bottom" in sticky_buy_bar.STYLE and "min-height:44px" in sticky_buy_bar.STYLE
    before = files(out)
    sticky_buy_bar.publish(out, cfg)
    assert files(out) == before


def test_phone_bar_in_real_chromium(tmp_path):
    result = subprocess.run(["node", str(Path(__file__).with_name("sticky_buy_bar_browser.cjs")), str(tmp_path)],
                            capture_output=True, text=True, timeout=240, cwd=Path(__file__).parent.parent)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "light: bar 56px" in result.stdout and "dark: bar 56px" in result.stdout
    assert "desktop: hidden" in result.stdout
