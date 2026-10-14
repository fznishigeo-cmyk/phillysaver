"""Shared public picks and price-free text for social posts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from ..source_policy import host_of, sources_for_url
from ..conditions import normalize_conditions


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


_ACRONYMS = {"REACTION", "SDRAM", "HDMI", "OLED", "QLED", "USB"}
_SHOPPER_SPEC = re.compile(
    r'\d+(?:[./]\d+)?-?(?:packs?|pieces?|inch(?:es)?|mp|[kmgt]b|[km]hz|[kwv]|p|oz|ml|mm|cm|lbs?)',
    re.IGNORECASE,
)


def social_title(item: str, brand: str) -> str:
    """Keep a short shopper-readable name, without variants, models or prices."""
    original = _without_prices(item)
    words = original.split(",", 1)[0].split()
    brand_words = _without_prices(brand).split()

    def key(word):
        return word.casefold().removesuffix("s")

    filtered = []
    prefix = None
    for word in words:
        token = word.strip("()[]{}:;.!?")
        if not _SHOPPER_SPEC.fullmatch(token):
            mixed = len(token) >= 5 and re.search(r"[a-z]", token, re.I) and re.search(r"\d", token)
            sku = re.fullmatch(r"[\d/-]+", token) and len(re.sub(r"\D", "", token)) >= 5
            if mixed or sku:
                if prefix is None:
                    prefix = filtered.copy()
                continue
        filtered.append(word)
    # API titles may omit brand metadata. Brand/category wording before a
    # model can still repeat later ("KOBE Range Hoods ... Range Hood").
    if prefix and (not brand_words or key(prefix[0]) == key(brand_words[0])):
        brand_words = prefix
    brand_keys = {key(word) for word in brand_words}
    kept = []
    seen_brand = set()
    for i, word in enumerate(filtered):
        word_key = key(word)
        if word_key in brand_keys:
            # Prefer the later product wording over a repeated brand suffix
            # ("KOBE Range Hoods ... Range Hood"), but keep the brand first.
            if word_key in seen_brand or (0 < i < len(brand_words)
                    and any(key(later) == word_key for later in filtered[len(brand_words):])):
                continue
            seen_brand.add(word_key)
        if word_key in brand_keys or (not brand_words and i == 0):
            if word.isalpha() and word.isupper() and len(word) > 4 and word not in _ACRONYMS:
                word = word.title()
        kept.append(word)
    return (_word_trim(" ".join(kept), 70) or _word_trim(original, 70)
            or original[:70] or "Deal")


#: Wording of scarce stock: one person buys it and it is gone for everyone.
_SCARCE = re.compile(
    r"\b(?:only\s+\d+\s+left|\d+\s+left\s+in\s+stock|(?:last|one)\s+left|last\s+one|"
    r"limited\s+(?:stock|quantit(?:y|ies)|supply|sizes?)|while\s+supplies\s+last|"
    r"select\s+(?:sizes?|colou?rs?|styles?)|clearance|closeout|liquidation|final\s+sale)\b",
    re.IGNORECASE)
#: Sized apparel and shoes: a deep price is usually one size or style left.
_SIZED = re.compile(
    r"\b(?:jerseys?|t-?shirts?|tees?|shirts?|polos?|hoodies?|sweatshirts?|sweaters?|"
    r"jackets?|coats?|vests?|dress(?:es)?|skirts?|pants|jeans|leggings|shorts|socks|bras?|"
    r"underwear|swimsuits?|shoes|sneakers|boots|sandals|heels|wedges?|pumps|flats|loafers|"
    r"clogs|slippers)\b", re.IGNORECASE)
#: Our own Amazon-price lanes: Amazon itself sells the item at that price.
_AMAZON_SOLD = {"keepa", "keepa_amazon_drops"}


def plenty_of_stock(db, deal: dict, cfg) -> bool:
    """Social posts reach many people at once; a one-off (one unit, one size,
    clearance) is gone after the first click (operator, 2026-10-13/14)."""
    if deal.get("price_unconfirmed") or (deal.get("category") or "") in set(cfg.social_exclude_categories):
        return False
    words = " ".join([str(deal.get("item") or ""), *map(str, deal.get("how") or []),
                      *map(str, deal.get("stack_steps") or [])])
    if _SCARCE.search(words) or _SIZED.search(str(deal.get("item") or "")):
        return False
    if not cfg.social_require_stock_evidence:
        return True
    row = db.get_deal(deal["dedupe_key"]) if db is not None else None
    if row is None:
        return False
    sources = {str(s.get("source_name") or "") for s in row.get("sightings") or []}
    sources = {s for s in sources | {str(row.get("source_name") or "")} if s}
    independent = len(sources - _AMAZON_SOLD)
    if float(deal.get("pct_at_checkout") or 0) >= cfg.social_extreme_pct:
        return independent >= 2
    return bool(sources & _AMAZON_SOLD) or independent >= 2


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
                or (cfg.social_require_earning_link and not deal.get("earns"))
                or set(normalize_conditions(deal.get("conditions")))
                & set(normalize_conditions(cfg.social_exclude_conditions))
                or not plenty_of_stock(db, deal, cfg)):
            continue
        image = urlsplit(deal.get("image_url") or "")
        if sources_for_url(deal.get("image_url") or "") or any(
                (host := host_of(c.get("url") or "")) and image.hostname
                and (image.hostname == host or image.hostname.endswith("." + host))
                for c in deal.get("credits") or []):
            continue
        path = paths.get(str(deal.get("key") or "")[:8], "")
        if (image.scheme != "https" or not image.hostname
                or not re.fullmatch(r"/deal/[a-z0-9-]+/", path)
                or not (out / path.strip("/") / "index.html").is_file()):
            continue
        candidates.append((deal, path))
    return sorted(candidates, key=lambda row: row[0]["pick"])
