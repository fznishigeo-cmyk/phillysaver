"""Seasonal hub selection, permanent URLs and publication guards, entirely offline."""

import copy
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone

import pytest
import yaml

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import event_pages, indexnow, seo, seo_audit, site
from tests.test_amazon_as_of import NOW as BASE_NOW, config as baseline_config, sample_deals
from tests.test_publish_site import make_deal
from tests.test_seo_deal_pages import _page

NOW = datetime(2026, 11, 10, 16, tzinfo=timezone.utc)


@pytest.fixture
def cfg():
    shipped = load_config()
    return AppConfig(custom_domain="wowpriced.com", paywall_open_access=True,
        seo_event_pages_enabled=True, seo_event_pages_events=shipped.seo_event_pages_events,
        seo_indexnow_key="0123456789abcdef", front_page={"niche_categories": ["auto"]})


@pytest.fixture
def db():
    database = DB(":memory:")
    yield database
    database.close()


def offers(n=10, **changes):
    return [make_deal(dedupe_key=f"event-{i}", item=f"Black Friday offer {i}",
        category="kitchen", codes=[], conditions=[], **changes) for i in range(n)]


def render(db, cfg, out, rows, now=NOW):
    return site.render_site(db, cfg, now, out_dir=out, deals=rows, last_run={"run_key": "events"})


def tree(out):
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}


def test_config_shipped_on_code_default_off_and_custom_values(tmp_path):
    assert not AppConfig().seo_event_pages_enabled
    shipped = load_config()
    assert shipped.seo_event_pages_enabled
    assert [e["date"] for e in shipped.seo_event_pages_events] == ["2026-11-27", "2026-11-30"]
    path = tmp_path / "config.yaml"
    path.write_text("{}")
    assert not load_config(path).seo_event_pages_enabled
    block = copy.deepcopy(shipped.raw["seo"]["event_pages"])
    block.update(min_items=2, max_items=4, intro={"black-friday": "Operator copy"})
    path.write_text(yaml.safe_dump({"seo": {"event_pages": block}}))
    configured = load_config(path)
    assert configured.seo_event_pages_min_items == 2 and configured.seo_event_pages_max_items == 4
    assert configured.seo_event_pages_intro == {"black-friday": "Operator copy"}


@pytest.mark.parametrize("field,value", [
    ("enabled", "false"), ("min_items", 0), ("max_items", 0), ("max_items", 9),
    ("events", {}), ("events", [None]), ("intro", []), ("intro", {"elsewhere": "text"}),
])
def test_invalid_event_config_rejected(tmp_path, field, value):
    block = copy.deepcopy(load_config().raw["seo"]["event_pages"])
    block[field] = value
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"seo": {"event_pages": block}}))
    with pytest.raises(ValueError, match="seo.event_pages"):
        load_config(path)


@pytest.mark.parametrize("field,value", [
    ("slug", "../escape"), ("name", ""), ("date", "2026-02-30"),
    ("window_days", [3, -28]), ("window_days", [-28]), ("window_days", [False, 3]),
    ("keywords", []), ("keywords", [""]), ("keywords", "black friday"),
])
def test_invalid_event_entry_rejected(tmp_path, field, value):
    block = copy.deepcopy(load_config().raw["seo"]["event_pages"])
    block["events"][0][field] = value
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"seo": {"event_pages": block}}))
    with pytest.raises(ValueError, match="seo.event_pages"):
        load_config(path)


def test_duplicate_event_slugs_rejected(tmp_path):
    block = copy.deepcopy(load_config().raw["seo"]["event_pages"])
    block["events"].append(block["events"][0])
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"seo": {"event_pages": block}}))
    with pytest.raises(ValueError, match="unique supported event slugs"):
        load_config(path)


@pytest.mark.parametrize("facts", [
    {"item": "BLACK FRIDAY cookware"}, {"conditions": ["Black Friday sale"]},
    {"evidence": {"final_price_quote": "Black Friday price $10"}},
])
def test_explicit_event_fields_qualify_all_year(cfg, facts):
    event = cfg.seo_event_pages_events[0]
    for day in (date(2026, 1, 1), date(2026, 11, 10), date(2026, 12, 10)):
        assert event_pages.qualifies({}, facts, event, day)
    assert not event_pages.qualifies({}, {"item": "Cyber Monday cookware"}, event, NOW.date())


@pytest.mark.parametrize("day,expected", [
    ("2026-10-29", False), ("2026-10-30", True), ("2026-11-30", True), ("2026-12-01", False),
])
def test_only_published_picks_qualify_by_window(cfg, day, expected):
    event = cfg.seo_event_pages_events[0]
    assert event_pages.qualifies({"pick": 1}, {"item": "Ordinary offer"}, event, date.fromisoformat(day)) is expected
    assert not event_pages.qualifies({}, {"item": "Ordinary offer"}, event, date.fromisoformat(day))


def test_selection_excludes_whole_wall_niche_dead_locked_unconfirmed_and_caps(cfg, tmp_path):
    originals = {str(i): {"item": "Black Friday" if i != 1 else "Ordinary offer"} for i in range(9)}
    rows = [dict(dedupe_key=str(i), key=str(i), item=f"Offer {i}", status="alive", category="kitchen",
                 image_url="https://img.example/offer.jpg" if i == 8 else "") for i in range(9)]
    rows[2]["locked"] = True
    rows[3]["status"] = "dead"
    rows[4]["price_unconfirmed"] = True
    rows[5]["category"] = "auto"
    originals["6"]["item"] = "Ordinary pick"
    rows[6]["pick"] = 1
    cfg.seo_event_pages_max_items = 2
    event_pages.publish(tmp_path, cfg, NOW, rows, originals, {},
        (seo.TEMPLATES_DIR / "hub.html").read_text(), "https://wowpriced.com")
    html = _page(tmp_path, "/black-friday/").read_text()
    assert "Offer 8" in html  # photo preferred, despite later wall position
    assert "Offer 0" in html
    for i in range(1, 8):
        assert f"Offer {i}" not in html
    cfg.seo_event_pages_max_items = 60
    event_pages.publish(tmp_path, cfg, NOW, rows, originals, {},
        (seo.TEMPLATES_DIR / "hub.html").read_text(), "https://wowpriced.com")
    html = _page(tmp_path, "/black-friday/").read_text()
    assert "Offer 6" in html  # a pick qualifies without an event keyword
    assert "Offer 1" not in html


def test_floors_sitemap_indexnow_links_schema_and_mobile_hub_layout(db, cfg, tmp_path):
    result = render(db, cfg, tmp_path, offers(10))
    black = _page(tmp_path, "/black-friday/").read_text()
    cyber = _page(tmp_path, "/cyber-monday/").read_text()
    assert '<h1>Black Friday Deals 2026</h1>' in black
    assert '10 live deals · updated every 15 minutes.' in black
    assert '<time datetime="2026-11-27">November 27, 2026</time>' in black
    assert '<link rel="canonical" href="https://wowpriced.com/black-friday/">' in black
    assert 'name="robots" content="noindex,follow"' not in black
    assert 'name="robots" content="noindex,follow"' in cyber
    sitemap = seo_audit.sitemap_urls(tmp_path, "https://wowpriced.com")
    due = json.loads(seo.due_list_path(tmp_path).read_text())
    assert "https://wowpriced.com/black-friday/" in sitemap and "https://wowpriced.com/black-friday/" in due
    assert "https://wowpriced.com/cyber-monday/" not in sitemap + due
    schemas = seo_audit.Page(black, "/black-friday/").schemas
    assert {s["@type"] for s in schemas} == {"BreadcrumbList", "ItemList"}
    assert result["seo_audit"]["indexable_by_family"]["event"] == 1
    assert all(result["seo_audit"][k] == 0 for k in (
        "empty_indexable", "schema_mismatches", "broken_internal_links", "sitemap_missing", "sitemap_extra"))
    hub = _page(tmp_path, "/deals/home-kitchen/").read_text()
    assert re.search(r"<style>(.*?)</style>", black, re.S)[1] == re.search(r"<style>(.*?)</style>", hub, re.S)[1]
    assert "<img" not in black and '{{' not in black
    for path in ("/", "/deals/home-kitchen/", "/best-deals-today/", "/about.html"):
        page = (tmp_path / path.lstrip("/") if path.endswith(".html") else _page(tmp_path, path)).read_text()
        footer = page.split("<footer>", 1)[1]
        for event_path, _ in event_pages.links(cfg):
            assert f'href="{event_path}"' in footer
            if path.startswith("/deals/"):
                assert f'href="{event_path}"' in page.split('<h2>More categories</h2>')[1].split('</main>')[0]
    home = _page(tmp_path, "/").read_text()
    assert re.search(r'</nav><nav class="hrow event-links".*?href="/black-friday/"', home)
    assert '.event-links a{min-height:44px}' in home
    render(db, cfg, tmp_path, offers(9), now=NOW + timedelta(hours=7))
    assert 'content="noindex,follow"' in _page(tmp_path, "/black-friday/").read_text()
    assert "https://wowpriced.com/black-friday/" not in seo_audit.sitemap_urls(tmp_path, "https://wowpriced.com")
    assert "https://wowpriced.com/black-friday/" not in json.loads(seo.due_list_path(tmp_path).read_text())


def test_home_chips_only_while_a_window_is_open(db, cfg, tmp_path):
    early = datetime(2026, 10, 16, 16, tzinfo=timezone.utc)
    render(db, cfg, tmp_path, offers(10), now=early)
    home = _page(tmp_path, "/").read_text()
    assert "event-links" not in home  # no extra row above the deals six weeks early
    footer = home.split("<footer>", 1)[1]
    assert 'href="/black-friday/"' in footer and 'href="/cyber-monday/"' in footer
    assert event_pages.open_links(cfg, early) == []
    assert [p for p, _ in event_pages.open_links(cfg, datetime(2026, 10, 30, 16, tzinfo=timezone.utc))] == [
        "/black-friday/"]


@pytest.mark.parametrize("day,early", [("2026-10-15", True), ("2026-12-15", False)])
def test_permanent_pages_show_current_top_picks_outside_window(db, cfg, tmp_path, day, early):
    cfg.front_page = load_config().front_page
    now = datetime.fromisoformat(day).replace(hour=16, tzinfo=timezone.utc)
    row = make_deal(item="Everyday cookware", category="kitchen", codes=[], conditions=[],
        first_seen_at=now.isoformat(), image_url="https://images.example/cookware.jpg")
    render(db, cfg, tmp_path, [row], now)
    for path, name in event_pages.links(cfg):
        html = _page(tmp_path, path).read_text()
        assert "Everyday cookware" in html
        assert (f"Early {name} deals" in html) is early
        assert "ended" not in re.search(r"<h1>(.*?)</h1>", html)[1].lower()


def test_amazon_hide_intro_escape_and_configured_cadence(db, cfg, tmp_path):
    cfg.amazon_history = "hide"
    cfg.drop_minutes = 30
    cfg.seo_event_pages_intro = {"black-friday": '<script>operator & copy</script>'}
    row = offers(1, store="Amazon", canonical_url="https://www.amazon.com/dp/B012345678",
                 verify_tier="history-checked", price_rechecked_at=NOW.isoformat())[0]
    for age in (1, 2, 3):
        db.record_price(row["dedupe_key"], row["store"], 15 + age,
                        NOW - timedelta(days=age), str(age))
    render(db, cfg, tmp_path, [row])
    html = _page(tmp_path, "/black-friday/").read_text()
    assert "updated every 30 minutes" in html
    assert "&lt;script&gt;operator &amp; copy&lt;/script&gt;" in html
    assert 'tier-verified' in html and 'tier-history-checked' not in html.split("</style>", 1)[1]
    body = html.split("</style>", 1)[1].casefold()
    for phrase in ("price history", "lowest since", "lowest we've seen", "90-day", "<svg class=\"price-history"):
        assert phrase not in body
    assert "operator &amp; copy" not in _page(tmp_path, "/cyber-monday/").read_text()


def test_membership_rotation_never_leaks_locked_event_deals(db, cfg, tmp_path):
    cfg.paywall_enabled, cfg.paywall_open_access, cfg.paywall_teaser_count = True, False, 2
    render(db, cfg, tmp_path, offers())
    public = json.loads((tmp_path / "deals.json").read_text())["deals"]
    free = [d for d in public if not d.get("locked")]
    assert len(free) == 2
    html = _page(tmp_path, "/black-friday/").read_text()
    assert '2 live deals' in html and 'content="noindex,follow"' in html
    assert html.count('<article class="card">') == 2
    assert {n.text() for n in seo_audit.Page(html, "/black-friday/").root.nodes() if n.has("title")} == {
        d["item"] for d in free}


def test_store_price_and_every_category_related_blocks_link_to_both_events(db, cfg, tmp_path):
    render(db, cfg, tmp_path, offers(15))
    paths = [seo.hub_href(slug) for _label, _group, slug in seo.HUB_GROUPS]
    paths += ["/stores/walmart/", "/deals/under-10/", "/black-friday/", "/cyber-monday/"]
    for path in paths:
        html = _page(tmp_path, path).read_text()
        related = html.split('<h2>More categories</h2>')[1].split('</main>')[0]
        for event_path, _name in event_pages.links(cfg):
            assert f'href="{event_path}"' in related


def test_same_publish_is_byte_identical_and_disabling_removes_pages(db, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    rows = offers()
    render(db, cfg, tmp_path, rows)
    before = tree(tmp_path)
    render(db, cfg, tmp_path, rows)
    assert tree(tmp_path) == before
    cfg.seo_event_pages_enabled = False
    render(db, cfg, tmp_path, rows)
    assert not (tmp_path / "black-friday").exists() and not (tmp_path / "cyber-monday").exists()
    assert all(seo_audit.page_family(url) != "event" for url in json.loads(seo.due_list_path(tmp_path).read_text()))
    for html in tmp_path.rglob("*.html"):
        assert 'href="/black-friday/"' not in html.read_text()


@pytest.mark.parametrize("enabled", [True, False])
def test_previously_pending_events_leave_submission_manifest_below_floor_or_disabled(db, cfg, tmp_path, enabled):
    render(db, cfg, tmp_path, offers(10))
    url = "https://wowpriced.com/black-friday/"
    assert url in json.loads(indexnow.manifest_path(tmp_path).read_text())["versions"]
    assert db.indexnow_pages()[url]["pending_since"]
    cfg.seo_event_pages_enabled = enabled
    render(db, cfg, tmp_path, offers(9), now=NOW + timedelta(hours=7))
    manifest = json.loads(indexnow.manifest_path(tmp_path).read_text())
    assert url not in manifest["versions"]
    assert url not in json.loads(seo.due_list_path(tmp_path).read_text())


@pytest.mark.parametrize("mode,expected", [
    ("membership", "fb6b8a629a7dcbd43cd560a0cfb1c28e0ac65d7dc998200b867397535489afc7"),
    ("open", "bbec8f42c4f94c2f02ea87298cd82b302aa13160da24d07238faa4dcec70a165"),
    ("support", "66c7c8f883517d379c92d96ee09f3687eaa7571660dc49c1cad417a413ca2812"),
    ("shipped", "293168d700ba0a7ef81c33fe89265639f0e50ea5ee02833f8716931af1732e28"),
])
@pytest.mark.usefixtures("pre_extension_privacy")
def test_off_matches_every_untouched_head_byte(db, tmp_path, monkeypatch, mode, expected):
    monkeypatch.setattr(site, "util_now", lambda: BASE_NOW)
    configured = baseline_config(mode)
    configured.seo_event_pages_enabled = False
    render(db, configured, tmp_path, sample_deals(), BASE_NOW)
    files = {name: hashlib.sha256(value).hexdigest() for name, value in tree(tmp_path).items()}
    assert hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest() == expected
