"""Crawlable inventory from stored facts, without inference or requests."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import unicodedata
from html import escape
from pathlib import Path

from ..product_images import link_key

TEMPLATES = Path(__file__).resolve().parent / "templates"
_BLOCK = re.compile(r"<!-- wp-inventory -->.*?<!-- /wp-inventory -->\n?", re.S)
_HEADERS = "\n/products/*\n  Cache-Control: no-cache\n"


def enabled(cfg) -> bool:
    return cfg.traffic_hub_pagination_enabled or cfg.traffic_product_families_enabled


def site_version(version: str, cfg) -> str:
    knobs = (cfg.traffic_hub_pagination_enabled, cfg.traffic_product_families_enabled,
             cfg.traffic_product_family_min_offers)
    data = (version + str(knobs)).encode() + Path(__file__).read_bytes()
    for name in ("inventory.js", "inventory.css"):
        data += (TEMPLATES / name).read_bytes()
    return hashlib.sha256(data).hexdigest()[:12]


def page_path(root: str, number: int) -> str:
    return root if number == 1 else f"{root}page/{number}/"


def deal_actions(script: str) -> str:
    """Extend the shared handler only in builds with inventory enabled."""
    script = script.replace("  var path = location.pathname;",
                            r'  var path = location.pathname.replace(/\/page\/(?:[2-9]\d*|1\d+)\/$/, "/");')
    return script.replace("  var method = ", r'  var method = /^\/products\/[^/]+\/$/.test(path) ? "product_family"' +
                          '\n    : ').replace("under-(10|25|50)", r"under-\d+")


def pagination(root: str, number: int, count: int, family: str) -> str:
    if count <= 1:
        return ""
    def link(n, label, rel=""):
        return (f'<a href="{escape(page_path(root, n))}" data-hub-page="{family}"'
                + (f' rel="{rel}"' if rel else "") + f'>{label}</a>')
    links = link(1, "First page") if number > 2 else ""
    if number > 1:
        links += link(number - 1, "Previous", "prev")
    links += (f'<a href="{escape(page_path(root, number))}" aria-current="page">'
              f'Page {number} of {count}</a>')
    if number < count:
        links += link(number + 1, "Next", "next")
    return '<nav class="inventory-nav" aria-label="Deal pages">' + links + '</nav>'


def _exact(value: str) -> str:
    # Preserve punctuation, numbers, token order, sizes and packs. No stemming,
    # stopword removal, substring/model guesses or fuzzy title matching.
    return " ".join(unicodedata.normalize("NFC", str(value or "")).split()).casefold()


def product_families(cfg, live: list[dict], source: list[dict]) -> tuple[dict, dict]:
    """One exact title/brand (or title/ASIN) group; duplicate listings count once.

    Stored unit/model facts further separate variants. Source rows supply only
    identity; every published card and price comes from the public live wall.
    """
    from .seo import deal_path, store_slug

    if not cfg.traffic_product_families_enabled:
        return {}, {}
    raw = {d["dedupe_key"]: d for d in source}
    groups = {}
    for d in sorted(live, key=lambda d: d["key"]):
        facts = raw[d["dedupe_key"]]
        item, brand = _exact(d.get("item")), _exact(d.get("brand"))
        asin = str(facts.get("asin") or "").strip().upper()
        asin = asin if re.fullmatch(r"[A-Z0-9]{10}", asin) else ""
        price = d.get("final_price")
        if (d.get("status") != "alive" or d.get("locked") or not item or item == brand
                or not (brand or asin) or facts.get("link_shared")
                or str(facts.get("currency") or "USD").upper() != "USD"
                or price is None or not math.isfinite(float(price)) or float(price) < 0
                or {"range", "per_unit"} & set(d.get("flags") or [])):
            continue
        identity = ("brand", brand) if brand else ("asin", asin)
        key = (*identity, item, _exact(facts.get("model")), _exact(facts.get("quantity_or_unit")))
        store = store_slug(str(d.get("store") or ""))
        if not store:
            continue
        try:
            product = asin or link_key(str(facts.get("canonical_url") or ""))
        except ValueError:
            product = ""
        # Different sources, prices, promo codes or tracking URLs do not make
        # another offer for the same store/product/physical condition.
        condition = tuple(sorted({"used", "open_box", "refurbished"} & set(d.get("conditions") or [])))
        offer = (store, product or item, condition)
        group = groups.setdefault(key, {"offers": {}, "members": []})
        previous = group["offers"].get(offer)
        if previous is None or (float(price), d["key"]) < (float(previous["final_price"]), previous["key"]):
            group["offers"][offer] = d
        group["members"].append(d)
    pages, members = {}, {}
    for key, group in sorted(groups.items()):
        rows = sorted(group["offers"].values(), key=lambda d: d["key"])
        if len(rows) < max(2, cfg.traffic_product_family_min_offers):
            continue
        digest = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:12]
        path = deal_path(key[2], digest).replace("/deal/", "/products/", 1)
        named = min(group["members"], key=lambda d: (str(d["item"]), d["key"]))
        name = " ".join(str(named["item"]).split())
        if named.get("brand") and not _exact(name).startswith(_exact(named["brand"]) + " "):
            name = " ".join(str(named["brand"]).split()) + " " + name
        for field in ("model", "quantity_or_unit"):
            fact = " ".join(str(raw[named["dedupe_key"]].get(field) or "").split())
            if fact and _exact(fact) not in _exact(name):
                name += f" ({fact})"
        pages[path] = name, rows
        for d in group["members"]:
            members[d["key"]] = path, name
    return pages, members


def family_link(d: dict, members: dict) -> str:
    if d.get("status") != "alive" or d["key"] not in members:
        return ""
    path, name = members[d["key"]]
    return ('<nav class="inventory-nav" aria-label="Offers for this product">'
            f'<a href="{escape(path)}" data-product-family>More {escape(name)} offers</a></nav>')


def family_intro(rows: list[dict]) -> str:
    from .seo import store_slug

    names = {}
    for name in sorted({str(d["store"]) for d in rows}):
        names.setdefault(store_slug(name), name)
    stores = sorted(names.values(), key=lambda name: (name.casefold(), name))
    lowest = min(float(d["final_price"]) for d in rows)
    return (f'{len(rows)} live offers. Lowest current price: ${lowest:.2f} USD. '
            f'Stores: {", ".join(stores)}. Conditions and checkout requirements are shown on each offer.')


def remove_stale(out: Path, emitted: list[str]) -> None:
    keep = set(emitted)
    for folder in (out / "products").glob("*"):
        if folder.is_dir() and f"/{folder.relative_to(out).as_posix()}/" not in keep:
            shutil.rmtree(folder)
    for root in ("deals", "stores", "products"):
        for folder in (out / root).glob("*/page/*"):
            if folder.is_dir() and f"/{folder.relative_to(out).as_posix()}/" not in keep:
                shutil.rmtree(folder)
        for folder in (out / root).glob("*/page"):
            if not any(folder.iterdir()):
                folder.rmdir()
    products = out / "products"
    if products.exists() and not any(products.iterdir()):
        products.rmdir()


def publish(out: Path, cfg) -> None:
    """Only pages with the new navigation need its scoped CSS and tiny script."""
    active = enabled(cfg)
    head = ""
    if active:
        css = (TEMPLATES / "inventory.css").read_text(encoding="utf-8")
        script = (TEMPLATES / "inventory.js").read_text(encoding="utf-8")
        head = '<!-- wp-inventory --><style>' + css + '</style>\n<script>' + script + '</script><!-- /wp-inventory -->\n'
    for path in sorted(out.rglob("*.html")):
        original = path.read_text(encoding="utf-8")
        page = _BLOCK.sub("", original)
        if active and 'class="inventory-nav"' in page:
            anchor = "<!-- wp-tracking -->" if "<!-- wp-tracking -->" in page else "</head>"
            page = page.replace(anchor, head + anchor, 1)
        if page != original:
            path.write_text(page, encoding="utf-8")
    headers = out / "_headers"
    current = headers.read_text(encoding="utf-8").replace(_HEADERS, "")
    headers.write_text(current + (_HEADERS if cfg.traffic_product_families_enabled else ""), encoding="utf-8")
