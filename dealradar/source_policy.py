"""Current source grants and source-host image policy."""

from functools import lru_cache
from urllib.parse import urlsplit

from .config import load_sources


def grant_active(source, cfg=None) -> bool:
    """Conditional grants use the configured monthly membership price."""
    if "membership price stays under $19.99/month" in source.crawl_permission:
        return getattr(cfg, "growth_monthly_price_usd", 9.99) < 19.99
    return True


def image_granted(source, cfg=None) -> bool:
    return source.image_permission and grant_active(source, cfg)


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


@lru_cache(maxsize=1)
def _sources():
    primary, backup = load_sources()
    return primary + backup


def sources_for_url(url: str) -> list:
    host = host_of(url)
    if not host:
        return []
    return [s for s in _sources() if any(
        base and (host == base or host.endswith("." + base))
        for base in (host_of(s.site), host_of(s.url)))]


def source_image_allowed(url: str, sightings: list, cfg=None) -> bool:
    """Source images need a current image grant and the source's credit link."""
    owners = sources_for_url(url)
    if not owners:
        return True
    credited = {host_of(s.get("source_url") or "") for s in sightings}
    return any(image_granted(s, cfg) and host_of(s.site) in credited for s in owners)
