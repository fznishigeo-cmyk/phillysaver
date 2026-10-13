"""Facts and source credits permitted in deal emails."""

import re
from html import escape

from ..http_client import extract_asin, tracker_target
from ..source_policy import host_of, source_image_allowed
from .social import _without_prices


def email_credits(deal: dict) -> list[tuple[str, str]]:
    return list(dict.fromkeys((s.get("source_name") or "", s.get("source_url") or "")
                              for s in deal.get("sightings") or []))


def email_line_html(line: str, deal: dict) -> str:
    urls = {u for _, u in email_credits(deal) if u.startswith(("https://", "http://"))}
    url = email_deal(deal).get("canonical_url") or ""
    if url.startswith(("https://", "http://")):
        urls.add(url)
    if not urls:
        return escape(line)
    pattern = re.compile("|".join(re.escape(u) for u in sorted(urls, key=len, reverse=True)))
    pieces, end = [], 0
    for match in pattern.finditer(line):
        pieces.append(escape(line[end:match.start()]))
        href = escape(match.group())
        pieces.append(f'<a href="{href}">{href}</a>')
        end = match.end()
    return "".join(pieces) + escape(line[end:])


def email_deal(deal: dict, cfg=None) -> dict:
    """Amazon prices stay on the site; emails use the credited article link."""
    d = dict(deal)
    url = d.get("canonical_url") or ""
    target = tracker_target(url) or url
    host = host_of(target)
    amazon = (str(d.get("store") or "").casefold() == "amazon" or d.get("asin")
              or extract_asin(target) or re.search(r"(^|\.)amazon\.[a-z.]+$", host)
              or host in {"amzn.to", "a.co", "link.amazon"})
    d["email_hide_price"] = bool(amazon)
    if amazon:
        d["item"] = _without_prices(str(d.get("item") or ""))
        d["store"] = _without_prices(str(d.get("store") or ""))
        d["conditions"] = [_without_prices(str(c)) for c in d.get("conditions") or []]
        d["final_price"] = d["regular_price"] = d["abs_savings"] = None
        purchase_host = host_of(url)
        if purchase_host in {"amzn.to", "a.co", "link.amazon"} or re.search(r"(^|\.)amazon\.[a-z.]+$", purchase_host):
            d["canonical_url"] = next((u for _, u in email_credits(d) if u), "")
    if not source_image_allowed(d.get("image_url") or "", d.get("sightings") or [], cfg):
        d["image_url"] = ""
    return d
