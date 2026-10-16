"""Opt-in TWA context and navigation-only offline fallback; no price computation."""

import hashlib
import html
import json
import re
import shutil
from pathlib import Path

from ..verify import is_amazon_deal

TEMPLATES = Path(__file__).with_name("templates")
HEADERS = ("\n/.well-known/assetlinks.json\n  Content-Type: application/json\n"
           "  Cache-Control: public, max-age=300\n")
WORKER_HEADERS = ("\n/sw.js\n  Content-Type: application/javascript\n  Cache-Control: no-cache\n"
                  "\n/offline.html\n  X-Robots-Tag: noindex\n  Cache-Control: no-cache\n")
BLOCK = re.compile(r'<!-- wp-android -->.*?<!-- /wp-android -->\n?', re.S)


def enabled(cfg):
    return cfg.pwa_android["enabled"]


def amazon(deal):
    return deal.get("amazon") is True or is_amazon_deal(deal)


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    data = version.encode() + Path(__file__).read_bytes() + json.dumps(cfg.pwa_android, sort_keys=True).encode()
    for name in ("android.js", "android.css", "android-checker.js", "android-sw.js", "android-offline.html"):
        data += (TEMPLATES / name).read_bytes()
    return hashlib.sha256(data).hexdigest()[:12]


def head(cfg):
    from .checker import js_rules

    script = (TEMPLATES / "android.js").read_text().replace("{{PACKAGE}}", json.dumps(cfg.pwa_android["package"]))
    script = script.replace("{{LIMITED}}", json.dumps(cfg.pwa_android["amazon_in_app"] == "limited"))
    script = script.replace("{{AMAZON_HOST}}", json.dumps(js_rules()["canonical"]["amazon_host"]))
    return ('<!-- wp-android -->\n<style>' + (TEMPLATES / "android.css").read_text()
            + '</style>\n<script>' + script + '</script>\n<!-- /wp-android -->\n')


def checker_script(script, cfg):
    """Only the enabled app changes the reader bytes; ordinary checks stay private."""
    if not enabled(cfg):
        return script
    helpers = (TEMPLATES / "android-checker.js").read_text()
    script = script.replace('  function show(entry, parts) {', '  function show(entry, parts) {\n'
                            '    if (wpAndroidLimited() && entry.store === "amazon")\n'
                            '      entry = {kind:"link_only", item:entry.item, deal_path:entry.deal_path};')
    script = script.replace('"Open the link first, then paste the full product address"',
                            '"Open the link first, then paste the full product address" + '
                            '(wpAndroidApp() ? " Open it in Chrome, then share the product page from there." : "")')
    script = script.replace('new URLSearchParams(location.search).get("u")', 'wpAndroidSharedURL(location.search)')
    script = script.replace('  if (prefill) {input.value = prefill;run();}',
                            '  if (prefill) {input.value = prefill;run();}\n'
                            '  else if (["text", "url", "title"].some(k => new URLSearchParams(location.search).has(k))) notFound("");')
    return helpers + script


def product_content(content, page, cfg, deal_paths, source_facts=None):
    if not enabled(cfg):
        return content
    sources = [source_facts.get(key) for key in page.get("offers", {})] if source_facts else []
    rows = sources if sources and all(sources) else (
        [row for row in sources if row] + page["checks"] + ([page["live"]] if page["live"] else []))
    rows = rows or [page]
    if not any(amazon(row) for row in rows):
        return content
    if all(amazon(row) for row in rows):
        current = page["live"]
        keys = ([current["key"][:8]] if current else []) + [hashlib.sha256(key.encode()).hexdigest()[:8]
                                                                for key in sorted(page["offers"])]
        path = next((deal_paths[key] for key in keys if key in deal_paths), "")
        note = '<p class="app-amazon-note">Amazon price history is on wowpriced.com in your browser.</p>'
        if path:
            note += f'<p class="app-amazon-note"><a href="{html.escape(path, quote=True)}">See the deal</a></p>'
        return '<div data-app-amazon-history>' + content + '</div>' + note
    # Mixed histories also contain Amazon observations: hide those aggregate
    # facts in app context while keeping the other store's current offer.
    at = content.index('<p class="detail-store">')
    content = '<div data-app-amazon-history>' + content[:at] + '</div>' + content[at:]
    content = re.sub(r'(<figure class="price-history">.*?</figure>)', r'<div data-app-amazon-history>\1</div>', content, flags=re.S)
    return re.sub(r'(<h2>Deals we found</h2><ul>.*?</ul>)', r'<div data-app-amazon-history>\1</div>', content, flags=re.S)


def publish(out, cfg):
    asset = out / ".well-known" / "assetlinks.json"
    headers = out / "_headers"
    current_headers = headers.read_text() if headers.exists() else ""
    rules = current_headers.replace(HEADERS, "").replace(WORKER_HEADERS, "")
    if not enabled(cfg):
        for name in ("offline.html", "sw.js"):
            (out / name).unlink(missing_ok=True)
        asset.unlink(missing_ok=True)
        if asset.parent.is_dir() and not any(asset.parent.iterdir()):
            asset.parent.rmdir()
        if current_headers != rules:
            headers.write_text(rules)
        return
    # Android can be enabled separately from the web install prompt.
    from ._pwa_web import ICONS, ICON_SIZES
    (out / "pwa-icons").mkdir(exist_ok=True)
    for name in ICON_SIZES:
        shutil.copyfile(ICONS / name, out / "pwa-icons" / name)
    fingerprints = cfg.pwa_android["sha256_cert_fingerprints"]
    asset.unlink(missing_ok=True)  # replace even a stale symlink with a regular file
    if fingerprints:
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_text(json.dumps([{"relation": ["delegate_permission/common.handle_all_urls"],
            "target": {"namespace": "android_app", "package_name": cfg.pwa_android["package"],
                       "sha256_cert_fingerprints": fingerprints}}], separators=(",", ":")))
        rules += HEADERS
    elif asset.parent.is_dir() and not any(asset.parent.iterdir()):
        asset.parent.rmdir()
    headers.write_text(rules + WORKER_HEADERS)
    block = head(cfg)
    for path in sorted(out.rglob("*.html")):
        if path == out / "offline.html":
            continue
        page = BLOCK.sub("", path.read_text())
        marker = "<!-- wp-pwa-head -->\n"
        at = page.find(marker)
        if at >= 0:
            at += len(marker)
        if at < 0:
            at = page.find("<!-- wp-tracking -->")
        if at < 0:
            at = page.find("</head>")
        page = page[:at] + block + page[at:]
        for name, result in (("loadGoogle()", ""), ("showSupportAsk(success)", " false")):
            start = '  function ' + name + ' {\n'
            guard = start + '    if (wpAndroidApp()) return' + result + ';\n'
            if guard not in page:
                page = page.replace(start, guard)
        if path == out / "check" / "index.html" and "worker-src 'self'" not in page:
            page = page.replace("connect-src 'self';", "connect-src 'self'; worker-src 'self';")
        path.write_text(page)
    # Written after the global analytics passes: the fallback is self-contained,
    # has no price, and never attempts to load trackers or other online assets.
    (out / "offline.html").write_text((TEMPLATES / "android-offline.html").read_text())
    doc = json.loads((out / "deals.json").read_text())
    version = doc["meta"]["site_version"]
    (out / "sw.js").write_text((TEMPLATES / "android-sw.js").read_text().replace("{{VERSION}}", version))
