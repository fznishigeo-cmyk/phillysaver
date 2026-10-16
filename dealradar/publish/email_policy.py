"""Facts and source credits permitted in deal emails."""

import re
from html import escape

from ..http_client import extract_asin, tracker_target
from ..source_policy import host_of, source_image_allowed
from .credits import source_display_name, source_sightings
from .social import _without_prices


AMAZON_MARKS = re.compile(
    r"\b(?:alexa|kindle|echo\s+(?:dot|show|pop|spot|studio|auto|hub|smart\s+speaker)|"
    r"echo\s*\(\d+(?:st|nd|rd|th)\s+gen\)|"
    r"fire\s*(?:tv|tablet|hd|max|kids|7)|eero|"
    r"ring\s+(?:alarm|intercom|(?:(?:video|battery|wired|spotlight|floodlight|stick\s+up|indoor)\s+)*(?:doorbell|cam(?:era)?))|"
    r"blink\s+(?:mini|outdoor|indoor|video|doorbell|camera|cam))(?!\w)", re.I)


def email_content_excluded(cfg, *parts: str) -> bool:
    """Fail closed on excluded store names/links anywhere in the final copy."""
    tokens = set(cfg.newsletter_exclude_stores) | {"amazon", "amzn.", "a.co/"}
    return any(token in part.casefold() for part in parts for token in tokens)


def email_excluded(deal: dict, cfg) -> bool:
    """Shared store/product exclusions for every customer email channel."""
    from .amazon_history import store_of

    url = deal.get("canonical_url") or ""
    excluded = set(cfg.newsletter_exclude_stores) | {"amazon"}
    text = " ".join(str(deal.get(k) or "") for k in ("item", "store", "conditions"))
    return (store_of(dict(deal, canonical_url=tracker_target(url) or url)) in excluded
            or email_deal(deal, cfg)["email_hide_price"]
            or email_content_excluded(cfg, text)
            or bool(AMAZON_MARKS.search(text)))


def email_message(deal: dict, cfg) -> str:
    from .telegram_pub import format_message

    sightings = [dict(s, source_name=source_display_name(s.get("source_name") or ""))
                 for s in deal.get("sightings") or []]
    return format_message(dict(deal, sightings=sightings), cfg=cfg)


def email_credits(deal: dict) -> list[tuple[str, str]]:
    return [(s.get("source_name") or "", s.get("source_url") or "")
            for s in source_sightings(deal)]


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
