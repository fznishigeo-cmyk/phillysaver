"""Amazon root names and ASIN roots learned from existing Keepa responses."""
from __future__ import annotations

import json

NAMES_META = "keepa_category_names"
ROOTS_META = "keepa_category_roots"
#: ASIN -> root id entries kept (the newest), so the meta value stays small.
MAX_ROOTS = 6000

_NAME_MAP = {
    "beauty & personal care": "beauty", "health & household": "health",
    "grocery & gourmet food": "grocery", "home & kitchen": "home",
    "tools & home improvement": "home", "appliances": "kitchen",
    "clothing, shoes & jewelry": "fashion", "electronics": "electronics",
    "cell phones & accessories": "electronics", "camera & photo": "electronics",
    "musical instruments": "electronics", "computers": "computers",
    "computers & accessories": "computers", "video games": "gaming",
    "toys & games": "toys_games", "baby": "kids_baby", "baby products": "kids_baby",
    "sports & outdoors": "sports", "patio, lawn & garden": "outdoors",
    "automotive": "auto", "pet supplies": "pets", "office products": "office",
    "books": "books_media", "kindle store": "books_media",
    "movies & tv": "books_media", "cds & vinyl": "books_media",
}


def category_from_name(name: str, title_category: str = "") -> str:
    name = name.strip().casefold()
    if name == "home & kitchen" and title_category == "kitchen":
        return "kitchen"
    return _NAME_MAP.get(name, "")


def cached_meta(db, key: str) -> dict:
    try:
        value = json.loads(db.meta_get(key) or "{}") if db is not None else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def root_id(row: dict) -> str:
    value = row.get("rootCat") or row.get("rootCategory")
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 9223372036854775807:
        return str(value)
    return ""


def remember_roots(db, rows: list[dict]) -> None:
    if db is None:
        return
    roots = cached_meta(db, ROOTS_META)
    updated = dict(roots)
    for row in rows:
        asin = str(row.get("asin") or "").strip().upper()
        root = root_id(row)
        if asin and root:
            updated[asin] = root
    if updated != roots:
        # state.db is committed every run: keep the newest ASINs only.
        if len(updated) > MAX_ROOTS:
            updated = dict(list(updated.items())[-MAX_ROOTS:])
        db.meta_set(ROOTS_META, json.dumps(updated))
