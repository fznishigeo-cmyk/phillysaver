"""Static enhancements around the existing brand-page copy and public offers."""

import re
from html import escape
from html.parser import HTMLParser
from pathlib import Path

from ..config import support_link
from ..savings import display_pct
from . import seo

TEMPLATES = Path(__file__).with_name("templates")
PAGES = {
    "about.html": "Real deals, checked before you see them",
    "join.html": "Good deals, straight to you",
    "contact.html": "Let’s talk good deals",
    "careers.html": "Deals, done honestly",
    "hacks.html": "A little know-how. A better buy.",
    "how-it-works.html": "Good deals start with good checks",
}
_ICONS = {
    "check": '<path d="m5 12 4 4L19 6"/>',
    "shield": '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l4 2"/>',
    "bag": '<path d="M5 8h14l1 13H4Z"/><path d="M8 9V7a4 4 0 0 1 8 0v2"/>',
    "search": '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
    "link": '<path d="m9 15 6-6m-5-3 2-2a5 5 0 0 1 7 7l-2 2m-3 5-2 2a5 5 0 0 1-7-7l2-2"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 6 9 7 9-7"/>',
}


def icon(name):
    return ('<svg class="brand-icon" viewBox="0 0 24 24" width="24" height="24" '
            'fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" '
            'stroke-linejoin="round" aria-hidden="true">' + _ICONS[name] + '</svg>')


class _Blocks(HTMLParser):
    """Split trusted template markup at top-level elements without rewriting it."""

    def __init__(self, html):
        super().__init__(convert_charrefs=False)
        self.html, self.blocks, self.depth, self.end = html, [], 0, 0
        self.lines = [0]
        for match in re.finditer("\n", html):
            self.lines.append(match.end())
        self.feed(html)
        if html[self.end:].strip():
            self.blocks.append(html[self.end:])

    def handle_starttag(self, tag, attrs):
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.depth += 1

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        self.depth -= 1
        if self.depth == 0:
            line, col = self.getpos()
            end = self.html.index(">", self.lines[line - 1] + col) + 1
            self.blocks.append(self.html[self.end:end])
            self.end = end


def _stories(body):
    groups, current, label = [], [], ""
    for block in _Blocks(body).blocks:
        if re.match(r'\s*<h2 class="sr-only">', block):
            label += block
            continue
        standalone = re.match(r'\s*(?:<section\b|<div class="hack")', block)
        if current and (standalone or re.match(r"\s*<h2\b", block)):
            groups.append("".join(current))
            current = []
        if standalone:
            groups.append(block)
        else:
            current.append(block)
    if current:
        groups.append("".join(current))
    symbols = ("shield", "search", "bag", "link", "clock", "mail")
    return label + '<div class="brand-stories">' + "".join(
        '<section class="brand-story">' + icon(symbols[n % len(symbols)]) + group + '</section>'
        for n, group in enumerate(groups)) + '</div>'


def _live(doc):
    return list({d["key"]: d for d in doc["deals"] + doc.get("spotlight", [])
                 if not d.get("locked") and d.get("status") == "alive"}.values())


def collage_rows(doc, cfg):
    # Use the homepage's public projection and ordering, never private deal rows.
    rows = seo.home_order(doc["deals"], cfg.front_page, click_price=cfg.keepa_click_price_rule == "on")
    if cfg.front_page.get("top_picks"):
        rows = [d for d in rows if d.get("pick")]
    return [d for d in rows if not d.get("locked") and d.get("status") == "alive"
            and not (d.get("price_unconfirmed") or d.get("price_aged") or d.get("click_price_unknown"))
            and d.get("final_price") is not None and str(d.get("image_url") or "").startswith("https://")
            and re.fullmatch(r"/deal/[a-z0-9-]+/", d.get("deal_path", ""))][:4]


def _collage(rows):
    if not rows:
        return ""  # Sparse publishes never acquire made-up cards or placeholder prices.
    cards = []
    for n, d in enumerate(rows):
        pct = d.get("pct_at_checkout")
        badge = f'<span class="brand-discount">{display_pct(pct)}% off</span>' if pct is not None else ""
        notes = [str(c).replace("_", " ") for c in d.get("conditions", []) if c != "new"]
        if d.get("needs_steps"):
            notes.append("See price steps")
        note = '<span class="brand-deal-note">' + escape(" · ".join(notes)) + '</span>' if notes else ""
        if d.get("amazon_as_of"):
            note += '<span class="brand-deal-note">Price as of ' + escape(d["amazon_as_of"]) + '</span>'
        cards.append(
            f'<a class="brand-deal" data-key="{escape(d["key"])}" href="{escape(d["deal_path"])}">'
            '<span class="brand-photo">'
            f'<img src="{escape(d["image_url"], quote=True)}" alt="" width="320" height="240" '
            f'loading="{"eager" if n == 0 else "lazy"}" decoding="async"></span>'
            f'<span class="brand-deal-body"><span class="brand-store">{escape(d.get("store") or "")}</span>'
            f'<span class="brand-deal-title">{escape(d["item"])}</span>'
            f'<span class="brand-price">${float(d["final_price"]):.2f}</span>{badge}{note}</span></a>')
    return '<div class="brand-collage" aria-label="Live deals from today’s top picks">' + "".join(cards) + '</div>'


def _why(minutes):
    blocks = (("shield", "Verified prices", "Price evidence checked word for word."),
              ("clock", f"Fresh every {minutes} minutes", "New drops. Clear conditions."),
              ("bag", "For everyday life", "Everyday things people actually buy."))
    return ('<section class="brand-why" aria-labelledby="brand-why-title">'
            '<h2 id="brand-why-title">Why wowpriced</h2><div class="brand-reasons">' + "".join(
                '<div>' + icon(symbol) + f'<h3>{title}</h3><p>{copy}</p></div>'
                for symbol, title, copy in blocks) + '</div></section>')


def _proof(doc, minutes):
    rows = _live(doc)
    stores = len({d["store"] for d in rows if d.get("store")})
    return ('<dl class="brand-proof" aria-label="This publication">'
            f'<div><dt>Live deals</dt><dd>{len(rows):,}</dd></div>'
            f'<div><dt>Stores</dt><dd>{stores:,}</dd></div>'
            f'<div><dt>Refreshed every</dt><dd>{minutes} <span>minutes</span></dd></div></dl>')


def _values(minutes):
    return '<ul class="brand-values" aria-label="Our values">' + "".join(
        '<li>' + icon("check") + '<span>' + label + '</span></li>' for label in (
            "Real prices only", "Shoppers first", f"Fresh every {minutes} minutes", "Polite to every store")) + '</ul>'


def _day():
    return ('<section class="brand-day" aria-labelledby="brand-day-title">'
            '<h2 id="brand-day-title">Every day at wowpriced</h2><ol>' + "".join(
                '<li>' + icon(symbol) + f'<span class="brand-step" aria-hidden="true">0{n}</span><h3>{label}</h3></li>'
                for n, (symbol, label) in enumerate((("search", "Find offers"), ("shield", "Check prices"),
                    ("link", "Credit sources"), ("clock", "Refresh deals")), 1)) + '</ol></section>')


def _difference():
    return ('<section class="brand-difference" aria-labelledby="brand-difference-title">'
            '<h2 id="brand-difference-title">What makes us different</h2><ul>' + "".join(
                f'<li><strong>{label}</strong> — {copy}</li>' for label, copy in (
                    ("Our why", "Help you shop with confidence."),
                    ("How it works", "Checked prices, clear conditions, credited sources."),
                    ("Our promise", "We remove deals once they end."),
                    ("Who we are", "A deal site that puts prices in context."))) + '</ul>'
            '<div class="brand-actions"><a class="brand-primary" href="/careers">Work with us</a>'
            '<a class="brand-pill" href="/contact">Contact us</a></div></section>')


def _layout(html, name, doc, cfg):
    main = re.search(r"<main\b[^>]*>(.*?)</main>", html, re.S)
    body = main[1]
    if name == "join.html":
        label = "Join wowpriced"
        lede = "Get the best deals by email."
        body = body.replace('<h1 id="join-title">', '<h2 id="join-title">').replace('</h1>', '</h2>')
        body = '<div class="brand-join">' + body + '<ul class="brand-join-points">' + "".join(
            '<li>' + icon("check") + label + '</li>' for label in (
                "Free to join", "The best deals by email", "Unsubscribe anytime")) + '</ul></div>'
    else:
        heading = re.search(r"<h1>(.*?)</h1>", body, re.S)
        intro = re.search(r'<p class="lede">(.*?)</p>', body, re.S)
        label, lede = heading[1], intro[1]
        body = _stories(body.replace(heading[0], "", 1).replace(intro[0], "", 1))
    action, href, secondary = "Start saving now", "/", ""
    if name == "about.html":
        action = "See today's deals"
        secondary = '<a class="brand-secondary" href="/contact.html">Contact us</a>'
    if name in {"careers.html", "contact.html"}:
        email = cfg.contact_email
        if email != "set-me@example.com" and re.fullmatch(r"[^\s@\"'<>\\&]+@[^\s@\"'<>\\&]+", email or ""):
            action = "Say hello" if name == "careers.html" else email
            href = "mailto:" + email
            secondary = '<a class="brand-secondary" href="/">See today’s deals</a>'
    rows = collage_rows(doc, cfg)
    hero = ('<section class="brand-hero' + (" brand-hero-sparse" if not rows else "") + '">'
            '<div class="brand-hero-copy"><p class="brand-eyebrow"><span>WOWPRICED</span> / ' + label + '</p>'
            f'<h1>{PAGES[name]}</h1><p class="brand-lede">{lede}</p>'
            '<div class="brand-actions">'
            f'<a class="brand-primary" href="{escape(href)}">{escape(action)}'
            '<span aria-hidden="true"> ↗</span></a>' + secondary + '</div>'
            '<ul class="brand-promises">' + "".join('<li>' + icon("check") + text + '</li>'
                for text in ("Checked prices", "Clear conditions", "Source credits")) + '</ul></div>'
            + _collage(rows) + '</section>')
    minutes = int(cfg.drop_minutes)
    content = hero + (_values(minutes) if name == "careers.html" else "") + body
    if name == "about.html":
        content += _difference()
    if name == "careers.html":
        content += _day()
    content += _why(minutes) + _proof(doc, minutes)
    before, after = html[:main.start()], html[main.end():]
    # Give the new main room while leaving the existing header/footer containers intact.
    if re.search(r'<div class="wrap">\s*$', before):
        before = re.sub(r'<div class="wrap">\s*$', '', before)
        after = '\n<div class="wrap">' + after
    return before + '<main class="brand-page">' + content + '</main>' + after


def publish(out, cfg, doc):
    from .site import JOIN_FORM, support_script

    css = (TEMPLATES / "brand-pages.css").read_text(encoding="utf-8")
    motion = (TEMPLATES / "brand-pages.js").read_text(encoding="utf-8")
    link = support_link(cfg.support_payment_link)
    monthly = support_link(cfg.support_monthly_link) if link else ""
    script = support_script(link, monthly, cfg.support_panel_delay_s)
    meta = {key: doc["meta"][key] for key in ("api_url", "join", "support")}
    enhanced = support_script(link, monthly, cfg.support_panel_delay_s, brand_meta=meta)
    for name in PAGES:
        path = out / name
        if not path.exists():  # Join remains support-mode only.
            continue
        html = _layout(path.read_text(encoding="utf-8"), name, doc, cfg)
        style = css
        if cfg.support_enabled:
            html = html.replace(script, enhanced, 1)
            if name != "join.html":
                card = (TEMPLATES / "join-card.html").read_text(encoding="utf-8")
                card = card.replace("{{JOIN_FORM}}", JOIN_FORM).replace("{{JOIN_HEADING}}", "h2")
                card = card.replace("{{JOIN_CLOSE}}", '<button type="button" class="joinclose" '
                    'data-join-close aria-label="Close sign-up prompt">✕</button>')
                html = html.replace('</body>', '<div id="join-prompt" class="joinoverlay" hidden>'
                    '<div role="dialog" aria-modal="true" aria-labelledby="join-title" '
                    'aria-describedby="join-description" tabindex="-1">' + card + '</div></div>\n</body>')
                # The controller must see the dialog when it initializes.
                html = html.replace('<script>' + enhanced + '</script>', '')
                html = html.replace('</body>', '<script>' + enhanced + '</script>\n</body>')
                style = (TEMPLATES / "join-card.css").read_text(encoding="utf-8") + css
        html = html.replace('</head>', '<style id="brand-pages">' + style + '</style>\n</head>', 1)
        html = html.replace('</body>', '<script>' + motion + '</script>\n</body>', 1)
        path.write_text(html, encoding="utf-8")
