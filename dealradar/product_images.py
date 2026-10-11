"""Which photo of a post belongs to which deal.

A roundup post (one article, several products) shows one photo per product
next to that product's link (fetch.rss.product_images). A deal knows its
link twice: as the post gave it (often an affiliate redirect) and resolved
to the retailer's product page. match_product_image pairs them; when
nothing matches it returns '' rather than guess — a wrong photo on a card
is worse than the store-name tile.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

#: Query params that only say who sent the click, never which product.
_TRACKING = frozenset({
    "ref", "ref_", "tag", "ascsubtag", "afsrc", "cjevent", "clickid",
    "irclickid", "sid", "cid", "pid", "u1", "subid",
})
#: Affiliate redirectors carry the retailer URL in one of these params.
_WRAP_PARAMS = frozenset({"url", "u", "murl", "ued", "dest", "destination",
                          "target", "redirect", "r", "link", "to"})
_AMAZON_HOST = re.compile(r"(^|\.)amazon\.[a-z]{2,3}(\.[a-z]{2})?$")
#: (host, product-id pattern) per retailer: the id names the product
#: whatever slug, tracking or variant params surround it.
_RETAILER_IDS = (
    ("target.com", re.compile(r"/-/A-(\d+)", re.I)),
    ("walmart.com", re.compile(r"/ip/(?:.*/)?(\d+)")),
    ("bestbuy.com", re.compile(r"[?&]skuId=(\d+)|/(\d{7})\.p", re.I)),
    ("homedepot.com", re.compile(r"/p/.*/(\d{9})(?:[/?#]|$)")),
    ("lowes.com", re.compile(r"/pd/.*/(\d+)(?:[/?#]|$)")),
    ("kohls.com", re.compile(r"/prd-(\d+)")),
    ("costco.com", re.compile(r"\.product\.(\d+)")),
    ("wayfair.com", re.compile(r"[?&]((?:piid|sku)=[^&#]+)", re.I)),
    ("woot.com", re.compile(r"/offers/([^/?#]+)", re.I)),
)


def _normalize(url: str) -> str:
    """Lowercase scheme and host, no fragment, no tracking params, no
    trailing slash; the params kept are sorted."""
    parts = urlsplit(url.strip())
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if not k.lower().startswith("utm_")
                   and k.lower() not in _TRACKING)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                       parts.path.rstrip("/"), urlencode(query), ""))


def _unwrap(url: str) -> str:
    """The URL an affiliate redirect points to ('' when it carries none):
    the first wrap param whose decoded value is an absolute URL."""
    for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        if key.lower() not in _WRAP_PARAMS:
            continue
        for _ in range(2):  # a value may arrive percent-encoded twice
            if not value.lower().startswith(("http%3a", "https%3a")):
                break
            value = unquote(value)
        if value.lower().startswith(("http://", "https://")):
            return value
    return ""


def _forms(url: str) -> list[str]:
    """The URL normalized, then unwrapped up to two redirect levels deep."""
    out = [_normalize(url)]
    inner = url
    for _ in range(2):
        inner = _unwrap(inner)
        if not inner:
            break
        out.append(_normalize(inner))
    return out


def _product_id(url: str) -> str:
    """'<retailer>:<id>' for a known retailer's product page; for Amazon
    the ASIN or '' (a search or store link names no product); for any
    other page 'path:<host+path>', or '' when the path is too short to
    name a product (a redirector's /r.cfm?..., a home page)."""
    from .http_client import extract_asin  # lazy: written by another layer

    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if _AMAZON_HOST.search(host):
        asin = extract_asin(url)
        return f"amazon:{asin}" if asin else ""
    where = parts.path + ("?" + parts.query if parts.query else "")
    for retailer, pattern in _RETAILER_IDS:
        if host == retailer or host.endswith("." + retailer):
            m = pattern.search(where)
            if m:
                return f"{retailer}:{next(g for g in m.groups() if g)}".lower()
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 2:
        return ""
    return "path:" + host.removeprefix("www.") + "/" + "/".join(segments)


def _names_no_product(url: str) -> bool:
    """An Amazon link (after unwrapping) without an ASIN: a search, a
    store page or the deals page — it is every product, so none."""
    from .http_client import extract_asin  # lazy: written by another layer

    deepest = _forms(url)[-1]
    host = (urlsplit(deepest).hostname or "").lower()
    return bool(_AMAZON_HOST.search(host)) and not extract_asin(deepest)


def match_product_image(urls: list[str], product_images: dict[str, str]) -> str:
    """The photo a post shows next to this deal's link, or ''.

    ``urls`` are the deal's links, strongest first (the link as posted,
    then the resolved retailer URL). Each level is tried for every link
    before the next, weaker level: exact key; normalized (case, fragment,
    tracking params, trailing slash); affiliate-unwrapped on either side;
    then the product id (Amazon ASIN, Target A-id, Walmart item, Best Buy
    SKU, ...; else host+path). An Amazon link without an ASIN matches
    nothing. A bare host+path counts only when one photo stands behind it:
    two different photos behind one path is a redirector, not a product.
    """
    urls = [u for u in urls if u and u.lower().startswith(("http://", "https://"))
            and not _names_no_product(u)]
    keys = [(k, v) for k, v in (product_images or {}).items() if v]
    if not urls or not keys:
        return ""
    for url in urls:
        hit = next((v for k, v in keys if k == url), "")
        if hit:
            return hit
    forms = {k: _forms(k) for k, _v in keys}
    for url in urls:
        mine = _normalize(url)
        hit = next((v for k, v in keys if forms[k][0] == mine), "")
        if hit:
            return hit
    for url in urls:
        mine_all = set(_forms(url))
        hit = next((v for k, v in keys if mine_all & set(forms[k])), "")
        if hit:
            return hit
    ids = {k: _product_id(forms[k][-1]) for k, _v in keys}
    for url in urls:
        mine = _product_id(_forms(url)[-1])
        if not mine:
            continue
        found = [v for k, v in keys if ids[k] == mine]
        if found and (not mine.startswith("path:") or len(set(found)) == 1):
            return found[0]
    return ""
