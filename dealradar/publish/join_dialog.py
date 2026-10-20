"""Lazy header dialog; isolated shared /join card avoids duplicate form IDs."""

import hashlib
import json
import re
from pathlib import Path

from . import tool_hero

TEMPLATES = Path(__file__).with_name("templates")
ASSETS = ("join-dialog.js", "join-dialog.json", "join-loader.js")


def publish(out, cfg):
    for name in ASSETS:
        (out / name).unlink(missing_ok=True)
    if not tool_hero.enabled(cfg):
        return
    from .site import JOIN_FORM, support_script

    card = (TEMPLATES / "join-card.html").read_text().replace("{{JOIN_FORM}}", JOIN_FORM).replace(
        "{{JOIN_HEADING}}", "h2").replace("{{JOIN_CLOSE}}",
        '<button class="joinclose" type="button" aria-label="Close sign-up" id="dialog-close">×</button>')
    hub = (TEMPLATES / "hub.html").read_text()
    brand = re.search(r'<p class="brand">.*?</p>', hub)[0]
    card = card.replace('<h2 id="join-title">Join wowpriced</h2>', brand + '<h2 id="join-title">Join free</h2>')
    card = card.replace("The day's best deals in one email. Free.", "The best verified deals in your inbox. No spam.")
    card = card.replace('<span aria-hidden="true">@</span>', '<span aria-hidden="true">✉</span>')
    card = card.replace('We use your email only to send deals. Unsubscribe anytime. See our <a href="/privacy.html">privacy policy</a>.',
                        'Free · unsubscribe anytime')
    # Reuse both authentication and subscription handlers verbatim. Only the
    # card's page lifecycle/copy changes inside this same-origin dialog frame.
    script = support_script()
    script = re.sub(r'var onJoinPage = [^\n]+', 'var onJoinPage = true;', script)
    script = script.replace('email ? "What\'s your email?" : "Join wowpriced"',
                            'email ? "What\'s your email?" : "Join free"')
    script = script.replace('theme: "filled_black"', 'theme: "outline"').replace(
        'shape: "rectangular"', 'shape: "pill"')
    script = script.replace('if (onJoinPage) location.replace("/");',
                            'if (onJoinPage) parent.postMessage({joinDialog:"close"}, location.origin);')
    script = script.replace('fetch("/deals.json?t=" + Date.now(), {cache: "no-store"})\n'
                            '      .then(function (r) { return r.json(); }).then(function (d) { refresh(d.meta || {}); }).catch(function () {});',
                            'refresh(' + json.dumps(dict(api_url=cfg.paywall_api_url,
                                google_client_id=cfg.paywall_google_client_id), separators=(",", ":")).replace("<", "\\u003c") + ');')
    page = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="robots" content="noindex,nofollow"><title>Join free</title><style>'
            + (TEMPLATES / 'join-card.css').read_text() + '\n'
            + (TEMPLATES / 'join-dialog.css').read_text() + '</style><body>' + card + '<script>'
            + script + '\n' + (TEMPLATES / 'join-dialog-frame.js').read_text() + '</script></body></html>')
    (out / "join-dialog.json").write_text(json.dumps({"html": page.replace("location.origin", "parent.location.origin")}))
    module = (TEMPLATES / "join-dialog.js").read_text()
    (out / "join-dialog.js").write_text(module)
    version = hashlib.sha256((module + page).encode()).hexdigest()[:12]
    bootstrap = (TEMPLATES / 'join-dialog-loader.js').read_text().replace('__VERSION__', version)
    (out / 'join-loader.js').write_text(bootstrap)
    # Only the three requested header links gain enhancement. /join and all
    # other Join links retain the normal page flow.
    for path in (out / 'index.html', *(out / slug / 'index.html' for slug in (
            'amazon-deal-checker', 'amazon-discount-finder'))):
        if not path.exists():
            continue
        html = path.read_text()
        if 'href="/join"' in html:
            path.write_text(html.replace('</body>', '<script defer src="/join-loader.js?v=' + version + '"></script></body>'))
