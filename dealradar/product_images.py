"""Which photo of a post belongs to which deal.

A roundup post (one article, several products) shows one photo per
product. Where a photo sits says nothing provable about whose it is (three
review rounds of layout pairing each found photos shifted to the next
product), so a deal gets a photo only on evidence (pick_photo):
  1. in its link: the photo inside <a href=X> is X's (fetch.rss.post_photos
     keeps only links that name a product), matched to the deal's link by
     match_product_image;
  2. by name: the photo whose alt, title, caption or file name covers the
     deal's item beyond its brand, clearly better than any other photo,
     and fits no other product the page names as well (photo_by_name);
  3. both, and they differ: none.
On a roundup the post's featured photos (og:image, its first image, one
named after the post) are no product's.
A deal knows its link twice: as the post gave it (often an affiliate
redirect) and resolved to the retailer's product page. When nothing
matches the answer is '' rather than a guess — a wrong photo on a card is
worse than the store-name tile.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

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
    ("target.com", re.compile(r"^/(?:c/|s/?$)", re.I)),
    ("walmart.com", re.compile(r"^/(?:browse|cp|search)(?:/|$)", re.I)),
    ("bestbuy.com", re.compile(r"^/site/(?:promo|searchpage\.jsp)", re.I)),
    ("kohls.com", re.compile(r"^/search\.jsp", re.I)),
)


def _names_no_product(url: str) -> bool:
    """A link (after unwrapping) that is every product, so none: an Amazon
    link without an ASIN (a search /s?k=, a node /b?node=, a store or the
    deals page), a retailer's category, sale, search or promo page (Target
    /c/ /s, Walmart /browse/ /cp/ /search, Best Buy /site/promo and
    searchpage.jsp, Kohl's search.jsp), or a home page (path "/")."""
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
#: A price or a percentage names no product ("$19.99", "50% off").
_NAME_PRICE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?|\d+(?:\.\d+)?\s?%")
#: A count is its number: 40ct, 40pk, 40pc and "40 Count" are one word.
_NAME_COUNT = re.compile(r"^(\d+)(?:ct|pk|pcs|pc)$")
#: A photo names a deal when its words show this share of the deal's
#: distinguishing words...
_NAME_COVER = 0.6
#: ...of which the deal has at least this many...
_NAME_DISTINCT = 2
#: ...and no other photo of the post comes within this share of it.
_NAME_MARGIN = 0.1


def _name_seq(text: str, file_name: bool = False) -> list[str]:
    """name_words in the order the name gives them (its brand first)."""
    out: list[str] = []
    text = _NAME_PRICE.sub(" ", re.sub(r"['\u2019]", "", text.lower()))
    for word in re.split(r"[^a-z0-9]+", text):
        if not word or word in _NAME_STOP or re.fullmatch(r"\d+x\d+", word) \
                or (file_name and word.isdigit()):
            continue
        word = _NAME_COUNT.sub(r"\1", word)
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        if (len(word) > 1 or word.isdigit()) and word not in _NAME_STOP \
                and word not in out:
            out.append(word)
    return out


def name_words(text: str, file_name: bool = False) -> frozenset[str]:
    """The words of a name, normalized: lowercase alphanumerics, no
    stopwords, generic deal words, prices or -WxH size tokens, a count as
    its number (40ct -> 40), a plural's s dropped; numbers kept (Series 9,
    5-Pack), but a file name loses its digits-only tokens (upload
    counters, dates)."""
    return frozenset(_name_seq(text, file_name))


def file_words(url: str) -> frozenset[str]:
    """name_words of an image's file name (LEGO-Classic-Bricks-300x300.jpg
    -> lego, classic, brick), extension dropped."""
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    return name_words(re.sub(r"\.[a-z0-9]{2,5}$", "", name, flags=re.I), file_name=True)


def _dice(a: frozenset[str], b: frozenset[str]) -> float:
    """How well two names fit each other (0-1), both ways at once."""
    return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0.0


def _link_keys(links) -> set[str]:
    return {link_key(u) for u in links
            if u and u.lower().startswith(("http://", "https://"))} - {""}


def photo_by_name(item: str, links: list[str], items: list[tuple[str, str]],
                  photos: PostPhotos) -> str:
    """The photo whose words name this deal's item, or ''.

    ``links`` are the deal's own links, ``items`` (name, link) of every
    deal of the post (this one included), ``photos`` the post's. A photo
    names the deal only when it COVERS the item, not merely overlaps it:
      - its words show at least 60% of the item's distinguishing words, of
        which there are two or more: the item's words minus its brand —
        its first word (of three or more) and the leading words it shares
        with another product or photo of the page (Crayola, Hot Wheels,
        Apple Watch Series) — and reach past the start of the name: a
        photo naming only a brand (hot-wheels-sale.jpg, "Melissa & Doug")
        never matches;
      - it beats the deal's next-best photo by 10 points;
      - it fits no other product named on the page as well (Dice): another
        deal, a product-ish heading or link text (PostPhotos.names),
        another photo's words; nor names one besides (two or more of its
        words beyond the item's are another's: a collage);
      - no other product of the page has the same distinguishing words
        under a different link (a variant, or one product at two stores:
        nothing tells their photos apart).
    On a page that is not single_product(), the post's featured photos
    (og:image, its first image, one named after the post) are no one's.
    """
    seq = _name_seq(item)
    mine = frozenset(seq)
    own = _link_keys(links)
    rest = list(items)
    me = next((i for i, (name, link) in enumerate(rest) if name == item and link in links),
              next((i for i, (name, _link) in enumerate(rest) if name == item), None))
    if me is not None:
        del rest[me]
    rivals: list[frozenset[str]] = []      # every other product the page names
    for name, link in rest:
        words, keys = name_words(name), _link_keys([link])
        if words == mine:
            if keys == own or keys & own:
                continue                  # this product, parsed twice
            return ""                     # a variant under another link
        rivals.append(words)
    for words, key in photos.names:
        if (key and key in own) or (words == mine and not key):
            continue                      # this deal's own heading or link text
        if words == mine:
            return ""                     # a variant under another link
        rivals.append(words)
    candidates = photos.named if single_product(photos) else {
        url: words for url, words in photos.named.items()
        if photo_key(url) not in photos.featured}

    def brand_and_cover(url: str) -> tuple[frozenset[str], float]:
        words = photos.named[url]
        shared = frozenset().union(*rivals, *(w for u, w in photos.named.items() if u != url))
        brand = seq[:1] if len(seq) >= 3 else []
        for word in seq[len(brand):]:
            if word not in shared:
                break
            brand.append(word)
        distinct = mine - set(brand)
        if len(distinct) < _NAME_DISTINCT:
            return frozenset(brand), 0.0
        start = next((i for i, w in enumerate(seq) if w not in words), len(seq))
        if 0 < start < len(seq) and not words & set(seq[start:]):
            return frozenset(brand), 0.0  # names only the start of it: a brand
        return frozenset(brand), len(distinct & words) / len(distinct)

    ranked = sorted(((brand_and_cover(url)[1], url) for url in candidates), reverse=True)
    if not ranked or ranked[0][0] < _NAME_COVER:
        return ""
    best, url = ranked[0]
    if len(ranked) > 1 and best - ranked[1][0] < _NAME_MARGIN:
        return ""
    words, brand = photos.named[url], brand_and_cover(url)[0]
    fit = _dice(words, mine)
    for other in rivals + [w for u, w in photos.named.items() if u != url and w]:
        if _dice(words, other) >= fit or len((words - mine) & other) >= 2:
            return ""                     # another product's photo, or several
    if any(other - brand == mine - brand for other in rivals):
        return ""                         # a variant: nothing tells them apart
    return url


def pick_photo(links: list[str], item: str, items: list[tuple[str, str]],
               photos: PostPhotos, in_link: bool = True) -> str | None:
    """This deal's photo in a post: the one inside its own product link
    (unless ``in_link`` is off: a link several products share, or one
    resolving to a store or search page), or the one named after its item;
    '' when neither (a single-product post may then lend its own image);
    None when both give one and they differ — no photo at all."""
    linked = match_product_image(links, photos.in_link) if in_link else ""
    named = photo_by_name(item, links, items, photos)
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
