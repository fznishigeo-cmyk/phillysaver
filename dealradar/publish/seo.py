"""Search engines: the public wall, category hubs, deal landing pages,
Best deals today, the 404 page, Cloudflare's _headers, sitemap and IndexNow.

Live deal facts come from the teaser render_site already built
(docs/paywall.md): free deals in full, locked deals only as a count and a %
range, never a name, price, link or photo. Permanent product pages reuse
allowed product photos. Ended deal pages retain previously public facts until the row is deleted.
Hub stats only read; stable deal paths and fact fingerprints keep meta keys; IndexNow
receipts live in their own tables (publish/indexnow.py).
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
from zoneinfo import ZoneInfo
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from ..savings import display_pct
from ..verify import is_amazon_deal
from ..util import iso, parse_iso
from ..rank import pick_order
from .. import checkout_prices
from .price_history import chart_html as price_history_chart, STYLE as PRICE_HISTORY_STYLE
from . import amazon_history, checker
from .. import amazon_reference
from . import amazon_as_of, event_pages, inventory, manifest_refresh, product_pages, seo_audit, traffic
from . import add_to_cart, short_cards
from .indexnow import INDEXNOW_FILE, INDEXNOW_ENDPOINT, KEY_RE as _KEY_RE, due_list_path, indexnow_due

logger = logging.getLogger("dealradar.publish.seo")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

#: The wall's category tabs, one hub each (Trending is the home page):
#: (label, categories or "USED", hub slug). index.html's tabs and the hubs
#: both read this one list, so they cannot drift apart.
CATGROUPS: tuple[tuple[str, Any, str], ...] = (
    ("Trending", None, ""),  # the home wall: top picks (operator, 2026-10-12)
    # Every live deal, newest first, endless scroll (operator, 2026-10-12:
    # "a button ... that has everything"). No hub of its own: it is the home.
    ("All deals", None, ""),
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
#: The tabs with a hub page of their own (Trending and All deals are the home).
HUB_GROUPS = tuple(g for g in CATGROUPS if g[2])

#: The page's badge labels (index.html TIER_LABEL); no badge = single source.
_TIER_LABEL = {"corroborated": "✓ 2+ sources", "history-checked": "✓ price history", "verified": "✓ verified"}
#: Open deals drawn as static HTML on the home page and on each hub (open
#: access puts every deal in the teaser: hundreds of cards would make the
#: page heavy before its script draws the live wall).
_STATIC_WALL_MAX = 48
_HUB_MAX = 60

STATIC_PHOTO_STYLE = """
.media{height:150px;background:#fff;display:flex;align-items:center;justify-content:center}
.media img{width:88%;height:88%;object-fit:contain}
.deal-detail>.detail-photo{height:320px;margin-bottom:16px;border:1px solid var(--line);border-radius:14px;overflow:hidden}
.detail-photo img{width:100%;height:100%}
.deal-detail .related-grid{grid-template-columns:repeat(auto-fill,minmax(240px,1fr))}
.related-grid .title,.related-grid .headline{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
@media (max-width:520px){.media{height:118px}.deal-detail .related-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (prefers-color-scheme:dark){.media{background:var(--photo-tile,#d9dee4)}.media img{mix-blend-mode:multiply}}
"""

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


def in_group(deal: dict[str, Any], group: Any) -> bool:
    """The page's tab filter: USED by condition, else by category."""
    if group is None:
        return True
    if group == "USED":
        return bool({"used", "open_box"} & set(deal.get("conditions") or []))
    return (deal.get("category") or "other") in group


def hub_href(slug: str) -> str:
    return f"/deals/{slug}/" if slug else "/"


def _deal_hub(deal: dict[str, Any]) -> tuple[str, Any, str]:
    # Used & Open-Box is a condition hub, not a category.
    return next((g for g in HUB_GROUPS if g[1] != "USED" and in_group(deal, g[1])),
                CATGROUPS[-1])


def _pct(v: Any) -> int:
    """A percent as the page shows it (JavaScript's Math.round)."""
    return display_pct(v)


def _http(url: Any) -> str:
    """A link target for static HTML: http(s) only."""
    url = str(url or "")
    return url if url.startswith(("https://", "http://")) else ""


def _store_hue(store: str) -> int:
    h = 0
    for ch in store:
        h = (h * 31 + ord(ch)) % 360
    return h


def spotlight_time(ends_at: str, now: datetime) -> str:
    end = parse_iso(ends_at)
    if end is None or end <= now:
        return ""
    minutes = math.ceil((end - now).total_seconds() / 60)
    return f"ends in {minutes // 60}h {minutes % 60}m"


def _outbound_attrs(d: dict[str, Any]) -> str:
    """Explicit, bounded labels; never infer analytics from link destinations."""
    store = re.sub(r"[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]", "", str(d.get("store") or ""))[:60]
    category = str(d.get("category") or "other")
    if not re.fullmatch(r"[a-z0-9_]{1,40}", category):
        category = "other"
    requirement = ' data-step-offer' if (d.get("checkout") or {}).get("summary") else ''
    if re.fullmatch(r"[0-9a-f]{16}", str(d.get("_click_key") or "")):
        requirement += f' data-click-key="{d["_click_key"]}"'
    return f' data-deal-outbound data-store="{escape(store)}" data-category="{escape(category)}"' + requirement


def card_html(d: dict[str, Any], deal_paths: dict[str, str] | None = None,
              now: datetime | None = None, *, photo: bool = False,
              static_photos: bool = False, credit_links: bool = True, fresh_config=None,
              client_how: bool = False, short: bool = False) -> str:
    """One free or dead wall card; never a locked stub. static_photos draws
    HTTPS photos, with photo reserving eager loading. With the switch off,
    photo retains the home page's original first-four image markup. client_how
    draws the How box as the page script does (keep_first_cards); short, the
    shorter card (short_cards)."""
    e = lambda v: escape(str(v if v is not None else ""))
    dead = d.get("status") == "dead"
    spotlight = d.get("spotlight") or {}
    price_only = bool(spotlight) and d.get("pct_at_checkout") is None
    url = _http(d.get("url"))
    store = str(d.get("store") or "")
    conds = d.get("conditions") or []
    media = (f'<div class="ph" style="background:hsl({_store_hue(store)},42%,62%)" '
             f'aria-hidden="true"><span>{e((store or "deal")[:18])}</span></div>')
    if static_photos and str(d.get("image_url") or "").startswith("https://"):
        loading = 'fetchpriority="high" loading="eager"' if photo else 'loading="lazy" decoding="async"'
        media = (f'<img {loading} width="400" height="400" referrerpolicy="no-referrer" '
                 f'src="{e(d["image_url"])}" alt="{e(d.get("item"))}" onerror="this.style.display=\'none\';'
                 'this.nextElementSibling.style.display=\'flex\'">'
                 f'<div class="ph" style="background:hsl({_store_hue(store)},42%,62%);display:none" '
                 f'aria-hidden="true"><span>{e((store or "deal")[:18])}</span></div>')
    elif static_photos:
        pass  # Missing or non-HTTPS photos keep the store tile.
    elif spotlight and d.get("image_url"):
        media = (f'<img src="{e(d["image_url"])}" alt="{e(d.get("item"))}" '
                 'referrerpolicy="no-referrer" fetchpriority="high" loading="eager">')
    elif photo and d.get("image_url"):
        media = ('<img fetchpriority="high" loading="eager" referrerpolicy="no-referrer" '
                 f'src="{e(d["image_url"])}" alt="{e(d.get("item"))}" onerror="this.style.display=\'none\';'
                 'this.nextElementSibling.style.display=\'flex\'">'
                 f'<div class="ph" style="background:hsl({_store_hue(store)},42%,62%);display:none" '
                 f'aria-hidden="true"><span>{e((store or "deal")[:18])}</span></div>')
    if spotlight:
        media += f'<span class="today-ribbon">Today only · {e(spotlight["source"])}</span>'
    if d.get("pct_at_checkout") is not None:
        media += f'<span class="flag">-{_pct(d["pct_at_checkout"])}%</span>'
    elif d.get("price_drop"):
        media += '<span class="flag">Price drop</span>'
    tag = "OPEN BOX" if "open_box" in conds else ("USED" if "used" in conds else "")
    from . import open_box
    scarcity = open_box.label(d, now or datetime.now(timezone.utc)) if "open_box" in conds else ""
    tag = scarcity or tag
    if tag:
        media += f'<span class="condtag{" scarcity" if scarcity else ""}">{tag}</span>'
    if dead:
        died = parse_iso(d.get("died_at") or "")
        hours = max(0, ((now or datetime.now(timezone.utc)) - died).total_seconds() / 3600) if died else 0
        ago = "gone just now" if hours < 1 else f"gone {math.floor(hours + .5)}h ago"
        media += f'<span class="gonetag">Just missed · {ago}</span>'
    price = ""
    if d.get("final_price") is not None:
        price = f'<span class="now">${float(d["final_price"]):.2f}</span>'
    if not price_only and d.get("regular_price") is not None:
        if d.get("regular_price_basis") == "List price":
            price += ' List price'
        price += f' <s>${float(d["regular_price"]):.2f}</s>'
    if not price_only and not dead and d.get("abs_savings") is not None:
        price += f' <span class="save">save ${float(d["abs_savings"]):.2f}</span>'
    page = (deal_paths or {}).get(str(d.get("key") or "")[:8])
    title = (f'<a href="{e(page)}">{e(d.get("item"))}</a>' if page and not dead else
             f'<a href="{e(url)}" target="_blank" rel="sponsored noopener"{_outbound_attrs(d)}>{e(d.get("item"))}</a>'
             if url and not dead else e(d.get("item")))
    tier = d.get("badge") or "unverified"
    meta = f'<span class="chip storechip">{e(store)}</span>'
    if d.get("select_sizes") and fresh_config is None:
        meta += '<span class="chip">select sizes</span>'
    # Right after the store, so the one-line phone row never clips it.
    checked = parse_iso(d.get("checked_at") or "")
    if checked is not None and not dead and checked <= (now or datetime.now(timezone.utc)):
        minutes = int(((now or datetime.now(timezone.utc)) - checked).total_seconds() / 60)
        age = f"{minutes}m" if minutes < 60 else f"{minutes // 60}h" if minutes < 1440 else f"{minutes // 1440}d"
        as_of = f'Price as of {d["amazon_as_of"]}. {amazon_as_of.DISCLAIMER}' if d.get("amazon_as_of") else ""
        title_attr = f' title="{e(as_of)}"' if as_of else ""
        hidden = f'<span class="sr-only"> ({e(as_of)}) </span>' if as_of else ""
        meta += (f'<span class="chip price-checked" data-checked-at="{e(iso(checked))}"{title_attr}>'
                 f'<span class="pcl">price checked </span>{hidden}{age} ago</span>')
    if d.get("select_sizes") and fresh_config is not None:
        meta += '<span class="chip">select sizes</span>'
    countdown = spotlight_time(spotlight.get("ends_at", ""), now or datetime.now(timezone.utc))
    if countdown:
        meta += (f'<span class="chip spotlight-time" data-ends-at="{e(spotlight["ends_at"])}">'
                 f'{countdown}</span>')
    if tier in _TIER_LABEL:
        meta += f'<span class="badge tier-{e(tier)}">{_TIER_LABEL[tier]}</span>'
    if fresh_config is not None:
        from .drop_fresh import hot_html
        meta += hot_html(d, fresh_config)
    for c in [c for c in conds if c not in ("used", "open_box")][:2]:
        label = ("price drops in cart" if fresh_config is not None and fresh_config.card_tidy and c == "in_cart"
                 else e(c).replace("_", " "))
        meta += f'<span class="chip">{label}</span>'
    if fresh_config is not None and not spotlight and not (checked and checked <= now):
        hours = d.get("age_hours")
        if hours is not None:
            age = ("just in" if hours < 1 else f"{math.floor(hours + .5)}h ago" if hours < 24
                   else f"{math.floor(hours / 24 + .5)}d ago")
            meta += f'<span class="chip">{age}</span>'
    credits = ", ".join(
        f'via <a href="{e(_http(c.get("url")))}" rel="noopener">{e(c.get("name"))}</a>'
        if credit_links and _http(c.get("url")) else f"via {e(c.get('name'))}"
        for c in d.get("credits") or [])
    how = ""
    checkout = d.get("checkout") or {}
    lines = short_cards.lines_html(d, client_how) if short else ""
    if client_how:
        from .keep_first_cards import how_html
        from .phone_first_screen import steps_html
        how = (short_cards.how_html(d, lines) if short else how_html(d, fresh_config)) + steps_html(d, page, fresh_config)
    elif short:
        how = short_cards.how_html(d, lines)
    elif not dead and (checkout.get("summary") or d.get("needs_steps")):
        steps = " &rarr; ".join(e(x) for x in d.get("how") or [])
        codes = "".join(f'<code class="code">{e(c)}</code>' for c in d.get("codes") or [])
        how = (f'<details class="how" data-requirements><summary><b>How to get this price:</b> '
               f'{e(checkout.get("summary"))}</summary><p>{e(checkout.get("now"))} '
               f'{e(checkout.get("shipping"))}</p>'
               f'{steps or ("" if codes or checkout.get("summary") else "the steps are in the deal post")}'
               f'{(("<br>" if steps else "") + "Code " + codes) if codes else ""}</details>')
    actions = ((f'<a class="getdeal" href="{e(url)}" target="_blank" rel="sponsored noopener"{_outbound_attrs(d)}>Get deal</a>'
                if url and not dead else "") + f'<span class="credit">{credits}</span>')
    actions += product_pages.history_link(d.get("product_path"))
    return (f'<article class="card{" gone" if dead else ""}{" spotlight" if spotlight else ""}"'
            + (' role="region" aria-label="Today only deal"' if spotlight else "") + '>'
            f'<div class="media">{media}</div><div class="body">'
            f'<div class="pricerow">{price or "&nbsp;"}</div>'
            + ('<div class="price-unconfirmed">Price not confirmed</div>' if d.get("price_unconfirmed") else "")
            + (f'<div class="price-aged">{e(d["price_aged"])}</div>' if d.get("price_aged") else "")
            + f'<h3 class="title">{title}</h3>'
            + (f'<div class="headline">{e(d["headline"])}</div>' if d.get("headline") and not short else "")
            + checkout_prices.html(d) + lines + how + f'<div class="metarow">{meta}</div><div class="actions">{actions}</div>'
            "</div></article>")


def cards_html(rows: list[dict[str, Any]], deal_paths: dict[str, str] | None = None,
               now: datetime | None = None, *, static_photos: bool = False,
               eager_photos: int = 4, credit_links: bool = True, fresh_config=None,
               client_how: bool = False, short: bool = False) -> str:
    """A static grid: reserve eager loading for the page's first four photos."""
    if not credit_links:
        rows = place_gone(rows, every=10)
    eligible = [d for d in rows if str(d.get("image_url") or "").startswith("https://")]
    eager = {id(d) for d in eligible[:eager_photos]}
    return "".join(card_html(d, deal_paths, now, static_photos=static_photos,
                             photo=static_photos and id(d) in eager,
                             credit_links=credit_links, fresh_config=fresh_config,
                             client_how=client_how, short=short) for d in rows)


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


def place_gone(rows: list[dict[str, Any]], *, every: int = 8) -> list[dict[str, Any]]:
    """Defer gone cards with a sliding-window cap; the legacy interval is eight.

    Excess gone cards wait off the wall if there are not enough live cards.
    """
    from collections import deque

    out, pending = [], deque()
    next_gone = max(8, every - 1)
    for d in rows:
        if d.get("status") == "dead":
            pending.append(d)
        if pending and len(out) >= next_gone:
            out.append(pending.popleft())
            next_gone = len(out) + every - 1
        if d.get("status") != "dead":
            out.append(d)
    if pending and len(out) >= next_gone:
        out.append(pending.popleft())
    return out


def home_order(rows: list[dict[str, Any]], front_page: dict, *,
               click_price: bool = False, gone_every: int = 8) -> list[dict[str, Any]]:
    """Home only: enforce allure and gone limits on the final visible cards.

    click_price: the visible wall's Add-to-Cart order (unknown offers last),
    applied before allure spacing, as the browser does."""
    from ..appeal import allure_spacing, is_allure, space_allure
    from ..click_price import HOME_FIRST, home_rows

    options = front_page.get("appeal") or {}
    if (options.get("want") or {}).get("enabled"):
        ordered = pick_order(rows, front_page)
        ordered = place_gone(space_allure(home_rows(ordered) if click_price else ordered, options))
        first, every = allure_spacing(options)
        out, last_allure, next_gone = [], -every, 8
        for row in ordered:
            if click_price and row.get("click_price_unknown") and len(out) < HOME_FIRST:
                continue
            allure = row.get("allure") or is_allure(row, options)
            dead = row.get("status") == "dead"
            if allure and (len(out) < first or len(out) - last_allure < every):
                continue
            if dead and len(out) < next_gone:
                continue
            if allure:
                last_allure = len(out)
            if dead:
                next_gone = len(out) + 8
            out.append(row)
        from .drop_rotation import clothing_order
        ordered = clothing_order(out, front_page)
        return place_gone(ordered, every=gone_every) if gone_every != 8 else ordered
    ordered = (place_gone(pick_order(rows, front_page)) if front_page.get("top_picks")
               else pick_order(place_gone(rows), front_page))
    ordered = home_rows(ordered) if click_price else ordered
    return place_gone(ordered, every=gone_every) if gone_every != 8 else ordered


def static_wall(teaser: list[dict[str, Any]], open_access: bool = False,
                deal_paths: dict[str, str] | None = None, now: datetime | None = None,
                front_page: dict | None = None, spotlight: list[dict[str, Any]] | None = None,
                *, static_photos: bool = False, credit_links: bool = True, same_product_spacing: bool = False, fresh_config=None,
                fresh_mark: int | None = None, short: bool = False) -> str:
    """Public cards inline, with the JS spacing rules and a static card cap."""
    spotlight = spotlight or []
    fresh_rows = teaser + spotlight if fresh_config is not None else []
    if (front_page or {}).get("used_tab_only"):
        teaser = [d for d in teaser if not in_group(d, "USED")]
        spotlight = [d for d in spotlight if not in_group(d, "USED")]
    spotlight_keys = {d["key"] for d in spotlight}
    free = [d for d in teaser if not d.get("locked") and d.get("key") not in spotlight_keys]
    free = home_order(free, front_page or {}, click_price=True, gone_every=8 if credit_links else 10)
    appeal_options = (front_page or {}).get("appeal") or {}
    if (appeal_options.get("want") or {}).get("enabled"):
        spotlight = [d for d in spotlight if not d.get("allure")]
    if same_product_spacing:
        from .same_product_spacing import rules, space_rows
        policy = rules(front_page or {}, 8 if credit_links else 10)
        spotlight = space_rows(spotlight, rules=policy)
        free = space_rows(free, spotlight, policy)
    if fresh_config is not None:
        from .drop_fresh import rotation
        from ..front_mix import page
        from .same_product_spacing import rules
        pool = rotation(fresh_rows, front_page or {})
        mark = fresh_mark if fresh_mark is not None else int(now.timestamp()) // (fresh_config.drop_minutes * 60)
        free = page(pool, mark, front_page or {}, rules(front_page or {}))
        spotlight = []
    more = max(0, len(free) - _STATIC_WALL_MAX) if fresh_config is None else 0
    if fresh_config is None:
        free = free[:_STATIC_WALL_MAX]
    stubs = [d for d in teaser if d.get("locked") and not d.get("click_price_unknown")
             and d.get("key") not in spotlight_keys]
    html = ('<noscript><p class="meta">JavaScript is off: these are the '
            + ("top" if open_access else "free") + " deals as of "
            "the last update. Turn it on for live drops, search and sorting, or browse the "
            "categories above.</p></noscript>")
    if not (free or stubs or spotlight):
        return html + '<p class="empty">Verified offers appear here after passing our price checks.</p>'
    html += '<h2 class="secth sr-only">Verified deals right now</h2>'
    if spotlight:
        html += '<div class="grid spotlight-grid">' + cards_html(
            spotlight, deal_paths, now, static_photos=static_photos, credit_links=credit_links, short=short) + '</div>'
    if free:
        from . import keep_first_cards
        client_how = fresh_config is not None and keep_first_cards.enabled(fresh_config)
        # The first four photos match the page script's eager, high-priority ones.
        if static_photos:
            eager = max(0, 4 - sum(str(d.get("image_url") or "").startswith("https://") for d in spotlight))
            cards = cards_html(free, deal_paths, now, static_photos=True, eager_photos=eager,
                               credit_links=credit_links, fresh_config=fresh_config, client_how=client_how, short=short)
        else:
            photos = {id(d) for d in [d for d in free if d.get("image_url")][:4]}
            cards = "".join(card_html(d, deal_paths, now, photo=id(d) in photos,
                                      credit_links=credit_links, fresh_config=fresh_config,
                                      client_how=client_how, short=short) for d in free)
        html += '<div class="grid">' + cards + "</div>"
    if more:
        n = ("1,000+" if more >= 1000 else f"{more // 100 * 100:,}+"  # never the exact count
             if more >= 100 else str(more))
        html += (f'<p class="meta">{n} more deal{"" if more == 1 else "s"} on the live '
                 "wall and in the categories above.</p>")
    if fresh_config is not None:
        total = sum(d.get("status") != "dead" for d in teaser)
        size = (front_page or {}).get("fresh_page_size", 60)
        drops = (len(pool) + size - 1) // size
        if getattr(fresh_config, 'fresh_more_plain', False) is True:
            from .fresh_more_plain import count_label
            label, cycle = count_label(total), ''
        else:
            label = f'{total:,}+ deals'
            cycle = (f'<p class="meta">Full rotation: {drops} drops '
                     f'(about {drops * fresh_config.drop_minutes} minutes)</p>')
        html += (f'<div class="more fresh-more"><button type="button" id="fresh-all" class="pwbtn">'
                 f'See all {label}</button>{cycle}</div>')
        stubs = []
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


def _hub_links(event_links: list | None = None) -> str:
    return '<ul class="hubs">' + "".join(
        f'<li><a href="{hub_href(slug)}">{escape(label if slug else "All deals")}</a></li>'
        for label, _g, slug in CATGROUPS[1:]) + "".join(
        f'<li><a href="{escape(path)}">{escape(name)}</a></li>' for path, name in event_links or []) + "</ul>"


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
    confirmed = {k: sum(not d.get("price_unconfirmed") for d in rows) for k, rows in groups.items()}
    keys = sorted((k for k in groups if confirmed[k] >= floor),
                  key=lambda k: (-confirmed[k], k))[:cap]
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
        if sum(not d.get("price_unconfirmed") for d in rows) >= max(1, int(cfg.seo_price_page_min_deals)):
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
    rows = [d for d in teaser if not d.get("locked")
            and not (front_page.get("used_tab_only") and in_group(d, "USED"))]
    rows = home_order(rows, front_page)[:24]
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


def week_stats(db, cfg, now: datetime, *, offsite_protected_keys=None) -> dict[str, dict[str, Any]]:
    """Per hub: the deals first seen in the last 7 days that clear every
    publish gate at the site floor (alive or dead since), their median % off
    and how many 2+ sources confirmed. Historical context only; current
    visible offers determine indexability."""
    from .. import rank  # lazy, as in site.py
    from .site import _serialize_deal

    since, floor = now - timedelta(days=7), _site_floor(cfg)
    week = []
    for d in db.all_deals():
        seen = parse_iso(d.get("first_seen_at") or "")
        if seen is not None and seen >= since \
                and rank.is_publishable(dict(d, status="alive"), cfg, min_pct=floor)[0]:
            week.append(d)
            if offsite_protected_keys is not None:
                offsite_protected_keys.add(d["dedupe_key"])
    out = {}
    for _label, group, slug in HUB_GROUPS:
        rows = [d for d in week if in_group(d, group)]
        displayed = [_serialize_deal(d, now, cfg=cfg) for d in rows]
        pcts = [float(d["pct_at_checkout"]) for d in displayed if d.get("pct_at_checkout") is not None]
        out[slug] = {"count": len(rows),
                     "median_pct": _pct(statistics.median(pcts)) if pcts else None,
                     "corroborated": sum(d.get("verify_tier") == "corroborated" for d in rows)}
    return out


def _hub_copy(cfg) -> dict[str, dict[str, str]]:
    """Category copy; backend qualification floors stay private in list-price mode."""
    site_floor = _site_floor(cfg)
    used_floor = max(site_floor, getattr(cfg, "used_min_pct", site_floor))
    raw = yaml.safe_load((TEMPLATES_DIR / "hubs.yaml").read_text(encoding="utf-8")) or {}
    def fill(s):
        text = str(s)
        if amazon_reference.enabled(cfg):
            text = re.sub(r"\s+at\s+(?:least\s+)?\{(?:site|used)_floor\}% off(?: or more)?", "", text)
        return text.replace("{site_floor}", f"{site_floor:g}").replace("{used_floor}", f"{used_floor:g}")

    return {slug: {k: amazon_reference.template(fill(v), cfg) for k, v in entry.items()}
            for slug, entry in raw.items()}


def _hub_page(template: str, copy: dict[str, str], label: str, slug: str,
              rows: list[dict[str, Any]], stats: dict[str, Any], indexable: bool,
              site_url: str, open_access: bool = False,
              deal_paths: dict[str, str] | None = None, now: datetime | None = None,
              *, page_number: int = 1, page_count: int = 1, paginate: bool = False,
              product_links: str = "", static_photos: bool = False, credit_links: bool = True,
              short: bool = False) -> str:
    """One hub: its copy, our 7-day numbers, the category's free deals (the
    top _HUB_MAX) and its locked count and % range."""
    free = [d for d in rows if not d.get("locked")]
    more = max(0, len(free) - _HUB_MAX) if not paginate else 0
    free = free[(page_number - 1) * _HUB_MAX:page_number * _HUB_MAX]
    stubs = [d for d in rows if d.get("locked")] if page_number == 1 else []
    n = stats["count"]
    line = (f"<strong>Last 7 days:</strong> {n} new deal{'' if n == 1 else 's'} in this category "
            f"passed our checks, "
            + (f"median {stats['median_pct']}% off, " if stats['median_pct'] is not None else "") +
            f"{stats['corroborated']} confirmed by 2+ independent sources."
            if n else "We publish offers in this category only when they pass our price checks.")
    if open_access:
        deals = "<h2>Deals right now</h2>" + (
            '<div class="grid">' + cards_html(free, deal_paths, now, static_photos=static_photos, credit_links=credit_links,
                                              short=short) + "</div>" if free else
            '<p class="muted">This category features offers that pass our price checks. '
            'Explore <a href="/">all live deals</a>.</p>')
    else:
        deals = "<h2>Free right now</h2>" + (
            '<div class="grid">' + cards_html(free, deal_paths, now, static_photos=static_photos, credit_links=credit_links,
                                              short=short) + "</div>" if free else
            '<p class="muted">Browse the current free deals on <a href="/">the deal wall</a>. '
            'They rotate at every drop.</p>')
    if more:
        deals += (f'<p class="muted">{more} more in this category on '
                  '<a href="/">the live wall</a>.</p>')
    if stubs:
        deals += f'<p class="muted">{escape(_locked_line(stubs, f"in {label}"))}.</p>'
    path = inventory.page_path(hub_href(slug), page_number)
    title = copy["title"] + (f" — Page {page_number}" if page_number > 1 else "")
    breadcrumbs = [(copy["title"], hub_href(slug))]
    if page_number > 1:
        breadcrumbs.append((f"Page {page_number}", path))
    if paginate:
        deals += inventory.pagination(hub_href(slug), page_number, page_count, "category")
    deals += product_links
    return _fill(template, TITLE=escape(title), DESCRIPTION=escape(copy["description"]),
                 PAGE_TITLE=escape(title + " — wowpriced"),
                 CANONICAL=escape(site_url + path),
                 EXTRA_HEAD=_breadcrumbs(site_url, breadcrumbs)
                 + _item_list(free, deal_paths or {}, site_url), TABS="",
                 ROBOTS="" if indexable else '<meta name="robots" content="noindex,follow">\n',
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
                  indexable: bool = True, noindex_follow: bool = False,
                  extra_head: str = "", intro: str = "",
                  breadcrumbs: list[tuple[str, str]] | None = None) -> str:
    return _fill(template, TITLE=escape(heading), PAGE_TITLE=escape(title),
                 DESCRIPTION=escape(description), CANONICAL=escape(site_url + path),
                 ROBOTS=('<meta name="robots" content="noindex,follow">\n' if noindex_follow else
                         '<meta name="robots" content="noindex">\n' if dead else
                         '<meta name="robots" content="noindex,follow">\n' if not indexable else ""),
                 EXTRA_HEAD=extra_head + _breadcrumbs(site_url, breadcrumbs or [(heading, path)]),
                 INTRO=escape(intro), STATS="", DEALS=content,
                 TABS='<nav class="hrow" aria-label="categories">' + tabs_html() + '</nav>',
                 HUB_LINKS=_hub_links())


def _best_summary(rows: list[dict[str, Any]]) -> str:
    """Which categories today's picks come from, linked to their hubs: the
    page's own words, so it is more than a copy of the home wall (Google
    left it "Crawled - currently not indexed", 2026-10-16)."""
    counts: dict[tuple[str, str], int] = {}
    for d in rows:
        label, _group, slug = next((g for g in HUB_GROUPS if g[1] != "USED" and in_group(d, g[1])),
                                   CATGROUPS[-1])
        counts[(label, slug)] = counts.get((label, slug), 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
    if len(top) < 2:
        return ""
    parts = [f'<a href="{hub_href(slug)}">{escape(label)}</a> ({n})' for (label, slug), n in top]
    return f'<p class="best-summary">Top categories today: {", ".join(parts[:-1])} and {parts[-1]}.</p>'


def _listing_pages(template, heading, intro, root, site_url, rows, paths, now,
                   floor, paginate, family, *, static_photos=False, credit_links=True, short=False):
    count = max(1, math.ceil(len(rows) / _HUB_MAX)) if paginate else 1
    for number in range(1, count + 1):
        path = inventory.page_path(root, number)
        shown = rows[(number - 1) * _HUB_MAX:number * _HUB_MAX]
        page_heading = heading + (f" — Page {number}" if number > 1 else "")
        content = ('<h2 class="sr-only">Deals right now</h2><div class="grid">'
                   + cards_html(shown, paths, now, static_photos=static_photos, credit_links=credit_links,
                                short=short) + '</div>')
        if paginate:
            content += inventory.pagination(root, number, count, family)
        breadcrumbs = [(heading, root), (f"Page {number}", path)] if number > 1 else None
        yield path, _landing_page(template, f"{page_heading} | wowpriced", page_heading, intro,
            path, site_url, content, intro=intro, extra_head=_item_list(shown, paths, site_url),
            breadcrumbs=breadcrumbs, indexable=sum(not d.get("price_unconfirmed") for d in shown) >= floor)


def _write_page(out: Path, path: str, html: str) -> None:
    folder = out / path.strip("/")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text(html, encoding="utf-8")


def _basis_label(d: dict[str, Any]) -> str:
    """What the regular price is, from who reported it: never a bare "list
    price" the deal did not claim (Keepa's basis is its 90-day average)."""
    if d.get("regular_price_basis"):
        return str(d["regular_price_basis"])
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
    return sorted(after + [r for r in ring if str(r.get("key")) < str(d.get("key"))],
                  key=lambda r: bool(r.get("price_unconfirmed")))[:6]


def _deal_page(template: str, d: dict[str, Any], path: str, site_url: str,
               disclosure: str, live: list[dict[str, Any]], paths: dict[str, str],
               observations: list[dict[str, Any]], now: datetime, *, indexable: bool = True,
               noindex_follow: bool = False,
               discovery: str = "", sharing: str = "", share_head: str = "",
               also_at: str = "", lowest_seen: str = "", app_amazon: bool = False,
               static_photos: bool = False, credit_links: bool = True, cart: str = "",
               trusted: str = "", short: bool = False) -> str:
    from .site import linked_disclosure

    dead = d.get("status") == "dead"
    item, store = str(d.get("item") or ""), str(d.get("store") or "")
    pct = _pct(d["pct_at_checkout"]) if d.get("pct_at_checkout") is not None else None
    suffix = f" — {pct}% off at {store} | wowpriced" if pct is not None else f" at {store} | wowpriced"
    title = item[:max(0, 70 - len(suffix))].rstrip() + suffix
    label, group, slug = _deal_hub(d)
    hub = f'<a href="{hub_href(slug)}">{escape(label)} deals</a>'
    content = '<p class="ended"><strong>This deal has ended</strong></p>' if dead else ""
    hero = static_photos and str(d.get("image_url") or "").startswith("https://")
    if hero:
        content += (f'<div class="detail-photo media"><img src="{escape(d["image_url"])}" '
                    f'alt="{escape(item)}" width="400" height="400" loading="eager" fetchpriority="high" '
                    'referrerpolicy="no-referrer" onerror="this.style.display=\'none\';'
                    'this.nextElementSibling.style.display=\'flex\'">'
                    f'<div class="ph" style="background:hsl({_store_hue(store)},42%,62%);display:none" '
                    f'aria-hidden="true"><span>{escape((store or "deal")[:18])}</span></div></div>')
    content += f'<p class="detail-store">Store: {escape(store)}</p>'
    checked = parse_iso(d.get("checked_at") or "")
    if checked is not None:
        eastern = checked.astimezone(ZoneInfo("America/New_York"))
        content += (f'<p class="detail-checked">Price checked '
                    f'<time datetime="{iso(checked)}">{eastern.strftime("%b %d, %Y %I:%M %p")}</time> ET</p>')
    facts = [f"at {store}"]
    if d.get("final_price") is not None:
        price = f'{float(d["final_price"]):.2f}'
        content += f'<p class="detail-price">Sale price: <strong>${price}</strong> USD</p>'
        facts.insert(0, f"${price}")
    if checked is not None and checked <= now and d.get("amazon_as_of"):
        content += (f'<p class="detail-checked amazon-as-of">Amazon price as of {escape(d["amazon_as_of"])}</p>'
                    f'<details class="amazon-price-details"><summary>Details</summary>'
                    f'<p>{amazon_as_of.DISCLAIMER}</p></details>')
    if d.get("price_unconfirmed"):
        content += ('<div class="price-unconfirmed">Price not confirmed</div>'
                    '<p class="price-unconfirmed">We couldn\'t re-check this price recently; it may have changed.</p>')
    if d.get("price_aged"):
        content += f'<div class="price-aged">{escape(d["price_aged"])}</div>'
    if d.get("regular_price") is not None:
        regular = f'{float(d["regular_price"]):.2f}'
        basis = _basis_label(d)
        if basis == "List price":
            content += f'<p>List price ${regular}</p>'
            facts.append(f"List price ${regular}")
        else:
            content += f'<p>Regular price: ${regular} ({escape(basis)})</p>'
            facts.append(f"regular price ${regular} ({basis})")
    if pct is not None:
        content += f'<p>{pct}% off'
        content += (f' · save ${float(d["abs_savings"]):.2f}'
                    if d.get("abs_savings") is not None else "") + '</p>'
    elif d.get("price_drop"):
        content += '<p><span class="chip">Price drop</span></p>'
    chart = price_history_chart(observations, now, d.get("final_price"), ended=dead)
    content += chart
    conds = [str(c).replace("_", " ") for c in d.get("conditions") or []]
    if not {"used", "open_box", "refurbished"} & set(d.get("conditions") or []):
        conds.insert(0, "new")
    content += '<p>Condition: ' + escape(", ".join(conds)) + '</p>'
    from . import open_box
    if scarcity := open_box.label(d, now):
        content += f'<p class="detail-scarcity">{scarcity}</p>'
    if not dead and not d.get("price_unconfirmed"):
        content += '<p class="detail-availability">Availability: Live offer</p>'
    badge = d.get("badge") or "unverified"
    explanation = {"history-checked": "The price was checked against price history.",
                   "verified": "The price was verified.",
                   "corroborated": "Two or more independent sources confirmed this price.",
                   "trusted": escape(trusted)}  # site.trusted_line; "" falls back below
    verification_attrs = ' data-app-amazon-history' if app_amazon and badge == "history-checked" else ""
    content += (f'<p{verification_attrs}>{_TIER_LABEL.get(badge, "single source")}: '
                f'{explanation.get(badge) or "One source reported this price; it is not yet independently confirmed."}</p>')
    checkout = d.get("checkout") or {}
    checkout_text = (f'<p>{escape(checkout.get("now", ""))} '
                     f'{escape(checkout.get("shipping", ""))}</p>') if checkout else ""
    content += checkout_prices.html(d)
    if checkout.get("summary") or d.get("needs_steps") or d.get("codes"):
        content += '<div class="how"><h2>How to get this price</h2>'
        content += checkout_text
        if d.get("how"):
            content += '<ol>' + ''.join(f'<li>{escape(str(s))}</li>' for s in d.get("how") or []) + '</ol>'
        for c in d.get("codes") or []:
            code = escape(str(c))
            content += f'<p class="detail-code">Code: <code>{code}</code>'
            if not dead:
                content += (f'<button type="button" class="copy-code" data-copy-code="{code}" '
                            f'aria-label="Copy code {code}" hidden>Copy code</button>')
            content += '</p>'
        if d.get("codes") and not dead:
            content += '<p class="code-status" role="status" aria-live="polite"></p>'
        if not (d.get("how") or d.get("codes") or checkout.get("summary")):
            content += '<p>The steps are in the deal post.</p>'
        content += '</div>'
    else:
        content += checkout_text
    seen = parse_iso(d.get("first_seen_at") or "")
    if seen:
        content += f'<p>First seen: <time datetime="{iso(seen)}">{_date(seen)}</time> (UTC)</p>'
    credits = ', '.join(
        f'<a href="{escape(_http(c.get("url")))}" rel="noopener">{escape(str(c.get("name") or ""))}</a>'
        if _http(c.get("url")) else escape(str(c.get("name") or ""))
        for c in d.get("credits") or [])
    if credit_links:
        content += f'<p class="detail-sources">Sources: {credits}</p>'
    else:
        credits = ', '.join('via ' + escape(str(c.get("name") or "")) for c in d.get("credits") or [])
        content += f'<p class="detail-sources muted">{credits}</p>'
    url = _http(d.get("url"))
    if not dead and url:
        content += (f'<a class="getdeal" href="{escape(url)}" target="_blank" rel="sponsored noopener"{_outbound_attrs(d)}>Get deal</a>'
                    + cart + f'<p class="disclosure">{linked_disclosure(disclosure)}</p>')
    if not dead and not d.get("price_unconfirmed"):
        content += lowest_seen + also_at
    content += product_pages.history_link(d.get("product_path"))
    content += sharing + discovery + f'<p>Browse {hub}.</p>'
    related = [] if dead else _related(d, live, group)
    if static_photos:
        related = related[:4]
    if related:
        grid = 'grid related-grid' if static_photos else 'grid'
        content += f'<h2>Related live deals</h2><div class="{grid}">' + cards_html(
            related, paths, now, static_photos=static_photos, eager_photos=3 if hero else 4,
            credit_links=credit_links, short=short) + '</div>'
    ld = ""
    # Google requires a product image for merchant listings (Search Console,
    # 2026-10-14: "Missing field image" is critical): no photo, no Product
    # markup. Return policy, shipping, reviews and ratings are not ours to
    # state (we are not the seller and have no reviews), so they stay absent.
    if not dead and not d.get("price_unconfirmed") and d.get("final_price") is not None \
            and str(d.get("image_url") or "").startswith("https://"):
        conditions = set(d.get("conditions") or [])
        condition = "UsedCondition" if {"used", "open_box"} & conditions else \
            "RefurbishedCondition" if "refurbished" in conditions else "NewCondition"
        product: dict[str, Any] = {"@context": "https://schema.org", "@type": "Product",
                                  "name": seo_audit.word_limit(item, seo_audit.NAME_LIMIT),
                                  "image": d["image_url"],
                                  "description": seo_audit.word_limit('; '.join(facts) + '.', 5000),
                                  "offers": {"@type": "Offer", "price": price,
                                             "priceCurrency": "USD", "availability": "https://schema.org/InStock",
                                             "url": site_url + path,
                                             "seller": {"@type": "Organization", "name": store},
                                             "itemCondition": "https://schema.org/" + condition}}
        if d.get("brand"):
            product["brand"] = {"@type": "Brand", "name": d["brand"]}
        ld = _json_ld(product)
    return _landing_page(template, title, item, '; '.join(facts) + '.', path, site_url,
                         '<div class="deal-detail" data-deal-detail' + (' data-app-amazon' if app_amazon else '') + '>' + content + '</div>', dead=dead,
                         indexable=indexable, noindex_follow=noindex_follow,
                         extra_head=ld + (PRICE_HISTORY_STYLE if chart else "") + share_head
                         + (add_to_cart.STYLE if cart and not dead else ""),
                         breadcrumbs=[(label, hub_href(slug)), (item, path)])


_POSTED_REDIRECTS_META = "seo_posted_deal_redirects"
_DEAL_PATH_RE = re.compile(r"/deal/[a-z0-9]+(?:-[a-z0-9]+)*-[0-9a-f]{8}/")


def _redirect_record(db, key):
    try:
        value = json.loads(db.meta_get(key) or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _social_posted_keys(db):
    posted = {row[0] for row in db.conn.execute(
        "SELECT DISTINCT deal_key FROM alerts_sent WHERE channel IN "
        "('pinterest','facebook','instagram','telegram','meta:facebook:live','meta:instagram:live')")}
    # Pinterest and Meta use their own success ledgers, rather than alerts_sent.
    posted.update(_redirect_record(db, "pinterest_posted"))
    meta = _redirect_record(db, "meta_posted")
    for network in ("facebook", "instagram"):
        if isinstance(meta.get(network), dict):
            posted.update(meta[network])
    return posted


def remember_posted_pages(db, cfg, public):
    """Capture a first send after publication, before that run can be replayed."""
    if not cfg.seo_posted_deal_redirects_enabled:
        return
    posted = _social_posted_keys(db)
    saved = db.meta_get(_POSTED_REDIRECTS_META)
    remembered = _redirect_record(db, _POSTED_REDIRECTS_META)
    for d in public:
        path = d.get("deal_path")
        key = d.get("dedupe_key")
        if key in posted and isinstance(path, str) and _DEAL_PATH_RE.fullmatch(path) \
                and path.endswith("-" + hashlib.sha256(str(key).encode()).hexdigest()[:8] + "/"):
            remembered[path] = {"target": hub_href(_deal_hub(d)[2]), "added_at": None}
    if remembered:
        value = json.dumps(remembered, sort_keys=True)
        if value != saved:
            db.meta_set(_POSTED_REDIRECTS_META, value)


def _posted_deal_redirects(db, cfg, now, out, paths, previous, raw) -> dict[str, int]:
    """Keep only paths, category targets and activation dates in private state.

    Published posted paths wait with no date: their category must survive pruning,
    but they are not redirect rules and do not consume the static-rule allowance.
    """
    summary = {"rules": 0, "added_today": 0, "dropped_by_cap": 0}
    output = out / "_redirects"
    if not cfg.seo_posted_deal_redirects_enabled:
        output.unlink(missing_ok=True)
        return summary

    saved = db.meta_get(_POSTED_REDIRECTS_META)
    targets = {hub_href(g[2]) for g in HUB_GROUPS if g[1] != "USED"} | {"/"}
    remembered = {p: {"target": v["target"], "added_at": v.get("added_at"),
                      **({"dropped": True} if v.get("dropped") is True else {})}
                  for p, v in _redirect_record(db, _POSTED_REDIRECTS_META).items()
                  if _DEAL_PATH_RE.fullmatch(p) and isinstance(v, dict)
                  and isinstance(v.get("target"), str) and v["target"] in targets
                  and (v.get("added_at") is None or (isinstance(v["added_at"], str)
                       and parse_iso(v["added_at"]) is not None))}
    posted = {hashlib.sha256(str(k).encode()).hexdigest()[:8] for k in _social_posted_keys(db)}
    published = set(paths.values())
    for key in posted:
        path = paths.get(key) or previous.get(key)
        if not isinstance(path, str) or not _DEAL_PATH_RE.fullmatch(path) or not path.endswith(f"-{key}/"):
            continue
        if path in published or path not in remembered:
            remembered[path] = {"target": hub_href(_deal_hub(raw.get(key, {}))[2]), "added_at": None}
    stamp = iso(now.astimezone(timezone.utc))
    for path, rule in remembered.items():
        if path in published:
            rule["added_at"] = None
            rule.pop("dropped", None)
        elif rule["added_at"] is None:
            rule["added_at"] = stamp
    # A capped path whose row still exists must not be admitted afresh on the
    # next publish. Keep only its routing marker until the row is pruned or
    # the page returns; it is not an active redirect rule.
    stored_paths = {previous[k] for k in raw if isinstance(previous.get(k), str)}
    for path in list(remembered):
        if remembered[path].get("dropped") and path not in stored_paths:
            del remembered[path]
    rules = sorted((p for p in remembered if p not in published and not remembered[p].get("dropped")),
                   key=lambda p: (parse_iso(remembered[p]["added_at"]), p))
    dropped = rules[:max(0, len(rules) - cfg.seo_posted_deal_redirects_max_rules)]
    for path in dropped:
        if path in stored_paths:
            remembered[path]["dropped"] = True
        else:
            del remembered[path]
    rules = sorted(p for p in remembered if p not in published and not remembered[p].get("dropped"))
    summary = {"rules": len(rules),
               "added_today": sum(parse_iso(remembered[p]["added_at"]).date() == now.astimezone(timezone.utc).date()
                                  for p in rules),
               "dropped_by_cap": len(dropped)}
    if rules:
        output.write_text(''.join(f"{p} {remembered[p]['target']} 301\n" for p in rules), encoding="utf-8")
    else:
        output.unlink(missing_ok=True)
    if remembered or saved is not None:
        value = json.dumps(remembered, sort_keys=True)
        if value != saved:
            db.meta_set(_POSTED_REDIRECTS_META, value)
    return summary


def _deal_pages(db, cfg, now: datetime, out: Path, teaser: list[dict[str, Any]],
                source_deals: list[dict[str, Any]]) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, str], dict[str, int]]:
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
    from ..amazon_used import excluded as amazon_used_excluded
    for k, d in raw.items():
        suppressed = amazon_used_excluded(d, cfg)
        if (suppressed and k in remembered) or (d.get("status") == "dead"
                and d.get("pct_at_checkout") is not None and (k in remembered or k in public)):
            if k not in public:
                d = dict(d, sightings=db.sightings_for(d["id"]))
            # Retire previously published URLs through the existing ended-page
            # renderer; the stored offer and its history remain untouched.
            if suppressed:
                d = dict(d, status="dead", death=d.get("death") or "suppressed")
            rows.append(_serialize_deal(d, now, cfg=cfg) if suppressed
                        else public.get(k) or _serialize_deal(d, now, cfg=cfg))
    paths, lastmods = {}, {}
    for d in rows:
        k = d["key"][:8]
        paths[k] = remembered.setdefault(k, deal_path(str(d.get("item") or ""), k))
        lastmods[paths[k]] = raw[k].get("price_since") or d.get("first_seen_at") or ""
    redirects = _posted_deal_redirects(db, cfg, now, out, paths, previous, raw)
    db.meta_set("seo_deal_paths", json.dumps(remembered, sort_keys=True))
    wanted = {p.split('/')[2] for p in paths.values()}
    for folder in (out / "deal").glob("*"):
        if folder.name not in wanted:
            if folder.is_dir():
                shutil.rmtree(folder)
            else:
                folder.unlink()
    return paths, rows, lastmods, redirects




def publish(db, cfg, now: datetime, out: Path, teaser: list[dict[str, Any]],
            site_url: str, public: list[str], meta: dict[str, Any],
            source_deals: list[dict[str, Any]], *, spotlight: list[dict[str, Any]] | None = None,
            deal_pages: tuple[dict[str, str], list[dict[str, Any]], dict[str, str], dict[str, int]],
            products: dict | None = None,
            run_key: str | None = None, offsite_protected_keys=None, best_extra=()) -> tuple[list[str], str]:
    """Emit the wall, hubs and deal pages; return paths and the static wall.

    publish_sitemap reads the final tracked pages for dates and the audit.
    """
    from .paywall import is_open
    from .site import copy_variant, site_footer, trusted_line

    spotlight = spotlight or []
    products = products or {}
    open_access = is_open(cfg)
    paths, deal_rows, _previous_dates, _redirects = deal_pages
    from .. import clicks
    if clicks.enabled(cfg) and clicks.endpoint(cfg):
        # Render-only copies: no new fields in public/member JSON or features.
        def marked(rows):
            return [dict(d, _click_key=d.get("key", "")) if not d.get("locked") else d for d in rows]
        teaser, spotlight, deal_rows = marked(teaser), marked(spotlight), marked(deal_rows)
        products = {key: dict(p, live=dict(p["live"], _click_key=p["live"]["key"])) if p["live"] else p
                    for key, p in products.items()}
    from . import drop_fresh
    wall = static_wall(teaser, open_access, paths, now, cfg.front_page, spotlight,
                       static_photos=cfg.static_photos, credit_links=cfg.credit_links,
                       same_product_spacing=cfg.same_product_spacing,
                       fresh_config=cfg if drop_fresh.enabled(cfg) else None, short=short_cards.enabled(cfg),
                       fresh_mark=int((parse_iso(meta.get("published_at")) or now).timestamp()) // (cfg.drop_minutes * 60))
    index = (out / "index.html").read_text(encoding="utf-8")
    groups = json.dumps([list(g) for g in CATGROUPS]).replace("<", "\\u003c")
    (out / "index.html").write_text(
        _fill(index, CATGROUPS=groups, CAT_TABS=tabs_html(), STATIC_WALL=wall,
              IMAGE_PRECONNECTS=_image_preconnects(spotlight + teaser if spotlight else teaser, site_url, cfg.front_page),
              AMAZON_STRIP_HIDDEN="" if sum(
                  d.get("status") != "dead" and store_slug(str(d.get("store") or "")) == "amazon"
                  for d in teaser) >= 10 else "hidden",
              TICKER_HIDDEN="" if sum(d.get("status") != "dead" for d in teaser) >= 3 else "hidden"), encoding="utf-8")
    events, home_events = event_pages.links(cfg), event_pages.open_links(cfg, now)
    if home_events:
        index_file = out / "index.html"
        home = index_file.read_text(encoding="utf-8")
        chips = ('<nav class="hrow event-links" aria-label="Seasonal deals">' + ''.join(
            f'<a class="cattab" href="{escape(path)}">{escape(name)}</a>' for path, name in home_events) + '</nav>')
        home = re.sub(r'(<nav id="cattabs"[^>]*>.*?</nav>)', lambda m: m[0] + chips, home, count=1, flags=re.S)
        home = home.replace('</style>', '.event-links{justify-content:center;padding-top:0}\n'
            '.event-links a{min-height:44px}\n</style>', 1)
        index_file.write_text(home, encoding="utf-8")
    floors = seo_audit.minimums(cfg)
    index_file = out / "index.html"
    if seo_audit.Page(index_file.read_text(encoding="utf-8"), "/").live_offers() < floors["home"]:
        index_file.write_text(index_file.read_text(encoding="utf-8").replace(
            "</head>", '<meta name="robots" content="noindex,follow">\n</head>', 1), encoding="utf-8")
    support = bool(getattr(cfg, "support_enabled", False))
    not_found = site_footer((TEMPLATES_DIR / "404.html").read_text(encoding="utf-8"),
                            now, meta["disclosure"], support,
                            amazon_as_of_enabled=cfg.amazon_as_of_enabled,
                            product_pages_enabled=cfg.seo_product_pages_enabled and not amazon_reference.enabled(cfg), event_links=events,
                            checker_enabled=cfg.checker["enabled"])
    (out / "404.html").write_text(_fill(not_found,
                                        HUB_LINKS=_hub_links(events),
                                        DROP_MINUTES=str(int(cfg.drop_minutes))), encoding="utf-8")
    (out / "_headers").write_text(_HEADERS, encoding="utf-8")
    if cfg.checker["enabled"]:
        with (out / "_headers").open("a", encoding="utf-8") as headers:
            headers.write(checker.HEADERS)
    if manifest_refresh.enabled(cfg):
        with (out / "_headers").open("a", encoding="utf-8") as headers:
            headers.write(manifest_refresh.HEADERS)
    if cfg.seo_product_pages_enabled:
        with (out / "_headers").open("a", encoding="utf-8") as headers:
            headers.write("\n/p/*\n  Cache-Control: no-cache\n")
    if events:
        with (out / "_headers").open("a", encoding="utf-8") as headers:
            for path, _name in events:
                headers.write(f"\n{path}*\n  Cache-Control: no-cache\n")

    stats, copy = week_stats(db, cfg, now, offsite_protected_keys=offsite_protected_keys), _hub_copy(cfg)
    actions = (TEMPLATES_DIR / "deal-actions.js").read_text(encoding="utf-8")
    if inventory.enabled(cfg):
        actions = inventory.deal_actions(actions)
    template = _fill((TEMPLATES_DIR / "hub.html").read_text(encoding="utf-8"),
                     DROP_MINUTES=str(int(cfg.drop_minutes)),
                     DEAL_ACTIONS_SCRIPT=actions)
    template = checkout_prices.template(template, checkout_prices.enabled(cfg))
    from . import open_box
    template = open_box.template(template, cfg.open_box_scarcity, counts=cfg.open_box_counts)
    template = copy_variant(template, open_access, support)
    template = site_footer(template, now, meta["disclosure"], support,
                           amazon_as_of_enabled=cfg.amazon_as_of_enabled,
                           product_pages_enabled=cfg.seo_product_pages_enabled and not amazon_reference.enabled(cfg), event_links=events,
                           checker_enabled=cfg.checker["enabled"])
    template = _fill(template, HUB_LINKS=_hub_links(events))
    if cfg.static_photos:
        template = template.replace("</style>", STATIC_PHOTO_STYLE + "</style>", 1)
    if cfg.seo_product_pages_enabled:
        template = template.replace("</style>", product_pages.LINK_STYLE + "</style>", 1)
    if cfg.amazon_as_of_enabled:
        template = template.replace("</style>", amazon_as_of.STYLE + "</style>", 1)
    if cfg.unconfirmed_label_enabled or cfg.non_amazon_freshness_enabled:
        from .price_labels import STYLE
        template = template.replace("</style>", STYLE + "</style>", 1)
    if cfg.non_amazon_freshness_enabled:
        from ..non_amazon_freshness import STYLE as AGED_STYLE
        template = template.replace("</style>", AGED_STYLE + "</style>", 1)
    live = [d for d in teaser if d.get("status") == "alive" and not d.get("locked")]
    order = {d["dedupe_key"]: i for i, d in enumerate(source_deals)}
    live.sort(key=lambda d: (bool(d.get("price_unconfirmed")), order[d["dedupe_key"]]))
    stores, prices = _store_pages(cfg, live), _price_pages(cfg, live)
    families, family_members = inventory.product_families(
        cfg, [d for d in live if not d.get("price_unconfirmed")], source_deals)
    inventory_paths = []
    store_links = [(path, name) for path, (name, _rows) in stores.items()]
    price_links = [(path, name) for path, (name, _rows) in prices.items()]
    browse_template = _fill(template, PRICE_LINKS=_link_row("By price", price_links))
    template = _fill(browse_template, STORE_LINKS=_link_row("Stores", store_links))
    event_paths = event_pages.publish(out, cfg, now, live,
        {d["dedupe_key"]: d for d in source_deals}, paths, template, site_url)
    for slug in ("black-friday", "cyber-monday"):
        folder = out / slug
        if folder.is_dir() and f"/{slug}/" not in event_paths:
            shutil.rmtree(folder)
    from . import product_offers
    from .. import product_shadow

    shadow_run = run_key or "publish:" + iso(now)
    # OFF (None) keeps the previous call exactly; ON collects contributor keys.
    also_at, lowest, shadow = product_offers.evaluate(
        db, cfg, now, deal_rows, source_deals, paths, shadow_run,
        **({} if offsite_protected_keys is None else {"offsite_protected_keys": offsite_protected_keys}))
    original_facts = {d["dedupe_key"]: d for d in db.all_deals() + source_deals}
    for d in deal_rows:
        path = paths[d["key"][:8]]
        hide_history = amazon_history.hidden(cfg, original_facts.get(d["dedupe_key"], d))
        observations = ([] if hide_history else
                        db.price_observations(d["dedupe_key"], now - timedelta(days=90), now))
        sharing, share_head = traffic.share_html(d, path, site_url) if (
            cfg.traffic_share_enabled and not d.get("price_unconfirmed")) else ("", "")
        discovery = traffic.related_hubs(d, stores, prices) if cfg.traffic_related_hubs_enabled else ""
        discovery += inventory.family_link(d, family_members)
        detail_template = (template.replace(' · <a href="/p/">Price history</a>', "")
                           if hide_history else template)
        html = _deal_page(detail_template, d, path, site_url, meta["disclosure"], live, paths,
                          observations, now, indexable=floors["deal"] <= 1 and not d.get("price_unconfirmed"),
                          noindex_follow=not cfg.seo_deal_pages_indexable,
                          discovery=discovery, sharing=sharing, share_head=share_head,
                          app_amazon=cfg.pwa_android["enabled"] and (
                              original_facts.get(d["dedupe_key"], d).get("amazon") is True
                              or is_amazon_deal(original_facts.get(d["dedupe_key"], d))),
                          also_at=also_at.get(d["dedupe_key"], ""),
                          lowest_seen=product_offers.lowest_html(lowest.get(d["dedupe_key"]))
                          if cfg.product_entities_lowest_seen_enabled and not hide_history else "",
                          static_photos=cfg.static_photos, credit_links=cfg.credit_links,
                          cart=add_to_cart.deal_button(d, cfg),
                          trusted=trusted_line(original_facts.get(d["dedupe_key"], d), cfg)
                          if d.get("badge") == "trusted" else "", short=short_cards.enabled(cfg))
        folder = out / path.strip("/")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(html, encoding="utf-8")
    app_sources = None
    if cfg.pwa_android["enabled"]:
        # Retained/pruned offers and mislabeled Amazon URLs follow the same
        # original-store facts as the publication history policy.
        app_sources = {r["dedupe_key"]: {"store": r["store"], "canonical_url":
                       r["dedupe_key"][4:] if r["dedupe_key"].startswith("url|") else ""}
                       for r in db.conn.execute("SELECT dedupe_key,store FROM product_offers")}
        app_sources.update(original_facts)
    product_paths = product_pages.publish(out, cfg, now, products, template, site_url, meta["disclosure"], paths,
                                         app_sources=app_sources)
    best_path = "/best-deals-today/"
    best_title = "Best deals today | wowpriced"
    n = sum(d.get("status") == "alive" and not d.get("price_unconfirmed") for d in teaser)
    # Counts top out at "1,000+", like the home header (operator, 2026-10-12).
    count = "1,000+" if n >= 1000 else f"{n // 100 * 100:,}+" if n >= 100 else str(n)
    confirmed = [d for d in live if not d.get("price_unconfirmed") and not d.get("price_aged")]
    intro = (f"The {min(30, len(confirmed))} best of {count} live deals right now."
             if confirmed else "Our top picks are selected from offers that pass our price checks.")
    from ..front_mix import lead
    best_rows = lead(pick_order(confirmed, cfg.front_page), cfg.front_page, best_extra)[:30]
    best_content = ('<h2 class="sr-only">Best live deals</h2>' + _best_summary(best_rows)
                    + '<div class="grid">' + cards_html(best_rows, paths, now, static_photos=cfg.static_photos,
                                                       credit_links=cfg.credit_links, short=short_cards.enabled(cfg))
                    + '</div>' if best_rows else '<p><a href="/">Explore current verified deals</a>.</p>')
    best_html = _landing_page(template, best_title, best_title.removesuffix(" | wowpriced"),
                              intro, best_path, site_url, best_content,
                              intro=intro, extra_head=_item_list(best_rows, paths, site_url),
                              indexable=len(best_rows) >= floors["best_today"])
    (out / "best-deals-today").mkdir(parents=True, exist_ok=True)
    (out / "best-deals-today" / "index.html").write_text(best_html, encoding="utf-8")
    browse_pages = stores | prices
    for path, (name, rows) in browse_pages.items():
        heading = f"{name} deals today" if path in stores else f"{name} today"
        confirmed_rows = [d for d in rows if not d.get("price_unconfirmed")]
        count = ("1,000+" if len(confirmed_rows) >= 1000 else f"{len(confirmed_rows) // 100 * 100:,}+"
                 if len(confirmed_rows) >= 100 else str(len(confirmed_rows)))
        pcts = [float(d["pct_at_checkout"]) for d in confirmed_rows if d.get("pct_at_checkout") is not None]
        intro = f"{count} live deals right now"
        if path in stores and pcts:
            intro += f", median {_pct(statistics.median(pcts))}% off"
        intro += "."
        page_template = _fill(browse_template, STORE_LINKS=_link_row("Stores", store_links, path))
        family = "store" if path in stores else "price"
        for page, html in _listing_pages(page_template, heading, intro, path, site_url, rows,
                paths, now, floors[family], cfg.traffic_hub_pagination_enabled, family,
                static_photos=cfg.static_photos, credit_links=cfg.credit_links, short=short_cards.enabled(cfg)):
            _write_page(out, page, html)
            if page != path:
                inventory_paths.append(page)
    for path, (name, rows) in families.items():
        for page, html in _listing_pages(template, f"{name} offers", amazon_reference.template(inventory.family_intro(rows), cfg),
                path, site_url, rows, paths, now, floors["product_family"], True, "product_family",
                static_photos=cfg.static_photos, credit_links=cfg.credit_links, short=short_cards.enabled(cfg)):
            _write_page(out, page, html)
            inventory_paths.append(page)
    # Re-used output folders must lose pages that fall below a floor or cap.
    for folder in list((out / "stores").glob("*")) + list((out / "deals").glob("under-*")):
        if f"/{folder.relative_to(out).as_posix()}/" not in browse_pages:
            if folder.is_dir():
                shutil.rmtree(folder)
            else:
                folder.unlink()
    listed = 0
    for label, group, slug in HUB_GROUPS:
        rows = [d for d in teaser if d.get("status") == "alive" and in_group(d, group)]
        rows.sort(key=lambda d: bool(d.get("price_unconfirmed")))
        if cfg.front_page.get("used_tab_only"):
            rows.sort(key=lambda d: bool({"used", "open_box", "refurbished"} & set(d.get("conditions") or [])))
        free = sum(not d.get("locked") for d in rows)
        count = max(1, math.ceil(free / _HUB_MAX)) if cfg.traffic_hub_pagination_enabled else 1
        for number in range(1, count + 1):
            visible = [d for d in rows if not d.get("locked")][(number - 1) * _HUB_MAX:number * _HUB_MAX]
            indexable = sum(not d.get("price_unconfirmed") for d in visible) >= floors["category"]
            html = _hub_page(template, copy[slug], label, slug, rows, stats[slug], indexable,
                             site_url, open_access, paths, now, page_number=number, page_count=count,
                             paginate=cfg.traffic_hub_pagination_enabled,
                             static_photos=cfg.static_photos,
                             credit_links=cfg.credit_links, short=short_cards.enabled(cfg),
                             product_links=product_pages.hub_links(products, group)
                             if number == 1 and cfg.seo_product_pages_enabled and not amazon_reference.enabled(cfg) else "")
            path = inventory.page_path(hub_href(slug), number)
            _write_page(out, path, html)
            if number > 1:
                inventory_paths.append(path)
            elif indexable:
                listed += 1
    logger.info("seo: %d of %d category hubs indexable", listed, len(HUB_GROUPS))

    key = getattr(cfg, "seo_indexnow_key", "") or ""
    if key and not _KEY_RE.fullmatch(key):
        logger.warning("seo.indexnow_key is not 8-128 letters, digits or dashes: IndexNow off")
        key = ""
    if key:
        (out / f"{key}.txt").write_text(key, encoding="utf-8")
    emitted = (["/" + p for p in public] + ["/welcome.html", "/404.html"]
               + [hub_href(slug) for _label, _group, slug in HUB_GROUPS]
               + list(paths.values()) + [best_path] + list(browse_pages) + inventory_paths + product_paths + event_paths)
    inventory.remove_stale(out, emitted)
    product_shadow.record(db, cfg, now, shadow_run, shadow)
    return emitted, wall


def publish_sitemap(db, cfg, now: datetime, out: Path, site_url: str,
                    emitted: list[str], wall: str) -> dict[str, seo_audit.Page]:
    """Read final pages once for fingerprints, sitemap coverage and the audit."""
    pages = seo_audit.read_pages(out, emitted)
    for name in ("sitemap-pages.xml", "sitemap-products.xml"):
        if not cfg.seo_product_pages_enabled or not site_url:
            (out / name).unlink(missing_ok=True)
    if not site_url:
        (out / "sitemap.xml").unlink(missing_ok=True)
        return pages
    lastmods = seo_audit.fact_lastmods(db, cfg, now, site_url, pages)
    # Match the emitted robots directives, including empty home/best pages.
    indexable = {p: page for p, page in pages.items() if page.indexable}
    assert len(indexable) <= 50_000
    def write_urlset(name, paths):
        urls = ""
        for path in paths:
            lastmod = lastmods[path]
            urls += (f"  <url><loc>{escape(site_url + path)}</loc>"
                     + (f"<lastmod>{escape(lastmod)}</lastmod>" if lastmod else "") + "</url>\n")
        (out / name).write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{urls}</urlset>\n", encoding="utf-8")
    if cfg.seo_product_pages_enabled:
        write_urlset("sitemap-products.xml", [p for p in indexable if seo_audit.page_family(p) == "product"])
        write_urlset("sitemap-pages.xml", [p for p in indexable if seo_audit.page_family(p) != "product"])
        (out / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + ''.join(f'  <sitemap><loc>{escape(site_url)}/{name}</loc></sitemap>\n'
                      for name in ("sitemap-pages.xml", "sitemap-products.xml"))
            + '</sitemapindex>\n', encoding="utf-8")
    else:
        write_urlset("sitemap.xml", indexable)
    key = getattr(cfg, "seo_indexnow_key", "") or ""
    if _KEY_RE.fullmatch(key):
        def _read(path: str) -> str:
            if path.startswith('/compare/'):
                return pages[path].fingerprint()
            content = wall if path == "/" else (out / (path.lstrip("/") + "index.html" if path.endswith("/") else
                                                      path.lstrip("/"))).read_text(encoding="utf-8")
            # Labeled card prices cannot cause pings of listings or neighbours.
            return re.sub(r'<article\b[^>]*>.*?</article>',
                          lambda m: "" if 'class="price-unconfirmed"' in m[0] else m[0], content, flags=re.S)
        contents = {site_url + path: _read(path) for path in indexable}
        # Ended deal pages (noindex, out of the sitemap) still tell engines
        # they changed (task 072).
        contents.update({site_url + path: _read(path) for path in emitted
                         if path.startswith("/deal/") and site_url + path not in contents
                         and (out / path.strip("/") / "index.html").exists()})
        from .indexnow import product_exclusions
        excluded = {site_url + path for path, page in pages.items() if page.unconfirmed}
        excluded |= product_exclusions(db, site_url, pages)
        excluded |= event_pages.indexnow_exclusions(db, site_url, pages)
        from .tool_pages import indexnow_exclusions as tool_exclusions
        excluded |= tool_exclusions(cfg, site_url)
        due = indexnow_due(db, cfg, now, contents, site_dir=out, excluded=excluded)
        due_list_path(out).write_text(json.dumps(due), encoding="utf-8")
        logger.info("indexnow: %d of %d URL(s) due", len(due), len(contents))
    return pages
