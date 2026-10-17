"""Optional home product search. OFF leaves every published byte unchanged."""

import hashlib
import json
from pathlib import Path

from ..config import validated_search
from .template_slots import replace_slot

SCRIPT = Path(__file__).with_name("templates") / "search-results.js"
CONFIG = "search-config.json"


def enabled(cfg) -> bool:
    return (validated_search(cfg.search)["amazon_results"] != "off"
            and cfg.search_amazon and bool(cfg.affiliate_amazon_tag))


def script(cfg) -> str:
    settings = {"mode": validated_search(cfg.search)["amazon_results"],
                "tag": cfg.affiliate_amazon_tag}
    return SCRIPT.read_text(encoding="utf-8").replace(
        "{{SEARCH_RESULTS}}", json.dumps(settings).replace("<", "\\u003c"))


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + script(cfg).encode()
                          + json.dumps(validated_search(cfg.search), sort_keys=True).encode()
                          + Path(__file__).read_bytes()).hexdigest()[:12]


def template(html, cfg):
    if not enabled(cfg):
        return html

    def swap(old, new):
        nonlocal html
        html = replace_slot(html, old, new, template_name="index.html", slot_name="amazon-results")

    swap("  function renderSearchCount(rows) {", script(cfg) + "  function renderSearchCount(rows) {")
    swap("    amazon.hidden = !amazon.innerHTML;", "    amazon.hidden = !amazon.innerHTML;\n    queueAmazonResults(rows);")
    swap("'<div class=\"search-amazon\" data-clarity-mask=\"true\">' + searchAmazonHtml(true)",
         "'<div id=\"amazon-results\" data-clarity-mask=\"true\"></div><div class=\"search-amazon\" data-clarity-mask=\"true\">' + searchAmazonHtml(true)")
    return html


def publish(out, cfg):
    path = out / CONFIG
    if not enabled(cfg):
        path.unlink(missing_ok=True)
        return
    path.write_text(json.dumps(validated_search(cfg.search), separators=(",", ":")), encoding="utf-8")
    headers = out / "_headers"
    with headers.open("a", encoding="utf-8") as stream:
        stream.write(f"\n/{CONFIG}\n  Cache-Control: no-store\n  X-Robots-Tag: noindex\n")
