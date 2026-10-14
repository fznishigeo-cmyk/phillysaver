"""Offline checks of emitted HTML, and compact substantive-page fingerprints."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit
from xml.etree import ElementTree as ET

from ..util import iso, parse_iso

log = logging.getLogger("dealradar.publish.seo")
FAMILIES = ("home", "category", "store", "price", "best_today", "deal", "product_family")
_FACT_META = "seo_page_facts"
_MAX_FACT_URLS = 50_000
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def page_family(path: str) -> str:
    path = urlsplit(path).path
    if path in ("/", "/index.html"):
        return "home"
    if re.fullmatch(r"/deal/[^/]+/", path):
        return "deal"
    pagination = r"(?:page/(?:[2-9]\d*|1\d+)/)?"
    if re.fullmatch(r"/products/[^/]+/" + pagination, path):
        return "product_family"
    if re.fullmatch(r"/stores/[^/]+/" + pagination, path):
        return "store"
    if re.fullmatch(r"/deals/under-\d+/" + pagination, path):
        return "price"
    if re.fullmatch(r"/deals/[^/]+/" + pagination, path):
        return "category"
    return "best_today" if path == "/best-deals-today/" else ""


def minimums(cfg) -> dict[str, int]:
    return {"home": max(1, cfg.seo_home_min_items),
            "category": max(1, cfg.seo_hub_min_items),
            "store": max(1, cfg.seo_store_page_min_deals),
            "price": max(1, cfg.seo_price_page_min_deals),
            "best_today": max(1, cfg.seo_best_today_min_items), "deal": max(1, cfg.seo_deal_page_min_items),
            "product_family": max(2, cfg.traffic_product_family_min_offers)}


def _text(s: str) -> str:
    return " ".join(s.split())


# Google's merchant listings reject a Product name over 150 characters.
NAME_LIMIT = 150


def word_limit(text: str, limit: int) -> str:
    """At most ``limit`` characters, cut at a word with an ellipsis."""
    text = _text(str(text))
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0] if " " in text[:limit - 1] else text[:limit - 1]
    return cut.rstrip(" ,;:-") + "…"


@dataclass
class _Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def has(self, cls: str) -> bool:
        return cls in (self.attrs.get("class") or "").split()

    def hidden(self) -> bool:
        return ("hidden" in self.attrs or self.attrs.get("aria-hidden") == "true"
                or bool(re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)",
                                  self.attrs.get("style") or "", re.I)))

    def nodes(self):
        if self.hidden():
            return
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.nodes()

    def text(self) -> str:
        if self.hidden() or self.tag in {"script", "style", "svg"}:
            return ""
        return _text("".join(c.text() if isinstance(c, _Node) else c for c in self.children))


class Page(HTMLParser):
    """Read actual text and links, rather than trusting a second copy of deal data."""

    def __init__(self, html: str, path: str):
        super().__init__(convert_charrefs=True)
        self.path, self.root = path, _Node("")
        self.stack = [self.root]
        self.hrefs, self.schemas = [], []
        self.indexable, self.invalid_schema = True, False
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        node = _Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag in {"a", "link"} and attrs.get("href"):
            self.hrefs.append(attrs["href"])
        if tag == "meta" and (attrs.get("name") or "").lower() in {"robots", "googlebot"}:
            if {"noindex", "none"} & set(re.split(r"[,\s]+", (attrs.get("content") or "").lower())):
                self.indexable = False
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                node = self.stack[i]
                if tag == "script" and node.attrs.get("type") == "application/ld+json":
                    try:
                        self.schemas.append(json.loads("".join(node.children)))
                    except (ValueError, TypeError):
                        self.invalid_schema = True
                del self.stack[i:]
                return

    def handle_data(self, data):
        node = self.stack[-1]
        if node.tag not in {"script", "style"} or node.attrs.get("type") == "application/ld+json":
            node.children.append(data)

    def first(self, tag="", cls="", ident="", scope=None):
        return next((n for n in (scope or self.root).nodes()
                     if (not tag or n.tag == tag) and (not cls or n.has(cls))
                     and (not ident or n.attrs.get("id") == ident)), None)

    @property
    def scope(self):
        return (self.first(ident="content") if page_family(self.path) == "home" else
                self.first(tag="main")) or self.first(tag="body") or self.root

    @property
    def cards(self):
        return [n for n in self.scope.nodes() if n.tag == "article" and n.has("card")]

    @property
    def ended(self):
        return self.first(cls="ended", scope=self.scope) is not None

    def live_offers(self) -> int:
        if page_family(self.path) == "deal":
            return int(self.first(cls="deal-detail") is not None and not self.ended)
        return sum(not n.has("gone") for n in self.cards)

    def products(self):
        def walk(value):
            if isinstance(value, dict):
                if value.get("@type") == "Product":
                    yield value
                if "@graph" in value:
                    yield from walk(value["@graph"])
            elif isinstance(value, list):
                for entry in value:
                    yield from walk(entry)
        return [p for block in self.schemas for p in walk(block)]

    def schema_mismatch(self) -> bool:
        products = self.products()
        price = self.first(cls="detail-price")
        if self.invalid_schema or (self.ended and products):
            return True
        if not products:
            # Deal pages without a product photo carry no Product markup on
            # purpose (Google needs an image for merchant listings).
            return False
        if len(products) != 1 or page_family(self.path) != "deal" or not price:
            return True
        product = products[0]
        offer = product.get("offers")
        name, store = self.first(tag="h1"), self.first(cls="detail-store")
        availability = self.first(cls="detail-availability")
        condition = next((n.text().removeprefix("Condition: ").lower() for n in self.scope.nodes()
                          if n.tag == "p" and n.text().startswith("Condition: ")), "")
        expected_condition = ("UsedCondition" if re.search(r"\bused\b|\bopen box\b", condition) else
                              "RefurbishedCondition" if "refurbished" in condition else
                              "NewCondition" if re.search(r"\bnew\b", condition) else "")
        amount = re.search(r"\$([\d,.]+)\s+([A-Z]{3})\b", price.text())
        if not isinstance(offer, dict) or not name or not store or not amount or not availability:
            return True
        try:
            actual = Decimal(str(offer.get("price")))
            same_price = actual.is_finite() and actual == Decimal(amount[1].replace(",", ""))
        except InvalidOperation:
            same_price = False
        return not (_text(str(product.get("name") or "")) == word_limit(name.text(), NAME_LIMIT)
                    and offer.get("@type") == "Offer"
                    and same_price and offer.get("priceCurrency") == amount[2]
                    and availability.text() == "Availability: Live offer"
                    and offer.get("availability") == "https://schema.org/InStock"
                    and expected_condition and offer.get("itemCondition") == "https://schema.org/" + expected_condition
                    and isinstance(offer.get("seller"), dict)
                    and _text(str(offer["seller"].get("name") or "")) == store.text().removeprefix("Store: "))

    def fingerprint(self) -> str:
        def tokens(node, *, cards=False):
            if node.hidden() or node.tag in {"script", "style", "svg", "footer", "nav"} or node.has("hubs"):
                return []
            if node.has("card") and not cards:
                return []
            if node.has("gonetag"):
                return ["ended"]
            if node.has("spotlight-time"):
                return [node.attrs.get("data-ends-at") or ""]
            # A confirmation time is not a substantive change (no lastmod churn).
            if node.has("price-checked") or node.has("detail-checked"):
                return []
            # Site-wide browse navigation is not this page's offer set.
            if node.tag == "p" and any(isinstance(c, _Node) and c.tag == "strong"
                                      and c.text() in {"Stores", "By price"} for c in node.children):
                return []
            out = [node.attrs["href"]] if node.tag == "a" and node.attrs.get("href") else []
            for child in node.children:
                out.extend(tokens(child, cards=cards) if isinstance(child, _Node) else
                           [_text(child)] if _text(child) else [])
            return out

        title = self.first(tag="title")
        facts = {"title": title.text() if title else "", "primary": tokens(self.scope),
                 "offers": sorted(tokens(n, cards=True) for n in self.cards),
                 "products": self.products(), "ended": self.ended}
        return hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


def read_pages(out: Path, paths: list[str] | None = None) -> dict[str, Page]:
    if paths is None:
        paths = ["/" + p.relative_to(out).as_posix() for p in sorted(out.rglob("*.html"))]
        paths = [p.removesuffix("index.html") if p.endswith("/index.html") else p for p in paths]
    return {path: Page((out / (path.lstrip("/") + "index.html" if path.endswith("/")
                               else path.lstrip("/"))).read_text(encoding="utf-8"), path)
            for path in paths}


def fact_lastmods(db, cfg, now: datetime, site_url: str, pages: dict[str, Page]) -> dict[str, str]:
    """First observation starts a baseline; later dates move only with facts.

    Each URL holds [fingerprint, last substantive change, last emitted].
    Absent URLs retain that baseline briefly through sparse/locked rotations.
    """
    try:
        old = json.loads(db.meta_get(_FACT_META) or "{}")
    except ValueError:
        old = {}
    old = old if isinstance(old, dict) else {}
    old = {url: row for url, row in old.items() if isinstance(row, list) and len(row) == 3
           and isinstance(row[0], str) and re.fullmatch(r"[0-9a-f]{24}", row[0])
           and parse_iso(str(row[1])) is not None and parse_iso(str(row[2])) is not None}
    stamp, keep, dates = iso(now), {}, {}
    assert len(pages) <= _MAX_FACT_URLS
    for path, page in pages.items():
        url, fingerprint = site_url + path, page.fingerprint()
        prev = old.get(url)
        lastmod = prev[1] if prev and prev[0] == fingerprint else stamp
        keep[url] = [fingerprint, lastmod, stamp]
        dates[path] = lastmod
    cutoff = now - timedelta(days=cfg.seo_lastmod_retention_days)
    for url, row in sorted(old.items(), key=lambda kv: (kv[1][2], kv[0]), reverse=True):
        if len(keep) >= _MAX_FACT_URLS:
            break
        if url not in keep and parse_iso(row[2]) >= cutoff:
            keep[url] = row
    db.meta_set(_FACT_META, json.dumps(keep, sort_keys=True, separators=(",", ":")))
    return dates


def _target(path: str) -> str:
    path = unquote(path)
    if path.endswith("/index.html"):
        path = path.removesuffix("index.html")
    path = path.rstrip("/") or "/"
    return path.removesuffix(".html")


def audit(out: Path, cfg, site_url: str, paths: list[str] | None = None, *, pages: dict[str, Page] | None = None) -> dict:
    """Count offending pages and unique source/target link pairs; log paths only."""
    pages = read_pages(out, paths) if pages is None else pages
    floors = minimums(cfg)
    counts = dict.fromkeys(FAMILIES, 0)
    empty, schema, broken = set(), set(), set()
    base = site_url or "https://local.invalid"
    host = (urlsplit(base).hostname or "").removeprefix("www.")
    targets = {_target(p) for p in pages}
    targets.update(_target("/" + p.relative_to(out).as_posix()) for p in out.rglob("*")
                   if p.is_file() and p.suffix != ".html")
    for path, page in pages.items():
        family = page_family(path)
        if page.indexable and family:
            counts[family] += 1
            if page.live_offers() < floors[family]:
                empty.add(path)
        if page.schema_mismatch():
            schema.add(path)
        for href in page.hrefs:
            try:
                url = urlsplit(urljoin(base + path, href))
                if url.scheme not in {"http", "https"} or (url.hostname or "").removeprefix("www.") != host:
                    continue
                if _target(url.path) not in targets:
                    broken.add((path, url.path))
            except ValueError:
                broken.add((path, path))
    expected = {site_url + p for p, page in pages.items() if page.indexable}
    try:
        xml = ET.parse(out / "sitemap.xml")
        listed = [n.text or "" for n in xml.findall(".//{*}loc")]
    except (OSError, ET.ParseError):
        listed = None
    missing = expected - set(listed) if listed is not None else set()
    extra = set(listed) - expected if listed is not None else set()
    offenders = {"empty_indexable": sorted(empty)[:5], "schema_mismatches": sorted(schema)[:5],
                 "broken_internal_links": sorted({p for _source, p in broken})[:5],
                 "sitemap_missing": sorted(urlsplit(u).path for u in missing)[:5],
                 "sitemap_extra": sorted(urlsplit(u).path for u in extra)[:5]}
    result = {"indexable_by_family": counts, "empty_indexable": len(empty),
              "schema_mismatches": len(schema), "broken_internal_links": len(broken),
              "sitemap_urls": len(listed) if listed is not None else None,
              "emitted_indexable": len(expected), "sitemap_missing": len(missing) if listed is not None else None,
              "sitemap_extra": len(extra) if listed is not None else None,
              "sitemap_duplicates": sum(n - 1 for n in Counter(listed or []).values()),
              "offending_paths": offenders}
    log.info("seo audit: %s; empty %d; schema %d; broken links %d; sitemap %s / %d indexable",
             counts, len(empty), len(schema), len(broken), result["sitemap_urls"], len(expected))
    for kind, sample in offenders.items():
        if sample:
            log.warning("seo audit %s: %s", kind, ", ".join(re.sub(r"[\x00-\x1f\x7f]", "", p) for p in sample))
    return result
