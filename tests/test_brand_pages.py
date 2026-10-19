"""Task 289: frozen OFF bytes, public-only visuals, shared signup and offline QA."""
import hashlib
import json
import re
import socket
import subprocess
from html import escape, unescape
from pathlib import Path

import httpx
import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import brand_pages, site
from dealradar.savings import display_pct
from tests.brand_pages_fixture import NOW, PAGES, config, offers, publish
from tests.phone_budget_fixture import Inline, publish_fixture, sizes
from tests.test_public_copy import INTERNAL
from tests.test_pwa import tree_hash

ROOT = Path(__file__).resolve().parents[1]
PINS = json.loads((ROOT / "tests/fixtures/brand_pages_off.json").read_text())


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task289 network unavailable")
    monkeypatch.setattr(httpx.Client, "send", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(site, "util_now", lambda: NOW)


@pytest.mark.parametrize("value", [None, False, True])
def test_config_and_ship_off(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({} if value is None else {"site": {"brand_pages": value}}))
    assert load_config(path).brand_pages is (value is True)
    assert AppConfig().brand_pages is False
    assert load_config().brand_pages is True  # operator approved the preview 2026-10-17


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false", [], {}])
def test_strict_boolean(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"site": {"brand_pages": value}}))
    with pytest.raises(ValueError, match=r"^site.brand_pages must be a boolean$"):
        load_config(path)


@pytest.mark.parametrize("mode", PINS)
def test_recorded_off_missing_and_toggle_back_bytes(tmp_path, mode):
    cfg = config(mode)
    out = tmp_path / "site"
    for enabled in (None, False, True, False):
        if enabled is None:
            del cfg.brand_pages
        else:
            cfg.brand_pages = enabled
        publish(out, cfg)
        if enabled is True:
            assert 'class="brand-hero' in (out / "about.html").read_text()
            continue
        assert tree_hash(out) == PINS[mode]["tree"]
        for name, expected in PINS[mode]["pages"].items():
            assert hashlib.sha256((out / name).read_bytes()).hexdigest() == expected, name


def text(html):
    return " ".join(unescape(re.sub(r"<[^>]+>", "", html)).split())


def seo_head(html):
    head = html.split('</head>', 1)[0]
    return re.findall(r'<title>.*?</title>|<meta\b[^>]*>|<link\b[^>]*>|'
                      r'<script type="application/ld\+json">.*?</script>', head, re.S)


def section(html, tag):
    return re.search(rf'<{tag}\b[^>]*>(.*?)</{tag}>', html, re.S)[1]


@pytest.mark.parametrize("mode", PINS)
def test_enabled_preserves_words_seo_and_published_facts(tmp_path, mode):
    cfg = config(mode)
    cfg.newsletter_enabled = False
    out = tmp_path / "site"
    before_result = publish(out, cfg)
    original = {name: (out / name).read_text() for name in PAGES if (out / name).exists()}
    faq = {p.relative_to(out): p.read_bytes() for p in out.rglob("*.html") if 'faq' in str(p)}
    cfg.brand_pages = True
    result = publish(out, cfg)
    for metric in ("empty_indexable", "schema_mismatches", "broken_internal_links",
                   "sitemap_missing", "sitemap_extra", "sitemap_duplicates"):
        assert result["seo_audit"][metric] == before_result["seo_audit"][metric]
    doc = json.loads((out / "deals.json").read_text())
    public = {d["key"]: d for d in doc["deals"] if not d.get("locked")}
    selected = brand_pages.collage_rows(doc, cfg)
    assert 3 <= len(selected) <= 4
    for name, before in original.items():
        html = (out / name).read_text()
        assert seo_head(html) == seo_head(before), name
        assert re.findall(r'<script type="application/ld\+json">.*?</script>', html, re.S) == re.findall(
            r'<script type="application/ld\+json">.*?</script>', before, re.S)
        assert section(html, "header") == section(before, "header")
        assert section(html, "footer") == section(before, "footer")
        assert len(re.findall(r'<h1\b', html)) == 1
        assert 'class="brand-hero' in html and 'class="brand-primary"' in html
        assert 'href="/"' in section(html, "main")
        assert 'id="brand-why-title"' in html and 'class="brand-proof"' in html
        assert INTERNAL.search(html) is None
        assert '{{' not in html
        # Every original paragraph and heading remains in its original order.
        cursor = 0
        main = text(section(html, "main"))
        for fragment in re.findall(r'<(?:h[123]|p)\b[^>]*>.*?</(?:h[123]|p)>', section(before, "main"), re.S):
            fragment = text(fragment)
            if not fragment:
                continue
            found = main.find(fragment, cursor)
            assert found >= cursor, (name, fragment)
            cursor = found + len(fragment)
        cards = re.findall(r'<a class="brand-deal" data-key="([^"]+)" href="([^"]+)">(.*?)</a>', html, re.S)
        assert [key for key, _, _ in cards] == [d["key"] for d in selected]
        for key, href, card in cards:
            deal = public[key]
            assert href == deal["deal_path"] and (out / href.lstrip('/') / 'index.html').exists()
            assert f'<span class="brand-deal-title">{escape(deal["item"])}</span>' in card
            assert f'<span class="brand-store">{escape(deal["store"])}</span>' in card
            assert f'${deal["final_price"]:.2f}' in card
            assert f'src="{escape(deal["image_url"], quote=True)}"' in card
            assert 'width="320" height="240"' in card
            if deal.get("needs_steps"):
                assert "See price steps" in card
            if deal.get("amazon_as_of"):
                assert "Price as of " + escape(deal["amazon_as_of"]) in card
            if deal.get("pct_at_checkout") is not None:
                assert f'{display_pct(deal["pct_at_checkout"])}% off' in card
            else:
                assert 'brand-discount' not in card
        live = brand_pages._live(doc)
        assert f'<dt>Live deals</dt><dd>{len(live):,}</dd>' in html
        assert f'<dt>Stores</dt><dd>{len({d["store"] for d in live if d.get("store")}):,}</dd>' in html
        if cfg.support_enabled:
            assert html.count('window.wpSupport =') == 1
            assert ('id="join-prompt"' in html) is (name != "join.html")
            assert "first issue soon" not in html.lower()
            assert "Save up to <span>90% OFF</span>" in html
            assert "The day's best deals in one email. Free." in html
    for name, expected in faq.items():
        assert (out / name).read_bytes() == expected
    careers = (out / "careers.html").read_text()
    assert careers.index('class="brand-values"') < careers.index('class="brand-stories"') < careers.index('class="brand-day"')
    assert 'href="mailto:hello@wowpriced.com"' in careers
    about = (out / "about.html").read_text()
    difference = re.search(r'<section class="brand-difference".*?</section>', about, re.S)[0]
    assert re.findall(r'<strong>(.*?)</strong>', difference) == [
        "Our why", "How it works", "Our promise", "Who we are"]
    assert '<h2 id="brand-difference-title">What makes us different</h2>' in difference
    assert 'href="/careers">Work with us</a>' in difference
    assert 'href="/contact">Contact us</a>' in difference


@pytest.mark.parametrize("rows", [[], offers()[:1]])
def test_sparse_publications_never_invent_cards(tmp_path, rows):
    cfg = config()
    cfg.brand_pages = True
    publish(tmp_path, cfg, rows)
    doc = json.loads((tmp_path / "deals.json").read_text())
    for name in PAGES:
        html = (tmp_path / name).read_text()
        assert len(re.findall('class="brand-deal"', html)) <= len(rows)
        assert 'NaN' not in html
        if not rows:
            assert 'brand-discount' not in section(html, "main")
        assert f'<dt>Live deals</dt><dd>{len(brand_pages._live(doc))}</dd>' in html


def test_private_gone_unknown_and_missing_fields_never_supply_a_collage():
    cfg = config()
    valid = dict(key="public", status="alive", image_url="https://photos.example.invalid/one.jpg",
                 item="Published item", store="Published store", final_price=12.34, deal_path="/deal/public-12345678/")
    for changes in ({"locked": True}, {"status": "dead"}, {"price_unconfirmed": True},
                    {"price_aged": True}, {"click_price_unknown": True}, {"image_url": ""},
                    {"final_price": None}, {"deal_path": "https://example.invalid/private"}):
        assert brand_pages.collage_rows({"deals": [valid | changes]}, cfg) == []
    cfg.front_page = {"top_picks": {"max_cards": 24}}
    assert brand_pages.collage_rows({"deals": [valid]}, cfg) == []
    assert brand_pages.collage_rows({"deals": [valid | {"pick": 1}]}, cfg)
    html = brand_pages._collage([valid | {"item": '<script>bad()</script>', "store": 'A & B'}])
    assert '<script>' not in html and 'A &amp; B' in html
    assert 'brand-discount' not in html  # missing percent never becomes 0% off


def test_brand_text_palette_meets_aa_and_reduced_motion():
    def luminance(hex_color):
        channels = [int(hex_color[n:n + 2], 16) / 255 for n in (1, 3, 5)]
        channels = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
        return sum(c * weight for c, weight in zip(channels, (.2126, .7152, .0722)))

    css = (brand_pages.TEMPLATES / "brand-pages.css").read_text()
    # Both endpoints of each gradient; interpolation remains within these bounds.
    pairs = [(fg, bg) for fg, backgrounds in (
        ("#113b36", ("#e2f3ec", "#fff0d5", "#ffffff")),
        ("#435e58", ("#e2f3ec", "#fff0d5", "#ffffff")),
        ("#e4f6ef", ("#18352f", "#3d3420", "#161c23")),
        ("#b4cbc2", ("#18352f", "#3d3420", "#161c23")),
        ("#ffffff", ("#0e7569", "#085c53")),
        ("#082e27", ("#65dcc7", "#8ce7d7")),
        ("#483002", ("#ffdb87",)),
    ) for bg in backgrounds]
    for fg, bg in pairs:
        assert (fg in css or fg == "#ffffff") and (bg in css or bg in ("#ffffff", "#161c23"))
        low, high = sorted((luminance(fg), luminance(bg)))
        assert (high + .05) / (low + .05) >= 4.5, (fg, bg)
    assert 'prefers-reduced-motion:reduce' in css and ':focus-visible' in css
    assert 'aspect-ratio:4/3' in css
    assert '.joinoverlay [hidden]{display:none!important}' in css


@pytest.mark.parametrize("newsletter", [False, True])
def test_existing_email_copy_and_shared_prompt(tmp_path, newsletter):
    cfg = config()
    cfg.newsletter_enabled = newsletter
    cfg.drop_minutes = 20
    cfg.paywall_google_client_id = "configured-offline-id"
    cfg.brand_pages = False
    before = tmp_path / "before"
    publish(before, cfg)
    def card(html):
        markup = re.search(r'<section class="joincard".*?</section>', html, re.S)[0]
        markup = re.sub(r'<button[^>]*data-join-close.*?</button>', '', markup)
        return re.sub(r'\s+', ' ', markup.replace('h2', 'h1')).strip()
    home_before = (before / "index.html").read_text()
    shared_card = card(home_before)
    cfg.brand_pages = True
    out = tmp_path / "site"
    publish(out, cfg)
    for name in (*PAGES, "index.html"):
        html = (out / name).read_text()
        assert "first issue soon" not in html.lower()
        assert card(html) == shared_card
        assert "Save up to <span>90% OFF</span>" in html
        assert "The day's best verified deals in one free email. Unsubscribe anytime." in html
        if name != "index.html":
            assert 'Fresh every 20 minutes' in html
            assert 'every 15 minutes' not in html
    html = (out / "about.html").read_text()
    script = next(s for s in re.findall(r'<script>(.*?)</script>', html, re.S) if 'window.wpSupport =' in s)
    target = tmp_path / "support.js"
    target.write_text(script)
    motion = tmp_path / "motion.js"
    motion.write_text(next(s for s in re.findall(r'<script>(.*?)</script>', html, re.S) if 'brand-reveal' in s))
    result = subprocess.run(["node", str(ROOT / "tests/brand_pages_logic.cjs"), str(target),
                             str(motion)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["support", "current"])
def test_same_run_is_idempotent(tmp_path, mode):
    cfg = config(mode)
    cfg.brand_pages = True
    db = DB(":memory:")
    try:
        publish(tmp_path, cfg, db=db)
        before = tree_hash(tmp_path), list(db.conn.iterdump())
        publish(tmp_path, cfg, db=db)
        assert (tree_hash(tmp_path), list(db.conn.iterdump())) == before
    finally:
        db.close()


def test_enabled_shipped_phone_budgets(tmp_path):
    from dealradar.rank import photoless_expired
    from scripts.publication_budget import analyze
    from tests.phone_budget_fixture import NOW, fixture_offers

    cfg = load_config()
    cfg.brand_pages = True
    cfg.phone_budget["enabled"] = True

    def inspect(db, out):
        expected = sum(not photoless_expired(d, cfg, NOW) for d in fixture_offers())
        report = analyze(out, cfg, expected_offers=expected)
        assert report["ok"], report
        for name in PAGES:
            html = (out / name).read_text()
            parser = Inline(out)
            parser.feed(html)
            assert sizes(html.encode())["gzip"] <= cfg.phone_budget["home_html_gzip_max_bytes"]
            for metric in ("js", "css"):
                assert sizes(b''.join(parser.parts[metric]))["gzip"] <= cfg.phone_budget[f"home_{metric}_gzip_max_bytes"]
        print(json.dumps({key: value["gzip"] for key, value in report["metrics"].items()}, sort_keys=True))

    publish_fixture(tmp_path, cfg, inspect=inspect)


def test_real_chromium_screenshots(tmp_path):
    cfg = config("current")
    cfg.brand_pages = True
    cfg.tracking = {}
    publish(tmp_path, cfg)
    result = subprocess.run(["node", str(ROOT / "tests/brand_pages_browser.cjs"), str(tmp_path),
                             str(ROOT / "docs/research/brand-pages")], capture_output=True, text=True, timeout=120)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
