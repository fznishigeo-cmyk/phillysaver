"""Server-rendered deal cards; no scripts, pagination, or retailer crawling."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .. import util
from ..models import RawPost
from .base import SourceAdapter
from .rss import MAX_ENTRIES, _permission_host

PAGE_ADAPTERS = {"bradsdeals", "dealsofamerica", "dealmoon_us", "morningsave"}


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    children: list = field(default_factory=list)

    def find(self, *, tag=None, cls=None):
        for child in self.children:
            if isinstance(child, _Node):
                if (tag is None or child.tag == tag) and (
                        cls is None or cls in child.attrs.get("class", "").split()):
                    yield child
                yield from child.find(tag=tag, cls=cls)

    def text(self):
        if self.tag in {"script", "style", "noscript"}:
            return ""
        return " ".join(c.text() if isinstance(c, _Node) else c
                        for c in self.children).strip()


class _Tree(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self.stack = [self.root]
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input",
                       "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _first(node, **kw):
    return next(node.find(**kw), None)


def parse_page(source, body: bytes, now: datetime) -> list[RawPost]:
    root = _Tree(body.decode("utf-8", "replace")).root
    if source.adapter == "morningsave" and _first(root, cls="offer-details"):
        return _parse_morningsave_list(source, root, now)
    if source.adapter in {"dealmoon_us", "morningsave"}:
        return _parse_new_cards(source, root, now)
    brads = source.adapter == "bradsdeals"
    cards = root.find(cls="post-tile-vertical" if brads else "deal")
    posts, seen = [], set()
    for card in cards:
        if not brads and card.tag != "section":
            continue  # sidebar recommendations are not complete deal cards
        title_node = _first(card, cls="post-tile-vertical__headline-text" if brads else "title")
        details = _first(card, cls="post-tile-vertical__details-content" if brads else "more_details")
        if title_node is None or details is None:
            continue
        title = title_node.text() if brads else (_first(title_node, tag="a") or title_node).text()
        candidates = card.find(tag="a")
        url = ""
        for a in candidates:
            candidate = urljoin(source.url, a.attrs.get("href") or "")
            parts = urlsplit(candidate)
            if parts.scheme not in {"http", "https"} or parts.netloc != urlsplit(source.url).netloc:
                continue
            if (brads and parts.path.startswith("/deals/")
                    or not brads and re.fullmatch(r"/[^/]+/\d+\.htm", parts.path)):
                url = candidate.split("#", 1)[0].split("?", 1)[0]
                break
        text = title + "\n" + details.text()
        if not url or url in seen or not re.search(r"\$\s?\d", text):
            continue
        # Preserve affiliate links exactly as posted, never resolve or fetch them.
        for a in details.find(tag="a"):
            link = urljoin(source.url, a.attrs.get("href") or "")
            if urlsplit(link).scheme in {"http", "https"}:
                text += "\n" + link
        published = None
        if not brads:
            stamp = _first(card, tag="time")
            if stamp:
                published = util.parse_feed_date(stamp.text().removeprefix("Posted at ").strip(), source.tz, now)
        posts.append(RawPost(source.name, url, url, title, text, published, now))
        seen.add(url)
        if len(posts) >= MAX_ENTRIES:
            break
    return posts


def _parse_new_cards(source, root, now):
    dealmoon = source.adapter == "dealmoon_us"
    cards = (root.find(cls="mlist") if dealmoon else
             list(root.find(tag="a")) + list(root.find(cls="details"))
             + list(root.find(cls="bonus")))
    posts, seen = [], set()
    for card in cards:
        if dealmoon:
            if card.attrs.get("data-type") != "deal":
                continue
            title_node = _first(card, cls="zoom-title")
            details = _first(card, cls="zoom-brief")
            if title_node is None or details is None:
                raise RuntimeError("changed_deal_card")
            href = title_node.attrs.get("href") or ""
            title = title_node.text()
            text = title + "\n" + details.text()
            pattern = r"/en/[^/]+/\d+\.html"
        else:
            if card.tag == "a" and card.attrs.get("role") == "button":
                continue  # handled with its enclosing titled hero card
            link_node = card if card.tag == "a" else _first(card, cls="buy-it")
            href = link_node.attrs.get("href", "") if link_node else ""
            # Event previews and photo-only recommendations are not deal cards.
            if not urlsplit(href).path.startswith("/deals/"):
                continue
            title_node = _first(card, tag="h2") or _first(card, tag="h3")
            price = _first(card, cls="sale-price")
            if price is None:
                if title_node is not None:
                    raise RuntimeError("changed_deal_card")
                continue
            button = _first(card, cls="buy-it")
            label = button.attrs.get("aria-label", "") if button else ""
            if (title_node is None or not label.startswith("Sale price ")
                    or not re.search(r"\$\d", label)):
                raise RuntimeError("changed_deal_card")
            title = title_node.text()
            # The accessible label contains the exact unsplit dollar amounts.
            text = title + "\n" + label
            if card.tag == "a":
                text += "\n" + card.text()
            pattern = r"/deals/[^/]+"
        url = urljoin(source.url, href)
        parts = urlsplit(url)
        if (parts.scheme not in {"http", "https"}
                or parts.netloc != urlsplit(source.url).netloc
                or not re.fullmatch(pattern, parts.path)):
            raise RuntimeError("invalid_deal_permalink")
        url = url.split("#", 1)[0].split("?", 1)[0]
        if url in seen or not re.search(r"\$\s?\d", text):
            continue
        if dealmoon:
            buy = _first(card, cls="btn-buy")
            if buy and buy.attrs.get("href"):
                link = urljoin(source.url, buy.attrs["href"])
                if urlsplit(link).scheme in {"http", "https"}:
                    text += "\nBuy: " + link  # preserve credit; never fetch it
        posts.append(RawPost(source.name, url, url, title, text, None, now))
        seen.add(url)
        if len(posts) >= MAX_ENTRIES:
            break
    return posts


def _child(node, cls):
    return next((c for c in node.children if isinstance(c, _Node)
                 and cls in c.attrs.get("class", "").split()), None)


def _parse_morningsave_list(source, root, now):
    """MorningSave's list layout (probe 2026-10-13): each priced offer is a
    ``li`` holding ``div.offer-details`` (its price split into
    ``span.amount > span.symbol + span.whole``) and a ``/deals/`` link whose
    image alt (or title) names the item. The photo-only "superlatives"
    strip carries no price and is skipped. Price only: the page shows no
    list price. Only a page whose offers ALL fail to parse is a changed
    layout."""
    host = urlsplit(source.url).netloc
    posts, seen, offers = [], set(), 0
    for li in root.find(tag="li"):
        details = _child(li, "offer-details")
        if details is None:
            continue
        offers += 1
        link = next((a for a in li.find(tag="a")
                     if urlsplit(a.attrs.get("href") or "").path.startswith("/deals/")), None)
        amount = _first(details, cls="amount")
        if link is None or amount is None:
            continue
        img = _first(link, tag="img")
        title = (link.attrs.get("title") or (img.attrs.get("alt", "") if img else "")).strip()
        whole, cents = _first(amount, cls="whole"), _first(amount, cls="fraction")
        dollars = re.sub(r"[^\d,.]", "", whole.text()) if whole else ""
        cents = re.sub(r"\D", "", cents.text()) if cents else ""
        if not title or not re.match(r"\d", dollars):
            continue
        price = "$" + dollars + ("." + cents if cents and "." not in dollars else "")
        url = urljoin(source.url, link.attrs["href"])
        parts = urlsplit(url)
        if (parts.scheme not in {"http", "https"} or parts.netloc != host
                or not re.fullmatch(r"/deals/[^/]+", parts.path)):
            raise RuntimeError("invalid_deal_permalink")
        url = url.split("#", 1)[0].split("?", 1)[0]
        if url in seen:
            continue
        posts.append(RawPost(source.name, url, url, title,
                             f"{title}\nSale price {price}.", None, now))
        seen.add(url)
        if len(posts) >= MAX_ENTRIES:
            break
    if offers and not posts:
        raise RuntimeError("changed_deal_card")
    return posts


class PageAdapter(SourceAdapter):
    def fetch_posts(self, source, fetcher, db, cfg, now, deadline=None):
        posts = self.fetch_page(source, fetcher, now, deadline)
        if source.adapter != "bradsdeals":
            return posts
        enriched = []
        for post in posts:
            if db.post_is_known(post.content_hash):
                enriched.append(post)
                continue
            kw = {}
            if override := _permission_host(source, getattr(fetcher, "cfg", None)):
                kw["robots_override_host"] = override
            res = fetcher.get(post.url, conditional=False, deadline=deadline, **kw)
            if res.status != 200 or res.blocked_by_robots or not res.body:
                continue  # no usable shopping link; do not submit a paid parse
            root = _Tree(res.body.decode("utf-8", "replace")).root
            deal_id = re.search(r"-(blt[a-z0-9]+)$", urlsplit(post.url).path)
            if not deal_id:
                continue
            links = []
            for a in root.find(tag="a"):
                link = urljoin(post.url, a.attrs.get("href") or "")
                parts = urlsplit(link)
                if (parts.scheme in {"http", "https"}
                        and parts.netloc == urlsplit(source.url).netloc
                        and parts.path.startswith(f"/go/d/{deal_id[1]}-")):
                    if link not in links:
                        links.append(link)
            if links:
                enriched.append(replace(post, hash_text=post.text,
                                        text=post.text + "\nBuy: " + "\n".join(links)))
        if posts and not enriched:
            raise RuntimeError("missing_deal_shopping_links")
        return enriched

    @staticmethod
    def fetch_page(source, fetcher, now, deadline=None):
        if not source.allowed_html:
            raise RuntimeError("html_not_allowed")
        if source.adapter not in PAGE_ADAPTERS:
            raise ValueError(f"unknown page adapter {source.adapter!r}")
        kw = {}
        if override := _permission_host(source, getattr(fetcher, "cfg", None)):
            kw["robots_override_host"] = override
        res = fetcher.get(source.url, conditional=True, deadline=deadline, **kw)
        if res.not_modified or res.status == 304:
            return []
        if res.blocked_by_robots:
            raise RuntimeError("blocked_by_robots")
        if res.status != 200 or not res.body:
            raise RuntimeError(f"http_{res.status} {res.error}".strip())
        posts = parse_page(source, res.body, now)
        if not posts:
            raise RuntimeError("empty_or_changed_deal_page")
        return posts
