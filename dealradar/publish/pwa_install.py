"""Optional header install control, preserving the original web PWA when off."""

import hashlib
import re
from pathlib import Path

TEMPLATES = Path(__file__).with_name("templates")
FILES = ("pwa-install.js", "pwa-install.css", "pwa-install-button.html", "pwa-install-help.html")
_SUBSCRIBE = re.compile(r'<a\b[^>]*\bid="subscribe"[^>]*>.*?</a>', re.S)


def enabled(cfg):
    return cfg.pwa_enabled and cfg.pwa_install_button


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    data = version.encode() + Path(__file__).read_bytes() + str(cfg.pwa_install_phone_delay_s).encode()
    for name in FILES:
        data += (TEMPLATES / name).read_bytes()
    return hashlib.sha256(data).hexdigest()[:12]


def publish(out, cfg):
    if not enabled(cfg):
        return
    script, css, button, help_sheet = [(TEMPLATES / name).read_text() for name in FILES]
    script = script.replace("phoneDelay = 180000", f"phoneDelay = {cfg.pwa_install_phone_delay_s * 1000}", 1)
    legacy_script = (TEMPLATES / "pwa.js").read_text()
    legacy_help = (TEMPLATES / "pwa-footer.html").read_text().split("\n", 1)[1]
    for path in sorted(out.rglob("*.html")):
        page = path.read_text()
        page = page.replace(f"<script>{legacy_script}</script>",
                            f"<style>{css}</style>\n<script>{script}</script>")
        page = page.replace(legacy_help, help_sheet)
        if 'id="pwa-header-slot"' not in page:
            page = _SUBSCRIBE.sub(lambda match: '<span class="pwa-header-actions">'
                                  + button.strip() + match.group() + '</span>', page, count=1)
        path.write_text(page)
