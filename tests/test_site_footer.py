"""The same quiet footer on every published page, including nested pages."""
import json
import re
import shutil
import subprocess
from html import unescape
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import site
from tests.test_publish_site import make_deal
from tests.test_support import NOW, LINK, MONTHLY, render

LINKS = [
    ("/about.html", "About"), ("/how-it-works.html", "How it works"),
    ("/hacks.html", "Deal hacks"), ("/careers.html", "Work with us"),
    ("/contact.html", "Contact"), ("/subscribe.html", "Join & support"),
    ("/privacy.html", "Privacy"), ("/disclosure.html", "Disclosure"),
]
@pytest.mark.parametrize("support", [False, True])
@pytest.mark.parametrize("amazon", [False, True])
def test_every_page_has_one_shared_footer(tmp_path, support, amazon):
    shipped = load_config()
    cfg = AppConfig(support_enabled=support, support_payment_link=LINK, support_monthly_link=MONTHLY,
                    paywall_open_access=True, affiliate_amazon_tag="example-20" if amazon else "",
                    affiliate_disclosure=shipped.affiliate_disclosure,
                    ftc_disclosure=shipped.ftc_disclosure,
                    seo_store_page_min_deals=1)
    out, doc, _, _ = render(tmp_path, cfg, [make_deal(dedupe_key=f"footer-{i}", final_price=5, regular_price=50) for i in range(10)])
    pages = {str(p.relative_to(out)): p.read_text() for p in out.rglob("*.html")}
    assert {"index.html", "404.html", "welcome.html", "subscribe.html", "disclosure.html",
            "best-deals-today/index.html", "stores/walmart/index.html",
            "deals/under-10/index.html"} <= pages.keys()
    assert any(name.startswith("deal/") for name in pages)
    expected_disclosure = cfg.affiliate_disclosure if amazon else cfg.ftc_disclosure
    assert doc["meta"]["disclosure"] == expected_disclosure
    for name, html in pages.items():
        footers = re.findall(r"<footer>(.*?)</footer>", html, re.S)
        assert len(footers) == 1, name
        footer = footers[0]
        nav = re.search(r'<nav id="footerlinks"[^>]*>(.*?)</nav>', footer, re.S)[1]
        assert [(url, unescape(label)) for url, label in re.findall(r'<a href="([^"]+)">([^<]+)</a>', nav)] == LINKS, name
        assert " ".join(unescape(re.sub(r"<[^>]+>", "", nav)).split()) == "© 2026 wowpriced · " + " · ".join(label for _, label in LINKS)
        fine = re.search(r'<p class="footerfine">(.*?)</p>', footer, re.S)[1]
        assert unescape(re.sub(r"<[^>]+>", "", fine)) == expected_disclosure, name
        assert '<span id="metaline"><a href="/disclosure.html">Disclosure</a>:' in fine, name
        for removed in ("wowpriced is free for everyone.", "data-support-note", "Membership —",
                        "Every deal links to the seller", "re-checked live", "Once: any amount"):
            assert removed not in footer, (name, removed)
        for url, _ in LINKS:
            assert (out / url.lstrip("/")).is_file()
        assert ('id="footer-signup"' in footer) == support, name
        assert ('id="acctfoot"' in footer) == (name == "index.html" and not support), name
    support_page = pages["subscribe.html"]
    if support:
        assert site.SUPPORT_NOTE_MONTHLY in support_page.split("<footer>")[0]
        assert "not tax-deductible" in support_page and "until you cancel" in support_page
    assert "Every deal links to the seller" in pages["how-it-works.html"].split("<footer>")[0]
    if shutil.which("node"):
        script = (Path(__file__).resolve().parents[1] / "scripts/site_check.js").read_text()
        start = script.index("function check(ok,")
        end = script.index("async function walk(", start)
        checker = tmp_path / "footer-check.cjs"
        checker.write_text(script[start:end] + "\nconst fs = require('node:fs');\n"
                           + "const pages = JSON.parse(fs.readFileSync(process.argv[2]));\n"
                           + "const meta = JSON.parse(fs.readFileSync(process.argv[3]));\n"
                           + "for (const html of Object.values(pages)) checkFooter(html, meta);\n"
                           + "let caught = false; try {checkFooter(pages['index.html'].replace('About</a>', 'Wrong</a>'), meta);} catch {caught = true;}\n"
                           + "if (!caught) throw Error('footer regression not detected');\n")
        (tmp_path / "pages.json").write_text(json.dumps(pages))
        (tmp_path / "meta.json").write_text(json.dumps(doc["meta"]))
        subprocess.run(["node", str(checker), str(tmp_path / "pages.json"), str(tmp_path / "meta.json")],
                       check=True, capture_output=True, text=True)


def test_footer_year_is_the_render_year(tmp_path):
    db = DB(tmp_path / "state.db")
    try:
        site.render_site(db, AppConfig(), NOW.replace(year=2027), out_dir=tmp_path / "site", deals=[])
    finally:
        db.close()
    for page in (tmp_path / "site").rglob("*.html"):
        assert "© 2027 wowpriced" in page.read_text(), page


def test_footer_contrast_in_both_themes():
    def luminance(color):
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
        return sum(v * w for v, w in zip(linear, (.2126, .7152, .0722)))

    for path in site.TEMPLATES_DIR.glob("*.html"):
        html = path.read_text()
        if "{{SITE_FOOTER}}" not in html:
            continue
        colors = re.findall(r"--bg:(#[0-9a-f]{6});.*?--muted:(#[0-9a-f]{6});", html, re.S)
        assert len(colors) == 2, path
        for background, foreground in colors:
            light, dark = sorted((luminance(background), luminance(foreground)), reverse=True)
            assert (light + .05) / (dark + .05) >= 4.5, (path, foreground, background)


def test_site_check_budget_fits_the_walk_and_names_its_own_aborts():
    # 2026-10-13: the 40-request budget ran out on the phone walk's last page,
    # which failed as a bare net::ERR_FAILED. Still paced at 1 request/s.
    script = (Path(__file__).resolve().parents[1] / "scripts/site_check.js").read_text()
    assert "const SITE_REQUEST_LIMIT = 60;" in script
    assert "Math.max(0, 1000 - (Date.now() - lastSiteRequest))" in script
    assert "(guard: ${lastAbort})" in script
