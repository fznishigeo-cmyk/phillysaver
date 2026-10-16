"""Optional current-publication identity; no retained price files or browser storage."""

import hashlib
import json
from pathlib import Path

from ..config import validated_manifest_refresh
from .template_slots import replace_slot

NAME = "publish-manifest.json"
SCRIPT = Path(__file__).with_name("templates") / "manifest-refresh.js"
HEADERS = f"\n/{NAME}\n  X-Robots-Tag: noindex\n  Cache-Control: no-cache\n"


def settings(cfg) -> dict:
    return validated_manifest_refresh(getattr(cfg, "manifest_refresh", None))


def enabled(cfg) -> bool:
    return settings(cfg)["enabled"]


def script(cfg) -> str:
    return SCRIPT.read_text(encoding="utf-8").replace(
        "{{MANIFEST_SETTINGS}}", json.dumps(settings(cfg), separators=(",", ":")))


def site_version(version: str, cfg) -> str:
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + script(cfg).encode()
                          + Path(__file__).read_bytes()).hexdigest()[:12]


def _swap(text: str, old: str, new: str) -> str:
    """Each rewrite of the home script must find its anchor exactly once."""
    return replace_slot(text, old, new, template_name="index.html", slot_name="manifest-refresh")


def template(html: str, cfg) -> str:
    if not enabled(cfg):
        return html
    start = html.index("  function fetchPublish()")
    end = html.index("  function isNewPublish(", start)
    # Retain the existing envelope/deal validator and the no-store price path.
    body = _swap(html[start:end], "function fetchPublish()", "function fetchPublicBody()")
    body = _swap(body, "return r.json().then(function (data) {", "return r.text().then(function (raw) {\n          var data = JSON.parse(raw);")
    body = _swap(body, '          clock.tag = r.headers.get("etag") || r.headers.get("last-modified") || clock.tag;\n', "")
    body = _swap(body, "          maybeReloadForNewVersion(meta);\n", "")
    body = _swap(body, "key: dataKey(meta) };", 'key: dataKey(meta), raw: raw, tag: r.headers.get("etag") || r.headers.get("last-modified") };')
    html = html[:start] + script(cfg).replace("{{PUBLIC_BODY}}", body) + html[end:]
    html = _swap(html, "function isNewPublish(pub) { return !!pub &&", "function isNewPublish(pub) { return manifestCurrent(pub) &&")
    html = _swap(html, "  function merge(pub) {", "  function merge(pub) {\n    if (!manifestCurrent(pub)) return Promise.resolve();")
    html = _swap(html, "function setToken(t) {", "function setToken(t) { invalidateManifest();")
    html = _swap(html, "function dropSession() {", "function dropSession() {\n    invalidateManifest();")
    html = _swap(html, "if (auth.checking) memberDeals(false).then(showDeals);", "if (auth.checking) memberDeals(false).then(function (full) {\n"
                        "        if (manifestCurrent(pub)) showDeals(full);\n      });")
    # A bfcache restore may start a new caller while the old response is pending.
    # Only the caller in the current page lifetime may adopt or clear its slot.
    start = html.index("  function loadData()")
    end = html.index('  document.getElementById("load-retry")', start)
    load = _swap(html[start:end], "    loadRequest = fetchPublish()", "    var loadEpoch = manifestEpoch;\n    loadRequest = fetchPublish()")
    load = _swap(load, "      if (loadedOnce) return merge(pub);", "      if (!manifestCurrent(pub)) return;\n      if (loadedOnce) return merge(pub);")
    load = _swap(load, "      if (!loadedOnce) {", "      if (loadEpoch !== manifestEpoch) return;\n      if (!loadedOnce) {")
    load = _swap(load, "      retry.disabled = false;", "      if (loadEpoch !== manifestEpoch) return;\n      retry.disabled = false;")
    html = html[:start] + load + html[end:]
    start = html.index("  function refreshOnReturn()")
    end = html.index('  window.addEventListener("pageshow"', start)
    refresh = _swap(html[start:end], "    returnRefresh = fetchPublish()", "    var returnEpoch = manifestEpoch;\n    returnRefresh = fetchPublish()")
    refresh = _swap(refresh, "      render();", "      if (returnEpoch === manifestEpoch) render();")
    refresh = _swap(refresh, "returnRefresh = null;", "if (returnEpoch === manifestEpoch) returnRefresh = null;")
    html = html[:start] + refresh + html[end:]
    start = html.index("  // While a drop waits,")
    end = html.index("  // How old the list", start)
    return (html[:start] + "  // While a drop waits, revalidate public identity at the existing cadence.\n"
            "  function poll() {\n    fetchPublish().then(merge).catch(noop);\n  }\n" + html[end:])


def publish(out: Path, cfg) -> None:
    if not enabled(cfg):
        return
    body = (out / "deals.json").read_bytes()
    meta = json.loads(body)["meta"]
    doc = {"schema": 1, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
           "published_at": meta["published_at"], "site_version": meta["site_version"]}
    (out / NAME).write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
