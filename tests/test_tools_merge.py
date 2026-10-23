"""Operator 2026-10-23: four tools become two (tools_merge) and softer search buttons (soft_buttons)."""
import hashlib
import json
import re
import subprocess
from html import unescape
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import site, soft_buttons, tools_merge
from tests.history_pins import pre_social_profiles
from tests.test_publish_site import make_deal
from tests.test_support import NOW, render

ITEMS = ("Women's soft knit cardigan sweater", "Wireless over-ear headphones", "Electric kettle 1.7L stainless",
         "Silk pillowcase queen size", "Women's running shoes, lightweight mesh", "Insulated tumbler 30 oz")
BANNED = re.compile(r"AI-powered|proprietary|million|testimonial|as seen on|as mentioned|add to chrome|trusted by|★|☆|"
                    r"\b\d[\d,]*\s+(?:ratings|reviews)\b", re.I)


def rows():
    return [make_deal(dedupe_key=f"merge-{i}", item=item, brand="", store="Amazon", category="home-kitchen",
                      verify_tier="verified", codes=[], stack_steps=[], conditions=["new"], final_price=20.0 + i,
                      regular_price=50.0, pct_at_checkout=55, abs_savings=30 - i, regular_price_basis="List price",
                      asin=f"B0MERGE{i:03d}", canonical_url=f"https://www.amazon.com/dp/B0MERGE{i:03d}",
                      first_seen_at=NOW.isoformat(), price_rechecked_at=NOW.isoformat(),
                      critic={"plausible": True, "confidence": .99, "reason": "Fixture"},
                      image_url=f"https://m.media-amazon.com/images/I/merge{i}.jpg") for i, item in enumerate(ITEMS)]


def build(tmp_path, monkeypatch, on, name="site", **extra):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = load_config()
    cfg.tools_merge = cfg.soft_buttons = on
    cfg.affiliate_amazon_tag = "example-20"
    cfg.keepa_click_price_rule = cfg.keepa_buybox_rule = "off"  # the fixture rows carry no Keepa readings
    for key, value in extra.items():
        setattr(cfg, key, value)
    (tmp_path / name).mkdir()
    out, *_ = render(tmp_path / name, cfg, rows())
    return out, cfg


def files(out):
    return {str(p.relative_to(out)): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


def page(out, path):
    return (out / path / "index.html").read_text() if path else (out / "index.html").read_text()


def text(html):
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)))


def test_switches_shipped_on_default_off_strict_and_pinned(tmp_path):
    cfg = load_config()
    assert cfg.tools_merge is True and cfg.soft_buttons is True  # operator approved 2026-10-23
    assert AppConfig().tools_merge is False and AppConfig().soft_buttons is False
    pinned = pre_social_profiles(AppConfig(tools_merge=True, soft_buttons=True))
    assert pinned.tools_merge is False and pinned.soft_buttons is False
    for key in ("tools_merge", "soft_buttons"):
        path = tmp_path / f"{key}.yaml"
        path.write_text(f"{key}: 1\n")
        with pytest.raises(ValueError, match=key):
            load_config(path)


def test_off_changes_no_byte(tmp_path, monkeypatch):
    off, _ = build(tmp_path, monkeypatch, False, "off")
    # The same build with both features removed altogether.
    monkeypatch.setattr(tools_merge, "enabled", lambda cfg: False)
    monkeypatch.setattr(tools_merge, "landing_on", lambda cfg: False)
    monkeypatch.setattr(tools_merge, "publish", lambda out, cfg: None)
    monkeypatch.setattr(tools_merge, "site_version", lambda version, cfg: version)
    monkeypatch.setattr(soft_buttons, "publish", lambda out, cfg: None)
    monkeypatch.setattr(soft_buttons, "site_version", lambda version, cfg: version)
    without, _ = build(tmp_path, monkeypatch, False, "without")
    assert files(off) == files(without)
    for name, data in files(off).items():
        for marker in (b"trusted-guide", b"words-box", b"report-row", b"tool_feedback", b"#1c62db", b"#b62d69",
                       b"mergeReport", b"price_check", b"tools_merge", b"landing-intro"):
            assert marker not in data, (name, marker)
    home = page(off, "")
    assert ">Discount Finder<span" in home and ">Deal Checker<span" in home
    assert 'class="guide-section section"' in page(off, "deal-spotter")
    assert 'id="tool-form"' in page(off, "amazon-deal-checker")  # the old tool page itself


def test_spotter_home_bands_menus_and_footer(tmp_path, monkeypatch):
    out, cfg = build(tmp_path, monkeypatch, True)
    html = page(out, "deal-spotter")
    tabs = re.findall(r'<button type="button" role="tab"[^>]*>([^<]+)</button>', html)
    assert tabs == ["Price grade", "90-day price history", "Price details", "Find discounts"]
    assert html.count('role="tabpanel"') == 4 and html.count('<span class="shot-caption">Illustration</span>') == 4
    assert 'class="guide-section' not in html and 'id="phone-next"' not in html
    # Fakespot's order: how it works, the dark band, Love (with its button), the grey guide, a safe feeling.
    order = [html.index(s) for s in ('id="how-it-works"', 'class="demo-section"', 'class="love-section',
                                     'class="trusted-guide', 'class="safe-section')]
    assert order == sorted(order)
    assert re.search(r'<a class="primary love-cta" href="/best-deals-today/">See today\'s best deals', html)
    footer = re.search(r'<footer class="spotter-footer merged-footer">.*?</footer>', html, re.S)[0]
    assert re.findall(r"<h3>([^<]+)</h3>", footer) == ["wowpriced", "Tools", "Support", "Social"]
    for link in ('href="/deal-spotter/">Fake Deal Spotter', 'href="/review-checker/">Review Checker', 'href="/about.html"',
                 'href="/how-it-works.html"', 'href="/contact.html"', 'href="/privacy.html"', 'href="/disclosure.html"',
                 'href="/amazon-deal-checker/">Amazon deal checker', 'href="/amazon-discount-finder/">Amazon discount finder'):
        assert link in footer, link
    menu = re.search(r'<details class="spotter-menu">.*?</details>', html, re.S)[0]
    assert "/amazon-deal-checker/" not in menu and "/amazon-discount-finder/" not in menu and "Fake Deal Spotter" in menu
    assert not BANNED.search(text(html)), BANNED.search(text(html))


def test_spotter_report_rows_single_verdict_and_tagged_deals(tmp_path, monkeypatch):
    out, cfg = build(tmp_path, monkeypatch, True)
    html = page(out, "deal-spotter")
    report = re.search(r'<section id="spotter-result".*?</section>\n<div id="spotter-story">', html, re.S)[0]
    rows = re.findall(r'<h2 id="row-[a-z]+-title">([^<]+)</h2>', report)
    assert rows == ["Price details", "Overview", "On wowpriced", "Top sellers like this", "Review signals"]
    # One price verdict on the page: the grade box. No list-price badge in the report.
    assert report.count('id="grade-letter"') == 1 and html.count('class="grade-letter') == 1
    assert "% off" not in text(report) and "Price history" not in report
    assert "What does this mean?" in report and "Was this analysis helpful to you?" in report
    assert report.count('data-helpful="') == 2
    # The cart slot (Add to Amazon cart, shipped OFF) stays right under the big button.
    assert ('<a id="result-buy" class="primary" target="_blank" rel="sponsored nofollow noopener">Get this deal on Amazon '
            '<span aria-hidden="true">↗</span></a>') in report
    sidebar = re.search(r'<aside class="report-sidebar".*?</aside>', report, re.S)[0]
    links = re.findall(r'<a href="([^"]+)" target="_blank" rel="sponsored nofollow noopener">Get deal', sidebar)
    assert len(links) == 5 and all(re.fullmatch(r"https://www\.amazon\.com/dp/B0MERGE\d{3}\?tag=example-20", u) for u in links)
    strip = re.search(r'<div class="recommends".*?</div></div>', html, re.S)[0]
    assert strip.count("TODAY’S DEAL") == 3 and strip.index("wowpriced recommends")
    assert html.index('class="recommends"') > html.index('<dialog id="analyze-modal"')
    script = (out / "deal-spotter.js").read_text()
    for needle in ("mergeReport(item, grade);", "#analyze-modal a[href]", "function namePanelRated(",
                   "function namePanel(panel, raw)", "else mergeQuery(params, submit);", "new URL(location.pathname, location.origin)"):
        assert needle in script, needle
    assert "phone-next" not in script and "'/deal-spotter/'" not in script
    prices = json.loads((out / "deal-spotter-prices.json").read_text())
    assert prices["deals"] and all(re.fullmatch(r"/deal/[a-z0-9-]+/", d["deal_path"]) for d in prices["deals"])
    assert json.loads((out / "search-config.json").read_text())["tools_merge"] is True


def test_typed_words_box_never_calls_the_api(tmp_path, monkeypatch):
    out, _ = build(tmp_path, monkeypatch, True)
    for name in ("deal-spotter.js", "review-checker.js"):
        result = subprocess.run(["node", "--check", str(out / name)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        script = (out / name).read_text()
        words = (Path(tools_merge.__file__).with_name("templates") / "tools-merge-words.js").read_text()
        code = re.sub(r"//[^\n]*", "", words)  # the header comment names the API it never calls
        assert words in script and "/api/" not in code and "fetch(" not in code
        inventory = re.search(r"^function toolInventory\(\) \{\n.*?^\}", script, re.S | re.M)[0]
        assert "fetch('/deals.json'" in inventory and "/api/" not in inventory
        assert "'See ' + discount.words + ' discounts on Amazon'" in script
        assert "p_n_deal_type%3A210906366011" in re.search(r"^function toolDiscount\(.*?^\}", script, re.S | re.M)[0]


def test_landing_pages_unique_indexable_and_spotter_tool(tmp_path, monkeypatch):
    out, cfg = build(tmp_path, monkeypatch, True)
    pages = {slug: page(out, slug) for slug in ("deal-spotter", "amazon-deal-checker", "amazon-discount-finder")}
    sitemap = (out / "sitemap.xml").read_text()
    seen = {"title": set(), "description": set(), "h1": set(), "faq": set()}
    for slug, html in pages.items():
        assert f'<link rel="canonical" href="https://wowpriced.com/{slug}/">' in html and "noindex" not in html
        assert f"<loc>https://wowpriced.com/{slug}/</loc>" in sitemap
        assert 'id="spotter-result"' in html and 'class="trusted-guide' in html and 'data-analyze' in html
        assert "/deal-spotter.js?v=" in html  # the same tool, no second grading logic
        seen["title"].add(re.search(r"<title>([^<]+)</title>", html)[1])
        seen["description"].add(re.search(r'<meta name="description" content="([^"]+)"', html)[1])
        seen["h1"].add(re.search(r'<section class="spotter-hero wrap" id="start">\s*<h1>(.*?)</h1>', html, re.S)[1])
        seen["faq"].add(tuple(re.findall(r"<details><summary>([^<]+)</summary>", html)))
        assert not BANNED.search(text(html)), (slug, BANNED.search(text(html)))
    assert all(len(values) == 3 for values in seen.values()), seen
    checker = pages["amazon-deal-checker"]
    assert "<h1>Amazon Deal Checker</h1>" in checker and "Deal Checker" in re.search(r"<title>([^<]+)", checker)[1]
    assert '"name": "Amazon Deal Checker by wowpriced"' in checker
    assert "<h1>Amazon Discount Finder</h1>" in pages["amazon-discount-finder"]
    assert ">Find discounts<span>" in pages["amazon-discount-finder"]
    for slug in ("amazon-deal-checker", "amazon-discount-finder"):
        intro = text(re.search(r'<section class="landing-intro wrap".*?</section>', pages[slug], re.S)[0])
        assert 200 <= len(intro.split()) <= 400, (slug, len(intro.split()))
    assert not (out / "_redirects").exists() or "deal-checker" not in (out / "_redirects").read_text()
    assert not (out / "tool-pages.js").exists()
    # The site footer keeps the two keyword pages; the menus name the two tools.
    footer = re.search(r'<nav\b[^>]*\bid="footerlinks"[^>]*>(.*?)</nav>', page(out, ""), re.S)[1]
    assert '<a href="/amazon-deal-checker/">Amazon Deal Checker</a>' in footer


def test_review_checker_menus_price_row_and_words(tmp_path, monkeypatch):
    out, _ = build(tmp_path, monkeypatch, True)
    for path in ("review-checker", "reviewmeta-alternative"):
        html = page(out, path)
        nav = re.search(r'<nav aria-label="Tools">.*?</nav>', html, re.S)[0] + re.search(
            r'<nav class="header-nav".*?</nav>', html, re.S)[0]
        assert "amazon-deal-checker" not in nav and "amazon-discount-finder" not in nav and "/deal-spotter/" in nav
        assert "Deal Checker →" not in html and "Discount Finder →" not in html
        assert 'Use <a href="/deal-spotter/">Fake Deal Spotter</a> to check today’s price' in html
    script = (out / "review-checker.js").read_text()
    assert "mergePriceRow(report, data, asin);" in script and "Is the price right?" not in script
    assert "'Price check: ' + check.grade + ' — ' + usual" in script and "'Check this price →'" in script
    assert "submit(typed.trim(), undefined, heroInput)" in script


def test_homepage_buttons_relabelled_and_relinked(tmp_path, monkeypatch):
    out, _ = build(tmp_path, monkeypatch, True)
    for path in ("", "black-friday"):
        html = page(out, path)
        buttons = re.search(r'<div class="tool-buttons">.*?</div>', html, re.S)[0]
        assert re.fullmatch(r'<div class="tool-buttons"><a class="all-discounts" id="search-discounts" href="/deal-spotter/" '
                            r'data-tool-link="deal-spotter"[^>]*>Fake Deal Spotter<span class="tool-arrow" aria-hidden="true"> →'
                            r'</span></a><a id="analyze-search" href="/review-checker/" data-tool-link="review-checker"[^>]*>'
                            r'Review Checker<span class="tool-arrow" aria-hidden="true"> →</span></a></div>', buttons), buttons
        assert 'return "/" + tool + "/" + (raw.trim() ? "?q=" + encodeURIComponent(raw) : "");' in html
        assert '{to_tool:true, tool:link.dataset.toolLink}' in html
        assert "amazon-deal-checker/\" data-tool-link" not in html
        assert 'tool_feedback: { ga4Only: true, methods: ["helpful", "not_helpful"] }' in html


def test_soft_buttons_in_place_contrast_and_scope(tmp_path, monkeypatch):
    out, _ = build(tmp_path, monkeypatch, True)
    for name in soft_buttons.SHEETS:
        css = (out / name).read_text()
        for old, new in soft_buttons.SOFTER:
            assert old not in css
        assert "--pill-colors:#1c62db,#125fdd 52%,#0f4fba;--pill-glow:rgba(59,130,246,.3)" in css
        assert "var(--pill-colors,#582aa1,#8a32da 50%,#b62d69)" in css
        version = hashlib.sha256(css.encode()).hexdigest()[:12]
        for path in out.rglob("*.html"):
            for found in re.findall(r"/" + re.escape(name) + r"\?v=([a-f0-9]+)", path.read_text()):
                assert found == version, (path, name)
    # No byte added to the home page for it: the phone CSS budget is nearly full.
    off, _ = build(tmp_path, monkeypatch, True, "soft-off", soft_buttons=False)
    for name in soft_buttons.SHEETS:
        assert len((out / name).read_bytes()) == len((off / name).read_bytes())

    def ratio(hex_color):
        def channel(c):
            c /= 255
            return c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
        return 1.05 / (.2126 * channel(r) + .7152 * channel(g) + .0722 * channel(b) + .05)
    stops = [c for _, new in soft_buttons.SOFTER for c in re.findall(r"#[0-9a-f]{6}", new)]
    assert len(stops) == 6 and min(ratio(c) for c in stops) >= 5.4  # white text AA on every stop
    assert all("ffc83d" not in old + new for old, new in soft_buttons.SOFTER)  # yellow Amazon buttons untouched


def test_live_monitor_merged_path_offline():
    result = subprocess.run(["node", str(Path(__file__).with_name("site_check_merged.cjs"))],
                            capture_output=True, text=True, timeout=60, cwd=Path(__file__).parent.parent)
    assert result.returncode == 0, result.stdout + result.stderr


def test_browser_phone_and_laptop(tmp_path):
    result = subprocess.run(["node", str(Path(__file__).with_name("tools_merge_browser.cjs")), str(tmp_path)],
                            capture_output=True, text=True, timeout=600, cwd=Path(__file__).parent.parent)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    for width in (390, 1280):
        for scheme in ("light", "dark"):
            assert f"{width} {scheme}: ok" in result.stdout, result.stdout[-2000:]
