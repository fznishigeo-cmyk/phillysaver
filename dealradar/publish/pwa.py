"""Web install metadata and the separately enabled Android app integration."""

from . import _pwa_web, android_app
from ._pwa_web import ICONS, ICON_SIZES, TEMPLATES, colors, manifest_bytes


def site_version(version, cfg):
    # The unchanged web module hashes its own source: keep existing web versions
    # byte-identical while Android is off, including the manifest and icons.
    if cfg.pwa_enabled:
        version = _pwa_web.site_version(version, cfg)
    return android_app.site_version(version, cfg)


def publish(out, cfg):
    headers = out / "_headers"
    if headers.exists():
        text = headers.read_text()
        clean = text.replace(android_app.HEADERS, "").replace(android_app.WORKER_HEADERS, "")
        if clean != text:
            headers.write_text(clean)
    _pwa_web.publish(out, cfg)
    android_app.publish(out, cfg)
