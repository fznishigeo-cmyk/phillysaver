"""Static site publisher: writes deals.json / health.json and copies templates.

Storage policy: only extracted facts, a product link, and credit to the source
are written — never full post text or images.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from ..util import iso, parse_iso

logger = logging.getLogger("dealradar.publish.site")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def rewrite_affiliate(url: str) -> str:
    """Return the product URL unchanged.

    Affiliate rewriting is intentionally NOT implemented. This function exists
    as the single injection point for it later: nothing else in the codebase
    may rewrite outbound links. When (if ever) affiliate tagging is added, it
    happens here and only here.
    """
    return url


def _serialize_deal(deal: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Project a deal dict to the public JSON shape (facts + link + credit)."""
    first_seen = parse_iso(deal.get("first_seen_at") or "")
    age_hours: float | None = None
    if first_seen is not None:
        age_hours = round((now - first_seen).total_seconds() / 3600.0, 2)
    credits = [
        {"name": s.get("source_name", ""), "url": s.get("source_url", "")}
        for s in (deal.get("sightings") or [])
    ]
    status = deal.get("status", "alive")
    return {
        "dedupe_key": deal.get("dedupe_key", ""),
        "store": deal.get("store", ""),
        "item": deal.get("item", ""),
        "brand": deal.get("brand", ""),
        "url": rewrite_affiliate(deal.get("canonical_url") or ""),
        "final_price": deal.get("final_price"),
        "regular_price": deal.get("regular_price"),
        "pct_at_checkout": deal.get("pct_at_checkout"),
        "pct_with_rebates": deal.get("pct_with_rebates"),
        "abs_savings": deal.get("abs_savings"),
        "basis": deal.get("basis", ""),
        "flags": deal.get("flags") or [],
        "codes": deal.get("codes") or [],
        "stack_steps": deal.get("stack_steps") or [],
        "rebates": deal.get("rebates") or [],
        "deferred_value": deal.get("deferred_value", ""),
        "conditions": deal.get("conditions") or [],
        "online_only": deal.get("online_only", "unknown"),
        "shipping_cost_or_threshold": deal.get("shipping_cost_or_threshold", ""),
        "quantity_or_unit": deal.get("quantity_or_unit", ""),
        "region": deal.get("region", "US"),
        "currency": deal.get("currency", "USD"),
        "expires_at": deal.get("expires_at", ""),
        "badge": deal.get("verify_tier", "unverified"),
        "status": status,
        "alive": status == "alive",
        "first_seen_at": deal.get("first_seen_at", ""),
        "last_seen_at": deal.get("last_seen_at", ""),
        "confidence": deal.get("confidence"),
        "score": deal.get("score"),
        "age_hours": age_hours,
        "credits": credits,
    }


def render_site(db, cfg, now: datetime, *, out_dir: str | Path | None = None,
                deals: list[dict[str, Any]] | None = None,
                last_run: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write deals.json, health.json and the two HTML templates to out_dir."""
    if deals is None:
        from dealradar import rank  # lazy: avoid hard import cycle at module load

        deals = rank.published_deals(db, cfg, now)

    out = Path(out_dir) if out_dir is not None else Path(cfg.site_dir)
    out.mkdir(parents=True, exist_ok=True)

    serialized = [_serialize_deal(d, now) for d in deals]
    deals_doc = {
        "meta": {
            "generated_at": iso(now),
            "last_run": last_run or {},
            "disclosure": cfg.ftc_disclosure,
            "count": len(serialized),
            "floors": {
                "min_pct": cfg.min_pct,
                "min_abs_savings": cfg.min_abs_savings,
            },
        },
        "deals": serialized,
    }
    (out / "deals.json").write_text(
        json.dumps(deals_doc, indent=2), encoding="utf-8"
    )

    sources = []
    for row in db.all_source_health():
        until = parse_iso(row.get("tripped_until") or "")
        entry = dict(row)
        entry["tripped"] = until is not None and now < until
        sources.append(entry)
    health_doc = {
        "generated_at": iso(now),
        "sources": sources,
        "last_run": last_run or {},
    }
    (out / "health.json").write_text(
        json.dumps(health_doc, indent=2), encoding="utf-8"
    )

    for name in ("index.html", "health.html"):
        shutil.copyfile(TEMPLATES_DIR / name, out / name)

    logger.info("site rendered: %d deals -> %s", len(serialized), out)
    return {"published": len(serialized), "out_dir": str(out)}
