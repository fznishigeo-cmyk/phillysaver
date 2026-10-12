"""Search engines: the server-rendered public wall, the category hubs
(/deals/<slug>/), the 404 page, Cloudflare's _headers, sitemap.xml and the
IndexNow due-list.

Every public byte here comes from the teaser render_site already built
(docs/paywall.md): free deals in full, locked deals only as a count and a %
range, never a name, price, link or photo. Static HTML carries no images at
all. Nothing here adds a DB row: the hub stats only read, and IndexNow keeps
one meta key.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import statistics
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

import httpx
import yaml

from ..util import iso, parse_iso

logger = logging.getLogger("dealradar.publish.seo")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

#: The wall's category tabs, one hub each (All is the home page):
#: (label, categories or "USED", hub slug). index.html's tabs and the hubs
#: both read this one list, so they cannot drift apart.
CATGROUPS: tuple[tuple[str, Any, str], ...] = (
    ("All", None, ""),
    # Sentinel tab: filters on the used/open_box CONDITIONS, not category —
    # the Amazon Warehouse giveaway-priced finds get their own front door.
    ("Used & Open-Box", "USED", "used-open-box"),
    ("Home & Kitchen", ["home", "kitchen"], "home-kitchen"),
    ("Beauty & Personal Care", ["beauty"], "beauty-personal-care"),
    ("Clothing, Shoes & Jewelry", ["fashion"], "clothing-shoes-jewelry"),
    ("Electronics", ["electronics", "computers", "gaming"], "electronics"),
    ("Sports & Outdoors", ["sports", "outdoors"], "sports-outdoors"),
    ("Health & Household", ["health", "grocery"], "health-household"),
    ("Toys & Games", ["toys_games", "kids_baby"], "toys-games"),
    # Own tab by operator call: 85%-off rotors are the "WOW that's cheap"
    # screenshot material — they get buried in a grab-bag Other tab.
    ("Automotive", ["auto"], "automotive"),
    ("Pets", ["pets"], "pets"),
    ("Other", ["office", "books_media", "other"], "other"),
)

#: The page's badge labels (index.html TIER_LABEL); no badge = single source.
_TIER_LABEL = {"corroborated": "✓ 2+ sources", "history-checked": "✓ price history"}
#: Open deals drawn as static HTML on the home page and on each hub (open
#: access puts every deal in the teaser: hundreds of cards would make the
#: page heavy before its script draws the live wall).
_STATIC_WALL_MAX = 48
_HUB_MAX = 60

# Cloudflare Pages only (GitHub Pages serves it as a plain file): the
# pages.dev copies of the site are never indexed, nor is the data file.
_HEADERS = """https://:project.pages.dev/*
  X-Robots-Tag: noindex

https://:version.:project.pages.dev/*
  X-Robots-Tag: noindex

/deals.json
  X-Robots-Tag: noindex
"""

#: meta key: {url: [content hash, iso time]} as last listed for IndexNow.
_INDEXNOW_META = "indexnow_sent"
#: The due-list, written next to the site folder (never inside it).
INDEXNOW_FILE = "indexnow_urls.json"
INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"
_KEY_RE = re.compile(r"[A-Za-z0-9-]{8,128}")


def in_group(deal: dict[str, Any], group: Any) -> bool:
    """The page's tab filter: USED by condition, else by category."""
    if group is None:
        return True
    if group == "USED":
        return bool({"used", "open_box"} & set(deal.get("conditions") or []))
    return (deal.get("category") or "other") in group


def hub_href(slug: str) -> str:
    return f"/deals/{slug}/" if slug else "/"


def _pct(v: Any) -> int:
    """A percent as the page shows it (JavaScript's Math.round)."""
    return math.floor(float(v) + 0.5)


def _http(url: Any) -> str:
    """A link target for static HTML: http(s) only."""
    url = str(url or "")
    return url if url.startswith(("https://", "http://")) else ""


def _store_hue(store: str) -> int:
    h = 0
    for ch in store:
        h = (h * 31 + ord(ch)) % 360
    return h


def card_html(d: dict[str, Any]) -> str:
    """One free or dead deal as the wall draws it (index.html cardHtml), with
    the store tile in place of the photo: item, store, price, was-price, %
    off, badge, product link and the "via Source" credit links. Never called
    with a locked stub."""
    e = lambda v: escape(str(v if v is not None else ""))
    dead = d.get("status") == "dead"
    url = _http(d.get("url"))
    store = str(d.get("store") or "")
    conds = d.get("conditions") or []
    media = (f'<div class="ph" style="background:hsl({_store_hue(store)},42%,62%)" '
             f'aria-hidden="true"><span>{e((store or "deal")[:18])}</span></div>')
    if d.get("pct_at_checkout") is not None:
        media += f'<span class="flag">-{_pct(d["pct_at_checkout"])}%</span>'
    tag = "OPEN BOX" if "open_box" in conds else ("USED" if "used" in conds else "")
    if tag:
        media += f'<span class="condtag">{tag}</span>'
    if dead:
        media += '<span class="gonetag">GONE</span>'
    price = ""
    if d.get("final_price") is not None:
        price = f'<span class="now">${float(d["final_price"]):.2f}</span>'
    if d.get("regular_price") is not None:
        price += f' <s>${float(d["regular_price"]):.2f}</s>'
    if not dead and d.get("abs_savings") is not None:
        price += f' <span class="save">save ${float(d["abs_savings"]):.2f}</span>'
    title = (f'<a href="{e(url)}" rel="sponsored noopener">{e(d.get("item"))}</a>'
             if url else e(d.get("item")))
    tier = d.get("badge") or "unverified"
    meta = f'<span class="chip">{e(store)}</span>'
    if tier in _TIER_LABEL:
        meta += f'<span class="badge tier-{e(tier)}">{_TIER_LABEL[tier]}</span>'
    for c in [c for c in conds if c not in ("used", "open_box")][:2]:
        meta += f'<span class="chip">{e(c).replace("_", " ")}</span>'
    credits = ", ".join(
        f'via <a href="{e(_http(c.get("url")))}" rel="noopener">{e(c.get("name"))}</a>'
        if _http(c.get("url")) else f"via {e(c.get('name'))}"
        for c in d.get("credits") or [])
    how = ""
    if d.get("needs_steps"):  # url is the source post that explains the steps
        steps = " &rarr; ".join(e(x) for x in d.get("how") or [])
        codes = "".join(f'<code class="code">{e(c)}</code>' for c in (d.get("codes") or [])[:3])
        how = (f'<div class="how"><b>How to get this price:</b> '
               f'{steps or ("" if codes else "the steps are in the deal post")}'
               f'{(("<br>" if steps else "") + "Code " + codes) if codes else ""}</div>')
    actions = ((f'<a class="getdeal" href="{e(url)}" rel="sponsored noopener">Get deal</a>'
                if url and not dead else "") + f'<span class="credit">{credits}</span>')
    return (f'<article class="card{" gone" if dead else ""}">'
            f'<div class="media">{media}</div><div class="body">'
            f'<div class="pricerow">{price or "&nbsp;"}</div>'
            f'<h3 class="title">{title}</h3>'
            + (f'<div class="headline">{e(d["headline"])}</div>' if d.get("headline") else "")
            + how + f'<div class="metarow">{meta}</div><div class="actions">{actions}</div>'
            "</div></article>")


def _locked_line(stubs: list[dict[str, Any]], where: str) -> str:
    """'12 more deals at Walmart, 52–81% off, members only': a count and a %
    range, the only facts of a locked deal any page states."""
    pcts = [_pct(s["pct_at_checkout"]) for s in stubs if s.get("pct_at_checkout") is not None]
    rng = ""
    if pcts:
        lo, hi = min(pcts), max(pcts)
        rng = f", {lo}% off" if lo == hi else f", {lo}–{hi}% off"
    n = len(stubs)
    return f"{n} more deal{'' if n == 1 else 's'} {where}{rng}, members only"


def static_wall(teaser: list[dict[str, Any]], open_access: bool = False) -> str:
    """index.html's #content as plain HTML at publish time (the page's
    script replaces it with the live wall): the free deals (the top
    _STATIC_WALL_MAX), one line per store for the locked ones, and the dead
    deals deals.json carries."""
    free = [d for d in teaser if d.get("status") == "alive" and not d.get("locked")]
    more = max(0, len(free) - _STATIC_WALL_MAX)
    free = free[:_STATIC_WALL_MAX]
    stubs = [d for d in teaser if d.get("locked")]
    dead = sorted((d for d in teaser if d.get("status") != "alive" and not d.get("locked")),
                  key=lambda d: str(d.get("died_at") or ""), reverse=True)
    html = ('<noscript><p class="meta">JavaScript is off: these are the '
            + ("top" if open_access else "free") + " deals as of "
            "the last update. Turn it on for live drops, search and sorting, or browse the "
            "categories above.</p></noscript>")
    if not (free or stubs or dead):
        return html + '<p class="empty">No qualifying deals right now</p>'
    html += '<h2 class="secth">Verified deals right now</h2>'
    if free:
        html += '<div class="grid">' + "".join(card_html(d) for d in free) + "</div>"
    if more:
        n = f"{more // 100 * 100:,}+" if more >= 100 else str(more)  # never the exact count
        html += (f'<p class="meta">{n} more deal{"" if more == 1 else "s"} on the live '
                 "wall and in the categories above.</p>")
    if stubs:
        by_store: dict[str, list[dict[str, Any]]] = {}
        for s in stubs:
            by_store.setdefault(str(s.get("store") or ""), []).append(s)
        rows = sorted(by_store.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))
        html += ('<h2 class="secth">Members-only deals</h2><ul class="lockedlist">'
                 + "".join(f"<li>{escape(_locked_line(v, f'at {k}' if k else 'at other stores'))}</li>"
                           for k, v in rows) + "</ul>")
    if dead:
        html += ('<h2 class="secth missed">Just missed</h2><div class="grid">'
                 + "".join(card_html(d) for d in dead) + "</div>")
    return html


def tabs_html() -> str:
    """The category tabs as real links (the page's renderTabs draws the same)."""
    return "".join(
        f'<a class="cattab{" active" if i == 0 else ""}" href="{hub_href(slug)}" '
        f'data-i="{i}">{escape(label)}</a>' for i, (label, _g, slug) in enumerate(CATGROUPS))


def _hub_links() -> str:
    return '<ul class="hubs">' + "".join(
        f'<li><a href="{hub_href(slug)}">{escape(label if slug else "All deals")}</a></li>'
        for label, _g, slug in CATGROUPS) + "</ul>"


def _fill(template: str, **tokens: str) -> str:
    """Replace {{NAME}} tokens in one pass (filled-in text is never rescanned)."""
    return re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: tokens.get(m.group(1), m.group(0)), template)


def _site_floor(cfg) -> float:
    return min(cfg.min_pct, getattr(cfg, "site_show_from_pct", cfg.min_pct))


def week_stats(db, cfg, now: datetime) -> dict[str, dict[str, Any]]:
    """Per hub: the deals first seen in the last 7 days that clear every
    publish gate at the site floor (alive or dead since), their median % off
    and how many 2+ sources confirmed. Our own data, stable from one drop to
    the next: the hubs' thin-page floor reads the count."""
    from .. import rank  # lazy, as in site.py

    since, floor = now - timedelta(days=7), _site_floor(cfg)
    week = []
    for d in db.all_deals():
        seen = parse_iso(d.get("first_seen_at") or "")
        if seen is not None and seen >= since \
                and rank.is_publishable(dict(d, status="alive"), cfg, min_pct=floor)[0]:
            week.append(d)
    out = {}
    for _label, group, slug in CATGROUPS[1:]:
        rows = [d for d in week if in_group(d, group)]
        pcts = [float(d["pct_at_checkout"]) for d in rows]
        out[slug] = {"count": len(rows),
                     "median_pct": _pct(statistics.median(pcts)) if pcts else None,
                     "corroborated": sum(d.get("verify_tier") == "corroborated" for d in rows)}
    return out


def _hub_copy(cfg) -> dict[str, dict[str, str]]:
    """templates/hubs.yaml with the configured floors filled in."""
    site_floor = _site_floor(cfg)
    used_floor = max(site_floor, getattr(cfg, "used_min_pct", site_floor))
    raw = yaml.safe_load((TEMPLATES_DIR / "hubs.yaml").read_text(encoding="utf-8")) or {}
    fill = lambda s: str(s).replace("{site_floor}", f"{site_floor:g}") \
        .replace("{used_floor}", f"{used_floor:g}")
    return {slug: {k: fill(v) for k, v in entry.items()} for slug, entry in raw.items()}


def _hub_page(template: str, copy: dict[str, str], label: str, slug: str,
              rows: list[dict[str, Any]], stats: dict[str, Any], indexable: bool,
              site_url: str, open_access: bool = False) -> str:
    """One hub: its copy, our 7-day numbers, the category's free deals (the
    top _HUB_MAX) and its locked count and % range."""
    free = [d for d in rows if not d.get("locked")]
    more = max(0, len(free) - _HUB_MAX)
    free = free[:_HUB_MAX]
    stubs = [d for d in rows if d.get("locked")]
    n = stats["count"]
    line = (f"<strong>Last 7 days:</strong> {n} new deal{'' if n == 1 else 's'} in this category "
            f"passed our checks, median {stats['median_pct']}% off, "
            f"{stats['corroborated']} confirmed by 2+ independent sources."
            if n else "No deal in this category passed our checks in the last 7 days.")
    if open_access:
        deals = "<h2>Deals right now</h2>" + (
            '<div class="grid">' + "".join(card_html(d) for d in free) + "</div>" if free else
            '<p class="muted">No deal in this category right now; new deals land at every '
            'drop on <a href="/">the live wall</a>.</p>')
    else:
        deals = "<h2>Free right now</h2>" + (
            '<div class="grid">' + "".join(card_html(d) for d in free) + "</div>" if free else
            '<p class="muted">No free deal in this category right now; the free deals change '
            'at every drop on <a href="/">the live wall</a>.</p>')
    if more:
        deals += (f'<p class="muted">{more} more in this category on '
                  '<a href="/">the live wall</a>.</p>')
    if stubs:
        deals += f'<p class="muted">{escape(_locked_line(stubs, f"in {label}"))}.</p>'
    return _fill(template, TITLE=escape(copy["title"]), DESCRIPTION=escape(copy["description"]),
                 ROBOTS="" if indexable else '<meta name="robots" content="noindex">\n',
                 SITE_URL=site_url, SLUG=slug, INTRO=escape(copy["intro"]),
                 STATS=f'<div class="box"><p>{line}</p></div>', DEALS=deals,
                 HUB_LINKS=_hub_links())


def _newest(rows: list[dict[str, Any]]) -> str:
    """The newest first sighting among a hub's deals (its sitemap lastmod):
    free and locked together, so the rotating free picks never move it."""
    times = [t for t in (parse_iso(d.get("first_seen_at") or "") for d in rows) if t]
    return iso(max(times)) if times else ""


def due_list_path(site_dir: str | Path) -> Path:
    return Path(site_dir).resolve().parent / INDEXNOW_FILE


def _listed_at(prev: Any) -> datetime | None:
    return parse_iso(str(prev[1])) if isinstance(prev, list) and len(prev) == 2 else None


def indexnow_due(db, cfg, now: datetime, pages: dict[str, str]) -> list[str]:
    """The URLs whose content (what a crawler sees) changed since they were
    last listed, once seo.indexnow_min_hours have passed since that listing;
    a new URL is due at once. One meta key holds each URL's listed hash and
    time. The listing is recorded before the POST (state.db is committed
    before the deploys), so an unchanged URL is listed again after
    seo.indexnow_resubmit_days: a failed POST is not lost for good."""
    try:
        sent = json.loads(db.meta_get(_INDEXNOW_META) or "{}")
    except ValueError:
        sent = {}
    sent = sent if isinstance(sent, dict) else {}
    wait = timedelta(hours=float(getattr(cfg, "seo_indexnow_min_hours", 6.0)))
    redo = timedelta(days=float(getattr(cfg, "seo_indexnow_resubmit_days", 7.0)))
    due, keep = [], {}
    for url, content in pages.items():
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        prev, last = sent.get(url), _listed_at(sent.get(url))
        if last is not None and (now - last < wait
                                 or (prev[0] == h and not (redo and now - last >= redo))):
            keep[url] = prev
        else:
            due.append(url)
            keep[url] = [h, iso(now)]
    # A URL out of the list for now (a hub dipping under its floor) keeps its
    # entry through its interval, so coming back does not make it new.
    for url, prev in sent.items():
        last = _listed_at(prev)
        if url not in keep and last is not None and now - last < wait:
            keep[url] = prev
    db.meta_set(_INDEXNOW_META, json.dumps(keep, sort_keys=True))
    return due


def publish(db, cfg, now: datetime, out: Path, teaser: list[dict[str, Any]],
            site_url: str, public: list[str]) -> None:
    """The search-engine side of a publish, into out: the static wall and
    tabs in index.html, 404.html, the hubs, _headers, the IndexNow key file,
    sitemap.xml and the IndexNow due-list (beside out, never in it)."""
    from .paywall import is_open

    open_access = is_open(cfg)
    wall = static_wall(teaser, open_access)
    index = (out / "index.html").read_text(encoding="utf-8")
    groups = json.dumps([list(g) for g in CATGROUPS]).replace("<", "\\u003c")
    (out / "index.html").write_text(
        _fill(index, CATGROUPS=groups, CAT_TABS=tabs_html(), STATIC_WALL=wall), encoding="utf-8")
    (out / "404.html").write_text(_fill((TEMPLATES_DIR / "404.html").read_text(encoding="utf-8"),
                                        HUB_LINKS=_hub_links(),
                                        DROP_MINUTES=str(int(cfg.drop_minutes))), encoding="utf-8")
    (out / "_headers").write_text(_HEADERS, encoding="utf-8")

    stats, copy = week_stats(db, cfg, now), _hub_copy(cfg)
    template = _fill((TEMPLATES_DIR / "hub.html").read_text(encoding="utf-8"),
                     DROP_MINUTES=str(int(cfg.drop_minutes)))
    floor = int(getattr(cfg, "seo_hub_min_items", 10))
    listed: list[tuple[str, str, str]] = []  # (slug, lastmod, html) of indexable hubs
    for label, group, slug in CATGROUPS[1:]:
        rows = [d for d in teaser if d.get("status") == "alive" and in_group(d, group)]
        indexable = stats[slug]["count"] >= floor
        html = _hub_page(template, copy[slug], label, slug, rows, stats[slug], indexable,
                         site_url, open_access)
        (out / "deals" / slug).mkdir(parents=True, exist_ok=True)
        (out / "deals" / slug / "index.html").write_text(html, encoding="utf-8")
        if indexable:
            listed.append((slug, _newest(rows), html))
    logger.info("seo: %d of %d category hubs indexable", len(listed), len(CATGROUPS) - 1)

    key = getattr(cfg, "seo_indexnow_key", "") or ""
    if key and not _KEY_RE.fullmatch(key):
        logger.warning("seo.indexnow_key is not 8-128 letters, digits or dashes: IndexNow off")
        key = ""
    if key:
        (out / f"{key}.txt").write_text(key, encoding="utf-8")
    if not site_url:
        return
    # Fixed pages carry no lastmod; a hub's is its newest deal's first
    # sighting, so two publishes of the same data write the same sitemap.
    urls = "".join(f"  <url><loc>{escape(site_url)}/{escape(p)}</loc></url>\n" for p in public)
    urls += "".join(f"  <url><loc>{escape(site_url)}/deals/{slug}/</loc>"
                    + (f"<lastmod>{lastmod}</lastmod>" if lastmod else "") + "</url>\n"
                    for slug, lastmod, _html in listed)
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}</urlset>\n", encoding="utf-8")
    if key:
        pages = {f"{site_url}/{p}": wall if p == "" else (out / p).read_text(encoding="utf-8")
                 for p in public}
        pages.update({f"{site_url}/deals/{slug}/": html for slug, _lastmod, html in listed})
        due = indexnow_due(db, cfg, now, pages)
        due_list_path(out).write_text(json.dumps(due), encoding="utf-8")
        logger.info("indexnow: %d of %d URL(s) due", len(due), len(pages))


def submit_indexnow(cfg, http_client: Any = None) -> int:
    """POST the due-list the last publish wrote to IndexNow (Bing, Yahoo,
    DuckDuckGo, Yandex share it). 0 when accepted or nothing is due; logs
    counts and the HTTP status only."""
    host, key = getattr(cfg, "custom_domain", ""), getattr(cfg, "seo_indexnow_key", "")
    try:
        urls = json.loads(due_list_path(cfg.site_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        urls = []
    if not (host and key and isinstance(urls, list) and urls):
        logger.info("indexnow: nothing to submit")
        return 0
    body = {"host": host, "key": key, "keyLocation": f"https://{host}/{key}.txt",
            "urlList": urls}
    client = http_client or httpx.Client(timeout=20)
    try:
        resp = client.post(INDEXNOW_ENDPOINT, content=json.dumps(body).encode("utf-8"),
                           headers={"Content-Type": "application/json; charset=utf-8",
                                    "User-Agent": cfg.resolved_user_agent()})
    except httpx.HTTPError as e:
        logger.warning("indexnow: %d URL(s) not submitted (%s)", len(urls), type(e).__name__)
        return 1
    finally:
        if http_client is None:
            client.close()
    logger.info("indexnow: %d URL(s) submitted, HTTP %d", len(urls), resp.status_code)
    return 0 if resp.status_code in (200, 202) else 1
