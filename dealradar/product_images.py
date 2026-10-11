"""Which photo of a post belongs to which deal.

A roundup post (one article, several products) shows one photo per
product. Where a photo sits says nothing provable about whose it is (three
review rounds of layout pairing each found photos shifted to the next
product), so a deal gets a photo only on evidence (pick_photo):
  1. in its link: the photo inside <a href=X> is X's (fetch.rss.post_photos
     keeps only links that name a product), matched to the deal's link by
     match_product_image;
  2. by name: the photo whose alt, title, caption or file name names the
     deal's item, clearly better than any other photo and no better for
     another deal of the post (photo_by_name);
  3. both, and they differ: none.
A deal knows its link twice: as the post gave it (often an affiliate
redirect) and resolved to the retailer's product page. When nothing
matches the answer is '' rather than a guess — a wrong photo on a card is
worse than the store-name tile.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from rapidfuzz import fuzz

from .models import PostPhotos

#: Query params that only say who sent the click, never which product.
#: Not pid/sid/cid: Old Navy, Gap, Banana Republic and Athleta name the
#: product by ?pid= (product.do?pid=111002 vs 222002).
_TRACKING = frozenset({
    "ref", "ref_", "tag", "ascsubtag", "afsrc", "cjevent", "clickid",
    "irclickid", "u1", "subid",
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


#: Retailer pages listing many products (category, sale, search, promo).
_LISTING_PAGES = (
    ("target.com", re.compile(r"^/c/", re.I)),
    ("walmart.com", re.compile(r"^/(?:browse|cp|search)(?:/|$)", re.I)),
    ("bestbuy.com", re.compile(r"^/site/promo", re.I)),
)


def _names_no_product(url: str) -> bool:
    """A link (after unwrapping) that is every product, so none: an Amazon
    link without an ASIN (a search /s?k=, a node /b?node=, a store or the
    deals page), a retailer's category, sale, search or promo page (Target
    /c/, Walmart /browse/ /cp/ /search, Best Buy /site/promo), or a home
    page (path "/")."""
    from .http_client import extract_asin  # lazy: written by another layer

    deepest = _forms(url)[-1]
    parts = urlsplit(deepest)
    host = (parts.hostname or "").lower()
    if _AMAZON_HOST.search(host):
        return not extract_asin(deepest)
    if not parts.path.strip("/"):
        return True
    return any((host == r or host.endswith("." + r)) and p.search(parts.path)
               for r, p in _LISTING_PAGES)


def match_product_image(urls: list[str], product_images: dict[str, str]) -> str:
    """The photo a post shows next to this deal's link, or ''.

    ``urls`` are the deal's links, strongest first (the link as posted,
    then the resolved retailer URL). Each level is tried for every link
    before the next, weaker level: exact key; normalized (case, fragment,
    tracking params, trailing slash); affiliate-unwrapped on either side;
    then the product id (Amazon ASIN, Target A-id, Walmart item, Best Buy
    SKU, ...; else host+path). An Amazon link without an ASIN matches
    nothing. A normalized, unwrapped or host+path hit counts only when
    every link it matches shows the same photo: two photos behind one
    form is two products. A bare host+path also never matches a link
    whose shared query param says otherwise (all trx-hub links of an
    article share one path; only ?q= names the product).
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
        hit = _one({v for k, v in keys if forms[k][0] == mine})
        if hit:
            return hit
    for url in urls:
        mine_all = set(_forms(url))
        hit = _one({v for k, v in keys if mine_all & set(forms[k])})
        if hit:
            return hit
    ids = {k: _product_id(forms[k][-1]) for k, _v in keys}
    for url in urls:
        deepest = _forms(url)[-1]
        mine = _product_id(deepest)
        if not mine:
            continue
        if not mine.startswith("path:"):
            hit = next((v for k, v in keys if ids[k] == mine), "")
        else:
            hit = _one({v for k, v in keys if ids[k] == mine
                        and not _params_differ(forms[k][-1], deepest)})
        if hit:
            return hit
    return ""


def link_identity(url: str) -> str:
    """The page a link opens: normalized and affiliate-unwrapped, so two
    links differing only in tracking or redirect are one; '' for none."""
    if not url.lower().startswith(("http://", "https://")):
        return ""
    return _forms(url)[-1]


def link_key(url: str) -> str:
    """One key per product a link names, to count a page's products: the
    retailer's product id (Amazon ASIN, Target A-id, ...), else the link
    normalized and unwrapped (a redirector's host+path is every product)."""
    pid = _product_id(_forms(url)[-1])
    return pid if pid and not pid.startswith("path:") else link_identity(url)


def photo_key(url: str) -> str:
    """One photo whatever size, CDN or query it is served with: the last
    three path segments (WordPress: year/month/file), lowercase, without a
    -WxH or -scaled suffix."""
    segments = [s for s in urlsplit(url.lower()).path.split("/") if s][-3:]
    return re.sub(r"-(?:\d+x\d+|scaled)(?=\.[a-z0-9]+$)", "", "/".join(segments))


#: Words that name no product: English glue, the generic deal words, pack
#: sizes, and upload noise.
_NAME_STOP = frozenset((
    "a an and the of for with in on at by to or from only just now free reg "
    "regularly was is are this that these your our my plus more each per "
    "deal sale off price amazon walmart target new best today "
    "set pack pk piece pc pcs count ct size color style img image photo scaled"
).split())
#: A photo names a deal from this rapidfuzz token_set_ratio up (0-100)...
_NAME_MIN = 70
#: ...and only this many points clear of the deal's next-best photo.
_NAME_MARGIN = 10


def name_words(text: str, file_name: bool = False) -> frozenset[str]:
    """The words of a name, normalized: lowercase alphanumerics, no
    stopwords or generic deal words, no -WxH size tokens, a plural's s
    dropped; a file name also loses its digits-only tokens (upload
    counters, dates)."""
    out = set()
    for word in re.split(r"[^a-z0-9]+", re.sub(r"['\u2019]", "", text.lower())):
        if word in _NAME_STOP or re.fullmatch(r"\d+x\d+", word) \
                or (file_name and word.isdigit()):
            continue
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        if len(word) > 1 and word not in _NAME_STOP:
            out.add(word)
    return frozenset(out)


def file_words(url: str) -> frozenset[str]:
    """name_words of an image's file name (LEGO-Classic-Bricks-300x300.jpg
    -> lego, classic, brick), extension dropped."""
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    return name_words(re.sub(r"\.[a-z0-9]{2,5}$", "", name, flags=re.I), file_name=True)


def _name_score(a: frozenset[str], b: frozenset[str]) -> float:
    """How well two names match (0-100). One shared word (a brand: two
    Crayola products) never counts."""
    if len(a & b) < 2:
        return 0.0
    return fuzz.token_set_ratio(" ".join(sorted(a)), " ".join(sorted(b)))


def photo_by_name(item: str, items: list[str], named: dict[str, frozenset[str]]) -> str:
    """The photo whose words name this deal's item, or ''. ``items`` are
    the names of every deal of the post (this one included); ``named``
    maps each photo to its words (PostPhotos.named). The best photo must
    score _NAME_MIN or more, beat the deal's next-best photo by
    _NAME_MARGIN, score this deal higher than any other deal of the post,
    and name no other deal besides (two or more of its words beyond this
    deal's name are another deal's: a collage, a bundle shot)."""
    mine = name_words(item)
    others = {name_words(i) for i in items} - {mine}
    ranked = sorted(((_name_score(mine, words), url) for url, words in named.items()),
                    reverse=True)
    if not ranked or ranked[0][0] < _NAME_MIN:
        return ""
    best, url = ranked[0]
    if len(ranked) > 1 and best - ranked[1][0] < _NAME_MARGIN:
        return ""
    rest = named[url] - mine
    if any(_name_score(other, named[url]) >= best or len(rest & other) >= 2
           for other in others):
        return ""
    return url


def pick_photo(links: list[str], item: str, items: list[str],
               photos: PostPhotos) -> str | None:
    """This deal's photo in a post: the one inside its own product link,
    or the one named after its item; '' when neither (a
    single-product post may then lend its own image); None when both give
    one and they differ — no photo at all."""
    linked = match_product_image(links, photos.in_link)
    named = photo_by_name(item, items, photos.named)
    if linked and named and photo_key(linked) != photo_key(named):
        return None
    return linked or named


def single_product(photos: PostPhotos) -> bool:
    """A post whose own image may be its product's: at most
    one distinct off-site product link and one photo besides its featured
    one."""
    return len(photos.links) <= 1 and len(photos.images) <= 1


def _one(photos: set[str]) -> str:
    """The photo when exactly one stands behind a match, else ''."""
    return next(iter(photos)) if len(photos) == 1 else ""


def _params_differ(a: str, b: str) -> bool:
    """Both (normalized) links carry a query param with different values."""
    qa = dict(parse_qsl(urlsplit(a).query, keep_blank_values=True))
    qb = dict(parse_qsl(urlsplit(b).query, keep_blank_values=True))
    return any(qa[k] != qb[k] for k in qa.keys() & qb.keys())
