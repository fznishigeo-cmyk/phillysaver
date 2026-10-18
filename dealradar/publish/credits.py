"""Resolve internal source IDs to names for public deal credits."""

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from ..config import load_sources


_CSS_TOKENS = re.compile(
    r'''/\*.*?\*/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|'''
    r'''url\((?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\\.|[^)"'\\])*\)|'''
    r'''\\(?:[0-9a-f]{1,6}(?:\r\n|[ \t\r\n\f])?|.)|\s+''', re.I | re.S)


def _compact_css(css):
    """Collapse whitespace tokens, retaining strings, URLs, comments and escapes."""
    return _CSS_TOKENS.sub(lambda m: ' ' if m[0].isspace() else m[0], css)


def home_template(html, cfg):
    """Plain attribution and final gone spacing; the linked template stays exact."""
    if cfg.credit_links:
        return html
    from .template_slots import replace_slot

    changes = (
        ('return !c.url ? "via " + esc(c.name)\n'
         '        : \'via <a href="\' + esc(c.url) + \'" rel="noopener">\' + esc(c.name) + "</a>";',
         'return "via " + esc(c.name);'),
        ('function placeGone(rows) {\n    var out = [], pending = [], nextGone = 8;',
         'function placeGone(rows, every) {\n    var out = [], pending = [], nextGone = every ? every - 1 : 8;'),
        ('nextGone = out.length + 7;', 'nextGone = out.length + (every || 8) - 1;'),
        # Run after sorting, filters, allure and rotation, before pagination.
        ('    browseRows = spotlight.concat(rows);',
         '    rows = placeGone(rows, 10);\n    browseRows = spotlight.concat(rows);'),
    )
    for old, new in changes:
        html = replace_slot(html, old, new, template_name="index.html", slot_name="plain-credits")
    return html


def checker_template(script, cfg):
    if cfg.credit_links:
        return script
    from .template_slots import replace_slot

    old = '''        if (typeof c.url === "string" && /^https?:\\/\\//i.test(c.url)) {
          const a = text("a", c.name);a.href = c.url;a.rel = "noopener";via.append(a);
        } else via.append(text("span", c.name));'''
    return replace_slot(script, old, '        via.append(text("span", (i ? "via " : "") + c.name));',
                        template_name="checker.js", slot_name="plain-credits")


def tracking_template(script, cfg):
    """Plain credits have neither a click target nor a credit-click event."""
    if cfg.credit_links:
        return script
    script = script.replace('    credit_click: { ga4Only: true },\n', '')
    script = re.sub(r'    if \(name === "credit_click"\) \{.*?\n    }\n', '', script, count=1, flags=re.S)
    return re.sub(r'  if \(on.ga4 && document.addEventListener\).*?\n  }\);\n', '', script, count=1, flags=re.S)


def publish(out, cfg):
    """Trim the final home CSS to retain phone headroom with plain attribution."""
    if cfg.credit_links:
        return

    def rule(match):
        selectors = match[1].split(',')
        kept = [s for s in selectors if '.credit a' not in s and '.detail-sources a' not in s]
        if len(kept) == len(selectors):
            return match[0]
        return ','.join(kept) + '{' + match[2] + '}' if kept else ''

    path = out / 'index.html'
    html = path.read_text(encoding='utf-8')
    html = re.sub(r'<style>(.*?)</style>', lambda m: '<style>' + _compact_css(re.sub(
        r'([^{}]+)\{([^{}]*)\}', rule, m[1])) + '</style>', html, flags=re.S)
    path.write_text(html, encoding='utf-8')


def site_version(version, cfg):
    if cfg.credit_links:
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]


def source_sightings(deal: dict) -> list[dict]:
    """One public credit per partner, even across multiple posts/channels."""
    seen, sightings = set(), []
    for sighting in deal.get("sightings") or []:
        name = sighting.get("source_name") or ""
        if name not in seen:
            seen.add(name)
            sightings.append(sighting)
    return sightings


@lru_cache(maxsize=1)
def _display_names() -> dict[str, str]:
    primary, backup = load_sources()
    return {s.name: s.display_name or s.name for s in primary + backup}


def source_display_name(name: str) -> str:
    """Keep unknown/historical source IDs usable as credits."""
    return _display_names().get(name, name)


@lru_cache(maxsize=1)
def _deal_source_hosts() -> set[str]:
    """Known source/blog hosts, including paused sources; stores can also have feeds."""
    primary, backup = load_sources()
    retailers = {"woot.com", "meh.com", "morningsave.com", "sidedeal.com",
                 "dealgenius.com", "dailysteals.com", "tanga.com", "1sale.com"}
    return {(urlsplit(s.site or s.url).hostname or "").lower().removeprefix("www.")
            for s in primary + backup} - retailers - {""}


def link_policy(amazon_tag="", overrides=None, *, sovrn_key="", sovrn_enabled=False,
                competitor_tracker_hosts=(), ebay_campid=""):
    """Resolve source policy, then freeze affiliate options for the caller."""
    from .link_facts import LinkPolicy

    return LinkPolicy(amazon_tag=amazon_tag, overrides=overrides or {},
                      sovrn_key=sovrn_key, sovrn_enabled=sovrn_enabled,
                      competitor_tracker_hosts=competitor_tracker_hosts, ebay_campid=ebay_campid,
                      source_names=_display_names(), source_hosts=(
                          _deal_source_hosts() if sovrn_enabled and re.fullmatch(r"[0-9a-f]{32}", sovrn_key)
                          else frozenset()))
