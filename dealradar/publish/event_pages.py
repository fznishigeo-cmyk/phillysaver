"""Permanent seasonal hubs, using only this publish's public deal facts."""

from datetime import date, timedelta
from html import escape
from zoneinfo import ZoneInfo

from ..rank import pick_order


def links(cfg):
    return [(f'/{e["slug"]}/', e["name"]) for e in cfg.seo_event_pages_events] if cfg.seo_event_pages_enabled else []


def open_links(cfg, now):
    """Home-page chips only while an event's shopping window is open (footers and hubs always link)."""
    if not cfg.seo_event_pages_enabled:
        return []
    today = now.astimezone(ZoneInfo(cfg.timezone)).date()
    return [(f'/{e["slug"]}/', e["name"]) for e in cfg.seo_event_pages_events
            if window(e)[0] <= today <= window(e)[1]]


def indexnow_exclusions(db, site_url, pages):
    """Exclude queued event URLs once their current page is no longer indexable."""
    from .seo_audit import page_family

    indexable = {site_url + path for path, page in pages.items()
                 if page_family(path) == "event" and page.indexable}
    events = {url for url in db.indexnow_pages() if page_family(url) == "event"}
    events.update(site_url + path for path in pages if page_family(path) == "event")
    return events - indexable


def window(event):
    stamp = date.fromisoformat(event["date"])
    return tuple(stamp + timedelta(days=n) for n in event["window_days"])


def qualifies(deal, original, event, today):
    """An explicit stored event mention, or a published pick during its window."""
    evidence = original.get("evidence") or {}
    text = [original.get("item") or "", *(original.get("conditions") or [])]
    text.extend(value for value in evidence.values() if isinstance(value, str))
    start, end = window(event)
    return (any(keyword in " ".join(text).casefold() for keyword in event["keywords"])
            or start <= today <= end and bool(deal.get("pick")))


def publish(out, cfg, now, live, originals, paths, template, site_url):
    from .seo import _item_list, _landing_page, _write_page, card_html

    if not cfg.seo_event_pages_enabled:
        return []
    today = now.astimezone(ZoneInfo(cfg.timezone)).date()
    niche = set(cfg.front_page.get("niche_categories", []))
    public = [d for d in live if d.get("status") == "alive" and not d.get("locked")
              and not d.get("price_unconfirmed") and d.get("category") not in niche]
    emitted = []
    for event in cfg.seo_event_pages_events:
        start, end = window(event)
        # Outside the window, keep useful current picks on the permanent page
        # (the brief explicitly requests early and post-event fallback content).
        rows = [d for d in public if qualifies(d, originals[d["dedupe_key"]], event, today)
                or not start <= today <= end and d.get("pick")]
        rows = pick_order(rows, cfg.front_page)
        rows.sort(key=lambda d: not bool(d.get("image_url")))
        rows = rows[:cfg.seo_event_pages_max_items]
        path = f'/{event["slug"]}/'
        heading = f'{event["name"]} Deals {date.fromisoformat(event["date"]).year}'
        dek = f'{len(rows)} live deals · updated every {int(cfg.drop_minutes)} minutes.'
        content = (f'<p>{escape(event["name"])}: '
                   f'<time datetime="{event["date"]}">{date.fromisoformat(event["date"]).strftime("%B %d, %Y")}</time>.</p>')
        intro = cfg.seo_event_pages_intro.get(event["slug"], "")
        if intro:
            content += f'<p>{escape(intro)}</p>'
        content += (f'<h2>Early {escape(event["name"])} deals</h2>' if today < start else
                    '<h2>Deals right now</h2>')
        content += ('<div class="grid">' + ''.join(card_html(d, paths, now) for d in rows) + '</div>' if rows else
                    '<p class="muted">No qualifying deals right now. Browse <a href="/">the live wall</a> for current picks.</p>')
        html = _landing_page(template, heading + ' | wowpriced', heading, dek, path, site_url,
                             content, intro=dek, extra_head=_item_list(rows, paths, site_url),
                             indexable=len(rows) >= cfg.seo_event_pages_min_items)
        _write_page(out, path, html)
        emitted.append(path)
    return emitted
