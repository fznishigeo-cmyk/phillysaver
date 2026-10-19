"""Optional event search entry and brand-style Support page; OFF adds no bytes."""

import hashlib
from pathlib import Path

from . import brand_pages, card_tidy, search_box_glow
from .template_slots import replace_slot

TEMPLATES = Path(__file__).with_name("templates")


def enabled(cfg):
    return getattr(cfg, "page_polish", False) is True


def search_css():
    return search_box_glow.CSS + card_tidy.CSS


def search_style():
    version = hashlib.sha256(search_css().encode()).hexdigest()[:12]
    return '<link rel="stylesheet" href="/page-polish.css?v=' + version + '">'


def publish(out, cfg):
    asset = out / 'page-polish.css'
    if enabled(cfg):
        asset.write_text(search_css(), encoding='utf-8')
        if cfg.support_enabled:
            home = out / 'index.html'
            html = home.read_text(encoding='utf-8')
            # Keep the existing delay, engagement and durable frequency rules;
            # an active query or focus anywhere in search postpones the ask.
            html = replace_slot(html, '  function maybePrompt() {',
                '  function maybePrompt() {\n'
                '    var q = document.getElementById("q");\n'
                '    if (q && (q.value.trim() || document.activeElement.closest(".search"))) {\n'
                '      if (timer === null) timer = setTimeout(function () { timer = null; refresh(); }, 1000);\n'
                '      return;\n'
                '    }', template_name='index.html', slot_name='page-polish-search-prompt')
            home.write_text(html, encoding='utf-8')
    else:
        asset.unlink(missing_ok=True)


def home(html, cfg):
    if not enabled(cfg):
        return html
    if search_box_glow.enabled(cfg) and card_tidy.enabled(cfg):
        # A render-blocking shared stylesheet preserves first-paint styling
        # while keeping the homepage's existing payload headroom.
        for css in (search_box_glow.CSS, card_tidy.CSS):
            html = replace_slot(html, css, '', template_name="index.html", slot_name="page-polish-css")
        html = html.replace('</style>', '</style>' + search_style(), 1)
    script = (TEMPLATES / "page-polish-home.js").read_text()
    html = replace_slot(html, '  function loadData() {', script + '  function loadData() {',
                        template_name="index.html", slot_name="page-polish-query")
    return replace_slot(html, '      render();\n      restoreInitialBrowse();',
                        '      if (!applyPagePolishQuery()) { render(); restoreInitialBrowse(); }',
                        template_name="index.html", slot_name="page-polish-load")


def event(html, cfg):
    if not enabled(cfg):
        return html
    css = (TEMPLATES / "page-polish-event.css").read_text()
    # Use the same rainbow and purple button rules as the homepage, regardless
    # of the independent home styling switches.
    form = (TEMPLATES / "page-polish-event.html").read_text()
    html = replace_slot(html, '</header>', form + '</header>',
                        template_name="hub.html", slot_name="page-polish-search")
    return html.replace('</head>', search_style() + '<style id="page-polish">' + css + '</style>\n</head>', 1)


def support(html, cfg):
    if not enabled(cfg) or not cfg.support_enabled:
        return html
    css = (TEMPLATES / "brand-pages.css").read_text() + (TEMPLATES / "page-polish-support.css").read_text()
    visual = '<div class="brand-collage support-visual" aria-hidden="true">' + ''.join(
        '<div class="brand-deal"><div class="brand-deal-body">' + brand_pages.icon(icon)
        + '<strong>' + label + '</strong></div></div>' for icon, label in (
            ("search", "Checked prices"), ("shield", "Clear conditions"),
            ("link", "Source credits"), ("bag", "Every deal stays free"))) + '</div>'
    replacements = (
        ('<div class="card">', '<div class="support-shell">'),
        ('<h1>Support our work</h1>', '<main class="brand-page"><section class="brand-hero">'
         '<div class="brand-hero-copy"><p class="brand-eyebrow"><span>WOWPRICED</span> / Support</p>'
         '<h1>Support our work</h1><p class="brand-lede">Help keep good deals coming.</p>'),
        ('  <h2>Join free: the best deals in your inbox</h2>', '</div>' + visual + '</section>'
         '<section class="brand-story support-join">' + brand_pages.icon("mail")
         + '<div><h2>Join free: the best deals in your inbox</h2>'),
        ('<p><a class="btn alt" href="/join">Join free</a></p>',
         '<p><a class="brand-primary" href="/join">Join free</a></p></div></section></main>'),
    )
    for old, new in replacements:
        html = replace_slot(html, old, new, template_name="subscribe.html", slot_name="page-polish-support")
    return html.replace('</head>', '<style id="page-polish">' + css + '</style>\n</head>', 1)


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    assets = b''.join(path.read_bytes() for path in sorted(TEMPLATES.glob('page-polish-*')))
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes() + assets).hexdigest()[:12]
