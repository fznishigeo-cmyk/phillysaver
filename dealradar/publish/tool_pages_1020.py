"""Task 339: gated tool refresh and homepage discovery; exact legacy rollback."""

import hashlib
import json
import re
from html import escape
from pathlib import Path

from . import tool_hero

TEMPLATES = Path(__file__).with_name("templates")
ASSET = "home-tools.css"
LEADS = {"checker": "AI-Powered Amazon Deal Analysis", "finder": "AI-Powered Amazon Discount Finder"}
# Operator-approved wording 2026-10-20 (every step is true: extraction, deal critic, verify_evidence, Amazon re-check).
AI_LEAD = "Yes. Deals go through four checks before you see them:"
AI_STEPS = ("Our AI reads new posts from trusted deal sites around the clock and pulls out the price and the list price.",
            "A second, independent AI double-checks that the list price is real, and inflated \"was\" prices get thrown out.",
            "Our proprietary system confirms each price against its source, so nothing is guessed.",
            "Amazon prices are re-checked right before they go live.")
AI_CLOSE = {"checker": "The Deal Checker compares your product with those verified deals and with Amazon's current price "
                       "and list price.",
            "finder": "The Discount Finder matches your search with those verified deals and opens Amazon's own "
                      "discount results."}
LINKS = (("amazon-deal-checker", "Deal Checker", '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>'),
         ("amazon-discount-finder", "Discount Finder", '<path d="M3 3h10l8 8-10 10-8-8Z"/><circle cx="8" cy="8" r="1"/>'))


def enabled(cfg):
    return getattr(cfg, "tool_pages_1020", False) is True


def ai_answer(mode):
    return " ".join([AI_LEAD] + [str(i) + ". " + step for i, step in enumerate(AI_STEPS, 1)] + [AI_CLOSE[mode]])


def ai_html(mode):
    return ('<p>' + escape(AI_LEAD) + '</p><ol>' + ''.join('<li>' + escape(step) + '</li>' for step in AI_STEPS)
            + '</ol><p>' + escape(AI_CLOSE[mode]) + '</p>')


def page_spec(page):
    return page | {"title": page["name"] + " — " + LEADS[page["mode"]] + " | wowpriced"}


def template(base, page, rows):
    mode = page["mode"]
    _, _, sub, last = tool_hero.COPY[mode]
    heading = ('<section class="hero"><div><h1><span class="eyebrow">' + LEADS[mode] + '</span>'
               '<span class="htitle">{{NAME}}</span></h1><p class="sub">' + sub + '</p>')
    base = base.replace('<h1>{{NAME}}</h1><p class="intro">{{INTRO}}</p>', heading)
    if mode == "checker":
        finder_keys = {d["key"] for d in tool_hero.cloud_selection(rows)}
        different = [d for d in rows if d["key"] not in finder_keys]
        # Prefer distinct photos, filling shared slots only when supply requires it.
        selected = tool_hero.cloud_selection(different)
        if not selected:
            selected = tool_hero.cloud_selection(different + [d for d in rows if d["key"] in finder_keys])
        visual = tool_hero.finder(selected).replace('class="cloud-wrap"', 'class="cloud-wrap checker-cloud"')
    else:
        visual = tool_hero.finder(rows)
    base = base.replace('</form>', '</form><ul class="trust"><li>Free</li><li>No sign-up</li><li>' + last
                        + '</li></ul></div>' + visual + '</section><div class="tool-body">', 1)
    base = base.replace('id="tool-status"', 'id="tool-status" tabindex="-1"')
    return base.replace('</main>', '</div></main>')


def script(source):
    runtime = (TEMPLATES / 'tool-pages-1020.js').read_text()
    timing, submit = runtime.split('// TOOL_SUBMIT_1020\n')
    start = source.index('function toolStepState(')
    end = source.index('function toolPaintDeals(', start)
    source = source[:start] + timing + source[end:]
    start = source.index('async function toolSubmit(')
    end = source.index('// TOOL_PAGE_MOUNT', start)
    source = source[:start] + submit + source[end:]
    source = source.replace('toolActive.cancelled = true;',
        'toolActive.cancelled = true;\n    toolActive.stopTiming(false);')
    source = source.replace('retry < 3 && toolCurrent(task)', 'retry < 3 && toolCurrent(task) && !task.expired')
    source = source.replace('if (!toolCurrent(task)) return {items:[]};', 'if (!toolCurrent(task) || task.expired) return {items:[]};')
    # Operator 2026-10-20: "in the yellow button remove the word all".
    return source.replace("'See all Amazon discounts →'", "'See Amazon discounts →'")


def links(kind, icons=False):
    return '<nav class="home-tools ' + kind + '" aria-label="Amazon tools">' + ('' if icons else ' · ').join(
        '<a href="/' + slug + '/">' + ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" aria-hidden="true">' + icon + '</svg>' if icons else '') + label + '</a>'
        for slug, label, icon in LINKS) + '</nav>'


def support_css(css):
    # These membership-only elements are absent from support-mode home pages.
    # Keep shared selectors (especially phone tap targets) in mixed rules.
    def trim(match):
        if not re.search(r'\.(?:zerotoday|trialband)\b', match[1]):
            return match[0]
        selectors = [s for s in match[1].split(',') if not re.search(r'\.(?:zerotoday|trialband)\b', s)]
        return ','.join(selectors) + '{' + match[2] + '}' if selectors else ''
    return re.sub(r'([^{}]+)\{([^{}]*)\}', trim, css)


def publish_home(out, cfg):
    asset = out / ASSET
    asset.unlink(missing_ok=True)
    if not enabled(cfg) or not getattr(cfg, "tool_pages", {}).get("public"):
        return
    if getattr(cfg, "tool_pages_1020_home_links", False) is not True:
        return
    path = out / 'index.html'
    html = path.read_text()
    # Move the existing ticker rules as-is: this buys phone HTML gzip headroom.
    start, end = html.index('.tk{'), html.index('.card.flash{')
    css = html[start:end] + (TEMPLATES / 'home-tools.css').read_text()
    asset.write_text(css)
    version = hashlib.sha256(css.encode()).hexdigest()[:12]
    # Keep its original cascade position, including the later phone tap rules.
    html = html[:start] + '</style><link rel="stylesheet" href="/' + ASSET + '?v=' + version + '"><style>' + html[end:]
    html = html.replace('<ul class="tksr" id="tklist"></ul>', '<ul class="tksr" id="tklist"></ul>' + links('ticker-tools'))
    if cfg.tool_pages_1020_phone_links == 'chips':
        chips = links('phone-tools', icons=True).replace('<nav ', '<span role="group" ', 1).replace('</nav>', '</span>')
        html = re.sub(r'(<nav id="cattabs"[^>]*>)(.*?)(</nav>)',
                      lambda m: m[1] + m[2] + chips + m[3], html, count=1, flags=re.S)
        # Reappend after each category redraw, without giving the links filter handlers.
        html = re.sub(r'(  function renderTabs\(\) \{[\s\S]*?)(\.join\(""\);)',
                      lambda m: m[1] + '.join("") + ' + json.dumps(chips) + ';', html, count=1)
    else:
        html = html.replace('</header>', '</header>' + links('phone-tools tool-line'), 1)
    if cfg.support_enabled:
        html = re.sub(r'(<style\b[^>]*>)(.*?)(</style>)',
                      lambda m: m[1] + support_css(m[2]) + m[3], html, flags=re.S)
    path.write_text(html)


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()
                          + cfg.tool_pages_1020_phone_links.encode()
                          + b''.join((TEMPLATES / name).read_bytes() for name in
                                     ('home-tools.css', 'tool-pages-1020.js', 'tool-hero-1020.css'))).hexdigest()[:12]
