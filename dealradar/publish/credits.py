"""Resolve internal source IDs to names for public deal credits."""

import re
from functools import lru_cache
from urllib.parse import urlsplit

from ..config import load_sources


def source_sightings(deal: dict) -> list[dict]:
    """One public credit per partner, even across multiple posts/channels."""
    seen, sightings = set(), []
    for sighting in deal.get("sightings") or []:
        name = sighting.get("source_name") or ""
        if name not in seen:
            seen.add(name)
            sightings.append(sighting)
    return sightings


@lru_cache(maxsize=1)
def _display_names() -> dict[str, str]:
    primary, backup = load_sources()
    return {s.name: s.display_name or s.name for s in primary + backup}


def source_display_name(name: str) -> str:
    """Keep unknown/historical source IDs usable as credits."""
    return _display_names().get(name, name)


@lru_cache(maxsize=1)
def _deal_source_hosts() -> set[str]:
    """Known source/blog hosts, including paused sources; stores can also have feeds."""
    primary, backup = load_sources()
    retailers = {"woot.com", "meh.com", "morningsave.com", "sidedeal.com",
                 "dealgenius.com", "dailysteals.com", "tanga.com", "1sale.com"}
    return {(urlsplit(s.site or s.url).hostname or "").lower().removeprefix("www.")
            for s in primary + backup} - retailers - {""}


def link_policy(amazon_tag="", overrides=None, *, sovrn_key="", sovrn_enabled=False,
                competitor_tracker_hosts=(), ebay_campid=""):
    """Resolve source policy, then freeze affiliate options for the caller."""
    from .link_facts import LinkPolicy

    return LinkPolicy(amazon_tag=amazon_tag, overrides=overrides or {},
                      sovrn_key=sovrn_key, sovrn_enabled=sovrn_enabled,
                      competitor_tracker_hosts=competitor_tracker_hosts, ebay_campid=ebay_campid,
                      source_names=_display_names(), source_hosts=(
                          _deal_source_hosts() if sovrn_enabled and re.fullmatch(r"[0-9a-f]{32}", sovrn_key)
                          else frozenset()))
