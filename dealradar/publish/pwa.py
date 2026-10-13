"""Install metadata and a quiet footer option. Offers always stay online."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent / "templates"
ICONS = Path(__file__).resolve().parent / "assets" / "pwa"
ICON_SIZES = {"icon-192.png": 192, "icon-512.png": 512,
              "icon-maskable-512.png": 512, "apple-touch-icon.png": 180}
_THEME_META = re.compile(r'<meta\b[^>]*\bname="theme-color"[^>]*>\n?', re.I)
_BLOCK = re.compile(r'<!-- wp-pwa-(head|footer) -->.*?<!-- /wp-pwa-\1 -->\n?', re.S)
_HEADERS = ("\n/manifest.webmanifest\n  Content-Type: application/manifest+json\n"
            "  Cache-Control: public, max-age=3600, must-revalidate\n"
            "\n/pwa-icons/*\n  Cache-Control: public, max-age=86400\n")


def colors() -> tuple[str, str, str]:
    """The existing home header's light/dark tokens and light page background."""
    home = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    headers = re.findall(r'\bheader\s*\{[^}]*--card:\s*(#[0-9a-f]{6})', home)
    background = re.search(r':root\s*\{[^}]*--bg:\s*(#[0-9a-f]{6})', home).group(1)
    return headers[0], headers[1], background


def manifest_bytes(cfg) -> bytes:
    light, _dark, background = colors()
    manifest = {"name": cfg.pwa_name, "short_name": cfg.pwa_short_name,
                "theme_color": light, "background_color": background,
                "display": "standalone", "start_url": "/", "id": "/", "scope": "/",
                "icons": [{"src": f"/pwa-icons/{name}", "sizes": f"{size}x{size}",
                           "type": "image/png",
                           "purpose": "maskable" if "maskable" in name else "any"}
                          for name, size in ICON_SIZES.items() if size != 180]}
    return (json.dumps(manifest, indent=2) + "\n").encode()


def site_version(version: str, cfg) -> str:
    data = version.encode() + manifest_bytes(cfg) + Path(__file__).read_bytes()
    for name in ("pwa.js", "pwa-footer.html", "pwa.css"):
        data += (TEMPLATES / name).read_bytes()
    for name in ICON_SIZES:
        data += (ICONS / name).read_bytes()
    return hashlib.sha256(data).hexdigest()[:12]


def _header_colors(page: str, light: str, dark: str) -> tuple[str, str]:
    # Older information pages use the root card token for their header.
    if re.search(r'\bheader\s*\{\s*background:var\(--card\)', page):
        cards = re.findall(r':root\s*\{[^}]*--card:\s*(#[0-9a-f]{6})', page)
        return cards[0], cards[1]
    return light, dark


def publish(out: Path, cfg) -> None:
    """Run after all page writers, before tracking. Disabled adds no bytes."""
    if not cfg.pwa_enabled:
        (out / "manifest.webmanifest").unlink(missing_ok=True)
        shutil.rmtree(out / "pwa-icons", ignore_errors=True)
        return
    (out / "manifest.webmanifest").write_bytes(manifest_bytes(cfg))
    (out / "pwa-icons").mkdir(exist_ok=True)
    for name in ICON_SIZES:
        shutil.copyfile(ICONS / name, out / "pwa-icons" / name)
    light, dark, _background = colors()
    css_template = (TEMPLATES / "pwa.css").read_text(encoding="utf-8")
    script = (TEMPLATES / "pwa.js").read_text(encoding="utf-8")
    footer = ('<!-- wp-pwa-footer -->\n'
              + (TEMPLATES / "pwa-footer.html").read_text(encoding="utf-8")
              + '<!-- /wp-pwa-footer -->\n')
    for path in sorted(out.rglob("*.html")):
        page = _BLOCK.sub("", path.read_text(encoding="utf-8"))
        page_light, page_dark = _header_colors(page, light, dark)
        css = css_template.replace("{{PWA_LIGHT}}", page_light).replace("{{PWA_DARK}}", page_dark)
        head = ('<!-- wp-pwa-head -->\n<link rel="manifest" href="/manifest.webmanifest">\n'
                '<link rel="apple-touch-icon" sizes="180x180" href="/pwa-icons/apple-touch-icon.png">\n'
                f'<meta name="theme-color" content="{page_light}" media="(prefers-color-scheme: light)">\n'
                f'<meta name="theme-color" content="{page_dark}" media="(prefers-color-scheme: dark)">\n'
                '<meta name="apple-mobile-web-app-capable" content="yes">\n'
                '<meta name="apple-mobile-web-app-status-bar-style" content="default">\n'
                f'<style>{css}</style>\n<script>{script}</script>\n<!-- /wp-pwa-head -->\n')
        page = _THEME_META.sub("", page)
        at = page.find("<!-- wp-tracking -->")
        if at < 0:
            at = page.find("</head>")
        page = page[:at] + head + page[at:]
        page = page.replace("</footer>", footer + "</footer>", 1)
        path.write_text(page, encoding="utf-8")
    headers = out / "_headers"
    headers.write_text(headers.read_text(encoding="utf-8").removesuffix(_HEADERS) + _HEADERS,
                       encoding="utf-8")
