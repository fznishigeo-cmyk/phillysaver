"""The full disclaimer, active affiliate programs and compact disclosure links."""

import json
import re
import shutil
import subprocess
from datetime import timedelta
from html import unescape
from xml.etree import ElementTree

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import seo, site
from tests.test_support import NOW, render

AMAZON = "As an Amazon Associate, wowpriced earns from qualifying purchases."
LINK = '<a href="/disclosure.html">Disclosure</a>'
SECTIONS = (
    "How we make money", "Programs we use", "Prices and availability",
    "No warranty / your own judgment", "Links to other sites",
    "Limitation of liability", "Support payments", "Contact and updates",
)


def sections(html):
    main = re.search(r"<main>(.*?)</main>", html, re.S)[1]
    return dict(re.findall(r"<h2>(.*?)</h2>(.*?)(?=<h2>|$)", main, re.S))


@pytest.mark.parametrize("support", [False, True])
def test_disclaimer_copy_tokens_sitemap_and_stable_update_date(tmp_path, support):
    cfg = AppConfig(support_enabled=support, contact_email="hello@wowpriced.com",
                    custom_domain="wowpriced.com", drop_minutes=20,
                    ftc_disclosure=load_config().ftc_disclosure,
                    seo_indexnow_key="0123456789abcdef0123456789abcdef")
    out, _, _, _ = render(tmp_path, cfg)
    page = (out / "disclosure.html").read_text()
    content = sections(page)
    assert tuple(content) == SECTIONS
    assert '<h1>Disclaimer &amp; Affiliate Disclosure</h1>' in page
    assert '<link rel="canonical" href="https://wowpriced.com/disclosure.html">' in page
    assert "refreshed every 20 minutes" in page and "{{" not in page
    assert 'href="mailto:hello@wowpriced.com">hello@wowpriced.com</a>' in content["Contact and updates"]
    for heading, facts in {
        "How we make money": ("Affiliate commissions help support wowpriced",
                               "Partner sources keep their own affiliate credit",
                               'href="/how-it-works.html"'),
        "Prices and availability": ("Prices and stock can change quickly", "times shown",
                                    "do not guarantee", "checkout price is final",
                                    "Codes, coupons, rebates and other steps can expire"),
        "No warranty / your own judgment": ("good faith", "general information",
                                            "without a warranty", "completeness or accuracy",
                                            "not the seller", "returns, shipping",
                                            "warranties and customer support"),
        "Links to other sites": ("do not control external websites", "privacy policies and terms apply"),
        "Limitation of liability": ("To the extent permitted by law", "losses or damages",
                                     "does not limit rights or liability"),
        "Support payments": ("voluntary", "for-profit business", "not tax-deductible",
                              "buys nothing extra"),
        "Contact and updates": ("Changes will be posted on this page", "Last updated",
                                 '<time datetime="2026-10-14">October 14, 2026</time>'),
    }.items():
        text = " ".join(content[heading].split())
        assert all(fact in text for fact in facts), heading
    assert f'<p class="lede">{cfg.ftc_disclosure}</p>' in page
    assert not re.search(r"<script|<form|<dialog", "".join(content.values()))
    urls = ElementTree.parse(out / "sitemap.xml").findall("{*}url")
    entry = [url for url in urls if url.find("{*}loc").text == "https://wowpriced.com/disclosure.html"]
    assert len(entry) == 1  # lastmod follows the page facts (task 070), like privacy
    assert "https://wowpriced.com/disclosure.html" in json.loads(seo.due_list_path(out).read_text())
    db = DB(tmp_path / "state.db")
    try:
        site.render_site(db, cfg, NOW + timedelta(days=1), out_dir=out, deals=[])
    finally:
        db.close()
    assert (out / "disclosure.html").read_text() == page


@pytest.mark.parametrize("amazon,ebay,sovrn,expected", [
    (False, False, False, []),
    (True, False, False, ["Amazon Associates"]),
    (True, True, False, ["Amazon Associates", "eBay Partner Network"]),
    (True, True, True, ["Amazon Associates", "eBay Partner Network", "Sovrn Commerce"]),
    (True, False, True, ["Amazon Associates", "Sovrn Commerce"]),
    (False, True, False, ["eBay Partner Network"]),
    (False, False, True, ["Sovrn Commerce"]),
    (False, True, True, ["eBay Partner Network", "Sovrn Commerce"]),
])
def test_programs_follow_loaded_config(tmp_path, amazon, ebay, sovrn, expected):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"affiliate": {
        "amazon_tag": "example-20" if amazon else "",
        "ebay_campaign_id": "5339221823" if ebay else "",
        "sovrn_enabled": sovrn,
    }}))
    out, _, _, _ = render(tmp_path, load_config(path))
    programs = sections((out / "disclosure.html").read_text())["Programs we use"]
    assert re.findall(r"<strong>(.*?)\.</strong>", programs) == expected
    assert programs.count(AMAZON) == int(amazon)
    assert ("no active affiliate programs of our own" in programs) == (not expected)
    assert ("<ul>" in programs) == bool(expected)


def test_unset_contact_uses_the_contact_page(tmp_path):
    out, _, _, _ = render(tmp_path, AppConfig(contact_email='invalid"@example.com'))
    contact = sections((out / "disclosure.html").read_text())["Contact and updates"]
    assert '<a href="/contact.html">Contact us</a>' in contact
    assert "mailto:" not in contact and "invalid" not in contact


@pytest.mark.parametrize("support", [False, True])
def test_disclosure_has_wrapping_structure_at_360px_in_both_themes(tmp_path, support):
    out, _, _, _ = render(tmp_path, AppConfig(support_enabled=support))
    page = (out / "disclosure.html").read_text()
    css = re.search(r"<style>(.*?)</style>", page, re.S)[1]
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in page
    assert "color-scheme: light dark" in css and "prefers-color-scheme: dark" in css
    assert "*{box-sizing:border-box}" in css
    body = re.search(r"body\{(.*?)\}", css, re.S)[1]
    wrap = re.search(r"\.wrap\{(.*?)\}", css, re.S)[1]
    header = re.search(r"\.hrow\{(.*?)\}", css, re.S)[1]
    assert "overflow-wrap:anywhere" in body and "margin:0" in body
    assert "max-width:720px" in wrap and "padding:0 16px 48px" in wrap
    assert not re.search(r"(?<![-\w])(?:width|min-width):", body + wrap)
    assert "flex-wrap:wrap" in header and "@media (max-width:640px)" in css
    assert "h1{font-size:26px}" in css
    # Auto-width blocks fit the 360px viewport; text can break anywhere. Every
    # fixed-width child also fits the 328px content area, with no nowrap text.
    for selectors, rules in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        assert all(int(width) <= 360 - 2 * 16 for width in
                   re.findall(r"(?<![-\w])(?:width|min-width):\s*(\d+)px", rules)), selectors
        if "white-space:nowrap" in rules:
            assert ".sr-only" in selectors  # only the hidden signup label
    assert 'footer #footerlinks a{display:inline-block' in css
    if support:
        assert "min-width:0;width:100%" in css and "flex:none;width:48px" in css


def test_home_refresh_keeps_the_link_and_escapes_custom_copy(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    out, _, _, _ = render(tmp_path, AppConfig())
    home = (out / "index.html").read_text()
    esc = re.search(r"  function esc\(s\) \{.*?\n  \}", home, re.S)[0]
    update = re.search(r"    var disclosure = esc\(m.disclosure.*?\+ disclosureLink;", home, re.S)[0]
    cases = [
        ("Disclosure: some links earn commissions.", LINK + ": some links earn commissions."),
        ('Disclosure: <img src=x onerror=alert(1)> & "offer"',
         LINK + ': &lt;img src=x onerror=alert(1)&gt; &amp; &quot;offer&quot;'),
        ("Custom & affiliate copy.", "Custom &amp; affiliate copy. " + LINK),
        ("", LINK),
    ]
    script = '''
const assert = require('node:assert/strict');
const update = new Function('m', 'document', process.argv[1]);
const line = {};
const document = {getElementById(id) { assert.equal(id, 'metaline'); return line; }};
for (const [disclosure, expected] of JSON.parse(process.argv[2])) {
  update({disclosure}, document);
  assert.equal(line.innerHTML, expected);
  update({disclosure}, document);
  assert.equal(line.innerHTML, expected);
}
'''
    subprocess.run(["node", "-e", script, esc + "\n" + update, json.dumps(cases)],
                   check=True, capture_output=True, text=True)
    for disclosure, expected in cases:
        assert site.linked_disclosure(disclosure) == expected
        if disclosure.startswith("Disclosure:"):
            assert unescape(re.sub(r"<[^>]+>", "", expected)) == disclosure
