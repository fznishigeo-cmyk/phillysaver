"""Tests for dealradar.publish.seo — the server-rendered wall, category hubs,
404 page, _headers, sitemap and IndexNow. Fully offline (httpx mocked)."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import yaml

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import seo
from dealradar.publish.site import render_site
from tests.test_publish_site import NOW, make_deal

ROOT = Path(__file__).resolve().parent.parent
KEY = "0123456789abcdef0123456789abcdef"
HUBS = [slug for _label, _group, slug in seo.HUB_GROUPS]


@pytest.fixture()
def db(tmp_path):
    database = DB(tmp_path / "seo_test.db")
    yield database
    database.close()


@pytest.fixture()
def cfg(tmp_path):
    return AppConfig(site_dir=str(tmp_path / "unused"), min_pct=70.0, min_abs_savings=5.0,
                     custom_domain="wowpriced.com", seo_indexnow_key=KEY,
                     seo_hub_min_items=3, paywall_enabled=True, paywall_teaser_count=2,
                     paywall_teaser_pool=48)


def _deals(n=6):
    """Two stores, two categories; distinctive names so a leak is obvious."""
    return [make_deal(dedupe_key=f"k{i}", item=f"ZZITEM-{i}", store=("Walmart", "Target")[i % 2],
                      category=("electronics", "kitchen")[i % 2], score=100.0 - i,
                      canonical_url=f"https://shop.example/p{i}",
                      pct_at_checkout=71.0 + i, image_url=f"https://img.example/p{i}.jpg")
            for i in range(n)]


def _seed_week(db, n, category="electronics", **payload):
    """n deals first seen yesterday that clear every gate (the 7-day stats)."""
    for i in range(n):
        db.insert_deal(dedupe_key=f"{category}|w{i}", store="Walmart", item=f"W{i}",
                       normalized_item=f"w{i}", canonical_url="", final_price=10.0,
                       pct_at_checkout=70.0 + 2 * i, abs_savings=40.0, online_only="true",
                       expires_at="", now=NOW - timedelta(days=1),
                       payload={"category": category, "conditions": [], **payload})


def _render(db, cfg, out, deals=None, now=NOW):
    return render_site(db, cfg, now, out_dir=out, deals=_deals() if deals is None else deals)


def test_static_wall_holds_the_free_deals_credits_and_locked_lines(db, cfg, tmp_path):
    out = tmp_path / "site"
    _render(db, cfg, out)
    teaser = json.loads((out / "deals.json").read_text(encoding="utf-8"))["deals"]
    free = [d for d in teaser if not d.get("locked")]
    assert len(free) == 2
    html = (out / "index.html").read_text(encoding="utf-8")
    wall = html[html.index('<div id="content" data-clarity-unmask="true">'):html.index('<div id="paywall"')]
    for d in free:
        assert d["item"] in wall and f'href="{d["url"]}"' in wall
    assert 'via <a href="https://slickdeals.net/f/1" rel="noopener">Slickdeals</a>' in wall
    assert '<h2 class="secth sr-only">' in wall and "<noscript>" in wall
    # Locked deals: one line per store, a count and a % range only.
    assert re.search(r"<li>2 more deals at (Walmart|Target), \d\d–\d\d% off, members only</li>", wall)
    assert "img.example" not in wall and "<img" not in wall
    # Tabs are real links to the hubs, drawn from the one Python list.
    nav = html[html.index('<nav id="cattabs"'):html.index("</nav>", html.index('<nav id="cattabs"'))]
    assert '<a class="cattab active" href="/" data-i="0">Trending</a>' in nav
    for slug in HUBS:
        assert f'href="/deals/{slug}/"' in nav
    assert "e.preventDefault();" in html and 'var CATGROUPS = [["Trending", null, ""]' in html
    assert '<meta name="robots" content="noimageindex">' in html


def test_dead_deals_on_the_wall_only_as_deals_json_has_them(db, cfg, tmp_path):
    out = tmp_path / "site"
    cfg.paywall_open_access = True
    gone = make_deal(dedupe_key="gone", item="ZZGONE", status="dead", death="confirmed",
                     died_at="2026-10-09T13:00:00+00:00")
    _render(db, cfg, out, deals=_deals(12) + [gone])
    assert "ZZGONE" in (out / "deals.json").read_text(encoding="utf-8")
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "Just missed · gone 1h ago" in html and "ZZGONE" in html
    assert 'secth missed' not in html
    for slug in HUBS:  # the hubs show live deals only
        assert "ZZGONE" not in (out / "deals" / slug / "index.html").read_text(encoding="utf-8")


def test_no_template_token_is_left_in_any_output_file(db, cfg, tmp_path):
    out = tmp_path / "site"
    _seed_week(db, 3)
    _render(db, cfg, out)
    files = [p for p in out.rglob("*") if p.is_file()]
    assert len(files) > 20
    for p in files:
        assert "{{" not in p.read_text(encoding="utf-8"), p


def test_hub_floor_reads_the_stable_7_day_count(db, cfg, tmp_path):
    out = tmp_path / "site"
    _seed_week(db, 3)
    db.update_deal("electronics|w0", verify_tier="corroborated")
    _render(db, cfg, out)
    sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
    elec = (out / "deals" / "electronics" / "index.html").read_text(encoding="utf-8")
    assert '<meta name="robots"' not in elec
    assert "<loc>https://wowpriced.com/deals/electronics/</loc><lastmod>" in sitemap
    assert "3 new deals in this category passed our checks, median 72% off, " \
           "1 confirmed by 2+ independent sources." in elec
    # Kitchen has live deals on the wall but nothing in the 7-day count:
    # thin, so noindex and out of the sitemap (never the free or locked count).
    kitchen = (out / "deals" / "home-kitchen" / "index.html").read_text(encoding="utf-8")
    assert "ZZITEM-1" in kitchen or "more deal" in kitchen
    assert '<meta name="robots" content="noindex">' in kitchen
    assert "/deals/home-kitchen/" not in sitemap
    assert '<meta name="robots" content="noindex">' in \
        (out / "deals" / "pets" / "index.html").read_text(encoding="utf-8")


def test_hubs_have_unique_branded_copy_and_root_absolute_links(db, cfg, tmp_path):
    out = tmp_path / "site"
    _render(db, cfg, out)
    copy = yaml.safe_load((seo.TEMPLATES_DIR / "hubs.yaml").read_text(encoding="utf-8"))
    assert set(copy) == set(HUBS)
    titles, descs = set(), set()
    banned = ("hip2save", "krazy", "kcl", "slickdeals", "dealnews", "brad", "reddit",
              "telegram", "keepa", "southern savers", "dealmoon", "wirecutter", "cnet",
              "9to5", "doctor of credit", "retailmenot", "freebie")
    for slug in HUBS:
        html = (out / "deals" / slug / "index.html").read_text(encoding="utf-8")
        title = re.search(r"<title>(.*)</title>", html).group(1)
        desc = re.search(r'<meta name="description" content="([^"]*)">', html).group(1)
        h1 = re.search(r"<h1>(.*)</h1>", html).group(1)
        assert title.endswith(" — wowpriced") and title.startswith(h1)
        titles.add(title)
        descs.add(desc)
        for text in (title, desc, h1):
            assert not any(b in text.lower() for b in banned), (slug, text)
        assert f'<link rel="canonical" href="https://wowpriced.com/deals/{slug}/">' in html
        for href in re.findall(r'href="([^"]*)"', html):
            assert href.startswith(("/", "https://", "data:")), (slug, href)
        assert 'fetch("/deals.json' in html and 'href="/about.html"' in html
        intro = re.search(r'<p class="lede">(.*)</p>', html).group(1)
        assert 2 <= intro.count(". ") + 1 <= 4 and "{" not in intro
    assert len(titles) == len(descs) == len(HUBS)
    used = (out / "deals" / "used-open-box" / "index.html").read_text(encoding="utf-8")
    assert "<h1>Used &amp; Open-Box Deals (incl. Amazon Resale)</h1>" in used


def test_404_page_and_cloudflare_headers(db, cfg, tmp_path):
    out = tmp_path / "site"
    _render(db, cfg, out)
    page = (out / "404.html").read_text(encoding="utf-8")
    assert '<meta name="robots" content="noindex">' in page and "wowpriced" in page
    assert 'href="/"' in page and 'href="/about.html"' in page
    for slug in HUBS:
        assert f'href="/deals/{slug}/"' in page
    assert (out / "_headers").read_text(encoding="utf-8") == (
        "https://:project.pages.dev/*\n  X-Robots-Tag: noindex\n\n"
        "https://:version.:project.pages.dev/*\n  X-Robots-Tag: noindex\n\n"
        "/deals.json\n  X-Robots-Tag: noindex\n  Cache-Control: no-store\n\n"
        "/\n  Cache-Control: no-cache\n\n"
        "/*.html\n  Cache-Control: no-cache\n\n"
        "/deal/*\n  Cache-Control: no-cache\n\n"
        "/deals/*\n  Cache-Control: no-cache\n\n"
        "/stores/*\n  Cache-Control: no-cache\n\n"
        "/best-deals-today/*\n  Cache-Control: no-cache\n\n"
        "/join\n  Cache-Control: no-cache\n")
    assert (out / f"{KEY}.txt").read_text(encoding="utf-8") == KEY
    robots = (out / "robots.txt").read_text(encoding="utf-8")
    assert "Allow: /\n" in robots and "deals.json" not in robots


def test_rotating_teaser_changes_only_deal_page_sitemap_entries(db, cfg, tmp_path):
    _seed_week(db, 3)
    _render(db, cfg, tmp_path / "a")
    _render(db, cfg, tmp_path / "b")
    a, b = ({d["key"] for d in json.loads((tmp_path / x / "deals.json").read_text())["deals"]
             if not d.get("locked")} for x in "ab")
    assert a and not a & b  # the free picks rotated between the two publishes...
    sitemap = (tmp_path / "a" / "sitemap.xml").read_bytes()
    other = (tmp_path / "b" / "sitemap.xml").read_bytes()
    without_deals = lambda xml: re.sub(rb"  <url><loc>https://wowpriced.com/deal/.*?</url>\n", b"", xml)
    assert without_deals(sitemap) == without_deals(other)  # hub dates remain stable
    assert sitemap != other  # only the newly unlocked deals get pages
    text = sitemap.decode()
    assert "<url><loc>https://wowpriced.com/about.html</loc></url>" in text
    assert "<url><loc>https://wowpriced.com/</loc></url>" in text
    assert "<lastmod>2026-10-09T12:00:00+00:00</lastmod>" in text  # newest deal shown


def test_indexnow_due_list_respects_the_interval_and_reruns(db, cfg, tmp_path):
    cfg.paywall_enabled = False  # the wall changes only when the deals do
    cfg.seo_indexnow_min_hours = 6
    out = tmp_path / "site"
    due = lambda: json.loads((tmp_path / seo.INDEXNOW_FILE).read_text(encoding="utf-8"))
    _seed_week(db, 3)
    _render(db, cfg, out)
    first = due()
    assert "https://wowpriced.com/" in first and "https://wowpriced.com/about.html" in first
    assert "https://wowpriced.com/deals/electronics/" in first  # indexable hub
    assert "https://wowpriced.com/deals/pets/" not in first      # noindex hub
    assert not (out / seo.INDEXNOW_FILE).exists()                # never on the site
    _render(db, cfg, out)
    assert due() == []  # the same publish again lists nothing
    more = _deals() + [make_deal(dedupe_key="new", item="ZZNEW", category="electronics")]
    _render(db, cfg, out, deals=more, now=NOW + timedelta(hours=1))
    new_url = "https://wowpriced.com" + seo.deal_path("ZZNEW", "11507a0e")
    assert due() == [new_url]  # new page is due immediately; changes wait
    _render(db, cfg, out, deals=more, now=NOW + timedelta(hours=7))
    changed = {"https://wowpriced.com/", "https://wowpriced.com/deals/electronics/",
               "https://wowpriced.com/best-deals-today/"}
    changed.update("https://wowpriced.com" + seo.deal_path(d["item"],
                   hashlib.sha256(d["dedupe_key"].encode()).hexdigest()[:8])
                   for d in _deals() if d["category"] == "electronics")
    assert set(due()) == changed
    _render(db, cfg, out, deals=more, now=NOW + timedelta(hours=14))
    assert due() == ["https://wowpriced.com/best-deals-today/"]  # crossed UTC midnight
    sent = json.loads(db.meta_get("indexnow_sent"))
    assert set(sent) == set(first) | {new_url}


def test_indexnow_hub_dipping_under_its_floor_keeps_its_interval(db, cfg):
    hub = "https://wowpriced.com/deals/pets/"
    due = lambda minutes, pages: seo.indexnow_due(db, cfg, NOW + timedelta(minutes=minutes), pages)
    assert due(0, {hub: "3 deals"}) == [hub]
    assert due(10, {}) == []                 # noindex: out of the list for a while
    assert due(20, {hub: "4 deals"}) == []   # back with new content, inside the 6 h
    assert due(7 * 60, {hub: "4 deals"}) == [hub]
    assert due(14 * 60, {}) == []
    assert hub not in json.loads(db.meta_get("indexnow_sent"))  # gone past its interval


def test_indexnow_relists_an_unchanged_url_after_the_resubmit_days(db, cfg):
    # The listing is recorded at publish time, before the POST; if that POST
    # fails, a page that never changes must still come back.
    about = "https://wowpriced.com/about.html"
    cfg.seo_indexnow_resubmit_days = 7
    due = lambda hours: seo.indexnow_due(db, cfg, NOW + timedelta(hours=hours), {about: "same"})
    assert due(0) == [about]
    assert due(7) == [] and due(30) == [] and due(144) == []
    assert due(7 * 24) == [about]
    cfg.seo_indexnow_resubmit_days = 0  # off: unchanged URLs are never listed again
    assert due(60 * 24) == []


def test_hubs_and_indexnow_add_no_rows(db, cfg, tmp_path):
    _seed_week(db, 3)
    count = lambda: {t: db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                     for t in ("deals", "sightings", "price_history", "alerts_sent")}
    before = count()
    for run in range(2):
        _render(db, cfg, tmp_path / f"s{run}")
    assert count() == before


def _cli_config(tmp_path) -> Path:
    text = (ROOT / "config.yaml").read_text().replace(
        'site_dir: "site"', f'site_dir: "{tmp_path / "site"}"')
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


def _mock_client(monkeypatch, status=200):
    seen, real = [], httpx.Client

    def handler(request):
        seen.append(request)
        return httpx.Response(status)

    monkeypatch.setattr(seo.httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(handler)))
    return seen


def test_cli_posts_the_due_list_to_indexnow(tmp_path, monkeypatch, caplog):
    from dealradar.cli import main

    cfg_path = _cli_config(tmp_path)
    key = load_config(cfg_path).seo_indexnow_key
    assert re.fullmatch(r"[0-9a-f]{32}", key)
    urls = ["https://wowpriced.com/", "https://wowpriced.com/deals/electronics/"]
    (tmp_path / seo.INDEXNOW_FILE).write_text(json.dumps(urls))
    seen = _mock_client(monkeypatch, 202)
    with caplog.at_level("INFO"):
        assert main(["indexnow", "--config", str(cfg_path)]) == 0
    (req,) = seen
    assert str(req.url) == "https://api.indexnow.org/indexnow" and req.method == "POST"
    assert req.headers["content-type"] == "application/json; charset=utf-8"
    assert json.loads(req.content) == {
        "host": "wowpriced.com", "key": key,
        "keyLocation": f"https://wowpriced.com/{key}.txt", "urlList": urls}
    # Public Actions logs: counts and the status only.
    assert "2 URL(s) submitted, HTTP 202" in caplog.text
    assert "deals/electronics" not in caplog.text and key not in caplog.text


def test_cli_sends_nothing_when_nothing_is_due_and_fails_on_a_refusal(tmp_path, monkeypatch):
    from dealradar.cli import main

    cfg_path = _cli_config(tmp_path)
    seen = _mock_client(monkeypatch, 403)
    assert main(["indexnow", "--config", str(cfg_path)]) == 0  # no due-list yet
    (tmp_path / seo.INDEXNOW_FILE).write_text("[]")
    assert main(["indexnow", "--config", str(cfg_path)]) == 0
    assert seen == []
    (tmp_path / seo.INDEXNOW_FILE).write_text(json.dumps(["https://wowpriced.com/"]))
    assert main(["indexnow", "--config", str(cfg_path)]) == 1  # 403: the key file is not live
    assert len(seen) == 1
