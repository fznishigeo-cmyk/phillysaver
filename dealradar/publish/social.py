"""Shared public picks and price-free text for social posts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit


def _word_trim(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit + 1].rsplit(" ", 1)[0] if " " in text[:limit + 1] else ""


def _without_prices(text: str) -> str:
    # Item names can contain a quoted price or a gift-card denomination.
    text = re.sub(r"(?:[$£€]\s*[\d,.]+|\bUSD\s*[\d,.]+|[\d,.]+\s*USD\b)", "", text,
                  flags=re.IGNORECASE)
    return " ".join(text.replace("$", "").split())


def _candidates(db, cfg, posted: dict) -> list[tuple[dict, str]]:
    out = Path(cfg.site_dir)
    deals = json.loads((out / "deals.json").read_text())["deals"]
    paths = json.loads(db.meta_get("seo_deal_paths") or "{}")
    if not isinstance(paths, dict):
        raise ValueError("invalid deal paths")
    candidates = []
    for deal in deals:
        if (deal.get("locked") or deal.get("status") != "alive" or not deal.get("pick")
                or not deal.get("dedupe_key") or deal["dedupe_key"] in posted
                or (cfg.social_require_earning_link and not deal.get("earns"))):
            continue
        image = urlsplit(deal.get("image_url") or "")
        path = paths.get(str(deal.get("key") or "")[:8], "")
        if (image.scheme != "https" or not image.hostname
                or not re.fullmatch(r"/deal/[a-z0-9-]+/", path)
                or not (out / path.strip("/") / "index.html").is_file()):
            continue
        candidates.append((deal, path))
    return sorted(candidates, key=lambda row: row[0]["pick"])
