"""Resolve internal source IDs to names for public deal credits."""

from functools import lru_cache

from ..config import load_sources


@lru_cache(maxsize=1)
def _display_names() -> dict[str, str]:
    primary, backup = load_sources()
    return {s.name: s.display_name or s.name for s in primary + backup}


def source_display_name(name: str) -> str:
    """Keep unknown/historical source IDs usable as credits."""
    return _display_names().get(name, name)
