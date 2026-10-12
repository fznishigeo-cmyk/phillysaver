"""Search engines: the public wall, category hubs, deal landing pages,
Best deals today, the 404 page, Cloudflare's _headers, sitemap and IndexNow.

Live deal facts come from the teaser render_site already built
(docs/paywall.md): free deals in full, locked deals only as a count and a %
range, never a name, price, link or photo. Static HTML carries no images at
all. Ended pages retain previously public facts until the row is deleted.
Hub stats only read; IndexNow and the stable deal paths each keep a meta key.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import shutil
import statistics
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import yaml

from ..util import iso, parse_iso
from ..rank import pick_order

logger = logging.getLogger("dealradar.publish.seo")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

#: The wall's category tabs, one hub each (Trending is the home page):
#: (label, categories or "USED", hub slug). index.html's tabs and the hubs
#: both read this one list, so they cannot drift apart.
CATGROUPS: tuple[tuple[str, Any, str], ...] = (
    ("Trending", None, ""),  # the home wall: top picks (operator, 2026-10-12)
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
  Cache-Control: no-store

/
  Cache-Control: no-cache

/*.html
  Cache-Control: no-cache

/deal/*
  Cache-Control: no-cache

/deals/*
  Cache-Control: no-cache

/stores/*
  Cache-Control: no-cache

/best-deals-today/*
  Cache-Control: no-cache

/join
  Cache-Control: no-cache
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


def card_html(d: dict[str, Any], deal_paths: dict[str, str] | None = None,
              now: datetime | None = None) -> str:
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
        died = parse_iso(d.get("died_at") or "")
        hours = max(0, ((now or datetime.now(timezone.utc)) - died).total_seconds() / 3600) if died else 0
        ago = "gone just now" if hours < 1 else f"gone {math.floor(hours + .5)}h ago"
        media += f'<span class="gonetag">Just missed · {ago}</span>'
    price = ""
    if d.get("final_price") is not None:
        price = f'<span class="now">${float(d["final_price"]):.2f}</span>'
    if d.get("regular_price") is not None:
        price += f' <s>${float(d["regular_price"]):.2f}</s>'
    if not dead and d.get("abs_savings") is not None:
        price += f' <span class="save">save ${float(d["abs_savings"]):.2f}</span>'
    page = (deal_paths or {}).get(str(d.get("key") or "")[:8])
    title = (f'<a href="{e(page)}">{e(d.get("item"))}</a>' if page and not dead else
             f'<a href="{e(url)}" rel="sponsored noopener">{e(d.get("item"))}</a>'
             if url and not dead else e(d.get("item")))
    tier = d.get("badge") or "unverified"
    meta = f'<span class="chip">{e(store)}</span>'
    if tier in _TIER_LABEL:
        meta += f'<span class="badge tier-{e(tier)}">{_TIER_LABEL[tier]}</span>'
    for c in [c for c in conds if c not in ("used", "open_box")][:2]:
        meta += f'<span class="chip">{e(c).replace("_", " ")}</span>'
    credits = ", ".join(
        f'via <a href="{e(_http(c.get("url")))}" rel="noopener">{e(c.get("name"))}</a>'
        if not dead and _http(c.get("url")) else f"via {e(c.get('name'))}"
        for c in d.get("credits") or [])
    how = ""
    if not dead and d.get("needs_steps"):  # url is the source post that explains the steps
        steps = " &rarr; ".join(e(x) for x in d.get("how") or [])
        codes = "".join(f'<code class="code">{e(c)}</code>' for c in (d.get("codes") or [])[:3])
        how = (f'<div class="how"><b>How to get this price:</b> '
               f'{steps or ("" if codes else "the steps are in the deal post")}'
               f'{(("<br>" if steps else "") + "Code " + codes) if codes else ""}</div>')
    actions = ((f'<a class="getdeal" href="{e(url)}" rel="sponsored noopener">Get deal</a>'
                if url and not dead else "") + f'<span class="credit">{credits}</span>')
    disabled = ' aria-disabled="true"' if dead else ""
    return (f'<article class="card{" gone" if dead else ""}"{disabled}>'
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


def place_gone(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Defer gone cards until slot 9, then leave seven cards between them.

    Excess gone cards wait off the wall if there are not enough live cards.
    """
    from collections import deque

    out, pending = [], deque()
    next_gone = 8
    for d in rows:
        if d.get("status") == "dead":
            pending.append(d)
        if pending and len(out) >= next_gone:
            out.append(pending.popleft())
            next_gone = len(out) + 7
        if d.get("status") != "dead":
            out.append(d)
    if pending and len(out) >= next_gone:
        out.append(pending.popleft())
    return out


def static_wall(teaser: list[dict[str, Any]], open_access: bool = False,
                deal_paths: dict[str, str] | None = None, now: datetime | None = None,
                front_page: dict | None = None) -> str:
    """Public cards inline, with the JS spacing rules and a static card cap."""
    free = [d for d in teaser if not d.get("locked")]
    if (front_page or {}).get("top_picks"):
        free = place_gone(pick_order(free, front_page))
    else:
        free = pick_order(place_gone(free), front_page or {})
    more = max(0, len(free) - _STATIC_WALL_MAX)
    free = free[:_STATIC_WALL_MAX]
    stubs = [d for d in teaser if d.get("locked")]
    html = ('<noscript><p class="meta">JavaScript is off: these are the '
            + ("top" if open_access else "free") + " deals as of "
            "the last update. Turn it on for live drops, search and sorting, or browse the "
            "categories above.</p></noscript>")
    if not (free or stubs):
        return html + '<p class="empty">No qualifying deals right now</p>'
    html += '<h2 class="secth sr-only">Verified deals right now</h2>'
    if free:
        html += '<div class="grid">' + "".join(card_html(d, deal_paths, now) for d in free) + "</div>"
    if more:
        n = ("1,000+" if more >= 1000 else f"{more // 100 * 100:,}+"  # never the exact count
             if more >= 100 else str(more))
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


def store_slug(name: str) -> str:
    """ASCII store words; apostrophes join letters (Macy's becomes macys)."""
    name = re.sub(r"\.com$", "", name.strip(), flags=re.I)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", name.replace("'", "")).strip("-")


def _store_pages(cfg, live: list[dict[str, Any]]) -> dict[str, tuple[str, list[dict[str, Any]]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    names: dict[str, str] = {}
    for d in live:
        name = re.sub(r"\.com$", "", str(d.get("store") or "").strip(), flags=re.I)
        # One page per slug: "Macy's", "Macys" and "macys.com" are one store
        # (keyed by name, two spellings would collide on one path).
        key = store_slug(name)
        if not key:
            continue
        groups.setdefault(key, []).append(d)
        names.setdefault(key, name)
    floor = int(getattr(cfg, "seo_store_page_min_deals", 15))
    cap = int(getattr(cfg, "seo_store_pages_max", 12))
    keys = sorted((k for k in groups if len(groups[k]) >= floor),
                  key=lambda k: (-len(groups[k]), k))[:cap]
    pages = {}
    for k in keys:
        pages[f"/stores/{k}/"] = names[k], groups[k]
    return pages


def _price_pages(cfg, live: list[dict[str, Any]]) -> dict[str, tuple[str, list[dict[str, Any]]]]:
    pages = {}
    hubs = {slug for _label, _group, slug in CATGROUPS}
    for n in sorted(set(getattr(cfg, "seo_price_pages", [10, 25, 50]))):
        slug = f"under-{n}"
        assert slug not in hubs, f"Price page collides with category hub: {slug}"
        rows = [d for d in live if d.get("final_price") is not None and d["final_price"] <= n]
        if len(rows) >= 10:
            pages[f"/deals/{slug}/"] = f"Deals under ${n}", rows
    return pages


def _link_row(label: str, links: list[tuple[str, str]], current: str = "") -> str:
    links = [(path, name) for path, name in links if path != current]
    if not links:
        return ""
    return f'<p><strong>{label}</strong>: ' + " · ".join(
        f'<a href="{escape(path)}">{escape(name)}</a>' for path, name in links) + '</p>'


def _fill(template: str, **tokens: str) -> str:
    """Replace {{NAME}} tokens in one pass (filled-in text is never rescanned)."""
    return re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: tokens.get(m.group(1), m.group(0)), template)


def _json_ld(data: dict[str, Any]) -> str:
    return '<script type="application/ld+json">' + json.dumps(data).replace('<', '\\u003c') + '</script>'


def _breadcrumbs(site_url: str, pages: list[tuple[str, str]]) -> str:
    return _json_ld({"@context": "https://schema.org", "@type": "BreadcrumbList",
                     "itemListElement": [
                         {"@type": "ListItem", "position": i, "name": name, "item": site_url + path}
                         for i, (name, path) in enumerate([("Home", "/")] + pages, 1)]})


def _item_list(rows: list[dict[str, Any]], paths: dict[str, str], site_url: str) -> str:
    urls = [site_url + paths[d["key"][:8]] for d in rows if d["key"][:8] in paths][:30]
    if not urls:
        return ""  # an empty ItemList is a Search Console warning
    return _json_ld({"@context": "https://schema.org", "@type": "ItemList",
                     "itemListElement": [{"@type": "ListItem", "position": i, "url": url}
                                         for i, url in enumerate(urls, 1)]})


def _image_preconnects(teaser: list[dict[str, Any]], site_url: str, front_page: dict) -> str:
    rows = [d for d in teaser if not d.get("locked")]
    rows = (place_gone(pick_order(rows, front_page)) if front_page.get("top_picks")
            else pick_order(place_gone(rows), front_page))[:24]
    hosts: Counter[str] = Counter()
    for d in rows:
        try:
            image = urlsplit(d.get("image_url") or "")
            if image.scheme in ("http", "https") and image.hostname and image.hostname != "m.media-amazon.com":
                hosts[image.hostname] += 1
        except ValueError:
            continue
    links = '<link rel="preconnect" href="https://m.media-amazon.com">'
    if hosts:
        host = hosts.most_common(1)[0][0]
        own = (urlsplit(site_url).hostname or "").removeprefix("www.")
        if host.removeprefix("www.") != own:
            links += f'\n<link rel="preconnect" href="{escape("https://" + host)}">'
    return links


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
              site_url: str, open_access: bool = False,
              deal_paths: dict[str, str] | None = None) -> str:
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
            '<div class="grid">' + "".join(card_html(d, deal_paths) for d in free) + "</div>" if free else
            '<p class="muted">No deal in this category right now; new deals land at every '
            'drop on <a href="/">the live wall</a>.</p>')
    else:
        deals = "<h2>Free right now</h2>" + (
            '<div class="grid">' + "".join(card_html(d, deal_paths) for d in free) + "</div>" if free else
            '<p class="muted">No free deal in this category right now; the free deals change '
            'at every drop on <a href="/">the live wall</a>.</p>')
    if more:
        deals += (f'<p class="muted">{more} more in this category on '
                  '<a href="/">the live wall</a>.</p>')
    if stubs:
        deals += f'<p class="muted">{escape(_locked_line(stubs, f"in {label}"))}.</p>'
    return _fill(template, TITLE=escape(copy["title"]), DESCRIPTION=escape(copy["description"]),
                 PAGE_TITLE=escape(copy["title"] + " — wowpriced"),
                 CANONICAL=escape(f"{site_url}/deals/{slug}/"),
                 EXTRA_HEAD=_breadcrumbs(site_url, [(copy["title"], hub_href(slug))])
                 + _item_list(free, deal_paths or {}, site_url), TABS="",
                 ROBOTS="" if indexable else '<meta name="robots" content="noindex">\n',
                 SITE_URL=site_url, SLUG=slug, INTRO=escape(copy["intro"]),
                 STATS=f'<div class="box"><p>{line}</p></div>', DEALS=deals,
                 HUB_LINKS=_hub_links())


def deal_path(item: str, id8: str) -> str:
    """ASCII words, cut at a word within 60 characters; identity is the hash."""
    ascii_item = unicodedata.normalize("NFKD", item).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_item).strip("-")
    if len(slug) > 60:
        slug = slug[:61].rsplit("-", 1)[0] if "-" in slug[:61] else ""
    return f"/deal/{slug or 'deal'}-{id8}/"


def _date(dt: datetime, full: bool = False) -> str:
    dt = dt.astimezone(timezone.utc)
    return f"{dt.strftime('%B' if full else '%b')} {dt.day}, {dt.year}"


def _landing_page(template: str, title: str, heading: str, description: str,
                  path: str, site_url: str, content: str, *, dead: bool = False,
                  extra_head: str = "", intro: str = "",
                  breadcrumbs: list[tuple[str, str]] | None = None) -> str:
    return _fill(template, TITLE=escape(heading), PAGE_TITLE=escape(title),
                 DESCRIPTION=escape(description), CANONICAL=escape(site_url + path),
                 ROBOTS='<meta name="robots" content="noindex">\n' if dead else "",
                 EXTRA_HEAD=extra_head + _breadcrumbs(site_url, breadcrumbs or [(heading, path)]),
                 INTRO=escape(intro), STATS="", DEALS=content,
                 TABS='<nav class="hrow" aria-label="categories">' + tabs_html() + '</nav>',
                 HUB_LINKS=_hub_links())


def _basis_label(d: dict[str, Any]) -> str:
    """What the regular price is, from who reported it: never a bare "list
    price" the deal did not claim (Keepa's basis is its 90-day average)."""
    first = str(((d.get("credits") or [{}])[0] or {}).get("name") or "")
    used = bool({"used", "open_box"} & set(d.get("conditions") or []))
    if first == "Amazon price drop":
        return "new price on Amazon" if used else "90-day average price on Amazon"
    if used:
        return f"new-condition price, as stated by {first}" if first else "new-condition price"
    return f"as stated by {first}" if first else "as stated by the source"


def _related(d: dict[str, Any], live: list[dict[str, Any]], group: Any) -> list[dict[str, Any]]:
    """Up to 6 live deals from the same hub, the ones that follow this deal in
    key order: stable while the wall re-ranks, so a page only changes when a
    neighbour comes or goes (fewer re-uploads and IndexNow resubmissions)."""
    ring = sorted((r for r in live if r.get("key") != d.get("key") and in_group(r, group)),
                  key=lambda r: str(r.get("key")))
    after = [r for r in ring if str(r.get("key")) > str(d.get("key"))]
    return (after + [r for r in ring if str(r.get("key")) < str(d.get("key"))])[:6]


def _deal_page(template: str, d: dict[str, Any], path: str, site_url: str,
               disclosure: str, live: list[dict[str, Any]], paths: dict[str, str]) -> str:
    dead = d.get("status") == "dead"
    item, store = str(d.get("item") or ""), str(d.get("store") or "")
    pct = _pct(d["pct_at_checkout"]) if d.get("pct_at_checkout") is not None else None
    suffix = f" — {pct}% off at {store} | wowpriced" if pct is not None else f" at {store} | wowpriced"
    title = item[:max(0, 70 - len(suffix))].rstrip() + suffix
    # Use the category's hub; Used & Open-Box is a condition hub, not a category.
    label, group, slug = next((g for g in CATGROUPS[2:] if in_group(d, g[1])), CATGROUPS[-1])
    hub = f'<a href="{hub_href(slug)}">{escape(label)} deals</a>'
    content = '<p class="ended"><strong>This deal has ended</strong></p>' if dead else ""
    content += (f'<div class="ph" style="background:hsl({_store_hue(store)},42%,62%)" '
                f'aria-hidden="true"><span>{escape((store or "deal")[:18])}</span></div>'
                f'<p>Store: {escape(store)}</p>')
    facts = [f"at {store}"]
    if d.get("final_price") is not None:
        price = f'{float(d["final_price"]):.2f}'
        content += f'<p>Sale price: <strong>${price}</strong></p>'
        facts.insert(0, f"${price}")
    if d.get("regular_price") is not None:
        regular = f'{float(d["regular_price"]):.2f}'
        basis = _basis_label(d)
        content += f'<p>Regular price: ${regular} ({escape(basis)})</p>'
        facts.append(f"regular price ${regular} ({basis})")
    if pct is not None:
        content += f'<p>{pct}% off'
        content += (f' · save ${float(d["abs_savings"]):.2f}'
                    if d.get("abs_savings") is not None else "") + '</p>'
    conds = [str(c).replace("_", " ") for c in d.get("conditions") or []]
    if conds:
        content += '<p>Condition: ' + escape(", ".join(conds)) + '</p>'
    badge = d.get("badge") or "unverified"
    explanation = {"history-checked": "The price was checked against price history.",
                   "corroborated": "Two or more independent sources confirmed this price."}
    content += (f'<p>{_TIER_LABEL.get(badge, "single source")}: '
                f'{explanation.get(badge, "One source reported this price; it is not yet independently confirmed.")}</p>')
    if d.get("needs_steps"):
        content += '<div class="how"><h2>How to get this price</h2>'
        content += '<ol>' + ''.join(f'<li>{escape(str(s))}</li>' for s in d.get("how") or []) + '</ol>'
        content += ''.join(f'<p>Code: <code>{escape(str(c))}</code></p>' for c in d.get("codes") or [])
        if not (d.get("how") or d.get("codes")):
            content += '<p>The steps are in the deal post.</p>'
        content += '</div>'
    seen = parse_iso(d.get("first_seen_at") or "")
    if seen:
        content += f'<p>First seen: <time datetime="{iso(seen)}">{_date(seen)}</time> (UTC)</p>'
    credits = ', '.join(
        f'<a href="{escape(_http(c.get("url")))}" rel="noopener">{escape(str(c.get("name") or ""))}</a>'
        if _http(c.get("url")) else escape(str(c.get("name") or ""))
        for c in d.get("credits") or [])
    content += f'<p>Sources: {credits}</p>'
    url = _http(d.get("url"))
    if not dead and url:
        content += (f'<a class="getdeal" href="{escape(url)}" rel="sponsored noopener">Get deal</a>'
                    f'<p class="disclosure">{escape(disclosure)}</p>')
    content += f'<p>Browse {hub}.</p>'
    related = [] if dead else _related(d, live, group)
    if related:
        content += '<h2>Related live deals</h2><div class="grid">' + ''.join(
            card_html(r, paths) for r in related) + '</div>'
    ld = ""
    if not dead and d.get("final_price") is not None:
        conditions = set(d.get("conditions") or [])
        condition = "UsedCondition" if {"used", "open_box"} & conditions else \
            "RefurbishedCondition" if "refurbished" in conditions else "NewCondition"
        product: dict[str, Any] = {"@context": "https://schema.org", "@type": "Product", "name": item,
                                  "offers": {"@type": "Offer", "price": price,
                                             "priceCurrency": "USD", "availability": "https://schema.org/InStock",
                                             "url": site_url + path,
                                             "seller": {"@type": "Organization", "name": store},
                                             "itemCondition": "https://schema.org/" + condition}}
        if str(d.get("image_url") or "").startswith("https://"):
            product["image"] = d["image_url"]
        if d.get("brand"):
            product["brand"] = {"@type": "Brand", "name": d["brand"]}
        ld = _json_ld(product)
    return _landing_page(template, title, item, '; '.join(facts) + '.', path, site_url,
                         '<div class="deal-detail">' + content + '</div>', dead=dead, extra_head=ld,
                         breadcrumbs=[(label, hub_href(slug)), (item, path)])


def _deal_pages(db, cfg, now: datetime, out: Path, teaser: list[dict[str, Any]],
                source_deals: list[dict[str, Any]]) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, str]]:
    """Remember paths even through locked rotations; retain ended rows until deletion."""
    from .site import _serialize_deal

    try:
        previous = json.loads(db.meta_get("seo_deal_paths") or "{}")
    except ValueError:
        previous = {}
    previous = previous if isinstance(previous, dict) else {}
    # State, not the output directory: deployments start with a fresh site folder.
    raw = {hashlib.sha256(str(d["dedupe_key"]).encode()).hexdigest()[:8]: d
           for d in db.all_deals() + source_deals}
    remembered = {k: p for k, p in previous.items() if k in raw and isinstance(p, str)
                  and re.fullmatch(r"/deal/[a-z0-9-]+-" + re.escape(k) + r"/", p)}
    public = {str(d.get("key") or "")[:8]: d for d in teaser if not d.get("locked")}
    # source_deals is in rank order; teaser rotation may move free picks forward.
    live = [public[k] for k in raw if k in public and public[k].get("status") == "alive"]
    order = {hashlib.sha256(str(d["dedupe_key"]).encode()).hexdigest()[:8]: i
             for i, d in enumerate(source_deals)}
    live.sort(key=lambda d: order.get(d["key"][:8], len(order)))
    cap = int(getattr(cfg, "seo_deal_pages_max", 3000))
    rows = live[:cap]
    logger.info("seo: %d live deal pages; %d left out by seo.deal_pages_max", len(rows), max(0, len(live) - cap))
    for k, d in raw.items():
        if d.get("status") == "dead" and (k in remembered or k in public):
            if k not in public:
                d = dict(d, sightings=db.sightings_for(d["id"]))
            rows.append(public.get(k) or _serialize_deal(d, now))
    paths, lastmods = {}, {}
    for d in rows:
        k = d["key"][:8]
        paths[k] = remembered.setdefault(k, deal_path(str(d.get("item") or ""), k))
        lastmods[paths[k]] = raw[k].get("price_since") or d.get("first_seen_at") or ""
    db.meta_set("seo_deal_paths", json.dumps(remembered, sort_keys=True))
    wanted = {p.split('/')[2] for p in paths.values()}
    for folder in (out / "deal").glob("*"):
        if folder.name not in wanted:
            if folder.is_dir():
                shutil.rmtree(folder)
            else:
                folder.unlink()
    return paths, rows, lastmods


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
            site_url: str, public: list[str], meta: dict[str, Any],
            source_deals: list[dict[str, Any]]) -> None:
    """The search-engine side of a publish, into out: the static wall and
    tabs in index.html, 404.html, the hubs, _headers, the IndexNow key file,
    sitemap.xml and the IndexNow due-list (beside out, never in it)."""
    from .paywall import is_open

    open_access = is_open(cfg)
    paths, deal_rows, lastmods = _deal_pages(db, cfg, now, out, teaser, source_deals)
    wall = static_wall(teaser, open_access, paths, now, cfg.front_page)
    index = (out / "index.html").read_text(encoding="utf-8")
    groups = json.dumps([list(g) for g in CATGROUPS]).replace("<", "\\u003c")
    (out / "index.html").write_text(
        _fill(index, CATGROUPS=groups, CAT_TABS=tabs_html(), STATIC_WALL=wall,
              IMAGE_PRECONNECTS=_image_preconnects(teaser, site_url, cfg.front_page),
              TICKER_HIDDEN="" if sum(d.get("status") != "dead" for d in teaser) >= 3 else "hidden"), encoding="utf-8")
    (out / "404.html").write_text(_fill((TEMPLATES_DIR / "404.html").read_text(encoding="utf-8"),
                                        HUB_LINKS=_hub_links(),
                                        DROP_MINUTES=str(int(cfg.drop_minutes))), encoding="utf-8")
    (out / "_headers").write_text(_HEADERS, encoding="utf-8")

    stats, copy = week_stats(db, cfg, now), _hub_copy(cfg)
    template = _fill((TEMPLATES_DIR / "hub.html").read_text(encoding="utf-8"),
                     DROP_MINUTES=str(int(cfg.drop_minutes)))
    from .site import affiliate_copy, copy_variant, signup_footer
    template = copy_variant(template, open_access, bool(getattr(cfg, "support_enabled", False)))
    template = affiliate_copy(template, getattr(cfg, "affiliate_amazon_tag", ""))
    if getattr(cfg, "support_enabled", False):
        template = signup_footer(template)
    live = [d for d in teaser if d.get("status") == "alive" and not d.get("locked")]
    order = {d["dedupe_key"]: i for i, d in enumerate(source_deals)}
    live.sort(key=lambda d: order[d["dedupe_key"]])
    stores, prices = _store_pages(cfg, live), _price_pages(cfg, live)
    store_links = [(path, name) for path, (name, _rows) in stores.items()]
    price_links = [(path, name) for path, (name, _rows) in prices.items()]
    browse_template = _fill(template, PRICE_LINKS=_link_row("By price", price_links))
    template = _fill(browse_template, STORE_LINKS=_link_row("Stores", store_links))
    landing_pages = {}
    for d in deal_rows:
        path = paths[d["key"][:8]]
        html = _deal_page(template, d, path, site_url, meta["disclosure"], live, paths)
        folder = out / path.strip("/")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(html, encoding="utf-8")
        if d.get("status") == "alive":
            landing_pages[path] = html
    best_path = "/best-deals-today/"
    best_title = f"Best deals today ({_date(now, full=True)}) | wowpriced"
    n = sum(d.get("status") == "alive" for d in teaser)
    # Counts top out at "1,000+", like the home header (operator, 2026-10-12).
    count = "1,000+" if n >= 1000 else f"{n // 100 * 100:,}+" if n >= 100 else str(n)
    intro = f"The {min(30, len(live))} best of {count} live deals right now, ranked like the live wall."
    best_rows = pick_order(live, cfg.front_page)[:30]
    best_html = _landing_page(template, best_title, best_title.removesuffix(" | wowpriced"),
                              intro, best_path, site_url,
                              '<h2 class="sr-only">Best live deals</h2><div class="grid">' + ''.join(card_html(d, paths) for d in best_rows) + '</div>',
                              intro=intro, extra_head=_item_list(best_rows, paths, site_url))
    (out / "best-deals-today").mkdir(parents=True, exist_ok=True)
    (out / "best-deals-today" / "index.html").write_text(best_html, encoding="utf-8")
    landing_pages[best_path] = best_html
    browse_pages = stores | prices
    for path, (name, rows) in browse_pages.items():
        heading = f"{name} deals today" if path in stores else f"{name} today"
        title = f"{heading} ({_date(now, full=True)}) | wowpriced"
        count = ("1,000+" if len(rows) >= 1000 else f"{len(rows) // 100 * 100:,}+"
                 if len(rows) >= 100 else str(len(rows)))
        pcts = [float(d["pct_at_checkout"]) for d in rows if d.get("pct_at_checkout") is not None]
        intro = f"{count} live deals right now"
        if path in stores and pcts:
            intro += f", median {_pct(statistics.median(pcts))}% off"
        intro += "."
        page_template = _fill(browse_template, STORE_LINKS=_link_row("Stores", store_links, path))
        html = _landing_page(page_template, title, heading, intro, path, site_url,
                             '<h2 class="sr-only">Deals right now</h2><div class="grid">' + ''.join(card_html(d, paths) for d in rows[:_HUB_MAX])
                             + '</div>', intro=intro, extra_head=_item_list(rows[:_HUB_MAX], paths, site_url))
        folder = out / path.strip("/")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(html, encoding="utf-8")
        landing_pages[path] = html
        lastmods[path] = _newest(rows)
    # Re-used output folders must lose pages that fall below a floor or cap.
    for folder in list((out / "stores").glob("*")) + list((out / "deals").glob("under-*")):
        if f"/{folder.relative_to(out).as_posix()}/" not in browse_pages:
            if folder.is_dir():
                shutil.rmtree(folder)
            else:
                folder.unlink()
    floor = int(getattr(cfg, "seo_hub_min_items", 10))
    listed: list[tuple[str, str, str]] = []  # (slug, lastmod, html) of indexable hubs
    for label, group, slug in CATGROUPS[1:]:
        rows = [d for d in teaser if d.get("status") == "alive" and in_group(d, group)]
        indexable = stats[slug]["count"] >= floor
        html = _hub_page(template, copy[slug], label, slug, rows, stats[slug], indexable,
                         site_url, open_access, paths)
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
    assert len(public) + len(listed) + len(landing_pages) <= 50_000
    for path in landing_pages:
        lastmod = lastmods.get(path, "")
        urls += (f"  <url><loc>{escape(site_url + path)}</loc>"
                 + (f"<lastmod>{escape(lastmod)}</lastmod>" if lastmod else "") + "</url>\n")
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}</urlset>\n", encoding="utf-8")
    if key:
        pages = {f"{site_url}/{p}": wall if p == "" else (out / p).read_text(encoding="utf-8")
                 for p in public}
        pages.update({f"{site_url}/deals/{slug}/": html for slug, _lastmod, html in listed})
        pages.update({site_url + path: html for path, html in landing_pages.items()})
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
