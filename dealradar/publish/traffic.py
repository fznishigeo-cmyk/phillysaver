"""Free discovery from current public facts; no requests or persisted copies."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree as ET

from ..util import parse_iso

TEMPLATES = Path(__file__).resolve().parent / "templates"
_HEADERS = ("\n/feed.xml\n  Content-Type: application/rss+xml; charset=utf-8\n"
            "  Cache-Control: no-cache\n  X-Robots-Tag: noindex\n"
            "\n/traffic.js\n  Cache-Control: no-cache\n")
_BLOCK = re.compile(r"<!-- wp-traffic -->.*?<!-- /wp-traffic -->\n?", re.S)


def enabled(cfg) -> bool:
    return any((cfg.traffic_feed_enabled, cfg.traffic_share_enabled,
                cfg.traffic_related_hubs_enabled))


def site_version(version: str, cfg) -> str:
    data = (version + str((cfg.traffic_feed_enabled, cfg.traffic_share_enabled,
                          cfg.traffic_related_hubs_enabled))).encode() + Path(__file__).read_bytes()
    for name in ("traffic.js", "traffic.css"):
        data += (TEMPLATES / name).read_bytes()
    return hashlib.sha256(data).hexdigest()[:12]


def tagged_url(site_url: str, path: str, source: str) -> str:
    # Always start from the remembered canonical path, never the visitor's URL.
    return site_url + path + "?" + urlencode({"utm_source": source,
        "utm_medium": "referral" if source == "share" else "rss", "utm_campaign": "deal"})


def share_html(d: dict, path: str, site_url: str) -> tuple[str, str]:
    if d.get("status") != "alive" or not site_url:
        return "", ""
    url = escape(tagged_url(site_url, path, "share"))
    title = escape(str(d.get("item") or "Deal") + " at " + str(d.get("store") or "the store"))
    head = (f'<meta property="og:type" content="website">\n'
            f'<meta property="og:title" content="{title}">\n'
            '<meta property="og:description" content="See the price, condition and checkout requirements on wowpriced.">\n'
            f'<meta property="og:url" content="{escape(site_url + path)}">\n'
            '<meta name="twitter:card" content="summary">\n')
    html = ('<details class="traffic-share"><summary>Share this deal</summary>'
            f'<div data-share data-share-title="{title}">'
            '<div class="traffic-actions"><button type="button" data-share-native hidden>Share…</button>'
            '<button type="button" data-share-copy hidden>Copy link</button></div>'
            '<label data-share-manual>Deal link (select to copy)'
            f'<input type="text" readonly value="{url}" aria-label="Deal link to share" '
            'autocomplete="off" spellcheck="false" data-clarity-mask="true"></label>'
            '<p data-share-status role="status" aria-live="polite"></p></div></details>')
    return html, head


def related_hubs(d: dict, stores: dict, prices: dict) -> str:
    """Only destinations actually emitted, with another public live offer."""
    from .seo import store_slug

    links = []
    store_path = "/stores/" + store_slug(str(d.get("store") or "")) + "/"
    if store_path in stores:
        name, rows = stores[store_path]
        if any(r.get("key") != d.get("key") for r in rows):
            links.append((store_path, f"More {name} deals", "store"))
    for path, (name, rows) in prices.items():  # generated in ascending budget order
        if any(r.get("key") == d.get("key") for r in rows) and any(
                r.get("key") != d.get("key") for r in rows):
            links.append((path, name, "price"))
            break
    if not links:
        return ""
    return '<nav class="traffic-hubs" aria-label="More deals by store and budget">' + "".join(
        f'<a href="{escape(path)}" data-related-hub="{method}">{escape(name)}</a>'
        for path, name, method in links) + '</nav>'


def _xml_text(value: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", "", value)


def feed_bytes(rows: list[dict], paths: dict[str, str], site_url: str, limit: int,
               details: bool = True) -> bytes:
    """``details=False`` keeps codes, steps and checkout notes on our deal pages only."""
    rss = ET.Element("rss", {"version": "2.0", "xmlns:atom": "http://www.w3.org/2005/Atom"})
    channel = ET.SubElement(rss, "channel")

    def field(parent, tag, text, **attrs):
        ET.SubElement(parent, tag, attrs).text = _xml_text(str(text))

    field(channel, "title", "wowpriced deals")
    field(channel, "link", site_url + "/")
    field(channel, "description", "Current public deals. Check each offer for its latest price and requirements.")
    field(channel, "language", "en-us")
    ET.SubElement(channel, "atom:link", {"href": site_url + "/feed.xml", "rel": "self",
                                       "type": "application/rss+xml"})
    live = [d for d in rows if d.get("status") == "alive" and not d.get("locked")
            and str(d.get("key") or "")[:8] in paths]
    live.sort(key=lambda d: (parse_iso(d.get("first_seen_at") or "") or
                            datetime.min.replace(tzinfo=timezone.utc), str(d["key"])), reverse=True)
    for d in live[:limit]:
        path = paths[d["key"][:8]]
        item = ET.SubElement(channel, "item")
        field(item, "title", str(d.get("item") or "Deal") + " at " + str(d.get("store") or "the store"))
        field(item, "link", tagged_url(site_url, path, "feed"))
        field(item, "guid", site_url + path, isPermaLink="true")
        seen = parse_iso(d.get("first_seen_at") or "")
        if seen:
            field(item, "pubDate", format_datetime(seen.astimezone(timezone.utc), usegmt=True))
        facts = []
        if d.get("final_price") is not None:
            facts.append(f'Sale price: ${float(d["final_price"]):.2f} USD.')
        conditions = [str(c).replace("_", " ") for c in d.get("conditions") or []]
        if not {"used", "open_box", "refurbished"} & set(d.get("conditions") or []):
            conditions.insert(0, "new")
        facts.append("Condition: " + ", ".join(conditions) + ".")
        if details:
            checkout = d.get("checkout") or {}
            facts.extend(checkout[k] for k in ("now", "shipping", "summary") if checkout.get(k))
            facts.extend(str(s) for s in d.get("how") or [])
            facts.extend("Code: " + str(c) for c in d.get("codes") or [])
        else:
            facts.append("Codes, steps and checkout details are on the deal page.")
        description = "".join(f"<p>{escape(f)}</p>" for f in facts)
        for credit in d.get("credits") or []:
            name, url = str(credit.get("name") or ""), str(credit.get("url") or "")
            label = f'<a href="{escape(url)}">{escape(name)}</a>' if url.startswith(("https://", "http://")) else escape(name)
            description += f"<p>Source: {label}</p>"
        description += "<p>Prices and availability can change. Check the offer before buying.</p>"
        field(item, "description", description)
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True) + b"\n"


def publish(out: Path, cfg, rows: list[dict], paths: dict[str, str], site_url: str) -> None:
    """Run after page writers, before tracking/audit; repeat calls are stable."""
    active = enabled(cfg)
    feed = cfg.traffic_feed_enabled and urlsplit(site_url).scheme == "https" and bool(urlsplit(site_url).netloc)
    if feed:
        (out / "feed.xml").write_bytes(feed_bytes(rows, paths, site_url, cfg.traffic_feed_max_items,
                                                  cfg.traffic_feed_details))
    else:
        (out / "feed.xml").unlink(missing_ok=True)
    if active:
        script = (TEMPLATES / "traffic.js").read_bytes()
        (out / "traffic.js").write_bytes(script)
        version = hashlib.sha256(script).hexdigest()[:12]
        css = (TEMPLATES / "traffic.css").read_text(encoding="utf-8")
    else:
        (out / "traffic.js").unlink(missing_ok=True)
    for path in sorted(out.rglob("*.html")):
        original = path.read_text(encoding="utf-8")
        page = _BLOCK.sub("", original)
        # Checkout removes its session id before tags; it has no acquisition UI.
        if active and path.name != "welcome.html":
            head = (('<link rel="alternate" type="application/rss+xml" title="wowpriced deals" '
                     'href="/feed.xml">\n') if feed else "") + f'<style>{css}</style>\n'
            anchor = "<!-- wp-tracking -->" if "<!-- wp-tracking -->" in page else "</head>"
            page = page.replace(anchor, '<!-- wp-traffic -->' + head + '<!-- /wp-traffic -->\n' + anchor, 1)
            if feed:
                page = page.replace("</footer>", '<!-- wp-traffic --><p><a class="traffic-feed" '
                    'href="/feed.xml" data-feed-link>Follow deals by RSS</a></p><!-- /wp-traffic -->\n</footer>', 1)
            page = page.replace("</body>", '<!-- wp-traffic -->'
                f'<script src="/traffic.js?v={version}" defer></script><!-- /wp-traffic -->\n</body>', 1)
        if page != original:
            path.write_text(page, encoding="utf-8")
    headers = out / "_headers"
    current = headers.read_text(encoding="utf-8").replace(_HEADERS, "")
    headers.write_text(current + (_HEADERS if active else ""), encoding="utf-8")
